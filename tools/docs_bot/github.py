"""GitHub side effects: the sticky PR comment, the bot commit, and the REST calls used by apply.py."""
import base64

import requests

from . import gitutil

MARKER = "<!-- docs-bot-comment -->"
API = "https://api.github.com"
BOT = {"name": "docs-bot", "email": "docs-bot@users.noreply.github.com"}


def _session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                      "X-GitHub-Api-Version": "2022-11-28"})
    return s


def _api(token: str, method: str, path: str, **kwargs) -> dict:
    r = _session(token).request(method, path if path.startswith("https://") else API + path, timeout=30, **kwargs)
    r.raise_for_status()
    return r.json()


def find_comment(s: requests.Session, repo: str, pr: int) -> dict | None:
    url = f"{API}/repos/{repo}/issues/{pr}/comments?per_page=100"
    while url:
        r = s.get(url, timeout=30)
        r.raise_for_status()
        for comment in r.json():
            if MARKER in (comment.get("body") or ""):
                return comment
        url = r.links.get("next", {}).get("url")
    return None


def upsert_comment(repo: str, pr: int, body: str, token: str | None, create: bool = True) -> str | None:
    """Update the one docs-bot comment on the PR, or create it. Without a token (local runs) just print."""
    if not token:
        if create:
            print(body)
        return None
    s = _session(token)
    if existing := find_comment(s, repo, pr):
        r = s.patch(existing["url"], json={"body": body}, timeout=30)
    elif create:
        r = s.post(f"{API}/repos/{repo}/issues/{pr}/comments", json={"body": body}, timeout=30)
    else:
        return None
    r.raise_for_status()
    return r.json()["html_url"]


def update_comment(url: str, body: str, token: str) -> None:
    _api(token, "PATCH", url, json={"body": body})


def permission(repo: str, user: str, token: str) -> str:
    """'admin' | 'maintain' | 'write' | 'triage' | 'read' | 'none'."""
    return _api(token, "GET", f"/repos/{repo}/collaborators/{user}/permission")["permission"]


def get_pr(repo: str, number: int, token: str) -> dict:
    return _api(token, "GET", f"/repos/{repo}/pulls/{number}")


def get_file(repo: str, path: str, ref: str, token: str) -> tuple[str, str]:
    """(text, blob sha) of a file on a branch."""
    data = _api(token, "GET", f"/repos/{repo}/contents/{path}", params={"ref": ref})
    return base64.b64decode(data["content"]).decode("utf-8"), data["sha"]


def put_file(repo: str, path: str, branch: str, text: str, blob_sha: str, message: str, token: str) -> str:
    """Commit a new version of one file straight through the API; returns the commit sha."""
    data = _api(token, "PUT", f"/repos/{repo}/contents/{path}", json={
        "message": message, "branch": branch, "sha": blob_sha, "committer": BOT,
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii")})
    return data["commit"]["sha"]


def commit(path: str, message: str) -> str:
    gitutil.git("add", "--", path)
    gitutil.git("-c", f"user.name={BOT['name']}", "-c", f"user.email={BOT['email']}", "commit", "-m", message)
    return gitutil.git("rev-parse", "HEAD").strip()


def push(branch: str) -> None:
    gitutil.git("push", "origin", f"HEAD:refs/heads/{branch}")
