"""Correctness and performance tests for `prism.analysis.flow_engine` - the
absorbing Markov chain core of the Semantic Knowledge Engine.

Covers:
  - Markov absorption correctness against small synthetic DAGs whose exact
    absorption probabilities can be worked out by hand.
  - SCC cycle-contraction: a circular call graph (`A -> B -> C -> A`) must
    not hang, loop forever, or blow up numerically.
  - The explicit performance requirement from the spec: sparse matrix
    solving (`_build_transition_matrices` + `_power_series`, NOT the full
    `analyze()` call - see `flow_engine`'s own module docstring for why
    the two are held to different budgets) completes in under 20ms on a
    synthetic 1,000-node graph.
"""
from __future__ import annotations

import random
import time

import networkx as nx
import pytest

from prism.analysis.flow_engine import EffectVector, FlowEngine, classify_sink
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.symbol_table import GlobalSymbolTable, SymbolInfo


def _add_function(builder: ConcreteGraphBuilder, name: str, file: str = "sample.py") -> None:
    symbol = SymbolInfo(
        qualified_name=name, kind="function", file=file, line_range=(1, 1),
        language_id="python", module="sample",
    )
    builder.symbol_table.add(symbol)
    builder.graph.add_node(name)


def _add_call(builder: ConcreteGraphBuilder, caller: str, callee: str) -> None:
    builder.graph.add_node(callee, external=callee not in builder.symbol_table)
    builder.graph.add_edge(caller, callee, relation="CALLS")


@pytest.fixture
def builder() -> ConcreteGraphBuilder:
    return ConcreteGraphBuilder(repo_root="/tmp/does-not-matter", symbol_table=GlobalSymbolTable())


# --------------------------------------------------------------------- #
# Markov absorption correctness against known analytical solutions
# --------------------------------------------------------------------- #
def test_direct_call_absorbs_with_probability_one(builder: ConcreteGraphBuilder) -> None:
    """`a -> requests.post` (one internal node, one direct call to a
    single external sink) - the fundamental matrix is trivial (N = I,
    since Q is the 1x1 zero matrix) so the absorption probability into
    that one sink must be exactly 1.0."""
    _add_function(builder, "a")
    _add_call(builder, "a", "requests.post")

    result = FlowEngine().analyze(builder, {})
    egress = result.egress("a")
    assert egress["IO_NETWORK"] == pytest.approx(1.0)
    assert sum(egress.values) == pytest.approx(1.0)


def test_branching_call_splits_probability_mass_evenly(builder: ConcreteGraphBuilder) -> None:
    """`a` calls two distinct external sinks with equal weight (one edge
    each) - the analytical absorption probability into each is exactly
    0.5, since `Q` is the 1x1 zero matrix (`a` has no internal
    successors) and `R` normalizes `a`'s two out-edges to 0.5 each."""
    _add_function(builder, "a")
    _add_call(builder, "a", "requests.post")
    _add_call(builder, "a", "fs.readFileSync")

    result = FlowEngine().analyze(builder, {})
    egress = result.egress("a")
    assert egress["IO_NETWORK"] == pytest.approx(0.5)
    assert egress["IO_STORAGE"] == pytest.approx(0.5)


def test_two_hop_chain_propagates_downstream_effect(builder: ConcreteGraphBuilder) -> None:
    """`a -> b -> requests.post`: `a`'s only path to any sink is through
    `b`, so `a`'s absorption probability into `requests.post` must equal
    `b`'s (both 1.0) - the k=2 term of the truncated Neumann series
    (`Q^1 R`) is exactly what recovers this two-hop mass."""
    _add_function(builder, "a")
    _add_function(builder, "b")
    _add_call(builder, "a", "b")
    _add_call(builder, "b", "requests.post")

    result = FlowEngine().analyze(builder, {})
    assert result.egress("a")["IO_NETWORK"] == pytest.approx(1.0)
    assert result.egress("b")["IO_NETWORK"] == pytest.approx(1.0)


def test_diamond_graph_combines_both_downstream_sinks(builder: ConcreteGraphBuilder) -> None:
    """`a` fans out to `b` and `c` (one edge each, 0.5 probability apiece);
    `b` always reaches a network sink, `c` always reaches a storage sink -
    `a`'s combined egress must be the probability-weighted sum: 0.5 on
    each dimension."""
    _add_function(builder, "a")
    _add_function(builder, "b")
    _add_function(builder, "c")
    _add_call(builder, "a", "b")
    _add_call(builder, "a", "c")
    _add_call(builder, "b", "requests.post")
    _add_call(builder, "c", "fs.readFileSync")

    result = FlowEngine().analyze(builder, {})
    egress = result.egress("a")
    assert egress["IO_NETWORK"] == pytest.approx(0.5)
    assert egress["IO_STORAGE"] == pytest.approx(0.5)


def test_no_outgoing_calls_is_all_zero_egress(builder: ConcreteGraphBuilder) -> None:
    _add_function(builder, "leaf")
    result = FlowEngine().analyze(builder, {})
    assert sum(result.egress("leaf").values) == pytest.approx(0.0)


