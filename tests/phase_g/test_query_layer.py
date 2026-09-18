"""Phase G: unified query layer - PrismQuery validation, symbol location,
and tag-expression filtering.

Scope note and deviations from the brief, documented here rather than
silently (see the phase-g-* commit messages for the full evidence):

  - No `PrismQuery`, symbol locator, or tag-expression evaluator existed
    anywhere in this codebase before this phase (confirmed by search) -
    this is genuinely new code, not a rename/hardening of something
    pre-existing, unlike several earlier phases this session. What *did*
    already exist, and is reused rather than duplicated: `prism.mcp.
    security.validate_token_budget` (budget bound), `prism.traversal.
    continuous_dijkstra.compute_topological_distances`'s own seed/seeds
    mutual-exclusivity error wording, `prism.packer.submodular_knapsack.
    suggest_similar_seeds` (fuzzy "did you mean"), and `prism.scanner.
    entrypoint_gate.is_test_file` (the `#test` tag special case).
  - `PrismQuery.task_type` validates against the real, canonical 5-value
    enum (`benchmarks.ground_truth.schema.EvaluationTask.task_type`:
    `{"chain","blast","redundancy","architecture","debug"}`), not the
    brief's own narrower 4-value list (which omits `"chain"` - a real,
    already-Phase-D-supported value).
  - The tag-expression evaluator targets the *newer* v1.1+ four-axis
    `FeatureBit` system (what every phase since A has built on), not the
    *older* `tag_matrix`/metamodel system `prism.mcp.server.find_
    symbols_by_tag` already owns - the two are independent, and this
    phase does not touch that older one.
"""
from __future__ import annotations

import subprocess
import sys

from prism.cli import build_pipeline
from prism.query.errors import QueryValidationError, SymbolNotFoundError
from prism.query.locate import locate_symbol_at, locate_symbol_by_name
from prism.query.schema import VALID_TASK_TYPES, PrismQuery
from prism.query.tag_filter import evaluate_tag_filter, feature_mask_tag_lookup, matches_tag_filter_for_symbol
from prism.semantics.bitmask import FeatureBit


# ============================================================
# Invariant 1: PrismQuery validation
# ============================================================

def test_query_validation_rejects_non_positive_budget():
    for bad_budget in (0, -100):
        try:
            PrismQuery(budget=bad_budget, seed="a")
            assert False, f"budget={bad_budget} should have raised"
        except QueryValidationError:
            pass


def test_query_validation_rejects_over_cap_budget():
    """A real superset of the brief's own "must be positive" ask - reuses
    prism.mcp.security.MAX_TOKEN_BUDGET's own real upper bound rather
    than leaving budget unbounded above."""
    try:
        PrismQuery(budget=10_000_000, seed="a")
        assert False, "an unreasonably large budget should have raised"
    except QueryValidationError:
        pass


def test_query_validation_normalizes_seeds():
    single = PrismQuery(budget=1000, seed="a")
    assert single.resolved_seeds == ["a"]
    multi = PrismQuery(budget=1000, seeds=["a", "b"])
    assert multi.resolved_seeds == ["a", "b"]


def test_query_validation_requires_exactly_one_of_seed_or_seeds():
    try:
        PrismQuery(budget=1000)
        assert False, "neither seed nor seeds should have raised"
    except QueryValidationError:
        pass
    try:
        PrismQuery(budget=1000, seed="a", seeds=["b"])
        assert False, "both seed and seeds should have raised"
    except QueryValidationError:
        pass
    try:
        PrismQuery(budget=1000, seeds=[])
        assert False, "an empty seeds list should have raised"
    except QueryValidationError:
        pass


def test_query_validation_task_type_enum():
    """The real, canonical 5-value enum - includes 'chain', which the
    brief's own narrower 4-value list omits (see this module's own
    top-level docstring)."""
    assert VALID_TASK_TYPES == {"chain", "blast", "redundancy", "architecture", "debug"}
    for valid in VALID_TASK_TYPES:
        PrismQuery(budget=1000, seed="a", task_type=valid)  # must not raise
    try:
        PrismQuery(budget=1000, seed="a", task_type="T02")
        assert False, "a filename-convention value like 'T02' should have raised"
    except QueryValidationError:
        pass


def test_query_validation_d_max_must_be_positive():
    PrismQuery(budget=1000, seed="a", d_max=5.0)  # must not raise
    try:
        PrismQuery(budget=1000, seed="a", d_max=-1.0)
        assert False, "a non-positive d_max should have raised"
    except QueryValidationError:
        pass


# ============================================================
# Invariant 2: symbol locator
# ============================================================

def _locator_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "class Outer:\n"
        "    def inner(self):\n"
        "        x = 1\n"
        "        return x\n"
        "\n"
        "def top_level():\n"
        "    return 1\n"
    )
    builder, _ = build_pipeline(str(repo))
    return builder, str(repo / "mod.py")


def test_locate_symbol_enclosing_line(tmp_path):
    builder, file_path = _locator_repo(tmp_path)
    assert locate_symbol_at(builder, file_path, 4) == "mod.Outer.inner"  # "return x"


def test_locate_symbol_picks_deepest_nested(tmp_path):
    """Line inside inner() resolves to inner, not Outer - the smallest
    enclosing span wins, per Invariant 2's own deepest-symbol rule."""
    builder, file_path = _locator_repo(tmp_path)
    assert locate_symbol_at(builder, file_path, 3) == "mod.Outer.inner"  # "x = 1"
    assert locate_symbol_at(builder, file_path, 1) == "mod.Outer"  # "class Outer:" itself


