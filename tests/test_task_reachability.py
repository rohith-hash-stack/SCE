"""Test-practice: enforcement of the T02 ground-truth reachability rule
(`benchmarks/ground_truth/TASK_AUTHORING.md` item 2; `docs/
design_formalism.md` G40/G41, corrected to transitive reachability this
engagement after the original direct-edge rule rejected 15/20 real
tasks). **Finding surfaced by this file's own existence**: the rule is
documented in TASK_AUTHORING.md and was checked by hand (via one-off
scratchpad scripts) before every task commit, but nothing in
`benchmarks/ground_truth/loader.py` or `schema.py` actually enforces
it - `enforce_agreement_gate` checks inter-annotator kappa only. A
future task added without re-running the manual audit would not be
caught by any existing test or by `load_tasks_from_dir` itself. This
file is the first permanent enforcement: unit tests for the reachability
predicate's own logic (on small synthetic graphs, fast), plus one
integration test that runs it against the real, currently-committed
Django T02 corpus."""
from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from benchmarks.corpora.resolver import resolve
from benchmarks.ground_truth.loader import load_tasks_from_dir
from prism.cli import build_pipeline

ALLOWED_RELATIONS = {"CALLS", "INSTANTIATES", "EXTENDS", "IMPLEMENTS", "OVERRIDES", "EMBEDS"}


def _allowed_subgraph(edges: list[tuple[str, str, str]]) -> nx.DiGraph:
    """`edges` as `(source, target, relation)` triples - mirrors how
    `builder.graph` stores a `relation` attribute per edge, filtered to
    the six relations TASK_AUTHORING.md's rule allows."""
    g = nx.DiGraph()
    for u, v, relation in edges:
        g.add_node(u)
        g.add_node(v)
        if relation in ALLOWED_RELATIONS:
            g.add_edge(u, v)
    return g


def transitively_reachable(graph: nx.DiGraph, seed: str, target: str) -> bool:
    """The exact predicate TASK_AUTHORING.md item 2.3 specifies: a
    directed path of any length via the six allowed relations."""
    if seed not in graph or target not in graph:
        return False
    return nx.has_path(graph, seed, target)


# --- Unit tests: the predicate itself, on small synthetic graphs ---

def test_direct_edge_is_reachable():
    g = _allowed_subgraph([("a", "b", "CALLS")])
    assert transitively_reachable(g, "a", "b")


def test_multi_hop_path_is_reachable():
    g = _allowed_subgraph([("a", "b", "CALLS"), ("b", "c", "CALLS"), ("c", "d", "INSTANTIATES")])
    assert transitively_reachable(g, "a", "d")


def test_sibling_under_common_caller_is_not_directly_but_is_transitively_related():
    """The exact pattern the corrected rule exists for: `full_clean`
    calls both `_clean_fields` and `_clean_form` - no edge between the
    two siblings themselves, but each is independently reachable from
    the shared seed, which is what the rule actually requires (not a
    direct edge between consecutive pipeline entries)."""
    g = _allowed_subgraph([("full_clean", "_clean_fields", "CALLS"), ("full_clean", "_clean_form", "CALLS")])
    assert transitively_reachable(g, "full_clean", "_clean_fields")
    assert transitively_reachable(g, "full_clean", "_clean_form")
    assert not g.has_edge("_clean_fields", "_clean_form")  # confirms siblings, not a chain


def test_reads_state_only_path_is_not_reachable():
    """READS_STATE never counts, even as the only path - the rule
    excludes it explicitly, so a target connected purely by attribute
    read/write must report unreachable, not silently pass."""
    g = _allowed_subgraph([("a", "b", "READS_STATE")])
    assert not transitively_reachable(g, "a", "b")


def test_disconnected_target_is_not_reachable():
    g = _allowed_subgraph([("a", "b", "CALLS"), ("x", "y", "CALLS")])
    assert not transitively_reachable(g, "a", "y")


def test_target_missing_from_graph_entirely_is_not_reachable():
    g = _allowed_subgraph([("a", "b", "CALLS")])
    assert not transitively_reachable(g, "a", "nonexistent")


def test_reverse_direction_is_not_reachable():
    """Reachability is directed - a real predecessor, not a real
    successor, must not satisfy the rule."""
    g = _allowed_subgraph([("a", "b", "CALLS")])
    assert not transitively_reachable(g, "b", "a")


def test_mixed_allowed_and_reads_state_path_still_finds_the_real_route():
    """A READS_STATE edge sits alongside a real allowed-relation path
    between the same two nodes - the allowed path must still be found
    (READS_STATE is excluded from the subgraph entirely, not merely
    down-weighted, so it can never accidentally block or substitute for
    a real path)."""
    g = _allowed_subgraph([("a", "b", "READS_STATE"), ("a", "c", "CALLS"), ("c", "b", "CALLS")])
    assert transitively_reachable(g, "a", "b")


# --- Integration test: the real, currently-committed Django T02 corpus ---

@pytest.mark.slow
def test_full_django_t02_corpus_satisfies_transitive_reachability():
    """Guards the actual corpus, not just the predicate - real
    `build_pipeline` graph, real loaded tasks. If a future task is added
    (or an existing one edited) without re-running the manual audit this
    file's own docstring notes is otherwise the only enforcement, this
    test is what catches it."""
    repo_path = str(resolve("django"))
    builder, _ = build_pipeline(repo_path)

    allowed_graph = nx.DiGraph()
    allowed_graph.add_nodes_from(builder.graph.nodes())
    for u, v, data in builder.graph.edges(data=True):
        if data.get("relation", "CALLS") in ALLOWED_RELATIONS:
            allowed_graph.add_edge(u, v)

    tasks_dir = Path(__file__).resolve().parents[1] / "benchmarks" / "ground_truth" / "tasks" / "django"
    result = load_tasks_from_dir(tasks_dir)
    assert result.rejected == []

    t02_tasks = [t for t in result.accepted if t.task_type == "debug" and t.repo == "django"]
    assert len(t02_tasks) == 20

    failures = []
    for task in t02_tasks:
        pipeline = task.adjudicated.pipeline_symbols
        seed = task.seed_symbol
        if not pipeline or pipeline[0] != seed:
            failures.append((task.task_id, "seed != pipeline[0]"))
            continue
        for pi in pipeline[1:]:
            if not transitively_reachable(allowed_graph, seed, pi):
                failures.append((task.task_id, f"unreachable: {pi}"))
                break

    assert failures == []
