"""Tests for Item 14 (second post-implementation audit): Server
Concurrency & Thread-Safe Stateless Slicing.

The MCP server (`prism.mcp.server`) caches one `RepoContext` per
repository and reuses it across every subsequent tool call
(`prism.mcp.cache.GraphCache`) - a long-lived server process can receive
overlapping tool calls against the *same* cached context (several
concurrent agent sessions, or a client that doesn't serialize its own
calls), so the read path (`DistanceEngine.compute_all`,
`ContextKnapsackPacker.pack`, `ASTCompressor`, `mine_sibling_blueprint`)
needs to be safe to call concurrently from multiple threads against one
shared `ConcreteGraphBuilder` - what this file calls "stateless slicing":
nothing in that path may mutate shared state in a way that could produce
a wrong or non-deterministic result under concurrency.

This is proven two ways:
  1. Direct: N threads independently `pack()` the *same* seed against the
     *same* shared builder/tag_matrix/distance_engine, and every result
     must be identical to a single-threaded baseline.
  2. The one piece of actual shared *mutable* state in the read path -
     `ConcreteGraphBuilder`'s three lazy-memoization caches
     (`calls_graph`, `_go_method_registry`, `_go_class_registry`) - is
     exercised under concurrent *first* access specifically (a fresh,
     never-yet-queried builder), the scenario a race would actually be
     observable in.

See `tests/test_mcp_server.py`/`test_mcp_security.py` for the MCP-tool-
level surface; this file tests the underlying `prism.slicer`/
`prism.graph` engine directly, independent of the MCP transport.
"""
from __future__ import annotations

import threading

import networkx as nx

from prism.cli import build_pipeline
from prism.graph.contracts import compute_contracts
from prism.graph.metamodel import SemanticMetamodel
from prism.slicer.distance import DistanceConfig, DistanceEngine
from prism.slicer.knapsack import ContextKnapsackPacker

THREAD_COUNT = 10


def _order_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "orders.py").write_text(
        "class OrderValidator:\n"
        "    def validate(self, amount):\n"
        "        return amount > 0\n"
        "\n"
        "\n"
        "class OrderService:\n"
        "    def __init__(self):\n"
        "        self.validator = OrderValidator()\n"
        "\n"
        "    def create_order(self, amount):\n"
        "        if not self.validator.validate(amount):\n"
        "            raise ValueError('bad amount')\n"
        "        return self.finalize(amount)\n"
        "\n"
        "    def finalize(self, amount):\n"
        "        return amount\n"
        "\n"
        "\n"
        "class ReportService:\n"
        "    def __init__(self):\n"
        "        self.orders = OrderService()\n"
        "\n"
        "    def summarize(self, amount):\n"
        "        return self.orders.create_order(amount)\n"
    )
    return repo


def _pack(builder, tag_matrix, distance_engine, contracts, seed, budget=2000):
    return ContextKnapsackPacker(token_budget=budget).pack(
        seed, builder, tag_matrix, distance_engine, contracts=contracts
    )


def _item_fingerprint(result):
    """A stable, order-independent summary of exactly what a `PackResult`
    selected and how it rendered each item - the thing that must be
    bit-for-bit identical across threads, independent of `PackedItem`
    equality/hashability."""
    return sorted((item.symbol, item.resolution, item.content) for item in result.items)


def test_ten_threads_packing_the_same_seed_produce_identical_results(built_repo):
    """`built_repo` (`tests/conftest.py`) is a session-scoped fixture
    already shared read-only across the whole test suite - this proves
    it's also safe to share across *concurrent threads* querying it at
    once, not just across sequential test functions."""
    builder, tag_matrix = built_repo
    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig())
    contracts = compute_contracts(builder)
    seed = "src.services.billing.PaymentProcessor.charge"

    baseline = _pack(builder, tag_matrix, distance_engine, contracts, seed)

    results: list = [None] * THREAD_COUNT
    errors: list = []

    def worker(i):
        try:
            results[i] = _pack(builder, tag_matrix, distance_engine, contracts, seed)
        except Exception as exc:  # noqa: BLE001 - captured for the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(THREAD_COUNT)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent pack() raised: {errors}"
    for i, result in enumerate(results):
        assert result is not None, f"thread {i} produced no result"
        assert _item_fingerprint(result) == _item_fingerprint(baseline), f"thread {i} diverged"
        assert result.budget_exceeded == baseline.budget_exceeded
        assert result.knapsack_efficiency_ratio == baseline.knapsack_efficiency_ratio