# --------------------------------------------------------------------- #
# SCC cycle contraction (mitigation strategy 1)
# --------------------------------------------------------------------- #
def test_three_cycle_does_not_hang_or_diverge(builder: ConcreteGraphBuilder) -> None:
    """`A -> B -> C -> A`, with `C` also reaching an external sink - a
    naive (uncontracted) random walk over this graph has spectral radius
    >= 1 on the cyclic block, so a literal `(I - Q)^-1` would be singular
    and an uncontracted truncated series would never accumulate any sink
    mass for A/B/C. `FlowEngine._condense` must collapse the cycle into
    one meta-node before the absorption math runs, so this simply
    terminates (this test's own timeout is the real assertion: a hang
    here means SCC contraction regressed) and every member of the cycle
    ends up with identical, non-zero egress (they're the same meta-node)."""
    _add_function(builder, "a")
    _add_function(builder, "b")
    _add_function(builder, "c")
    _add_call(builder, "a", "b")
    _add_call(builder, "b", "c")
    _add_call(builder, "c", "a")
    _add_call(builder, "c", "requests.post")

    start = time.monotonic()
    result = FlowEngine().analyze(builder, {})
    elapsed = time.monotonic() - start

    assert elapsed < 5.0
    for node in ("a", "b", "c"):
        assert result.egress(node)["IO_NETWORK"] == pytest.approx(1.0)
    # All three collapsed into the same supernode.
    assert len({result.scc_representative[n] for n in ("a", "b", "c")}) == 1


def test_self_referential_cycle_with_no_escape_is_zero_mass(builder: ConcreteGraphBuilder) -> None:
    """A cycle with no edge leaving it at all (no external sink reachable)
    must resolve to zero absorbed mass everywhere in the cycle, not an
    error or non-terminating loop."""
    _add_function(builder, "a")
    _add_function(builder, "b")
    _add_call(builder, "a", "b")
    _add_call(builder, "b", "a")

    result = FlowEngine().analyze(builder, {})
    assert sum(result.egress("a").values) == pytest.approx(0.0)
    assert sum(result.egress("b").values) == pytest.approx(0.0)


# --------------------------------------------------------------------- #
# Sink taxonomy lookup
# --------------------------------------------------------------------- #
def test_classify_sink_distinguishes_same_package_different_members() -> None:
    """The spec's own worked example: `lodash.get` is PURE_LEAF,
    `lodash.template` is PROC_LIFECYCLE - proving sinks are keyed by fully
    qualified member path, not bare package name."""
    pure = classify_sink(("lodash", "get"))
    proc = classify_sink(("lodash", "template"))
    assert pure.dominant() == "PURE_LEAF"
    assert proc.dominant() == "PROC_LIFECYCLE"


def test_classify_sink_unknown_defaults_to_proc_lifecycle_not_pure() -> None:
    """An unrecognized sink must never be silently claimed PURE_LEAF - see
    `classify_sink`'s own docstring on why PROC_LIFECYCLE is the honest
    catch-all."""
    vector = classify_sink(("some_totally_unknown_package", "doSomething"))
    assert vector.dominant() == "PROC_LIFECYCLE"


# --------------------------------------------------------------------- #
# Performance: sparse solve only, on a 1,000-node synthetic graph
# --------------------------------------------------------------------- #
def _build_synthetic_graph(n: int, seed: int = 0) -> ConcreteGraphBuilder:
    rng = random.Random(seed)
    b = ConcreteGraphBuilder(repo_root="/tmp/perf", symbol_table=GlobalSymbolTable())
    names = [f"func_{i}" for i in range(n)]
    for name in names:
        _add_function(b, name)

    for i, name in enumerate(names):
        out_edges = rng.randint(0, 3)
        for _ in range(out_edges):
            target_i = rng.randint(0, n - 1)
            _add_call(b, name, names[target_i])
        if i % 10 == 0:
            _add_call(b, name, "requests.post" if i % 20 == 0 else "fs.readFileSync")

    # A deliberate cycle, to make sure the performance test also exercises
    # SCC condensation, not just an already-acyclic graph.
    _add_call(b, "func_1", "func_2")
    _add_call(b, "func_2", "func_3")
    _add_call(b, "func_3", "func_1")
    return b


def test_sparse_solve_completes_within_20ms_on_1000_node_graph() -> None:
    builder = _build_synthetic_graph(1000)
    engine = FlowEngine()
    g = builder.calls_graph
    internal_nodes, sink_nodes = engine._partition(builder, g)
    condensation, node_to_super = engine._condense(g, internal_nodes)
    super_ids = list(condensation.nodes)
    super_index = {sid: i for i, sid in enumerate(super_ids)}
    n_super = len(super_ids)
    sink_list = sorted(sink_nodes)
    sink_index = {s: i for i, s in enumerate(sink_list)}
    n_sink = len(sink_list)

    start = time.perf_counter()
    q_mat, r_mat = engine._build_transition_matrices(
        g, condensation, super_ids, super_index, sink_index, n_super, n_sink
    )
    engine._power_series(q_mat, r_mat, FlowEngine.MAX_HOPS)
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    assert elapsed_ms < 20.0, f"sparse solve took {elapsed_ms:.2f}ms, expected < 20ms"


def test_full_analyze_completes_on_1000_node_graph_without_error() -> None:
    """Not a strict latency assertion (see the module docstring on why the
    full `analyze()` call, dominated by `networkx` betweenness/pagerank,
    is a once-per-index cost rather than the <20ms-budgeted operation) -
    just proves the whole pipeline actually terminates and returns sane
    data on a graph two orders of magnitude larger than a typical single
    query's neighborhood."""
    builder = _build_synthetic_graph(1000)
    result = FlowEngine().analyze(builder, {})
    assert len(result.downstream_egress) > 0
    assert 0.0 <= result.fan_divergence
