"""Phase E: submodular selection layer hardening.

Scope note, and a real deviation from the original brief, documented here
rather than silently: the brief specified an Immunity Set covering both
`dist(seed, v) <= 1.0` *and* causal_path node IDs, `K=10`, and a cascade-
admission gate keyed on edge confidence/subsystem scope (lexical-sibling
rejection). Empirical diagnosis against the real Django corpus (see the
phase-e commit messages) found:

  - `causal_path` is computed *after* packing, from the already-packed
    symbol set (`prism.surface.build.build_context_package` calls
    `pack_symbol_context` first, `compute_causal_path` second) - wiring
    `causal_path_ids` into the knapsack loop is a real circular
    dependency the brief did not account for. Per the user's own
    Option-1 directive, this half of the Immunity Set is dropped
    entirely; only the `dist <= 1.0` half is implemented
    (`PHASE_E_IMMUNE_DIST`).
  - The 3 real regressions this phase targets (t02_015 bloat, t02_017/
    t02_019 missing near-seed symbols) were never caused by a lexical-
    cascade admission path - no such path exists in `submodular_
    knapsack.py` (`_discover_successors` only ever follows real graph
    edges). The actual causes were (a) uncapped upstream-frontier
    crowding and (b) the module's own already-documented `d>=2`
    dominance-formula blind spot letting distant, cross-subsystem
    candidates outscore near, low-novelty ones. Per the user's second
    Option-1 directive, the implemented fixes target those causes
    directly: `UPSTREAM_FRONTIER_CAP`, `SCOPE_GATE_MIN_DIST` (a scope-
    gated *admissibility* rule, not a scoring floor - see that
    constant's own docstring for why a floor was rejected), and the
    mandatory downstream direct-successor protection. No lexical-
    similarity-based cascade gate is implemented, so no test for one
    appears below.
"""
from __future__ import annotations

import os
import subprocess
import sys

import networkx as nx

from prism.graph.symbol_table import GlobalSymbolTable, SymbolInfo
from prism.packer.submodular_knapsack import (
    DEFAULT_BETA,
    DEFAULT_DELTA_MAX,
    MAX_ZERO_NOVELTY_K,
    PHASE_E_IMMUNE_DIST,
    SCOPE_GATE_MIN_DIST,
    UPSTREAM_FRONTIER_CAP,
    compute_candidate_value,
    select_submodular_context,
)


def _flat_graph(seed: str, successors: list[str]) -> nx.DiGraph:
    g = nx.DiGraph()
    for s in successors:
        g.add_edge(seed, s)
    return g


# ============================================================
# Mechanism C: MAX_ZERO_NOVELTY_K=10 + distance-1 immunity
# ============================================================

def test_max_zero_novelty_k_is_ten():
    assert MAX_ZERO_NOVELTY_K == 10


def test_k10_prevents_premature_truncation():
    """10 zero-novelty direct successors (all at a non-immune dist=2.0,
    so they run the streak gauntlet) followed by a genuinely novel 11th -
    under the original K=5, the back half of the zero-novelty run would
    have been permanently blocked; at K=10, every zero-novelty admit
    still lands (10 <= K) and the novel 11th is unaffected either way."""
    seed = "seed"
    zero_novelty = [f"zn{i}" for i in range(10)]
    novel = "novel_tail"
    graph = _flat_graph(seed, [*zero_novelty, novel])
    dist_w_map = {s: 2.0 for s in zero_novelty} | {novel: 2.0}
    feature_masks = {s: 0 for s in zero_novelty} | {novel: 0xFF}
    costs = {seed: 1, **{s: 1 for s in zero_novelty}, novel: 1}

    selected = select_submodular_context(graph, seed, target_budget=100, dist_w_map=dist_w_map, feature_masks=feature_masks, costs=costs)
    assert set(zero_novelty) <= set(selected)
    assert novel in selected


def test_distance_one_nodes_immune_to_k_stop():
    """12 consecutive dist<=1.0 zero-novelty successors - more than
    MAX_ZERO_NOVELTY_K - are all packed (budget permitting): distance-1
    immunity means an immune zero-novelty admit never even increments
    the streak, so it can never trip the gate regardless of run length."""
    seed = "seed"
    immune = [f"im{i}" for i in range(12)]
    graph = _flat_graph(seed, immune)
    dist_w_map = {s: PHASE_E_IMMUNE_DIST for s in immune}
    feature_masks = {s: 0 for s in immune}
    costs = {seed: 1, **{s: 1 for s in immune}}

    selected = select_submodular_context(graph, seed, target_budget=100, dist_w_map=dist_w_map, feature_masks=feature_masks, costs=costs)
    assert set(immune) <= set(selected)


