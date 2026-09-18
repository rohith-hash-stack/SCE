"""Vertical end-to-end integration test: Phases A through E, run against a
real multi-file, multi-package repo on disk (no synthetic graphs, no
mocks) - AST extraction through context-package assembly.

**Deviation from the brief, documented here rather than silently**: the
brief's own pseudocode calls for `task_type="T02"` and `task_type="T13"`.
Neither is a real value - `benchmarks.ground_truth.schema.EvaluationTask.
task_type`'s own literal enum is exactly `{"chain", "blast", "redundancy",
"architecture", "debug"}`; "T02"/"T13" are filename conventions from the
ground-truth task corpus, never real `task_type` strings anywhere in the
production code path (established directly, empirically, during this
session's own Phase D work - `prism.surface.build.causal_path_applies_to_
task_type`'s own docstring states this explicitly). This test uses the
real values a T02-shaped task actually carries (`"debug"`) and a real
T13-shaped one (`"blast"`) instead.

**A second, structural deviation**: `pkg/unrelated/gis.py`'s
`calculate_geometry` is deliberately left with no real caller anywhere in
the service chain, exactly as the brief's own file listing describes it
(no call site named for it). This makes the "excluded from the causal
path / never enters the pack" assertions below true by simple
*reachability* (it is never a graph successor of the seed at all, so it
is never even a candidate for either mechanism) - a real, worthwhile
invariant in its own right (the causal engine never hallucinates a
connection to genuinely unrelated code), but a different, weaker claim
than "a *reachable* cross-subsystem candidate gets actively filtered out
by the scope rule / scope gate." That stronger claim - a real graph edge
into an out-of-scope candidate that must be actively rejected - is
already covered directly, per-mechanism, by `tests/phase_d/
test_causal_path.py`'s `test_scope_rule_blocks_cross_namespace_without_
substance` and `tests/phase_e/test_selection_layer.py`'s
`test_scope_gate_excludes_distant_unrelated_candidate`; this test
verifies the vertical wiring end to end, not the scope rule's own
decision boundary a second time.
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.graph.symbol_table import SymbolInfo
from prism.packer.submodular_knapsack import pack_symbol_context
from prism.scanner.entrypoint_gate import is_test_file
from prism.surface.build import build_context_package
from prism.traversal.continuous_dijkstra import compute_topological_distances

SEED = "pkg.core.service.AppService.run_pipeline"
BASE_EXECUTE = "pkg.core.base.BaseService.execute"
PRODUCE = "pkg.core.helpers.produce"
CONSUME = "pkg.core.helpers.consume"
CALCULATE_GEOMETRY = "pkg.unrelated.gis.calculate_geometry"


def _build_repo(tmp_path):
    repo = tmp_path / "repo"
    pkg = repo / "pkg"
    core = pkg / "core"
    unrelated = pkg / "unrelated"
    tests_dir = repo / "tests"
    for d in (pkg, core, unrelated, tests_dir):
        d.mkdir(parents=True)

    (pkg / "__init__.py").write_text("")
    (core / "__init__.py").write_text("")
    (unrelated / "__init__.py").write_text("")

    (core / "base.py").write_text(
        "import sqlite3\n"
        "\n"
        "\n"
        "class BaseService:\n"
        "    def execute(self, query: str):\n"
        "        conn = sqlite3.connect(':memory:')\n"
        "        return conn.execute(query)\n"
    )
    (core / "helpers.py").write_text(
        "async def produce() -> dict:\n"
        "    return {'status': 'ok'}\n"
        "\n"
        "\n"
        "async def consume(payload: dict) -> bool:\n"
        "    return bool(payload)\n"
    )
    (core / "service.py").write_text(
        "from pkg.core.base import BaseService\n"
        "from pkg.core.helpers import consume, produce\n"
        "\n"
        "\n"
        "class AppService(BaseService):\n"
        "    async def run_pipeline(self):\n"
        "        data = await produce()\n"
        "        await consume(data)\n"
        "        self.execute('SELECT 1')\n"
    )
    (unrelated / "gis.py").write_text(
        "def calculate_geometry():\n"
        "    return {'area': 0.0}\n"
    )
    (tests_dir / "test_service.py").write_text(
        "def test_run_pipeline_smoke():\n"
        "    assert True\n"
    )
    return repo


def test_full_pipeline_a_to_e(tmp_path):
    repo = _build_repo(tmp_path)

    # ---------------------------------------------------------------- #
    # Phase A: AST extraction & filtering
    # ---------------------------------------------------------------- #
    builder, _tag_matrix = build_pipeline(str(repo))

    test_file_path = str(repo / "tests" / "test_service.py")
    assert is_test_file(test_file_path.replace(str(repo) + "/", ""))

    produce_info = builder.symbol_table.get(PRODUCE)
    consume_info = builder.symbol_table.get(CONSUME)
    assert isinstance(produce_info, SymbolInfo)
    assert isinstance(consume_info, SymbolInfo)
    assert produce_info.kind == "function"
    assert consume_info.kind == "function"

    # ---------------------------------------------------------------- #
    # Phase B: graph construction & resolution
    # ---------------------------------------------------------------- #
    assert builder.graph.has_edge("pkg.core.service.AppService", "pkg.core.base.BaseService")
    extends_data = builder.graph.get_edge_data("pkg.core.service.AppService", "pkg.core.base.BaseService")
    assert extends_data.get("relation") == "EXTENDS"

    # self.execute() inside run_pipeline resolves through AppService's own
    # MRO to the inherited BaseService.execute, not a dangling/unresolved
    # reference - real G40 inherited-method resolution, not a synthetic
    # stand-in.
    assert builder.graph.has_edge(SEED, BASE_EXECUTE)

    # ---------------------------------------------------------------- #
    # Phase C: continuous traversal
    # ---------------------------------------------------------------- #
    dist_w_map = compute_topological_distances(builder, seed=SEED, d_max=5.0)
    for target in (PRODUCE, CONSUME, BASE_EXECUTE):
        assert target in dist_w_map, f"{target} not reachable from {SEED} within d_max=5.0"
        assert dist_w_map[target] <= 3.0, f"{target} at dist_w={dist_w_map[target]}, expected <= 3.0"

    # ---------------------------------------------------------------- #
    # Phase D: causal path synthesis
    # ---------------------------------------------------------------- #
    pkg_debug = build_context_package(builder, SEED, str(repo), 4000, task_type="debug")
    assert pkg_debug.causal_path is not None
    stage_symbols = [s.symbol for s in pkg_debug.causal_path.stages]
    assert SEED == stage_symbols[0]
    assert BASE_EXECUTE in stage_symbols, f"causal path never reached {BASE_EXECUTE}: {stage_symbols}"
    assert CALCULATE_GEOMETRY not in stage_symbols

    pkg_blast = build_context_package(builder, SEED, str(repo), 4000, task_type="blast")
    assert pkg_blast.causal_path is None

    # ---------------------------------------------------------------- #
    # Phase E: submodular selection & knapsack packing
    # ---------------------------------------------------------------- #
    pack_result = pack_symbol_context(builder, SEED, target_budget=4000)
    selected = set(pack_result.selected)

    for required in (SEED, PRODUCE, CONSUME, BASE_EXECUTE):
        assert required in selected, f"{required} missing from packed context: {selected}"

    unrelated_in_pack = {s for s in selected if s.startswith("pkg.unrelated.")}
    assert unrelated_in_pack == set(), f"unexpected pkg.unrelated symbols in pack: {unrelated_in_pack}"

    assert pack_result.total_cost <= 4000
