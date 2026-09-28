"""Commit the LLM drafts from the docs-bot comment once a person ticks its "Apply these drafts" box.

Run by .github/workflows/docs-apply.yml on `issue_comment: edited`, always with the bot code from
the default branch. The drafts are read back from the comment itself, so exactly the text the
person approved is what gets committed. Fail-open like main.py: on error the box is unticked
again with the reason, so it can be retried.
"""
import json
import os
import re
import sys
import traceback
from pathlib import Path

from . import config, docfile, github

CHECKBOX = re.compile(r"^- \[([ xX])\] .*<!-- docs-bot:apply (\S+) -->[ \t]*$", re.M)
DRAFT = re.compile(r'Suggested draft for "([^"]+)".*?````markdown\n(.*?)\n````', re.S)
WRITERS = {"admin", "maintain", "write"}


def checkbox(component: str, note: str = "") -> str:
    return (f"- [ ] **Apply these drafts** to the doc (commits them to this PR){note} "
            f"<!-- docs-bot:apply {component} -->")


def newly_ticked(body: str, before: str | None) -> list[str]:
    """Components whose box is ticked now but was not ticked before this edit."""
    was_ticked = {name for mark, name in CHECKBOX.findall(before or "") if mark != " "}
    return [name for mark, name in CHECKBOX.findall(body) if mark != " " and name not in was_ticked]


def drafts_for(body: str, component: str) -> dict[str, str]:
    """The drafts shown in this component's part of the comment, keyed by section title."""
    parts = re.split(r"^### 📘 docs-bot — ", body, flags=re.M)
    return dict(DRAFT.findall(next((p for p in parts if p.startswith(component + "\n")), "")))


def execute(event: dict, repo: str, token: str) -> str:
    comment, user = event["comment"], event["sender"]["login"]
    if github.MARKER not in comment["body"] or "pull_request" not in event["issue"]:
        return "Not the docs-bot comment on a pull request."
    names = newly_ticked(comment["body"], event.get("changes", {}).get("body", {}).get("from"))
    if not names:
        return "No newly ticked box."
    if github.permission(repo, user, token) not in WRITERS:
        return f"@{user} has no write access; nothing applied."
    pr = github.get_pr(repo, event["issue"]["number"], token)
    if pr["head"]["repo"]["full_name"] != repo:
        return "Pull request comes from a fork; nothing applied."

    components = {c.name: c for c in config.load().components}
    body, done = comment["body"], []
    for name in names:
        doc = components[name].doc
        text, blob = github.get_file(repo, doc, pr["head"]["ref"], token)
        allowed = docfile.sections(text, docfile.LLM_TAG)  # never [owner] or script-owned parts
        drafts = {s: d for s, d in drafts_for(body, name).items() if s in allowed}
        if drafts:
            for section, draft in drafts.items():
                text = docfile.replace_section(text, docfile.LLM_TAG, section, draft)
            sha = github.put_file(repo, doc, pr["head"]["ref"], text, blob,
                                  f"docs({name}): apply suggested prose approved by @{user} [docs-bot]", token)
            line = f"✅ Drafts applied in {sha[:7]}, approved by @{user}."
            done.append(f"{name}: {', '.join(drafts)} → {sha[:7]}")
        else:
            line = "⚠️ Nothing applied: no drafts found for this doc's LLM sections."
        body = CHECKBOX.sub(lambda m: line if m.group(2) == name else m.group(0), body)
    github.update_comment(comment["url"], body, token)
    return "Applied " + "; ".join(done) if done else "Nothing applied."


def main() -> int:
    event, token = {}, os.environ.get("GITHUB_TOKEN", "")
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        result = execute(event, os.environ["GITHUB_REPOSITORY"], token)
    except Exception as e:  # fail open: untick the box with the reason so it can be retried
        traceback.print_exc()
        result = f"Skipped: {type(e).__name__}: {e}"
        try:
            note = f" (⚠️ last try failed: {type(e).__name__})"
            body = CHECKBOX.sub(lambda m: checkbox(m.group(2), note) if m.group(1) != " " else m.group(0),
                                event["comment"]["body"])
            github.update_comment(event["comment"]["url"], body, token)
        except Exception:
            traceback.print_exc()
    print(result)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### 📘 docs-bot apply\n\n{result}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
