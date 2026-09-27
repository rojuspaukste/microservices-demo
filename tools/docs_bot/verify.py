"""Check the LLM's claims against the head code before any of its prose reaches a human.

1. The cited file exists at head and the line range fits in it.
2. An identifier from the claim (a backticked name, or a word of 4+ characters) is in the cited lines ±2.
3. Env var names and ports mentioned in the draft exist in the head facts.
Draft sentences that cite failed claims, cite nothing, or mention unknown env vars/ports are removed.
"""
import re
from pathlib import PurePosixPath

from .extract import Facts

CITE = re.compile(r"([\w./-]+\.\w+):L?(\d+)(?:\s*[-–]\s*L?(\d+))?")
TICKED = re.compile(r"`([^`]+)`")
IDENT = re.compile(r"[A-Za-z_]\w*")
WORD = re.compile(r"[A-Za-z_]\w{3,}")
ENV_NAME = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
PORT = re.compile(r"(?:\bport\s+`?|:)(\d{2,5})\b", re.I)
LIST_ITEM = re.compile(r"^(\s*(?:[-*+]|\d+\.)\s+)?(.*)$")
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z`*\"])")
STOP = {"that", "this", "with", "from", "into", "when", "then", "than", "each", "only", "also", "which",
        "does", "have", "been", "were", "will", "they", "their", "there", "here", "every", "instead", "now"}


def _name(path) -> str:
    return PurePosixPath(str(path)).name


def _tokens(code: str) -> set[str]:
    """Lower-cased identifiers plus their snake_case / CamelCase parts."""
    out = set()
    for tok in IDENT.findall(code):
        out.add(tok.lower())
        out.update(p.lower() for p in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", tok))
    return out


def check_claim(claim: dict, files: dict[str, str]) -> tuple[str, int, int] | None:
    """(file name, first, last) if the claim holds up, else None."""
    matches = [p for p in files if p == claim.get("file") or _name(p) == _name(claim.get("file", ""))]
    lines = claim.get("lines")
    lines = [lines] if isinstance(lines, int) else lines
    try:
        first, last = int(lines[0]), int(lines[-1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if len(matches) != 1:
        return None
    code = files[matches[0]].splitlines()
    if not 1 <= first <= last <= len(code):
        return None
    window = _tokens("\n".join(code[max(first - 3, 0):last + 2]))
    text = str(claim.get("text", ""))
    names = {n.lower() for t in TICKED.findall(text) for n in IDENT.findall(t)}
    words = {w.lower() for w in WORD.findall(TICKED.sub(" ", text))} - STOP
    return (_name(matches[0]), first, last) if (names | words) & window else None


def _backed(sentence: str, ok: list[tuple[str, int, int]]) -> bool:
    cites = CITE.findall(sentence)
    return bool(cites) and all(
        any(_name(f) == name and int(a) <= last and first <= int(b or a) for name, first, last in ok)
        for f, a, b in cites)


def _unknown_facts(sentence: str, facts: Facts, code: str) -> bool:
    body = CITE.sub("", sentence)
    known_env = set(facts.env) | set(facts.manifest_env)
    if any(n not in known_env and n not in code for n in ENV_NAME.findall(body)):
        return True  # SCREAMING_CASE name that is neither an env var nor a constant in the code
    values = [r.default for r in facts.env.values()] + list(facts.manifest_env.values())
    known_ports = {str(p) for p in facts.ports} | set(re.findall(r"\d+", " ".join(v for v in values if v)))
    return any(p not in known_ports for p in PORT.findall(body))


def filter_draft(text: str, ok: list, facts: Facts, code: str) -> tuple[str, int]:
    kept, dropped = [], 0
    for line in text.splitlines():
        prefix, body = LIST_ITEM.match(line).groups()
        if not re.search(r"[A-Za-z]", body):  # blank lines, fences, rules
            kept.append(line)
            continue
        sentences = SENTENCE.split(body)
        good = [s for s in sentences if _backed(s, ok) and not _unknown_facts(s, facts, code)]
        dropped += len(sentences) - len(good)
        if good:
            kept.append((prefix or "") + " ".join(good))
    return "\n".join(kept).strip(), dropped


def verify(data: dict, files: dict[str, str], facts: Facts) -> tuple[dict[str, str], dict]:
    """Returns (verified drafts per section, counts for the metrics)."""
    claims = data.get("claims", [])
    ok = [r for c in claims if (r := check_claim(c, files))]
    stats = {"claims_total": len(claims), "claims_dropped": len(claims) - len(ok), "sentences_dropped": 0}
    if 2 * len(ok) < len(claims):  # more than half failed: don't trust any of the draft
        return {}, stats
    code, drafts = "\n".join(files.values()), {}
    for section, text in data.get("draft", {}).items():
        kept, dropped = filter_draft(text, ok, facts, code)
        stats["sentences_dropped"] += dropped
        if kept:
            drafts[section] = kept
    return drafts, stats