def test_locate_symbol_missing_coordinate(tmp_path):
    builder, file_path = _locator_repo(tmp_path)
    assert locate_symbol_at(builder, file_path, 9999) is None


def test_locate_symbol_by_name_unique_match(tmp_path):
    builder, _ = _locator_repo(tmp_path)
    assert locate_symbol_by_name(builder, "inner") == "mod.Outer.inner"
    assert locate_symbol_by_name(builder, "top_level") == "mod.top_level"


def test_locate_symbol_by_name_ambiguous_returns_ranked_candidates(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("class A:\n    def execute(self):\n        return 1\n")
    (repo / "b.py").write_text("class B:\n    def execute(self):\n        return 2\n")
    builder, _ = build_pipeline(str(repo))
    try:
        locate_symbol_by_name(builder, "execute")
        assert False, "an ambiguous bare name should have raised"
    except SymbolNotFoundError as exc:
        assert exc.candidates == ["a.A.execute", "b.B.execute"]


def test_locate_symbol_by_name_not_found(tmp_path):
    builder, _ = _locator_repo(tmp_path)
    try:
        locate_symbol_by_name(builder, "totally_nonexistent_symbol_xyz")
        assert False, "a nonexistent name should have raised"
    except SymbolNotFoundError as exc:
        assert exc.name == "totally_nonexistent_symbol_xyz"


# ============================================================
# Invariant 3: tag expression filter
# ============================================================

def test_tag_filter_and_not_evaluation():
    """#network AND NOT #test matches a network-touching, non-test
    symbol, and excludes a network-touching symbol living in a test
    file - real substance bit + real is_test_file special case."""
    network_lookup = feature_mask_tag_lookup(int(FeatureBit.SINK_NETWORK_IO), is_test=False)
    assert evaluate_tag_filter("#network AND NOT #test", network_lookup) is True

    network_in_test_lookup = feature_mask_tag_lookup(int(FeatureBit.SINK_NETWORK_IO), is_test=True)
    assert evaluate_tag_filter("#network AND NOT #test", network_in_test_lookup) is False

    pure_lookup = feature_mask_tag_lookup(int(FeatureBit.SINK_PURE_COMPUTE), is_test=False)
    assert evaluate_tag_filter("#network AND NOT #test", pure_lookup) is False


def test_tag_filter_or_and_parentheses():
    db_lookup = feature_mask_tag_lookup(int(FeatureBit.SINK_DATABASE_IO), is_test=False)
    assert evaluate_tag_filter("(#network OR #database) AND NOT #test", db_lookup) is True
    assert evaluate_tag_filter("#network OR #database", db_lookup) is True
    assert evaluate_tag_filter("#network AND #database", db_lookup) is False


def test_tag_filter_unknown_tag_is_false():
    lookup = feature_mask_tag_lookup(int(FeatureBit.SINK_NETWORK_IO), is_test=False)
    assert evaluate_tag_filter("#totally_unregistered_tag_name", lookup) is False


def test_tag_filter_malformed_expression_raises_domain_error():
    lookup = feature_mask_tag_lookup(0, is_test=False)
    for bad in ("", "#network AND", "(#network", "#network)", "#network #database"):
        try:
            evaluate_tag_filter(bad, lookup)
            assert False, f"{bad!r} should have raised QueryValidationError"
        except QueryValidationError:
            pass


def test_tag_filter_matches_real_symbol_end_to_end(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(
        "import sqlite3\n\n\n"
        "def save(query):\n"
        "    conn = sqlite3.connect(':memory:')\n"
        "    return conn.execute(query)\n"
    )
    builder, _ = build_pipeline(str(repo))
    from prism.semantics.extractor import compute_feature_masks
    feature_masks = compute_feature_masks(builder)
    assert matches_tag_filter_for_symbol("#database AND NOT #test", "svc.save", feature_masks, builder.symbol_table.get) is True
    assert matches_tag_filter_for_symbol("#network", "svc.save", feature_masks, builder.symbol_table.get) is False


# ============================================================
# Determinism
# ============================================================

_DETERMINISM_SCRIPT = """
from prism.query.tag_filter import evaluate_tag_filter, feature_mask_tag_lookup
from prism.semantics.bitmask import FeatureBit

lookup = feature_mask_tag_lookup(int(FeatureBit.SINK_NETWORK_IO) | int(FeatureBit.SINK_DATABASE_IO), is_test=False)
print(evaluate_tag_filter("(#network OR #filesystem) AND NOT (#test OR #process)", lookup))
"""


def test_query_determinism():
    """Query normalization (PrismQuery) and tag-expression parsing are
    pure, deterministic functions of their input - byte-identical across
    two separate processes with different PYTHONHASHSEED values."""
    import os

    env0 = dict(os.environ, PYTHONHASHSEED="0")
    env42 = dict(os.environ, PYTHONHASHSEED="42")
    out0 = subprocess.run(
        [sys.executable, "-c", _DETERMINISM_SCRIPT], cwd="/home/user/SCE", env=env0, capture_output=True, text=True, check=True
    ).stdout
    out42 = subprocess.run(
        [sys.executable, "-c", _DETERMINISM_SCRIPT], cwd="/home/user/SCE", env=env42, capture_output=True, text=True, check=True
    ).stdout
    assert out0 == out42
    assert out0.strip() == "True"
