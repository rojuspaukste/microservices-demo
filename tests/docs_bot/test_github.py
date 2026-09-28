import base64

from tools.docs_bot import github


class Resp:
    def __init__(self, data, links=None):
        self.data, self.links = data, links or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FakeSession:
    """Serves one page of PR comments per GET and records writes."""

    def __init__(self, *pages):
        self.pages, self.sent = list(pages), []

    def get(self, url, timeout):
        self.sent.append(("GET", url))
        page = self.pages.pop(0)
        return Resp(page, {"next": {"url": "page-2"}} if self.pages else {})

    def patch(self, url, json, timeout):
        self.sent.append(("PATCH", url, json["body"]))
        return Resp({"html_url": "patched"})

    def post(self, url, json, timeout):
        self.sent.append(("POST", url, json["body"]))
        return Resp({"html_url": "posted"})


def test_updates_the_existing_comment_found_on_a_later_page(monkeypatch):
    s = FakeSession([{"body": "lgtm"}], [{"body": f"{github.MARKER}\nold", "url": "comments/2"}])
    monkeypatch.setattr(github, "_session", lambda token: s)
    assert github.upsert_comment("me/demo", 7, "new", "token") == "patched"
    assert s.sent[-1] == ("PATCH", "comments/2", "new")
    assert not [x for x in s.sent if x[0] == "POST"]


def test_creates_a_comment_when_there_is_none(monkeypatch):
    s = FakeSession([{"body": "lgtm"}])
    monkeypatch.setattr(github, "_session", lambda token: s)
    assert github.upsert_comment("me/demo", 7, "new", "token") == "posted"
    assert s.sent[-1] == ("POST", "https://api.github.com/repos/me/demo/issues/7/comments", "new")


def test_does_not_create_when_create_is_false(monkeypatch):
    s = FakeSession([])
    monkeypatch.setattr(github, "_session", lambda token: s)
    assert github.upsert_comment("me/demo", 7, "all good", "token", create=False) is None
    assert s.sent == [("GET", "https://api.github.com/repos/me/demo/issues/7/comments?per_page=100")]


class ApiSession:
    def __init__(self, response):
        self.response, self.calls = response, []

    def request(self, method, url, timeout, **kwargs):
        self.calls.append((method, url, kwargs))
        return Resp(self.response)


def test_files_are_read_and_committed_through_the_contents_api(monkeypatch):
    text = "# doc — ünïcode\n| `X` | — |\n"
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    s = ApiSession({"content": encoded[:10] + "\n" + encoded[10:], "sha": "blob1"})  # GitHub wraps base64
    monkeypatch.setattr(github, "_session", lambda token: s)
    assert github.get_file("me/demo", "docs/x.md", "feature", "t") == (text, "blob1")
    assert s.calls[-1] == ("GET", "https://api.github.com/repos/me/demo/contents/docs/x.md", {"params": {"ref": "feature"}})

    s.response = {"commit": {"sha": "c0ffee"}}
    assert github.put_file("me/demo", "docs/x.md", "feature", text, "blob1", "msg", "t") == "c0ffee"
    method, url, kwargs = s.calls[-1]
    payload = kwargs["json"]
    assert (method, url) == ("PUT", "https://api.github.com/repos/me/demo/contents/docs/x.md")
    assert (payload["branch"], payload["sha"], payload["message"], payload["committer"]) == \
        ("feature", "blob1", "msg", github.BOT)
    assert base64.b64decode(payload["content"]).decode("utf-8") == text


def test_permission_and_comment_update_hit_the_right_urls(monkeypatch):
    s = ApiSession({"permission": "admin"})
    monkeypatch.setattr(github, "_session", lambda token: s)
    assert github.permission("me/demo", "rojus", "t") == "admin"
    github.update_comment("https://api.github.com/repos/me/demo/issues/comments/9", "new", "t")
    assert [c[:2] for c in s.calls] == [
        ("GET", "https://api.github.com/repos/me/demo/collaborators/rojus/permission"),
        ("PATCH", "https://api.github.com/repos/me/demo/issues/comments/9")]
    assert s.calls[-1][2] == {"json": {"body": "new"}}


def test_without_a_token_the_comment_is_printed(capsys):
    github.upsert_comment("me/demo", 7, "body", None)
    assert capsys.readouterr().out == "body\n"
