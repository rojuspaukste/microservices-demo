"""One Gemini call per component: JSON out, temperature 0, retries on 429/5xx. Never raises."""
import json
import time
from dataclasses import dataclass

SCHEMA = """{
  "behaviour_change": true,
  "confidence": "high|medium|low",
  "affected_sections": ["How it works", "What it does"],
  "summary": "one sentence: what changed in behaviour",
  "question_for_author": "one question about the intent of the change",
  "draft": {"How it works": "markdown body with (file.py:L1-L2) citations"},
  "claims": [{"text": "claim naming the `identifiers` it is about", "file": "path/to/file.py", "lines": [1, 2]}]
}"""

PROMPT = """You check whether a pull request makes the documentation of the `{component}` service outdated.
Decide if the code diff changes runtime behaviour so that the documentation below is no longer accurate.

Rules:
- Refactors, renames, logging, formatting and comments are NOT behaviour changes.
- Env vars, defaults, manifest values, ports and RPC lists are kept in script-owned tables. Do not restate
  them in drafts. A change that only adds or changes one of them is not a behaviour change by itself.
- Cite code as (file.py:L10-L12) using the head line numbers printed at the start of each diff line.
- Every sentence in a draft must contain a citation, and every citation must match an entry in "claims".
- In each claim, put the identifiers it is about in backticks. "lines" is [first, last] in the head file.
- Draft only the sections in affected_sections: the full replacement body (no heading), same style and length.
- The owner notes are human-written caveats. Never draft them, and never contradict them: do not claim a
  guarantee (e.g. stable or reproducible results) that an owner note rules out.
- If nothing needs to change, return behaviour_change false with empty affected_sections, draft and claims.

Respond with JSON only, in this shape:
{schema}

## Current documentation (the sections you may draft)
{sections}

## Owner notes (read-only context)
{owner}

## Fact changes in this PR (context only; already handled by scripts)
{facts}

## Code diff (head line numbers on the left)
{diff}
"""


@dataclass
class LLMResult:
    data: dict | None = None
    error: str | None = None
    attempts: int = 0
    latency_ms: int = 0
    tokens: int | None = None


def _as_markdown(sections: dict[str, str]) -> str:
    return "\n\n".join(f"### {k}\n{v}" for k, v in sections.items()) or "(none)"


def build_prompt(component: str, diff: str, sections: dict[str, str], facts: list[str],
                 owner: dict[str, str] | None = None) -> str:
    return PROMPT.format(component=component, schema=SCHEMA, diff=diff, sections=_as_markdown(sections),
                         owner=_as_markdown(owner or {}), facts="\n".join(f"- {f}" for f in facts) or "(none)")


def _section(name) -> str:
    return str(name).split(" [")[0].strip()  # "How it works [LLM, cited]" -> "How it works"


def normalise(data) -> dict:
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return {
        "behaviour_change": data.get("behaviour_change") is True,
        "confidence": str(data.get("confidence") or "low"),
        "affected_sections": [_section(s) for s in data.get("affected_sections") or []],
        "summary": str(data.get("summary") or ""),
        "question_for_author": str(data.get("question_for_author") or ""),
        "draft": {_section(k): str(v) for k, v in (data.get("draft") or {}).items()},
        "claims": [c for c in data.get("claims") or [] if isinstance(c, dict)],
    }


def analyse(prompt: str, model: str | None, api_key: str | None, client=None,
            attempts: int = 3, sleep=time.sleep) -> LLMResult:
    res = LLMResult()
    if client is None and not (api_key and model):
        res.error = "GEMINI_API_KEY or model id not set"
        return res
    start = time.monotonic()
    try:
        from google import genai
        from google.genai import errors, types
        client = client or genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=60_000))
        config = types.GenerateContentConfig(temperature=0, response_mime_type="application/json")
        while True:
            res.attempts += 1
            try:
                response = client.models.generate_content(model=model, contents=prompt, config=config)
                break
            except errors.APIError as e:
                if res.attempts >= attempts or not (e.code == 429 or e.code >= 500):
                    raise
                sleep(2 ** res.attempts)
        res.data = normalise(json.loads(response.text))
        usage = getattr(response, "usage_metadata", None)
        res.tokens = getattr(usage, "total_token_count", None)
    except Exception as e:  # degrade gracefully: the factual path still runs without the LLM
        res.error = f"{type(e).__name__}: {e}"
    res.latency_ms = int((time.monotonic() - start) * 1000)
    return res
