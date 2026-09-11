"""Blocker 1 performance work: shared repo-content-addressed cache-key
helpers for `prism/traversal/`'s own in-process caches -
`continuous_dijkstra.py`'s graph/distance caches (Steps 2/3) and
`causal_weights.py`'s edge caches (Step 4).

Extracted to its own module specifically to avoid a circular import:
`continuous_dijkstra.py` imports from `causal_weights.py` (`compute_
causal_edges`, `edge_cost`), so `causal_weights.py` cannot import
these helpers back from `continuous_dijkstra.py`. Both modules import
from here instead.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _run_git_head(cwd: str | Path) -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


@lru_cache(maxsize=1)
def engine_commit_hash() -> str:
    """The SCE repo's own HEAD - process-invariant (this repo's source
    cannot change while it's running), so this is cached once per
    process rather than re-shelled-out-to on every cache-key
    computation. Mirrors `benchmarks.engines.prism_engine_cache.
    _engine_commit_hash`'s own technique - duplicated here rather than
    imported, since production engine code (`prism/traversal/`) must
    never depend on the benchmark harness (`benchmarks/`)."""
    return _run_git_head(Path(__file__).resolve().parent) or "unknown"


#: `build_causal_graph`'s (and `causal_weights.py`'s edge functions')
#: real, non-first-party input `engine_commit_hash` does *not* cover:
#: tree-sitter grammars are external PyPI packages (see
#: `pyproject.toml`), independently versioned from this repo - a `pip
#: install --upgrade tree-sitter-python` changes what `builder.
#: calls_graph`/the edge-extraction functions see without any commit to
#: this repo at all. Verified directly (`pip show tree-sitter-python`
#: shows a version independent of this repo's own git history), not
#: assumed.
_GRAMMAR_PACKAGES = (
    "tree-sitter",
    "tree-sitter-python",
    "tree-sitter-javascript",
    "tree-sitter-typescript",
    "tree-sitter-go",
    "tree-sitter-java",
    "tree-sitter-c-sharp",
)


@lru_cache(maxsize=1)
def grammar_version() -> str:
    parts = []
    for pkg in _GRAMMAR_PACKAGES:
        try:
            parts.append(f"{pkg}=={importlib.metadata.version(pkg)}")
        except importlib.metadata.PackageNotFoundError:
            parts.append(f"{pkg}=missing")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def target_repo_file_signature(repo_root: str) -> str:
    """Same technique `benchmarks.engines.prism_engine_cache.
    _repo_file_signature` already uses (duplicated, not imported, for
    the same layering reason as `engine_commit_hash` above): the
    checked-out commit SHA if `repo_root` is a git working tree (cheap:
    one `git rev-parse HEAD`), else a real sha256 over every `*.py`
    file's own content (slower, but correct for a non-git fixture)."""
    git_head = _run_git_head(repo_root) if (Path(repo_root) / ".git").is_dir() else None
    if git_head:
        return f"git:{git_head}"
    hasher = hashlib.sha256()
    for path in sorted(Path(repo_root).rglob("*.py")):
        try:
            content = path.read_bytes()
        except OSError:
            continue
        hasher.update(str(path.relative_to(repo_root)).encode())
        hasher.update(content)
    return f"content:{hasher.hexdigest()}"


@dataclass(frozen=True)
class GraphCacheKey:
    """`(repo_path, engine_commit_hash, grammar_version, tag_rule_version,
    file_hash_set)` - the Blocker 1 cache-key matrix's own literal shape
    for `build_causal_graph`/`compute_causal_edges`/`compute_all_
    data_flow_edges`/`compute_guard_indicator_edges` (all "Same as
    build_causal_graph" per that matrix).

    **`tag_rule_version` is `engine_commit_hash`'s own value, not a
    separately-tracked constant** - verified directly: no
    independently-versioned tagging-rule scheme exists anywhere in this
    codebase (`prism/tagger/` is first-party source in this same repo,
    unlike the externally-versioned tree-sitter grammars `grammar_
    version` above exists specifically to cover). Using the same repo
    commit hash for both slots is not a fabricated placeholder value -
    it is the real, exact answer to "what version of this repo's own
    tagging rules produced this," since that logic lives in this same
    repo and changes only when this repo's own commit changes.
    """

    repo_path: str
    engine_commit_hash: str
    grammar_version: str
    tag_rule_version: str
    file_hash_set: str

    def digest(self) -> str:
        raw = "|".join(
            (self.repo_path, self.engine_commit_hash, self.grammar_version, self.tag_rule_version, self.file_hash_set)
        )
        return hashlib.sha256(raw.encode()).hexdigest()


def graph_cache_key(repo_root: str) -> GraphCacheKey:
    repo_path = str(Path(repo_root).resolve())
    commit_hash = engine_commit_hash()
    return GraphCacheKey(
        repo_path=repo_path,
        engine_commit_hash=commit_hash,
        grammar_version=grammar_version(),
        tag_rule_version=commit_hash,
        file_hash_set=target_repo_file_signature(repo_path),
    )
