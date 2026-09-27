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


def test_without_a_token_the_comment_is_printed(capsys):
    github.upsert_comment("me/demo", 7, "body", None)
    assert capsys.readouterr().out == "body\n"
