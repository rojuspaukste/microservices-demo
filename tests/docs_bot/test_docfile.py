import re

from tools.docs_bot import docfile, extract

BLOCKS = ["configuration", "api"]


def test_renderer_reproduces_seed_blocks_exactly(component, base_files):
    facts = extract.extract(component, base_files.get)
    doc = base_files[component.doc]
    for name in BLOCKS:
        _, header, rows_of, _ = docfile.BLOCKS[name]
        assert docfile.render(header, rows_of(facts)) == docfile.read_block(doc, name)
    assert docfile.sync(doc, facts, BLOCKS) == (doc, [])


def test_pure_line_shift_is_not_a_change(component, base_files):
    shifted = dict(base_files)
    server = component.code_files[0]
    shifted[server] = "# a new header comment\n\n\n" + shifted[server]
    facts = extract.extract(component, shifted.get)
    assert facts.env["PORT"].line == 133
    doc = base_files[component.doc]
    assert docfile.sync(doc, facts, BLOCKS) == (doc, [])


def test_pr1_adds_row_and_refreshes_line_numbers(component, base_files, pr1_files):
    doc = base_files[component.doc]
    new_doc, changes = docfile.sync(doc, extract.extract(component, pr1_files.get), BLOCKS)
    assert changes == ["Configuration: added `MAX_RECOMMENDATIONS` (default `5`, manifest `5`)"]
    block = docfile.read_block(new_doc, "configuration")
    assert "| `MAX_RECOMMENDATIONS` | `5` | `5` | `recommendation_server.py:71` |" in block
    assert "| `PORT` | `8080` | `8080` | `recommendation_server.py:131` |" in block  # shifted by the new rng line
    strip = lambda t: re.sub(r"<!-- docs-bot:begin configuration -->.*?<!-- docs-bot:end configuration -->", "", t, flags=re.S)
    assert strip(new_doc) == strip(doc)  # nothing outside the managed block moved


def test_changed_and_removed_values_are_described(component, base_files):
    facts = extract.extract(component, base_files.get)
    facts.env["PORT"].default = "9090"
    del facts.env["GCP_PROJECT_ID"]
    facts.rpcs.append(("Ping", "Empty", "Empty"))
    _, changes = docfile.sync(base_files[component.doc], facts, BLOCKS)
    assert changes == [
        "Configuration: removed `GCP_PROJECT_ID`",
        "Configuration: changed `PORT` (default `8080` → `9090`)",
        "API: added `Ping` (request `Empty`, response `Empty`)",
    ]


def test_sections_are_selected_by_tag(component, base_files):
    doc = base_files[component.doc]
    sections = docfile.sections(doc, docfile.LLM_TAG)
    assert list(sections) == ["What it does", "How it works"]
    assert sections["How it works"].startswith("1. Calls `ListProducts`")
    assert "Gotchas" not in sections["How it works"]
    owner = docfile.sections(doc, docfile.OWNER_TAG)
    assert list(owner) == ["Gotchas & history"]
    assert "Result order is not reproducible" in owner["Gotchas & history"]


def test_set_meta_updates_sha_and_visible_line(component, base_files):
    doc = docfile.set_meta(base_files[component.doc], "abcdef1234567890")
    assert "<!-- docs-bot:meta source-commit=abcdef1 -->" in doc
    assert "Generated from commit `abcdef1`" in doc
