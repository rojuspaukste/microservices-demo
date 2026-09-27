import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.docs_bot import config  # noqa: E402


def _edit(text: str, old: str, new: str) -> str:
    assert old in text, f"upstream code changed, fixture needs updating: {old!r}"
    return text.replace(old, new, 1)


@pytest.fixture
def component() -> config.Component:
    return config.load(str(ROOT / "docs-bot.yaml")).components[0]


@pytest.fixture
def base_files(component) -> dict[str, str]:
    paths = ["docs-bot.yaml", component.doc, component.manifest, component.proto_file, *component.code_files]
    return {p: (ROOT / p).read_text(encoding="utf-8") for p in paths}


@pytest.fixture
def pr1_files(base_files, component) -> dict[str, str]:
    """Demo PR 1: configurable recommendation count + deterministic selection per user."""
    files = dict(base_files)
    server = component.code_files[0]
    files[server] = _edit(files[server], "max_responses = 5",
                          'max_responses = int(os.environ.get("MAX_RECOMMENDATIONS", "5"))')
    files[server] = _edit(files[server], "        indices = random.sample(range(num_products), num_return)\n",
                          "        rng = random.Random(request.user_id)\n"
                          "        indices = rng.sample(range(num_products), num_return)\n")
    files[component.manifest] = _edit(files[component.manifest], '          value: "1"\n',
                                      '          value: "1"\n        - name: MAX_RECOMMENDATIONS\n'
                                      '          value: "5"\n')
    return files


@pytest.fixture
def pr2_files(base_files, component) -> dict[str, str]:
    """Demo PR 2: log message text only."""
    files = dict(base_files)
    server = component.code_files[0]
    files[server] = _edit(files[server], '"[Recv ListRecommendations] product_ids={}"',
                          '"ListRecommendations returning product_ids={}"')
    return files
