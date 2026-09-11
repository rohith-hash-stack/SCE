"""v1.1 Part 2.4: Continuous Dijkstra Topological Distance.

**Critical architectural invariant** (the spec's own framing): to prevent
a distant node "leapfrogging" a genuinely closer one mid-knapsack-
traversal, every pairwise `W(u, v)` and every shortest path from the seed
is computed *once*, up front, before `prism.packer.submodular_knapsack`'s
selection loop ever starts - not recomputed incrementally as the frontier
expands. This module is that one precomputation step.

The traversal graph this runs Dijkstra over is **directed**, and built
from the *union* of `builder.graph`'s real structural edges and
`prism.traversal.causal_weights`'s synthetic causal-coupling edges (see
that module's own docstring for why a synthetic edge is often the only
connection between two causally-coupled sibling calls at all) - forward-
only reachability from the seed, matching `submodular_knapsack`'s own
frontier-expansion contract (`graph.successors(node)`), not the
bidirectional caller+callee view `prism.slicer.distance.DistanceEngine`
uses for `D_hybrid`. The two distance models serve different consumers
with different needs and are not meant to produce the same numbers.

Edge cost is `c(e) = 1 / W(u, v)` (`prism.traversal.causal_weights.
edge_cost`) - a stronger causal coupling (higher `W`) costs *less* to
traverse, so Dijkstra naturally prefers a causally-coupled path over an
equal-hop-count uncoupled one, without ever needing a separate tie-break
rule the way `D_hybrid`'s `TagBonus` does.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import networkx as nx

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.traversal.causal_weights import compute_causal_edges, edge_cost

#: Blocker 1 performance work, Step 2/4: a correctly-keyed cache for
#: `build_causal_graph` - seed/budget-independent (a pure function of
#: `builder` alone), so a repo-content-addressed key is safe: the same
#: (repo, engine build, target-repo content) combination always
#: produces the same graph, on this instance or a different one, this
#: retrieve() call or a later one on the same engine.
#:
#: **Never** used for `compute_topological_distances` - that result is
#: seed-dependent and must live in its own, separately-keyed cache
#: (Step 3) specifically to prevent the silent-corruption bug a
#: distance lookup under a seed-less key would cause: a query for seed
#: B would silently receive seed A's distances - plausible-looking,
#: wrong output that a same-seed bit-identical test would never catch.
_GRAPH_CACHE: dict[str, nx.DiGraph] = {}


def _run_git_head(cwd: str | Path) -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


@lru_cache(maxsize=1)
def _engine_commit_hash() -> str:
    """The SCE repo's own HEAD - process-invariant (this repo's source
    cannot change while it's running), so this is cached once per
    process rather than re-shelled-out-to on every cache-key
    computation. Mirrors `benchmarks.engines.prism_engine_cache.
    _engine_commit_hash`'s own technique - duplicated here rather than
    imported, since production engine code (`prism/traversal/`) must
    never depend on the benchmark harness (`benchmarks/`)."""
    return _run_git_head(Path(__file__).resolve().parent) or "unknown"


#: `build_causal_graph`'s real, non-first-party input this repo's own
#: `engine_commit_hash` does *not* cover: tree-sitter grammars are
#: external PyPI packages (see `pyproject.toml`), independently
#: versioned from this repo - a `pip install --upgrade
#: tree-sitter-python` changes what `builder.calls_graph`/`compute_
#: causal_edges` see without any commit to this repo at all. Verified
#: directly (`pip show tree-sitter-python` shows a version independent
#: of this repo's own git history), not assumed.
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
def _grammar_version() -> str:
    parts = []
    for pkg in _GRAMMAR_PACKAGES:
        try:
            parts.append(f"{pkg}=={importlib.metadata.version(pkg)}")
        except importlib.metadata.PackageNotFoundError:
            parts.append(f"{pkg}=missing")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _target_repo_file_signature(repo_root: str) -> str:
    """Same technique `benchmarks.engines.prism_engine_cache.
    _repo_file_signature` already uses (duplicated, not imported, for
    the same layering reason as `_engine_commit_hash` above): the
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
class _GraphCacheKey:
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


def _graph_cache_key(builder: ConcreteGraphBuilder) -> _GraphCacheKey:
    repo_path = str(Path(builder.repo_root).resolve())
    commit_hash = _engine_commit_hash()
    return _GraphCacheKey(
        repo_path=repo_path,
        engine_commit_hash=commit_hash,
        grammar_version=_grammar_version(),
        tag_rule_version=commit_hash,
        file_hash_set=_target_repo_file_signature(repo_path),
    )


def build_causal_graph(builder: ConcreteGraphBuilder) -> nx.DiGraph:
    """The directed traversal graph `compute_topological_distances` runs
    Dijkstra over - every function/method `builder` indexed as a node,
    every real structural edge plus every synthetic causal-coupling edge
    as a weighted directed edge (`weight` = `edge_cost(W)`, the Dijkstra
    hop cost; `causal_weight` = the raw `W(u, v)` itself, kept on the
    edge for inspection/testing; `synthetic` = `True` for a
    causal-coupling-only edge with no real structural counterpart).

    Cached by `_graph_cache_key(builder)` (repo/engine/grammar/tag-rule/
    file-content addressed - see `_GraphCacheKey`'s own docstring) -
    the same (repo, engine build, target content) combination always
    returns the identical graph object, whether this is the 1st, 2nd,
    or 3rd call within one `retrieve()`, or a call from a later
    `retrieve()` on the same or a different engine instance.
    """
    key = _graph_cache_key(builder).digest()
    cached = _GRAPH_CACHE.get(key)
    if cached is not None:
        return cached
    weights, synthetic_edges = compute_causal_edges(builder)
    graph = nx.DiGraph()
    graph.add_nodes_from(builder.calls_graph.nodes())
    for (u, v), w in weights.items():
        graph.add_node(u)
        graph.add_node(v)
        graph.add_edge(u, v, weight=edge_cost(w), causal_weight=w, synthetic=(u, v) in synthetic_edges)
    _GRAPH_CACHE[key] = graph
    return graph


def compute_topological_distances(builder: ConcreteGraphBuilder, seed: str) -> dict[str, float]:
    """`{node: dist_w(seed, node)}` - every node forward-reachable from
    `seed` in the causal graph, via Dijkstra over `c(e) = 1/W(u, v)`
    edge costs. `seed` itself is never included (distance 0 to itself is
    implicit - every consumer of this map already treats "not present"
    as "not reachable/not the seed", the same convention `DistanceEngine.
    compute_all` uses). Empty dict if `seed` isn't in the graph at all.

    Not yet cached itself (Blocker 1 Step 3 adds a seed-keyed cache
    here) - `build_causal_graph`'s own cache (Step 2, above) already
    means a 2nd/3rd call within one `retrieve()` is a cheap cache hit
    on the graph itself, even before Step 3 lands.
    """
    graph = build_causal_graph(builder)
    if seed not in graph:
        return {}
    distances = nx.single_source_dijkstra_path_length(graph, seed, weight="weight")
    distances.pop(seed, None)
    return distances
