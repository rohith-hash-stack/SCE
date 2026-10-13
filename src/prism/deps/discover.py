"""Finding an installed distribution for an import name, and its cache key
(Operation Groundwire, offline export summarization).

A summary is keyed by distribution name, version and a hash of the
dist-info RECORD (what pip installed, file by file) - never by Prism's own
engine commit, so editing Prism does not invalidate every cached
dependency summary (`SUMMARY_SCHEMA_VERSION` in `prism.deps.summarizer`
does that, deliberately).
"""
from __future__ import annotations

import ast
import hashlib
import importlib.metadata as metadata
import os
import re
import sys
from dataclasses import dataclass

_STDLIB = frozenset(sys.stdlib_module_names) | {"__future__"}


@dataclass(frozen=True)
class DistributionRef:
    """One installed distribution providing one importable package."""

    dist: str
    version: str
    record_hash: str
    package: str  # import name, e.g. "requests"
    package_dir: str  # directory holding the package's source
    requires: tuple[str, ...] = ()  # declared runtime requirements (distribution names)

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.dist, self.version, self.record_hash, self.package)


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _top_level_names(dist: metadata.Distribution) -> set[str]:
    names = set()
    text = dist.read_text("top_level.txt")
    if text:
        names |= {line.strip() for line in text.splitlines() if line.strip()}
    for f in dist.files or ():
        parts = str(f).split("/")
        if parts and not parts[0].endswith((".dist-info", ".egg-info", ".data")) and parts[0] != "..":
            names.add(parts[0][:-3] if parts[0].endswith(".py") else parts[0])
    return names


def _record_hash(dist: metadata.Distribution, package_dir: str) -> str:
    record = dist.read_text("RECORD")
    if record:
        return hashlib.sha256(record.encode()).hexdigest()
    digest = hashlib.sha256()  # no RECORD (editable/legacy install): hash the sources themselves
    for root, dirs, files in os.walk(package_dir):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(files):
            if name.endswith(".py"):
                path = os.path.join(root, name)
                digest.update(os.path.relpath(path, package_dir).encode())
                with open(path, "rb") as fh:
                    digest.update(hashlib.sha256(fh.read()).digest())
    return digest.hexdigest()


def _requires(dist: metadata.Distribution) -> tuple[str, ...]:
    out = []
    for req in dist.requires or ():
        if ";" in req and "extra" in req.split(";", 1)[1]:
            continue  # optional extras are not part of the runtime closure
        name = re.split(r"[\s;<>=!~\[(]", req, maxsplit=1)[0]
        if name:
            out.append(_normalize(name))
    return tuple(sorted(set(out)))


def find_distribution(import_name: str, paths: list[str] | None = None) -> DistributionRef | None:
    """The installed distribution providing package `import_name`, from
    `paths` (default `sys.path`). `None` for the stdlib, an unknown name, or
    a single-module distribution (no package directory to summarize)."""
    if import_name in _STDLIB:
        return None
    dists = metadata.distributions(path=paths) if paths is not None else metadata.distributions()
    for dist in dists:
        if import_name not in _top_level_names(dist):
            continue
        package_dir = str(dist.locate_file(import_name))
        if not os.path.isdir(package_dir):
            continue
        return DistributionRef(
            dist=_normalize(dist.metadata["Name"]),
            version=dist.version,
            record_hash=_record_hash(dist, package_dir),
            package=import_name,
            package_dir=package_dir,
            requires=_requires(dist),
        )
    return None


def distribution_packages(dist_name: str, paths: list[str] | None = None) -> list[str]:
    """Import names a distribution provides (for following `requires`)."""
    dists = metadata.distributions(path=paths) if paths is not None else metadata.distributions()
    for dist in dists:
        if _normalize(dist.metadata["Name"] or "") == _normalize(dist_name):
            return sorted(n for n in _top_level_names(dist) if n.isidentifier() and not n.startswith("_"))
    return []


def imported_roots(paths: list[str]) -> set[str]:
    """Top-level names of every absolute import in these Python files."""
    roots: set[str] = set()
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
        except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return {r for r in roots if r not in _STDLIB}
