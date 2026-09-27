import subprocess
from pathlib import Path

import pytest

from tools.docs_bot import gitutil

ROOT = Path(__file__).resolve().parents[2]


def test_show_returns_none_only_for_missing_files(monkeypatch):
    monkeypatch.chdir(ROOT)
    assert "Apache License" in gitutil.show("HEAD", "LICENSE")
    assert gitutil.show("HEAD", "no/such/file.py") is None
    with pytest.raises(subprocess.CalledProcessError):  # a broken git call must not look like "no file"
        gitutil.show("0" * 40, "LICENSE")


def test_number_lines_uses_head_line_numbers():
    diff = "\n".join([
        "diff --git a/x.py b/x.py",
        "--- a/x.py",
        "+++ b/x.py",
        "@@ -10,3 +10,4 @@ def f():",
        " keep",
        "-old",
        "+new",
        "+added",
        "--- not a header, a removed line starting with dashes",
    ])
    assert gitutil.number_lines(diff).splitlines()[4:] == [
        "   10  keep",
        "      -old",
        "   11 +new",
        "   12 +added",
        "      --- not a header, a removed line starting with dashes",
    ]