def test_ten_threads_packing_different_seeds_concurrently_do_not_interfere(built_repo):
    """Different seeds packed concurrently must each get their own
    correct result - not a race that leaks one seed's selection into
    another's."""
    builder, tag_matrix = built_repo
    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig())
    contracts = compute_contracts(builder)
    seeds = [
        "src.services.billing.PaymentProcessor.charge",
        "src.services.pricing.compute_total",
    ]
    baselines = {seed: _pack(builder, tag_matrix, distance_engine, contracts, seed) for seed in seeds}

    results: dict[int, object] = {}
    errors: list = []
    lock = threading.Lock()

    def worker(i):
        seed = seeds[i % len(seeds)]
        try:
            result = _pack(builder, tag_matrix, distance_engine, contracts, seed)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)
            return
        with lock:
            results[i] = (seed, result)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(THREAD_COUNT)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent pack() raised: {errors}"
    assert len(results) == THREAD_COUNT
    for seed, result in results.values():
        assert _item_fingerprint(result) == _item_fingerprint(baselines[seed])


def test_calls_graph_first_access_is_thread_safe(tmp_path):
    """`calls_graph` is already warm by the time `build_pipeline` returns
    (tag inference reads it during indexing itself) - forced back to
    `None` here to directly exercise the double-checked-locking first-
    access path this test is actually about, the scenario a race on the
    memoization would be observable in."""
    repo = _order_repo(tmp_path)
    builder, _tag_matrix = build_pipeline(str(repo))
    builder._calls_graph_cache = None

    results: list = [None] * THREAD_COUNT
    errors: list = []

    def worker(i):
        try:
            results[i] = builder.calls_graph
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(THREAD_COUNT)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    baseline_edges = sorted(results[0].edges(data=True), key=lambda e: (e[0], e[1]))
    for i, g in enumerate(results):
        assert sorted(g.edges(data=True), key=lambda e: (e[0], e[1])) == baseline_edges, f"thread {i} diverged"
        assert sorted(g.nodes()) == sorted(results[0].nodes())


def test_go_registries_first_access_are_thread_safe(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.go").write_text(
        "package main\n"
        "\n"
        "type Context struct{}\n"
        "\n"
        "func (c *Context) JSON(code int) {}\n"
        "\n"
        "func Handler(c *Context) {\n"
        "    c.JSON(200)\n"
        "}\n"
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    # Go call resolution (Pass 2) already warms both registries during
    # indexing itself - forced back to None here to directly exercise the
    # double-checked-locking first-access path, same rationale as
    # `test_calls_graph_first_access_is_thread_safe` above.
    builder._go_method_registry_cache = None
    builder._go_class_registry_cache = None

    method_results: list = [None] * THREAD_COUNT
    class_results: list = [None] * THREAD_COUNT
    errors: list = []

    def worker(i):
        try:
            method_results[i] = builder._go_method_registry()
            class_results[i] = builder._go_class_registry()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(THREAD_COUNT)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    for i in range(THREAD_COUNT):
        assert method_results[i] == method_results[0], f"thread {i} method registry diverged"
        assert class_results[i] == class_results[0], f"thread {i} class registry diverged"
    assert method_results[0] == {"JSON": ["main.Context.JSON"]}


def test_builder_graph_is_unchanged_after_a_burst_of_concurrent_queries(built_repo):
    """Formalizes "immutable RepositoryGraph": nothing in the read path
    (pack/compress/blueprint mining) may mutate `builder.graph` in place,
    even under concurrent access - a byte-for-byte `node_link_data`
    snapshot taken before and after a concurrent query burst must match
    exactly.
    """
    builder, tag_matrix = built_repo
    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig())
    contracts = compute_contracts(builder)
    seed = "src.services.billing.PaymentProcessor.charge"

    before = nx.node_link_data(builder.graph, edges="edges")

    errors: list = []

    def worker():
        try:
            _pack(builder, tag_matrix, distance_engine, contracts, seed)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(THREAD_COUNT)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    after = nx.node_link_data(builder.graph, edges="edges")
    assert before == after
