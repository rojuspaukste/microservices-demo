"""Load docs-bot.yaml and map changed files to the components they belong to."""
from dataclasses import dataclass
from fnmatch import fnmatchcase

import yaml


@dataclass
class Component:
    name: str
    paths: list[str]
    code_files: list[str]
    doc: str
    manifest: str | None = None
    proto_file: str | None = None
    proto_service: str | None = None


@dataclass
class Config:
    components: list[Component]
    model_env: str = "GEMINI_MODEL"
    max_diff_chars: int = 12000
    ignore_globs: tuple[str, ...] = ()


def load(path: str = "docs-bot.yaml") -> Config:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    settings = raw.get("settings") or {}
    components = []
    for name, c in (raw.get("components") or {}).items():
        proto = c.get("proto") or {}
        components.append(Component(name, c["paths"], c.get("code_files", []), c["doc"],
                                    c.get("manifest"), proto.get("file"), proto.get("service")))
    return Config(components, settings.get("model_env", "GEMINI_MODEL"),
                  int(settings.get("max_diff_chars", 12000)), tuple(settings.get("ignore_globs", ())))


def glob_match(path: str, pattern: str) -> bool:
    """fnmatch, where '*' also crosses '/' and a leading '**/' may match zero directories."""
    return fnmatchcase(path, pattern) or (pattern.startswith("**/") and fnmatchcase(path, pattern[3:]))


def touched_components(cfg: Config, changed: list[str]) -> list[Component]:
    files = [f for f in changed if not any(glob_match(f, g) for g in cfg.ignore_globs)]
    return [c for c in cfg.components if any(glob_match(f, p) for f in files for p in c.paths)]
