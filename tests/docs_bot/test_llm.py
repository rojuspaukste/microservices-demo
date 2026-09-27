import json
from types import SimpleNamespace

from google.genai import errors

from tools.docs_bot import llm

ANSWER = {"behaviour_change": True, "confidence": "high", "affected_sections": ["How it works [LLM, cited]"],
          "summary": "s", "question_for_author": "q", "draft": {"How it works": "d"}, "claims": []}


def api_error(code: int) -> errors.APIError:
    return errors.APIError(code, {"error": {"code": code, "message": "boom", "status": "X"}})


class FakeClient:
    """Stands in for genai.Client: each call pops the next scripted response or error."""

    def __init__(self, *script):
        self.script, self.calls = list(script), []
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls.append((model, contents, config.temperature, config.response_mime_type))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(text=item, usage_metadata=SimpleNamespace(total_token_count=1234))


def run(client):
    sleeps = []
    res = llm.analyse("prompt", "gemini-flash", "key", client=client, sleep=sleeps.append)
    return res, sleeps


def test_success_returns_normalised_json_and_usage():
    client = FakeClient(json.dumps(ANSWER))
    res, sleeps = run(client)
    assert res.error is None and res.attempts == 1 and res.tokens == 1234 and sleeps == []
    assert res.data["affected_sections"] == ["How it works"]
    assert client.calls == [("gemini-flash", "prompt", 0, "application/json")]


def test_retries_429_and_5xx_with_backoff():
    res, sleeps = run(FakeClient(api_error(429), api_error(503), json.dumps(ANSWER)))
    assert res.data["behaviour_change"] is True
    assert res.attempts == 3 and sleeps == [2, 4]


def test_gives_up_after_three_attempts():
    res, sleeps = run(FakeClient(api_error(429), api_error(429), api_error(429)))
    assert res.data is None and "429" in res.error
    assert res.attempts == 3 and sleeps == [2, 4]


def test_client_errors_are_not_retried():
    res, sleeps = run(FakeClient(api_error(400)))
    assert res.data is None and res.attempts == 1 and sleeps == []


def test_invalid_json_degrades_instead_of_raising():
    res, _ = run(FakeClient("not json"))
    assert res.data is None and res.error.startswith("JSONDecodeError")


def test_missing_key_skips_the_call():
    res = llm.analyse("prompt", "gemini-flash", None)
    assert res.data is None and res.attempts == 0 and "not set" in res.error


def test_normalise_is_strict_about_booleans_and_shapes():
    out = llm.normalise([{"behaviour_change": "true", "claims": ["junk", {"text": "t"}]}])
    assert out["behaviour_change"] is False
    assert out["claims"] == [{"text": "t"}] and out["draft"] == {} and out["confidence"] == "low"


def test_prompt_contains_inputs():
    prompt = llm.build_prompt("svc", "  71 +x = 1", {"How it works": "body"}, ["Configuration: added `X`"])
    assert "`svc`" in prompt and "  71 +x = 1" in prompt and "### How it works\nbody" in prompt
    assert "- Configuration: added `X`" in prompt and '"claims"' in prompt
