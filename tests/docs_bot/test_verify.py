import pytest

from tools.docs_bot import extract, verify

GOOD = {"text": "`ListRecommendations` seeds `random.Random` with `user_id`",
        "file": "src/recommendationservice/recommendation_server.py", "lines": [79, 80]}
CATALOG = {"text": "calls `ListProducts` on every request",
           "file": "src/recommendationservice/recommendation_server.py", "lines": [73, 73]}
WRONG_LINES = {"text": "`MAX_RECOMMENDATIONS` caps the number of results",
               "file": "src/recommendationservice/recommendation_server.py", "lines": [3, 5]}


@pytest.fixture
def ctx(component, pr1_files):
    files = {p: pr1_files[p] for p in component.code_files}
    return files, extract.extract(component, pr1_files.get)


def test_claim_is_kept_when_identifier_is_in_cited_lines(ctx):
    files, _ = ctx
    assert verify.check_claim(GOOD, files) == ("recommendation_server.py", 79, 80)
    assert verify.check_claim({**GOOD, "file": "recommendation_server.py"}, files)  # basename is enough


def test_claim_is_dropped_when_cited_lines_lack_its_identifier(ctx):
    files, _ = ctx
    assert verify.check_claim(WRONG_LINES, files) is None


def test_claim_is_dropped_for_bad_range_or_unknown_file(ctx):
    files, _ = ctx
    assert verify.check_claim({**GOOD, "lines": [400, 401]}, files) is None
    assert verify.check_claim({**GOOD, "lines": [80, 79]}, files) is None
    assert verify.check_claim({**GOOD, "file": "src/other/other.py"}, files) is None


def test_sentences_citing_dropped_claims_are_removed(ctx):
    files, facts = ctx
    data = {"claims": [GOOD, CATALOG, WRONG_LINES], "draft": {"How it works": "\n".join([
        "1. Calls `ListProducts` for every request (recommendation_server.py:73).",
        "2. Samples with a generator seeded by `user_id` (recommendation_server.py:L79-L80). "
        "At most `MAX_RECOMMENDATIONS` are returned (recommendation_server.py:3-5).",
        "3. An uncited sentence is not allowed.",
    ])}}
    drafts, stats = verify.verify(data, files, facts)
    assert drafts == {"How it works": "1. Calls `ListProducts` for every request (recommendation_server.py:73).\n"
                                      "2. Samples with a generator seeded by `user_id` (recommendation_server.py:L79-L80)."}
    assert stats == {"claims_total": 3, "claims_dropped": 1, "sentences_dropped": 2}


def test_draft_is_dropped_when_more_than_half_the_claims_fail(ctx):
    files, facts = ctx
    bad = {**WRONG_LINES, "lines": [1, 2]}
    data = {"claims": [GOOD, WRONG_LINES, bad], "draft": {"How it works": "Seeded (recommendation_server.py:79-80)."}}
    drafts, stats = verify.verify(data, files, facts)
    assert drafts == {}
    assert stats["claims_dropped"] == 2


def test_unknown_env_vars_and_ports_are_removed_from_draft(ctx):
    files, facts = ctx
    data = {"claims": [GOOD], "draft": {"What it does": " ".join([
        "Returns up to `MAX_RECOMMENDATIONS` products, chosen per `user_id` (recommendation_server.py:79-80).",
        "Set `RECS_SEED` to change the seed (recommendation_server.py:79).",
        "It listens on port 9999 (recommendation_server.py:79).",
    ])}}
    drafts, stats = verify.verify(data, files, facts)
    assert drafts["What it does"].startswith("Returns up to `MAX_RECOMMENDATIONS`")
    assert "RECS_SEED" not in drafts["What it does"] and "9999" not in drafts["What it does"]
    assert stats["sentences_dropped"] == 2
