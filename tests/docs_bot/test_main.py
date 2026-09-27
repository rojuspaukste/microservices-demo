"""End to end on a throwaway git repo (real git, bare 'origin' to push to, LLM mocked, no network)."""
import json
import subprocess
from pathlib import Path

import pytest

from tools.docs_bot import docfile, github, llm, main

PR1_ANSWER = {
    "behaviour_change": True, "confidence": "high", "affected_sections": ["How it works", "What it does"],
    "summary": "`ListRecommendations` now seeds the random choice with `user_id` (recommendation_server.py:79-80).",
    "question_for_author": "Why was this change made, and should results be stable per user?",
    "draft": {"How it works": "1. Calls `ListProducts` for every request (recommendation_server.py:73).\n"
                              "2. Removes the caller's product IDs (recommendation_server.py:75).\n"
                              "3. Samples with a generator seeded by `user_id` (recommendation_server.py:79-80).",
              "Gotchas & history": "Owner section: must never be drafted (recommendation_server.py:79)."},
    "claims": [{"text": "calls `ListProducts`", "file": "src/recommendationservice/recommendation_server.py", "lines": [73, 73]},
               {"text": "removes `request.product_ids`", "file": "src/recommendationservice/recommendation_server.py", "lines": [75, 75]},
               {"text": "`random.Random` seeded with `user_id`", "file": "src/recommendationservice/recommendation_server.py", "lines": [79, 80]}],
}


