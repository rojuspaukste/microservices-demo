"""Thin wrappers around the git CLI."""
import re
import subprocess

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout


def changed_files(base: str, head: str) -> list[str]:
    return [f for f in git("diff", "--name-only", "-z", f"{base}...{head}", "--").split("\0") if f]


def show(sha: str, path: str) -> str | None:
    """File contents at a commit, or None if the file does not exist there.
    Any other git failure raises: silently returning None would wipe the doc's tables."""
    if not git("ls-tree", "--name-only", sha, "--", path).strip():
        return None
    return git("cat-file", "blob", f"{sha}:{path}")


def diff(base: str, head: str, paths: list[str], context: int = 10) -> str:
    return git("diff", f"--unified={context}", f"{base}...{head}", "--", *paths)


def commit_message(sha: str) -> str:
    return git("log", "-1", "--format=%B", sha, "--")


def number_lines(diff_text: str) -> str:
    """Prefix context and added lines of a unified diff with their line number in the head file,
    so the LLM can cite head lines without counting."""
    out, line_no, in_hunk = [], 0, False
    for line in diff_text.splitlines():
        if m := HUNK.match(line):
            line_no, in_hunk = int(m.group(1)), True
            out.append(line)
        elif line.startswith("diff --git"):
            in_hunk = False
            out.append(line)
        elif in_hunk and line[:1] in (" ", "+"):
            out.append(f"{line_no:>5} {line}")
            line_no += 1
        elif in_hunk and line[:1] == "-":
            out.append(f"{'':>5} {line}")
        else:
            out.append(line)
    return "\n".join(out)
