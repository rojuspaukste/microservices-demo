"""Deterministic fact extractors: env vars in Python code, manifest env/ports, proto RPCs.

Regex is enough for the PoC. Production would use tree-sitter so the same approach covers
every language in the repo.
"""
import ast
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Callable

import yaml

_NAME = r"(['\"])(\w+)\1"
ENV_INDEX = re.compile(r"os\.environ\[\s*" + _NAME + r"\s*\]")
ENV_GET = re.compile(r"os\.(?:environ\.get|getenv)\(\s*" + _NAME + r"\s*(?:,\s*((?:[^()]|\([^()]*\))+?))?\s*\)")
ENV_PRESENCE = re.compile(_NAME + r"\s+in\s+os\.environ\b")
RPC = re.compile(r"rpc\s+(\w+)\s*\(\s*(?:stream\s+)?([\w.]+)\s*\)\s*returns\s*\(\s*(?:stream\s+)?([\w.]+)\s*\)")


@dataclass
class EnvRead:
    name: str
    kind: str            # "index" (os.environ[...]) | "get" | "presence" ("X" in os.environ)
    default: str | None  # display form of the default, None when there is none
    file: str
    line: int


@dataclass
class Facts:
    env: dict[str, EnvRead] = field(default_factory=dict)
    manifest_env: dict[str, str] = field(default_factory=dict)
    ports: list[int] = field(default_factory=list)
    rpcs: list[tuple[str, str, str]] = field(default_factory=list)


def _default(expr: str | None) -> str | None:
    """'"8080"' -> '8080'. Empty strings and non-string expressions are shown as written."""
    if expr is None:
        return None
    try:
        value = ast.literal_eval(expr)
    except (ValueError, SyntaxError):
        return expr
    return value if isinstance(value, str) and value else expr


def env_reads(source: str, filename: str) -> list[EnvRead]:
    """Env vars read in a Python file; the first read of each name wins."""
    found: dict[str, EnvRead] = {}
    for no, line in enumerate(source.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        hits = [(m.start(), m.group(2), "index", None) for m in ENV_INDEX.finditer(line)]
        hits += [(m.start(), m.group(2), "get", _default(m.group(3))) for m in ENV_GET.finditer(line)]
        hits += [(m.start(), m.group(2), "presence", None) for m in ENV_PRESENCE.finditer(line)]
        for _, name, kind, default in sorted(hits, key=lambda h: h[0]):
            found.setdefault(name, EnvRead(name, kind, default, filename, no))
    return list(found.values())


def manifest_facts(text: str) -> tuple[dict[str, str], list[int]]:
    """Container env (name -> value) and containerPorts of the Deployment in a multi-doc YAML."""
    env: dict[str, str] = {}
    ports: list[int] = []
    for doc in yaml.safe_load_all(text):
        if not doc or doc.get("kind") != "Deployment":
            continue
        for container in doc["spec"]["template"]["spec"].get("containers", []):
            for e in container.get("env") or []:
                env.setdefault(e["name"], str(e["value"]) if "value" in e else "(valueFrom)")
            ports += [p["containerPort"] for p in container.get("ports") or [] if "containerPort" in p]
    return env, ports


def proto_rpcs(text: str, service: str) -> list[tuple[str, str, str]]:
    """(rpc, request, response) for each rpc of `service`, in declaration order."""
    text = re.sub(r"/\*.*?\*/", "", re.sub(r"//[^\n]*", "", text), flags=re.S)
    m = re.search(r"\bservice\s+" + re.escape(service) + r"\s*\{", text)
    if not m:
        return []
    depth, i = 1, m.end()
    while depth and i < len(text):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return RPC.findall(text[m.end():i])


def extract(component, read: Callable[[str], str | None]) -> Facts:
    """All facts for a component. `read(path)` returns file contents at some commit, or None."""
    facts = Facts()
    for path in component.code_files:
        for r in env_reads(read(path) or "", PurePosixPath(path).name):
            facts.env.setdefault(r.name, r)
    if component.manifest and (text := read(component.manifest)):
        facts.manifest_env, facts.ports = manifest_facts(text)
    if component.proto_file and (text := read(component.proto_file)):
        facts.rpcs = proto_rpcs(text, component.proto_service)
    return facts
