"""Import roots: resolve a Python absolute import of the repository's own package when that package is not at the
repository root (K4; `harness/experiments/r4/K4_DIAGNOSIS.md`).

PRISM names a module by its repository-relative path (`src/shelf/core/parse.py` -> `src.shelf.core.parse`,
`symbol_table.path_to_module`). Code imports it by its import name (`from shelf.core.parse import parse`). A relative
import resolves from the importing module's own name, but an absolute import kept its import text, named no indexed
symbol, and was linked as external. `ImportRoots.resolve` maps such an import onto the repository's module name.

The retry binds `x.y` to `<root>.x.y` only if all hold:
1. `<root>.x.y` is a module or a package directory in the repository, and `<root>` is a directory that is not itself a
   package (so `x` really is a top-level import name there);
2. `x` is not a known external dependency: the dependencies declared in any `pyproject.toml` (project, optional,
   dependency groups; poetry's), `requirements*.txt`, and the package names in `uv.lock` / `poetry.lock`, minus the
   distributions defined inside the repository itself (workspace members, path sources);
3. `<root>` is not vendored (`vendor/`, `third_party/`, `_vendor/`, `site-packages/`, `node_modules/`);
4. exactly one root matches, after preferring `src` and then roots outside tests, examples, docs and benchmarks.
Otherwise the import text is returned unchanged (external or unresolved, as before).
"""
from __future__ import annotations

import os
import re

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - older interpreters fall back to "no declared dependencies"
    tomllib = None

VENDORED = {"vendor", "vendored", "_vendor", "third_party", "third-party", "site-packages", "node_modules"}
SECONDARY = {"test", "tests", "testing", "example", "examples", "doc", "docs", "docs_src", "benchmark", "benchmarks",
             "bench", "scripts", "tools"}
PRUNE = VENDORED | {".git", ".venv", "venv", ".tox", ".nox", "build", "dist", "__pycache__", ".mypy_cache"}
MAX_DEPTH = 5
_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_LOCK_NAME = re.compile(r'^name\s*=\s*"([^"]+)"', re.M)


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def _dep_name(spec: str) -> str | None:
    m = _REQ_NAME.match(spec)
    return normalize(m.group(1)) if m else None


class ImportRoots:
    def __init__(self, repo_root: str, python_files: list[str]):
        self.repo_root = os.path.abspath(repo_root)
        rels = []
        for f in python_files:
            rel = os.path.relpath(os.path.abspath(f), self.repo_root)
            if rel.startswith(".."):
                continue                       # dependency headers outside the repository
            rels.append(rel.replace(os.sep, "/"))
        packages = {os.path.dirname(r) for r in rels if os.path.basename(r) in ("__init__.py", "__init__.pyi")}
        names: set[str] = set()                # dotted names of modules and of every directory holding modules
        for r in rels:
            parts = r[: -len(os.path.splitext(r)[1])].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            for i in range(1, len(parts) + 1):
                names.add(".".join(parts[:i]))
        self.root_level = {n for n in names}   # names importable from the repository root as they are
        self._index: dict[str, list[str]] = {}
        for n in names:
            parts = n.split(".")
            for i in range(1, len(parts)):
                prefix_dir = "/".join(parts[:i])
                if prefix_dir in packages:
                    continue                   # inside a package: not an import root
                self._index.setdefault(".".join(parts[i:]), []).append(".".join(parts[:i]))
        self.local_distributions, self.external = self._dependencies()
        self._cache: dict[str, str] = {}

    # ------------------------------------------------------------------ declared dependencies
    def _dependencies(self) -> tuple[set[str], set[str]]:
        local, declared = set(), set()
        for dirpath, dirnames, filenames in os.walk(self.repo_root):
            depth = os.path.relpath(dirpath, self.repo_root).count(os.sep)
            dirnames[:] = [] if depth >= MAX_DEPTH else [d for d in dirnames if d not in PRUNE and not d.startswith(".")]
            for fn in filenames:
                path = os.path.join(dirpath, fn)
                try:
                    if fn == "pyproject.toml" and tomllib is not None:
                        with open(path, "rb") as fh:
                            data = tomllib.load(fh)
                        project = data.get("project") or {}
                        poetry = (data.get("tool") or {}).get("poetry") or {}
                        for n in (project.get("name"), poetry.get("name")):
                            if isinstance(n, str):
                                local.add(normalize(n))
                        specs = list(project.get("dependencies") or [])
                        for group in (project.get("optional-dependencies") or {}).values():
                            specs += list(group or [])
                        for group in (data.get("dependency-groups") or {}).values():
                            specs += [g for g in (group or []) if isinstance(g, str)]
                        declared |= {d for d in (_dep_name(s) for s in specs if isinstance(s, str)) if d}
                        declared |= {normalize(k) for k in (poetry.get("dependencies") or {})}
                        for group in (poetry.get("group") or {}).values():
                            declared |= {normalize(k) for k in ((group or {}).get("dependencies") or {})}
                    elif fn.startswith("requirements") and fn.endswith(".txt"):
                        with open(path, encoding="utf8", errors="replace") as fh:
                            for line in fh:
                                line = line.split("#", 1)[0].strip()
                                if line and not line.startswith(("-", ".", "/")):
                                    d = _dep_name(line)
                                    if d:
                                        declared.add(d)
                    elif fn in ("uv.lock", "poetry.lock"):
                        with open(path, encoding="utf8", errors="replace") as fh:
                            declared |= {normalize(n) for n in _LOCK_NAME.findall(fh.read())}
                except (OSError, ValueError, TypeError, AttributeError):
                    continue                   # an unreadable or malformed file declares nothing
        declared.discard("python")
        return local, declared - local

    # ------------------------------------------------------------------ resolution
    def resolve(self, text: str) -> str:
        """The repository module name for the absolute import `text`, or `text` unchanged."""
        if not text or text in self.root_level:
            return text
        hit = self._cache.get(text)
        if hit is not None:
            return hit
        out = text
        top = text.split(".", 1)[0]
        if normalize(top) not in self.external:
            roots = [r for r in self._index.get(text, ()) if not (set(r.split(".")) & VENDORED)]
            if len(roots) > 1:
                roots = [r for r in roots if r == "src"] or [r for r in roots if not (set(r.split(".")) & SECONDARY)] or roots
            if len(roots) == 1:
                out = f"{roots[0]}.{text}"
        self._cache[text] = out
        return out
