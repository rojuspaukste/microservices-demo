"""Entry point, run by .github/workflows/docs-check.yml as `python -m tools.docs_bot.main`.

Fail-open: whatever happens, exit 0 and never block the merge.
Local runs: without GITHUB_TOKEN the comment is printed instead of posted;
DOCS_BOT_DRY_RUN=1 writes the doc but does not commit or push.
"""
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from . import config, docfile, extract, github, gitutil, llm, metrics, verify

BOT_TAG = "[docs-bot]"


@dataclass
class Report:
    component: config.Component
    changes: list[str] = field(default_factory=list)  # script-owned block changes
    new_doc: str | None = None
    analysis: dict | None = None                       # normalised LLM output
    drafts: dict[str, str] = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    llm_calls: int = 0
    llm_error: str | None = None
    commit_sha: str | None = None

    @property
    def asks(self) -> bool:
        return bool(self.analysis and self.analysis["behaviour_change"])


def check(comp: config.Component, cfg: config.Config, base: str, head: str, run: metrics.Run) -> Report:
    rep = Report(comp)
    base_facts = extract.extract(comp, lambda p: gitutil.show(base, p))
    head_facts = extract.extract(comp, lambda p: gitutil.show(head, p))
    blocks = ["configuration"] + (["api"] if comp.proto_file else [])
    doc = Path(comp.doc).read_text(encoding="utf-8")
    new_doc, rep.changes = docfile.sync(doc, head_facts, blocks)
    if rep.changes:
        rep.new_doc = new_doc
        run.fact_changes += rep.changes

    code_diff = gitutil.diff(base, head, comp.code_files)
    if not code_diff.strip():
        return rep
    numbered = gitutil.number_lines(code_diff)
    if len(numbered) > cfg.max_diff_chars:
        numbered = numbered[:cfg.max_diff_chars] + "\n[diff truncated]"
    sections = docfile.sections(doc, docfile.LLM_TAG)
    prompt = llm.build_prompt(comp.name, numbered, sections, docfile.fact_changes(base_facts, head_facts, blocks),
                              owner=docfile.sections(doc, docfile.OWNER_TAG))
    res = llm.analyse(prompt, os.environ.get(cfg.model_env), os.environ.get("GEMINI_API_KEY"))
    rep.llm_calls = 1 if res.attempts else 0
    run.llm_called |= bool(res.attempts)
    run.llm_attempts += res.attempts
    run.llm_latency_ms += res.latency_ms
    if res.tokens:
        run.tokens = (run.tokens or 0) + res.tokens
    if res.data is None:
        rep.llm_error = run.llm_error = res.error
        return rep

    rep.analysis = res.data
    run.behaviour_change = bool(run.behaviour_change) or rep.asks
    if rep.asks:
        run.confidence = run.confidence or rep.analysis["confidence"]
        rep.analysis["draft"] = {k: v for k, v in rep.analysis["draft"].items() if k in sections}  # never [owner]
        files = {p: t for p in comp.code_files if (t := gitutil.show(head, p)) is not None}
        rep.drafts, rep.stats = verify.verify(rep.analysis, files, head_facts)
        run.claims_total += rep.stats["claims_total"]
        run.claims_dropped += rep.stats["claims_dropped"]
    return rep


