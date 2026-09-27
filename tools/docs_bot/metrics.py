"""Run record (docs-bot-run.json) and the job summary table."""
import json
import os
from dataclasses import asdict, dataclass, field


@dataclass
class Run:
    pr: int | None = None
    components_touched: list[str] = field(default_factory=list)
    fact_changes: list[str] = field(default_factory=list)
    factual_update_committed: bool = False
    llm_called: bool = False
    llm_latency_ms: int = 0
    llm_attempts: int = 0
    tokens: int | None = None
    behaviour_change: bool | None = None
    confidence: str | None = None
    claims_total: int = 0
    claims_dropped: int = 0
    action: str = "none"  # commit | ask | commit+ask | none | skipped
    error: str | None = None
    llm_error: str | None = None
    note: str = ""
    duration_ms: int = 0


def write(run: Run, path: str = "docs-bot-run.json") -> None:
    record = asdict(run)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    lines = ["### 📘 docs-bot", "", run.note or f"Action: `{run.action}`", "", "| Metric | Value |", "| --- | --- |"]
    for key, value in record.items():
        value = "<br>".join(value) if isinstance(value, list) else "—" if value is None else value
        lines.append(f"| {key} | {value} |")
    text = "\n".join(lines) + "\n"
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)