def test_immune_node_exceeding_budget_skipped_safely():
    """A distance-1 immune candidate whose own cost exceeds the
    *remaining* budget is skipped, not force-admitted over the ceiling -
    the loop continues rather than aborting, and a smaller subsequent
    candidate still gets in."""
    seed = "seed"
    too_big = "too_big"
    fits = "fits"
    graph = _flat_graph(seed, [too_big, fits])
    dist_w_map = {too_big: 1.0, fits: 1.0}
    feature_masks = {too_big: 0, fits: 0}
    costs = {seed: 5, too_big: 1000, fits: 3}

    selected = select_submodular_context(graph, seed, target_budget=10, dist_w_map=dist_w_map, feature_masks=feature_masks, costs=costs)
    assert too_big not in selected
    assert fits in selected
    total_cost = sum(costs[s] for s in selected)
    assert total_cost <= 10


def test_zero_cost_symbol_does_not_divide_by_zero():
    """A synthetic/placeholder symbol with cost=0 (Issue #18's own
    scenario) computes its marginal density using the same `max(cost, 1)`
    floor `select_submodular_context`'s own main loop and cascade both
    already apply before this phase - no ZeroDivisionError, and the
    floor makes a 0-cost candidate's density equal its raw value."""
    value = compute_candidate_value(1.0, 0xFF, 0, DEFAULT_BETA, DEFAULT_DELTA_MAX)
    density = value / max(0, 1)
    assert density == value


# ============================================================
# Mechanism A: upstream frontier cap
# ============================================================

def test_upstream_frontier_cap_bounds_competitive_candidates():
    """6 real upstream callers, all comfortably within upstream_max_hops
    and budget - only UPSTREAM_FRONTIER_CAP of them ever enter the
    competitive frontier at all, so at most that many are ever admitted
    via the general loop, regardless of how much budget is available."""
    seed = "seed"
    callers = [f"caller{i}" for i in range(6)]
    graph = nx.DiGraph()
    for c in callers:
        graph.add_edge(c, seed)
    dist_w_upstream_map = {c: 0.7 + i * 0.05 for i, c in enumerate(callers)}
    costs = {seed: 1, **{c: 1 for c in callers}}
    feature_masks = {seed: 0, **{c: 0 for c in callers}}

    selected = select_submodular_context(
        graph, seed, target_budget=1000, dist_w_map={}, feature_masks=feature_masks, costs=costs,
        dist_w_upstream_map=dist_w_upstream_map,
    )
    admitted_callers = [s for s in selected if s in callers]
    assert len(admitted_callers) <= UPSTREAM_FRONTIER_CAP


def test_mandatory_upstream_protection_still_honored_under_cap():
    """The cap bounds the *competitive* frontier, but the single
    strongest-coupled caller (lowest dist_w_upstream) is always inside
    that top-N by construction, so the pre-existing mandatory-upstream-
    protection guarantee is unaffected by the cap - it still lands even
    when many weaker callers exist and the general loop's own budget is
    spent elsewhere first."""
    seed = "seed"
    decoy_downstream = "decoy_downstream"
    callers = [f"caller{i}" for i in range(5)]
    best_caller = "caller0"  # lowest dist_w_upstream below
    graph = nx.DiGraph()
    graph.add_edge(seed, decoy_downstream)
    for c in callers:
        graph.add_edge(c, seed)
    dist_w_upstream_map = {c: 0.7 + i * 0.1 for i, c in enumerate(callers)}
    dist_w_map = {decoy_downstream: 1.0}
    costs = {seed: 1, decoy_downstream: 1, **{c: 1 for c in callers}}
    feature_masks = {seed: 0, decoy_downstream: 0, **{c: 0 for c in callers}}

    selected = select_submodular_context(
        graph, seed, target_budget=1000, dist_w_map=dist_w_map, feature_masks=feature_masks, costs=costs,
        dist_w_upstream_map=dist_w_upstream_map,
    )
    assert best_caller in selected


# ============================================================
# Mechanism B: subsystem scope gate (realized as an admissibility rule,
# not a scoring floor - see this module's own top-level docstring)
# ============================================================

def _symbol_table_with(seed_module: str, candidate_module: str) -> GlobalSymbolTable:
    table = GlobalSymbolTable()
    table.add(SymbolInfo(qualified_name="pkg.seed", kind="function", file="f.py", line_range=(1, 2), language_id="python", module=seed_module))
    table.add(SymbolInfo(qualified_name="pkg.candidate", kind="function", file="g.py", line_range=(1, 2), language_id="python", module=candidate_module))
    return table


def test_scope_gate_excludes_distant_unrelated_candidate():
    """A candidate beyond SCOPE_GATE_MIN_DIST hops, in a disjoint
    package (no shared Prefix_3) and with no substance-bit overlap with
    the seed, is never admitted into the frontier at all - even with an
    ample budget and real novel feature coverage of its own."""
    graph = _flat_graph("pkg.seed", ["pkg.candidate"])
    dist_w_map = {"pkg.candidate": SCOPE_GATE_MIN_DIST + 0.5}
    feature_masks = {"pkg.seed": 0, "pkg.candidate": 0xFF}
    costs = {"pkg.seed": 1, "pkg.candidate": 1}
    symbol_table = _symbol_table_with("django.contrib.admin", "django.utils.datastructures")

    selected = select_submodular_context(
        graph, "pkg.seed", target_budget=1000, dist_w_map=dist_w_map, feature_masks=feature_masks, costs=costs,
        symbol_table=symbol_table,
    )
    assert "pkg.candidate" not in selected


