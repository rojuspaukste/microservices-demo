"""GitHub side effects: the sticky PR comment and the bot commit."""
import requests

from . import gitutil

MARKER = "<!-- docs-bot-comment -->"
API = "https://api.github.com"


def _session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                      "X-GitHub-Api-Version": "2022-11-28"})
    return s


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


def commit(path: str, message: str) -> str:
    gitutil.git("add", "--", path)
    gitutil.git("-c", "user.name=docs-bot", "-c", "user.email=docs-bot@users.noreply.github.com",
                "commit", "-m", message)
    return gitutil.git("rev-parse", "HEAD").strip()


def push(branch: str) -> None:
    gitutil.git("push", "origin", f"HEAD:refs/heads/{branch}")