def git(cwd, *args) -> str:
    return subprocess.run(["git", "-c", "user.name=dev", "-c", "user.email=dev@example.com", *args], cwd=cwd,
                          check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()


def write(root: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def repo(tmp_path, base_files, monkeypatch) -> Path:
    work = tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", "origin.git")
    git(tmp_path, "init", "-q", "-b", "main", "work")
    git(work, "config", "core.autocrlf", "false")
    write(work, base_files)
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "base")
    git(work, "remote", "add", "origin", str(tmp_path / "origin.git"))
    monkeypatch.chdir(work)
    for var in ("GITHUB_TOKEN", "GITHUB_STEP_SUMMARY", "DOCS_BOT_DRY_RUN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "test-model")
    return work


def open_pr(repo: Path, monkeypatch, branch: str, files: dict[str, str], message: str = "change") -> str:
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", branch)
    write(repo, files)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    head = git(repo, "rev-parse", "HEAD")
    event = repo.parent / f"{branch}.json"
    event.write_text(json.dumps({"pull_request": {"number": 7, "base": {"sha": base}, "head": {"sha": head, "ref": branch}},
                                 "repository": {"full_name": "me/demo"}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    return head


def fake_llm(answer: dict, prompts: list):
    def analyse(prompt, model, api_key, **_):
        prompts.append(prompt)
        return llm.LLMResult(data=llm.normalise(answer), attempts=1, latency_ms=5, tokens=100)
    return analyse


def record() -> dict:
    return json.loads(Path("docs-bot-run.json").read_text(encoding="utf-8"))


def test_pr1_commits_facts_and_asks_about_behaviour(repo, monkeypatch, capsys, pr1_files, component):
    prompts = []
    monkeypatch.setattr(llm, "analyse", fake_llm(PR1_ANSWER, prompts))
    head = open_pr(repo, monkeypatch, "pr1", pr1_files)
    assert main.main() == 0

    origin = repo.parent / "origin.git"
    assert git(origin, "log", "-1", "--format=%s", "pr1") == \
        "docs(recommendationservice): sync configuration/api with code [docs-bot]"
    assert git(origin, "rev-parse", "pr1~1") == head
    doc = git(origin, "show", f"pr1:{component.doc}")
    assert "| `MAX_RECOMMENDATIONS` | `5` | `5` | `recommendation_server.py:71` |" in doc
    assert f"source-commit={head[:7]}" in doc
    for tag in (docfile.LLM_TAG, docfile.OWNER_TAG):  # prose is never committed
        assert docfile.sections(doc, tag) == docfile.sections(pr1_files[component.doc], tag)

    out = capsys.readouterr().out
    assert out.count(github.MARKER) == 1
    for text in ["- Configuration: added `MAX_RECOMMENDATIONS` (default `5`, manifest `5`) → committed in",
                 "**Question:** Why was this change made", 'Suggested draft for "How it works" (3/3 cited claims verified)',
                 "_Confidence: high · 1 LLM call"]:
        assert text in out
    assert "Gotchas" not in out  # owner sections are never drafted
    assert "   79 +        rng = random.Random(request.user_id)" in prompts[0]  # head line numbers for citing
    assert "## Owner notes (read-only context)\n### Gotchas & history\n" in prompts[0]
    assert "Result order is not reproducible" in prompts[0]
    rec = record()
    assert rec["action"] == "commit+ask" and rec["factual_update_committed"] is True
    assert (rec["claims_total"], rec["claims_dropped"], rec["tokens"]) == (3, 0, 100)


def test_pr2_log_only_change_is_silent(repo, monkeypatch, capsys, pr2_files):
    monkeypatch.setattr(llm, "analyse", fake_llm({"behaviour_change": False, "confidence": "high"}, []))
    head = open_pr(repo, monkeypatch, "pr2", pr2_files)
    assert main.main() == 0
    assert git(repo, "rev-parse", "HEAD") == head
    assert github.MARKER not in capsys.readouterr().out
    rec = record()
    assert (rec["action"], rec["note"], rec["llm_called"]) == ("none", "No doc change needed.", True)


def test_without_llm_key_facts_are_still_committed(repo, monkeypatch, capsys, pr1_files, component):
    monkeypatch.delenv("GEMINI_API_KEY")
    open_pr(repo, monkeypatch, "pr1", pr1_files)
    assert main.main() == 0
    assert "MAX_RECOMMENDATIONS" in git(repo.parent / "origin.git", "show", f"pr1:{component.doc}")
    assert "_Behaviour check skipped (LLM unavailable)._" in capsys.readouterr().out
    rec = record()
    assert (rec["action"], rec["llm_called"]) == ("commit", False) and "not set" in rec["llm_error"]


def test_dry_run_writes_doc_but_does_not_commit(repo, monkeypatch, capsys, pr1_files, component):
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.setenv("DOCS_BOT_DRY_RUN", "1")
    head = open_pr(repo, monkeypatch, "pr1", pr1_files)
    assert main.main() == 0
    assert git(repo, "rev-parse", "HEAD") == head
    assert "MAX_RECOMMENDATIONS" in (repo / component.doc).read_text(encoding="utf-8")
    assert "written, not committed (dry run)" in capsys.readouterr().out


def test_loop_guard_skips_bot_commits(repo, monkeypatch, pr1_files):
    monkeypatch.setattr(llm, "analyse", lambda *a, **k: pytest.fail("LLM must not be called"))
    head = open_pr(repo, monkeypatch, "bot", pr1_files, message="docs(x): sync [docs-bot]")
    assert main.main() == 0
    assert git(repo, "rev-parse", "HEAD") == head
    assert record()["note"].startswith("Head commit is a docs-bot commit")


def test_unrelated_change_touches_no_component(repo, monkeypatch, base_files):
    monkeypatch.setattr(llm, "analyse", lambda *a, **k: pytest.fail("LLM must not be called"))
    open_pr(repo, monkeypatch, "other", {"src/frontend/main.go": "package main\n"})
    assert main.main() == 0
    assert record()["note"] == "No documented components touched."


def test_errors_fail_open_with_a_short_comment(repo, monkeypatch, capsys, pr1_files):
    monkeypatch.delenv("GEMINI_API_KEY")
    git(repo, "remote", "remove", "origin")  # push will fail
    open_pr(repo, monkeypatch, "pr1", pr1_files)
    assert main.main() == 0
    assert "docs check skipped (CalledProcessError)" in capsys.readouterr().out
    assert record()["action"] == "skipped"


def test_missing_event_still_exits_zero(repo, monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_PATH", "does-not-exist.json")
    assert main.main() == 0
    assert record()["error"].startswith("FileNotFoundError")