def render_comment(reports: list[Report], head: str) -> str | None:
    parts = []
    for r in reports:
        if not (r.changes or r.asks):
            continue
        parts.append(f"### 📘 docs-bot — {r.component.name}")
        if r.changes:
            where = f"committed in {r.commit_sha[:7]}" if r.commit_sha else "written, not committed (dry run)"
            parts += ["**Updated automatically (facts):**"] + [f"- {c} → {where}" for c in r.changes]
        if r.asks:
            a = r.analysis
            parts += ["", "**Needs your input (behaviour change detected):**", f"> {a['summary']}"]
            if a["affected_sections"]:
                parts.append("> Possibly outdated: " + ", ".join(f'"{s}"' for s in a["affected_sections"]))
            parts += ["", f"**Question:** {a['question_for_author']}"]
            kept = r.stats["claims_total"] - r.stats["claims_dropped"]
            for section, draft in r.drafts.items():
                parts += ["", f'<details><summary>Suggested draft for "{section}" '
                              f'({kept}/{r.stats["claims_total"]} cited claims verified)</summary>',
                          "", "```markdown", draft, "```", "", "</details>"]
            if a["draft"] and not r.drafts:
                parts += ["", "_Draft withheld: its citations did not check out against the code._"]
        elif r.llm_error:
            parts += ["", f"_Behaviour check skipped (LLM unavailable: {llm.short_reason(r.llm_error)})._"]
        confidence = f"Confidence: {r.analysis['confidence']} · " if r.asks else ""
        calls = f"{r.llm_calls} LLM call" + ("" if r.llm_calls == 1 else "s")
        parts += ["", f"_{confidence}{calls} · checked `{head[:7]}` · docs-bot PoC_", ""]
    return "\n".join([github.MARKER, *parts]) if parts else None


def execute(run: metrics.Run, ctx: dict) -> None:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    pr = event["pull_request"]
    run.pr = pr["number"]
    ctx["repo"] = os.environ.get("GITHUB_REPOSITORY") or event["repository"]["full_name"]
    base, head, branch = pr["base"]["sha"], pr["head"]["sha"], pr["head"]["ref"]
    if BOT_TAG in gitutil.commit_message(head):
        run.note = "Head commit is a docs-bot commit; nothing to do."
        return
    cfg = config.load()
    components = config.touched_components(cfg, gitutil.changed_files(base, head))
    run.components_touched = [c.name for c in components]
    if not components:
        run.note = "No documented components touched."
        return
    reports = [check(c, cfg, base, head, run) for c in components]

    dry_run = os.environ.get("DOCS_BOT_DRY_RUN") == "1"
    to_commit = [r for r in reports if r.new_doc]
    if to_commit and not dry_run and gitutil.git("rev-parse", "HEAD").strip() != head:
        raise RuntimeError(f"checked-out branch is not at the PR head {head[:7]}; not committing stale facts")
    for r in to_commit:
        Path(r.component.doc).write_text(docfile.set_meta(r.new_doc, head), encoding="utf-8", newline="\n")
        if not dry_run:
            r.commit_sha = github.commit(
                r.component.doc, f"docs({r.component.name}): sync configuration/api with code {BOT_TAG}")
    if to_commit and not dry_run:
        github.push(branch)
        run.factual_update_committed = True

    asks = any(r.asks for r in reports)
    run.action = ("commit+ask" if asks else "commit") if to_commit else ("ask" if asks else "none")
    token = os.environ.get("GITHUB_TOKEN")
    if body := render_comment(reports, head):
        github.upsert_comment(ctx["repo"], run.pr, body, token)
    else:
        skipped = f" Behaviour check skipped (LLM unavailable: {llm.short_reason(run.llm_error)})." if run.llm_error else ""
        run.note = "No doc change needed." + skipped
        # Only refresh a comment left by an earlier run; never open a new one just to say "all good".
        github.upsert_comment(ctx["repo"], run.pr, f"{github.MARKER}\n✅ docs-bot: docs are in sync as of "
                              f"`{head[:7]}`.{skipped}", token, create=False)


def main() -> int:
    start, run, ctx = time.monotonic(), metrics.Run(), {}
    try:
        execute(run, ctx)
    except Exception as e:  # fail open: say so, never block the PR
        traceback.print_exc()
        run.action, run.error = "skipped", f"{type(e).__name__}: {e} {getattr(e, 'stderr', '') or ''}".strip()
        if run.pr and ctx.get("repo"):
            try:
                github.upsert_comment(ctx["repo"], run.pr, f"{github.MARKER}\n⚠️ docs check skipped "
                                      f"({type(e).__name__}); see the workflow run for details.",
                                      os.environ.get("GITHUB_TOKEN"))
            except Exception:
                traceback.print_exc()
    run.duration_ms = int((time.monotonic() - start) * 1000)
    try:
        metrics.write(run)
    except Exception:
        traceback.print_exc()
    return 0


if __name__ == "__main__":
    sys.exit(main())
