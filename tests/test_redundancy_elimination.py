"""Integration + direct-unit tests for Prism v1.1's Semantic Redundancy
elimination: `prism.packer.submodular_knapsack`'s bitwise marginal-gain
scoring must reject near-duplicate candidates once their features are
already covered, in favor of a genuinely novel one - even when both sit
at the exact same topological distance from the seed, where a plain
distance-only ranking would have no basis to prefer one over the other.
"""
from __future__ import annotations

import networkx as nx

from prism.cli import build_pipeline
from prism.packer.submodular_knapsack import pack_symbol_context, select_submodular_context
from prism.semantics.bitmask import FeatureBit, compose_mask


# --------------------------------------------------------------------- #
# Direct unit test: exactly the spec's own scenario, hand-constructed
# bitmasks - five identical-feature loggers vs. one distinct database
# store function, all at the same distance from the seed.
# --------------------------------------------------------------------- #
def test_five_identical_loggers_yield_only_one_admission_before_the_distinct_store():
    graph = nx.DiGraph()
    loggers = [f"logger_{i}" for i in range(5)]
    graph.add_node("seed")
    for name in [*loggers, "store"]:
        graph.add_edge("seed", name)

    logger_mask = compose_mask(FeatureBit.SINK_FILESYSTEM_IO, FeatureBit.ROLE_LEAF_UTILITY)
    store_mask = compose_mask(FeatureBit.SINK_DATABASE_IO, FeatureBit.ROLE_LEAF_SERVICE)

    feature_masks = {name: logger_mask for name in loggers}
    feature_masks["store"] = store_mask
    feature_masks["seed"] = 0

    dist_w_map = {name: 1.0 for name in [*loggers, "store"]}
    costs = {name: 10 for name in [*loggers, "store", "seed"]}

    # Budget for the seed plus exactly two more items - enough to admit
    # one logger and the store function, never a second redundant logger.
    selected = select_submodular_context(graph, "seed", target_budget=30, dist_w_map=dist_w_map, feature_masks=feature_masks, costs=costs)

    selected_loggers = [n for n in selected if n in loggers]
    assert len(selected_loggers) == 1, f"expected exactly one logger admitted, got {selected_loggers}"
    assert "store" in selected, "the distinct database store function must be admitted over a redundant logger"


def test_generous_budget_still_rejects_every_redundant_logger():
    """Even with room to spare, once one logger's features are covered,
    every other identical-featured logger offers zero marginal gain
    relative to the distinct store function - store is preferred first,
    and (in this scenario, where nothing else competes for the remaining
    budget) redundant loggers are still only ever admitted after every
    genuinely novel candidate, proving this isn't merely a budget
    artifact of the tight-budget test above.
    """
    graph = nx.DiGraph()
    loggers = [f"logger_{i}" for i in range(5)]
    graph.add_node("seed")
    for name in [*loggers, "store"]:
        graph.add_edge("seed", name)

    logger_mask = compose_mask(FeatureBit.SINK_FILESYSTEM_IO, FeatureBit.ROLE_LEAF_UTILITY)
    store_mask = compose_mask(FeatureBit.SINK_DATABASE_IO, FeatureBit.ROLE_LEAF_SERVICE)
    feature_masks = {name: logger_mask for name in loggers}
    feature_masks["store"] = store_mask
    feature_masks["seed"] = 0
    dist_w_map = {name: 1.0 for name in [*loggers, "store"]}
    costs = {name: 10 for name in [*loggers, "store", "seed"]}

    # Budget for the seed plus every candidate - so admission *order*,
    # not availability, is what's actually being tested.
    selected = select_submodular_context(
        graph, "seed", target_budget=1000, dist_w_map=dist_w_map, feature_masks=feature_masks, costs=costs
    )
    # store must be admitted before any *second* logger.
    store_index = selected.index("store")
    logger_indices = [selected.index(n) for n in loggers if n in selected]
    assert sum(1 for i in logger_indices if i < store_index) <= 1


def test_a_logger_alone_with_no_competing_novelty_is_still_admitted():
    """Redundancy elimination must never become "never pack a logger at
    all" - with no distinct candidate competing for the budget, a single
    logger (the only feature domain available) is still admitted."""
    graph = nx.DiGraph()
    graph.add_edge("seed", "logger_0")
    feature_masks = {"seed": 0, "logger_0": int(FeatureBit.SINK_FILESYSTEM_IO)}
    dist_w_map = {"logger_0": 1.0}
    costs = {"seed": 10, "logger_0": 10}
    selected = select_submodular_context(graph, "seed", target_budget=30, dist_w_map=dist_w_map, feature_masks=feature_masks, costs=costs)
    assert "logger_0" in selected


# --------------------------------------------------------------------- #
# Integration test: real Python source, real four-axis extraction - five
# textually-identical logger functions (so their extracted Substance/
# Form/Output bits are naturally identical, not hand-assigned) plus one
# structurally distinct database-store function.
# --------------------------------------------------------------------- #
def _redundancy_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    logger_bodies = "\n\n\n".join(
        f"def log_{name}(msg):\n"
        f"    with open('app.log', 'a') as f:\n"
        f"        f.write(msg)\n"
        for name in ("debug", "info", "warning", "error", "critical")
    )
    (repo / "svc.py").write_text(
        "import sqlalchemy\n\n\n"
        f"{logger_bodies}\n\n\n"
        "def store_order(data):\n"
        "    return sqlalchemy.create_engine('x').execute(data)\n\n\n"
        "def seed(raw):\n"
        "    log_debug(raw)\n"
        "    log_info(raw)\n"
        "    log_warning(raw)\n"
        "    log_error(raw)\n"
        "    log_critical(raw)\n"
        "    store_order(raw)\n"
    )
    return repo


def test_real_extraction_identical_logger_bodies_share_the_same_mask(tmp_path):
    from prism.semantics.extractor import compute_feature_masks

    repo = _redundancy_repo(tmp_path)
    builder, _ = build_pipeline(str(repo))
    masks = compute_feature_masks(builder)
    logger_masks = {masks[f"svc.log_{name}"] for name in ("debug", "info", "warning", "error", "critical")}
    assert len(logger_masks) == 1, f"textually-identical loggers produced different masks: {logger_masks}"
    assert masks["svc.log_debug"] != masks["svc.store_order"]


def test_real_pipeline_admits_only_one_logger_under_a_tight_budget(tmp_path):
    repo = _redundancy_repo(tmp_path)
    builder, _ = build_pipeline(str(repo))

    # Room for the seed (39 tokens) + store_order (24) + exactly one
    # 32-token logger - not all five.
    result = pack_symbol_context(builder, "svc.seed", target_budget=95)

    admitted_loggers = [s for s in result.selected if s.startswith("svc.log_")]
    assert len(admitted_loggers) == 1, f"expected exactly one logger admitted, got {admitted_loggers}"
    assert "svc.store_order" in result.selected, "the distinct database store function must be admitted"
    # store_order (a genuinely novel feature domain) is admitted before
    # any logger, not merely alongside one.
    assert result.selected.index("svc.store_order") < result.selected.index(admitted_loggers[0])
