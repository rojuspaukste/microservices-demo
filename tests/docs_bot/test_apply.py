"""The "Apply these drafts" checkbox: rendered by main.py, acted on by apply.py (GitHub faked)."""
import json
from pathlib import Path

import pytest

from tools.docs_bot import apply, docfile, github, main

ROOT = Path(__file__).resolve().parents[2]
DRAFTS = {"What it does": "Returns product IDs picked per user (recommendation_server.py:79-80).",
          "How it works": "1. Calls `ListProducts` (recommendation_server.py:73).\n"
                          "2. Samples with a generator seeded by `user_id` (recommendation_server.py:79-80)."}


@pytest.fixture
def comment(component) -> str:
    analysis = {"behaviour_change": True, "confidence": "high", "affected_sections": list(DRAFTS),
                "summary": "s", "question_for_author": "q", "draft": DRAFTS}
    report = main.Report(component, analysis=analysis, drafts=DRAFTS, llm_calls=1,
                         stats={"claims_total": 2, "claims_dropped": 0})
    return main.render_comment([report], "a" * 40)


def tick(body: str) -> str:
    return body.replace("- [ ] **Apply these drafts**", "- [x] **Apply these drafts**")


@pytest.fixture
def fake_github(monkeypatch, base_files, component):
    """Records every GitHub write; the PR's doc starts as the seed doc."""
    calls = {"put": [], "comment": []}
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(github, "permission", lambda repo, user, token: "write")
    monkeypatch.setattr(github, "get_pr", lambda repo, n, token: {"head": {"ref": "feature", "repo": {"full_name": repo}}})
    monkeypatch.setattr(github, "get_file", lambda repo, path, ref, token: (base_files[component.doc], "blob1"))
    monkeypatch.setattr(github, "put_file", lambda *a: calls["put"].append(a) or "abcdef1234")
    monkeypatch.setattr(github, "update_comment", lambda url, body, token: calls["comment"].append(body))
    return calls


def event(body: str, before: str, user: str = "rojus") -> dict:
    return {"comment": {"body": body, "url": "https://api.github.com/c/1"}, "changes": {"body": {"from": before}},
            "issue": {"number": 4, "pull_request": {}}, "sender": {"login": user}}


def test_comment_round_trip(comment):
    assert apply.checkbox("recommendationservice") in comment
    assert apply.newly_ticked(comment, None) == []
    assert apply.newly_ticked(tick(comment), comment) == ["recommendationservice"]
    assert apply.newly_ticked(tick(comment), tick(comment)) == []  # already ticked: no second apply
    assert apply.drafts_for(tick(comment), "recommendationservice") == DRAFTS


def test_ticking_commits_the_drafts_and_marks_the_comment(fake_github, comment, base_files, component):
    result = apply.execute(event(tick(comment), comment), "me/demo", "token")
    assert result == "Applied recommendationservice: What it does, How it works → abcdef1"

    (repo, path, branch, text, blob, message, _), = fake_github["put"]
    assert (repo, path, branch, blob) == ("me/demo", component.doc, "feature", "blob1")
    assert message == "docs(recommendationservice): apply suggested prose approved by @rojus [docs-bot]"
    assert docfile.sections(text, docfile.LLM_TAG) == DRAFTS
    seed = base_files[component.doc]
    assert docfile.sections(text, docfile.OWNER_TAG) == docfile.sections(seed, docfile.OWNER_TAG)
    assert docfile.read_block(text, "configuration") == docfile.read_block(seed, "configuration")
    assert "\n\n## Where it fits [extracted]" in text  # spacing before the next heading is kept

    (new_comment,) = fake_github["comment"]
    assert "✅ Drafts applied in abcdef1, approved by @rojus." in new_comment
    assert "docs-bot:apply" not in new_comment  # the box is gone, so it can't be applied twice


def test_owner_sections_are_never_written_even_if_the_comment_is_edited(fake_github, comment, base_files, component):
    injected = tick(comment).replace('Suggested draft for "What it does"', 'Suggested draft for "Gotchas & history"')
    apply.execute(event(injected, comment), "me/demo", "token")
    (_, _, _, text, *_), = fake_github["put"]
    seed = base_files[component.doc]
    assert docfile.sections(text, docfile.OWNER_TAG) == docfile.sections(seed, docfile.OWNER_TAG)
    assert docfile.sections(text, docfile.LLM_TAG)["What it does"] == docfile.sections(seed, docfile.LLM_TAG)["What it does"]


def test_people_without_write_access_cannot_apply(fake_github, monkeypatch, comment):
    monkeypatch.setattr(github, "permission", lambda repo, user, token: "read")
    assert apply.execute(event(tick(comment), comment, "stranger"), "me/demo", "t") == \
        "@stranger has no write access; nothing applied."
    assert fake_github["put"] == [] and fake_github["comment"] == []


def test_other_edits_do_nothing(fake_github, comment):
    assert apply.execute(event(comment + "\nedited", comment), "me/demo", "t") == "No newly ticked box."
    assert fake_github["put"] == []


def test_failure_unticks_the_box_with_the_reason(fake_github, monkeypatch, comment, tmp_path):
    def broken(*a):
        raise RuntimeError("409 Conflict")
    monkeypatch.setattr(github, "put_file", broken)
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event(tick(comment), comment)), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "me/demo")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert apply.main() == 0
    (new_comment,) = fake_github["comment"]
    assert apply.checkbox("recommendationservice", " (⚠️ last try failed: RuntimeError)") in new_comment
    assert apply.newly_ticked(tick(new_comment), new_comment) == ["recommendationservice"]  # can be retried
