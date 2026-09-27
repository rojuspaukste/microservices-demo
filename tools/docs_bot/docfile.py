"""Read and rewrite the parts of a component doc that docs-bot owns.

Managed blocks sit between `<!-- docs-bot:begin NAME -->` and `<!-- docs-bot:end NAME -->`.
Sections tagged `[LLM, cited]` are only ever proposed in a comment; `[owner]` is never touched.
"""
import re
from datetime import date

from .extract import Facts

NONE = "—"
LLM_SECTION = re.compile(r"^## ([^\n]+?) \[LLM, cited\][ \t]*\n(.*?)(?=^## |\Z)", re.S | re.M)


def _block_re(name: str) -> re.Pattern:
    return re.compile(rf"(<!-- docs-bot:begin {name} -->\n)(.*?)(\n<!-- docs-bot:end {name} -->)", re.S)


def read_block(text: str, name: str) -> str | None:
    m = _block_re(name).search(text)
    return m.group(2) if m else None


def replace_block(text: str, name: str, content: str) -> str:
    return _block_re(name).sub(lambda m: m.group(1) + content + m.group(3), text, count=1)


def set_meta(text: str, sha: str) -> str:
    """Point the meta line (and the visible 'Generated from' line) at the commit the facts came from."""
    short = sha[:7]
    text = re.sub(r"<!-- docs-bot:meta source-commit=\S+ -->", f"<!-- docs-bot:meta source-commit={short} -->", text)
    text = re.sub(r"Generated from commit `\w+`", f"Generated from commit `{short}`", text)
    return re.sub(r"last verified \d{4}-\d{2}-\d{2}", f"last verified {date.today().isoformat()}", text)


def llm_sections(text: str) -> dict[str, str]:
    """Body of every `## Title [LLM, cited]` section, keyed by title."""
    return {m.group(1): m.group(2).strip() for m in LLM_SECTION.finditer(text)}


def _cell(value: str | None) -> str:
    return NONE if value is None else f"`{value}`"


def config_rows(f: Facts) -> dict[str, tuple[str, str, str]]:
    """name -> (default in code, manifest value, read at). Includes manifest-only vars."""
    rows = {}
    for name in sorted(set(f.env) | set(f.manifest_env)):
        r = f.env.get(name)
        default = NONE if r is None else f"{NONE} (presence check)" if r.kind == "presence" else _cell(r.default)
        read_at = f"`{r.file}:{r.line}`" if r else f"{NONE} (not read)"
        rows[name] = (default, _cell(f.manifest_env.get(name)), read_at)
    return rows


def api_rows(f: Facts) -> dict[str, tuple[str, str]]:
    return {rpc: (f"`{req}`", f"`{resp}`") for rpc, req, resp in f.rpcs}


# block name -> (label, table header, row builder, compared field names; later columns are ignored)
BLOCKS = {
    "configuration": ("Configuration", ["Env var", "Default in code", "Manifest value", "Read at"],
                      config_rows, ("default", "manifest")),
    "api": ("API", ["RPC", "Request", "Response"], api_rows, ("request", "response")),
}


def render(header: list[str], rows: dict[str, tuple]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
    return "\n".join(lines + [f"| `{k}` | " + " | ".join(v) + " |" for k, v in rows.items()])


def parse_table(block: str) -> dict[str, tuple[str, ...]]:
    rows = {}
    for line in block.splitlines()[2:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows[cells[0].strip("`")] = tuple(cells[1:])
    return rows


def describe(label: str, old: dict, new: dict, fields: tuple[str, ...]) -> list[str]:
    """Human-readable row changes, comparing only the first len(fields) columns."""
    n = len(fields)
    out = [f"{label}: added `{k}` (" + ", ".join(f"{f} {v}" for f, v in zip(fields, new[k])) + ")"
           for k in new if k not in old]
    out += [f"{label}: removed `{k}`" for k in old if k not in new]
    out += [f"{label}: changed `{k}` (" + ", ".join(f"{f} {a} → {b}" for f, a, b in zip(fields, old[k], new[k]) if a != b) + ")"
            for k in new if k in old and old[k][:n] != new[k][:n]]
    return out


def sync(text: str, facts: Facts, blocks: list[str]) -> tuple[str, list[str]]:
    """Regenerate each managed block whose facts differ from the doc. Pure line shifts are not a change."""
    changes = []
    for name in blocks:
        label, header, rows_of, fields = BLOCKS[name]
        current = read_block(text, name)
        if current is None:
            continue
        new = rows_of(facts)
        if diff := describe(label, parse_table(current), new, fields):
            text = replace_block(text, name, render(header, new))
            changes += diff
    return text, changes


def fact_changes(base: Facts, head: Facts, blocks: list[str]) -> list[str]:
    """What this PR changed factually (base vs head), independent of the doc."""
    out = []
    for name in blocks:
        label, _, rows_of, fields = BLOCKS[name]
        out += describe(label, rows_of(base), rows_of(head), fields)
    return out