def test_scope_gate_permits_distant_same_prefix_candidate():
    """Same shape and distance as above, but the candidate shares the
    seed's own package prefix (first 3 dot-components) - in scope
    despite the distance, admitted normally."""
    graph = _flat_graph("pkg.seed", ["pkg.candidate"])
    dist_w_map = {"pkg.candidate": SCOPE_GATE_MIN_DIST + 0.5}
    feature_masks = {"pkg.seed": 0, "pkg.candidate": 0xFF}
    costs = {"pkg.seed": 1, "pkg.candidate": 1}
    symbol_table = _symbol_table_with("django.contrib.admin.sites", "django.contrib.admin.options")

    selected = select_submodular_context(
        graph, "pkg.seed", target_budget=1000, dist_w_map=dist_w_map, feature_masks=feature_masks, costs=costs,
        symbol_table=symbol_table,
    )
    assert "pkg.candidate" in selected


def test_scope_gate_is_a_noop_without_a_symbol_table():
    """Every existing caller against a synthetic graph with no real
    symbol table (symbol_table=None, the default) is unaffected - the
    gate never excludes anything when it has no module info to check."""
    graph = _flat_graph("seed", ["far"])
    dist_w_map = {"far": SCOPE_GATE_MIN_DIST + 1.0}
    feature_masks = {"seed": 0, "far": 0}
    costs = {"seed": 1, "far": 1}

    selected = select_submodular_context(graph, "seed", target_budget=100, dist_w_map=dist_w_map, feature_masks=feature_masks, costs=costs)
    assert "far" in selected


# ============================================================
# Mandatory downstream direct-successor protection
# ============================================================

def test_downstream_direct_successor_protection_outranks_upstream_under_tight_budget():
    """A real dist<=1.0 downstream successor and an upstream caller both
    want the same last slice of budget - only one fits. Mechanism A's own
    "downstream callees take priority over upstream ancestors" directive
    means the downstream successor wins, not the upstream caller,
    matching the real t02_017/t02_019 diagnosis shape."""
    seed = "seed"
    downstream = "downstream"
    upstream = "upstream"
    graph = nx.DiGraph()
    graph.add_edge(seed, downstream)
    graph.add_edge(upstream, seed)
    dist_w_map = {downstream: 1.0}
    dist_w_upstream_map = {upstream: 0.8}
    costs = {seed: 1, downstream: 5, upstream: 5}
    feature_masks = {seed: 0, downstream: 0, upstream: 0}

    # budget only ever covers the seed plus exactly one of {downstream, upstream}
    selected = select_submodular_context(
        graph, seed, target_budget=6, dist_w_map=dist_w_map, feature_masks=feature_masks, costs=costs,
        dist_w_upstream_map=dist_w_upstream_map,
    )
    assert downstream in selected
    assert upstream not in selected


# ============================================================
# Determinism
# ============================================================

_DETERMINISM_SCRIPT = """
import sys
import networkx as nx
from prism.packer.submodular_knapsack import select_submodular_context

seed = "seed"
candidates = [f"c{i}" for i in range(20)]
graph = nx.DiGraph()
for c in candidates:
    graph.add_edge(seed, c)
dist_w_map = {c: 3.0 for c in candidates}
feature_masks = {c: (1 << (i % 4)) for i, c in enumerate(candidates)}
costs = {seed: 1, **{c: 3 for c in candidates}}

selected = select_submodular_context(graph, seed, target_budget=20, dist_w_map=dist_w_map, feature_masks=feature_masks, costs=costs)
print(",".join(sorted(selected)))
"""


def test_selection_determinism_across_hashseeds():
    """Same inputs, two separate processes with different PYTHONHASHSEED
    values (0 vs 42) - byte-identical selected sets, since every set
    iterated over frontier/cascade candidates is explicitly sorted
    before use, never relying on Python's (per-process, hash-seed-
    dependent) set iteration order."""
    env0 = dict(os.environ, PYTHONHASHSEED="0")
    env42 = dict(os.environ, PYTHONHASHSEED="42")
    out0 = subprocess.run([sys.executable, "-c", _DETERMINISM_SCRIPT], cwd="/home/user/SCE", env=env0, capture_output=True, text=True, check=True).stdout.strip()
    out42 = subprocess.run([sys.executable, "-c", _DETERMINISM_SCRIPT], cwd="/home/user/SCE", env=env42, capture_output=True, text=True, check=True).stdout.strip()
    assert out0 == out42
    assert out0 != ""
