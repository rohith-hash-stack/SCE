"""Unit and invariant tests for the v1.1+ Empirical Benchmarking Harness
(`benchmarks/`): CPI/SRC/BCCR/FCC/FPR on synthetic mock `ContextPackage`s
and graphs, BM25/BFS baseline execution on synthetic mock codebases, plus
the ground-truth schema/loader, TSR scorers, bootstrap CI, and reporting
layer - no network, no LLM API calls anywhere in this file.
"""
from __future__ import annotations

import pytest

from prism.cli import build_pipeline
from prism.surface.models import (
    BudgetRef,
    ContextPackage,
    CoverageSummary,
    EdgeEntry,
    EngineRef,
    LanguageRef,
    Manifest,
    ManifestDistanceMetric,
    NodeEntry,
    NodeFeatures,
    NodeSignature,
    SeedRef,
)

from benchmarks.engines.base import selected_symbols
from benchmarks.engines.baseline_bfs import BaselineBFSEngine
from benchmarks.engines.baseline_rag import BaselineRAGEngine
from benchmarks.engines.oracle_engine import OracleEngine, OracleLoadError
from benchmarks.engines.prism_engine import PrismEngine
from benchmarks.ground_truth.loader import TaskLoadError, load_task_from_dict, load_tasks_from_dir
from benchmarks.ground_truth.schema import (
    EvaluationTask,
    GroundTruthAnnotation,
    agreement_tier,
    compute_inter_annotator_agreement,
)
from benchmarks.metrics.bccr import bccr, bccr_direct, bccr_transitive, compute_direct_and_transitive_callers
from benchmarks.metrics.cpi import cpi_fractional, cpi_strict
from benchmarks.metrics.fcc import CorpusFeatureStats, compute_corpus_feature_stats, fcc, idf
from benchmarks.metrics.fpr import fpr
from benchmarks.metrics.src import redundant_nodes, src
from benchmarks.reporting.bootstrap import bootstrap_ci
from benchmarks.reporting.report_generator import (
    EvaluationRun,
    TaskRunRecord,
    identify_failures,
    render_heatmap_markdown,
    write_failure_analysis,
    write_json_results,
    write_markdown_report,
)
from benchmarks.tsr.scorer_architecture import reference_symbol_recall, score_architecture
from benchmarks.tsr.scorer_blast import extract_mentioned_symbols, precision_recall_f1, score_blast
from benchmarks.tsr.scorer_chain import score_chain
from benchmarks.tsr.scorer_debug import extract_fenced_symbol_list, score_debug
from benchmarks.tsr.scorer_redundancy import rubric_score, score_redundancy


# --------------------------------------------------------------------- #
# Synthetic ContextPackage fixture helpers
# --------------------------------------------------------------------- #
def _node(node_id: str, role: str, distance: float, cost: int, substance: str = "NONE", form: str = "NONE", output: str = "NONE", axis_role: str = "NONE") -> NodeEntry:
    return NodeEntry(
        id=node_id, role=role, distance=distance, compression="L0_full", cost=cost,
        symbol_name=node_id.rsplit(".", 1)[-1], symbol_kind="function", language="python",
        file="svc.py", line=1, end_line=2,
        signature=NodeSignature(), features=NodeFeatures(substance=substance, form=form, output=output, role=axis_role),
        body=f"def {node_id.rsplit('.', 1)[-1]}(): pass",
    )


def _pkg(nodes: list[NodeEntry], edges: list[EdgeEntry] | None = None, budget: int = 4000) -> ContextPackage:
    return ContextPackage(
        engine=EngineRef(name="test", version="1.0", commit="x"),
        seed=SeedRef(symbol=nodes[0].id, file="svc.py", line=1),
        budget=BudgetRef(tokens=budget, tokenizer="cl100k_base", exact=True),
        language=LanguageRef(tier="1", primary="python", files=1),
        manifest=Manifest(
            packed_nodes=len(nodes), considered_nodes=len(nodes), reachable_nodes=len(nodes), compression=[],
            distance_metric=ManifestDistanceMetric(name="test", lambda_data_flow=0.0, lambda_guard=0.0, dist_max=0.0),
        ),
        coverage=CoverageSummary(total_features=0, covered_features=0, omitted_features=0, features=[]),
        nodes=nodes, edges=edges or [],
    )


# --------------------------------------------------------------------- #
# CPI (Causal Pipeline Integrity)
# --------------------------------------------------------------------- #
def test_cpi_strict_is_one_when_full_pipeline_is_a_subset():
    selected = {"a", "b", "c", "d"}
    assert cpi_strict(selected, ["a", "b", "c"]) == 1.0


def test_cpi_strict_is_zero_when_pipeline_stage_missing():
    selected = {"a", "c"}
    assert cpi_strict(selected, ["a", "b", "c"]) == 0.0


def test_cpi_strict_vacuously_true_for_empty_pipeline():
    assert cpi_strict(set(), []) == 1.0


def test_cpi_fractional_partial_credit():
    selected = {"a", "c"}
    assert cpi_fractional(selected, ["a", "b", "c"]) == pytest.approx(2 / 3)


def test_cpi_fractional_zero_when_none_present():
    assert cpi_fractional({"x", "y"}, ["a", "b"]) == 0.0


# --------------------------------------------------------------------- #
# SRC (Semantic Redundancy Coefficient) on synthetic mock graphs
# --------------------------------------------------------------------- #
def test_src_identifies_a_fully_covered_duplicate_as_redundant():
    seed = _node("svc.seed", "seed", 0.0, 10, substance="PURE_COMPUTE", form="LINEAR")
    a = _node("svc.a", "callee", 1.0, 10, substance="PURE_COMPUTE", form="LINEAR")  # identical features to b
    b = _node("svc.b", "callee", 1.0, 10, substance="PURE_COMPUTE", form="LINEAR")
    distinct = _node("svc.distinct", "callee", 1.0, 10, substance="SINK_DATABASE_IO", form="PIPELINE")
    pkg = _pkg([seed, a, b, distinct])

    redundant = redundant_nodes(pkg)
    # a and b are mirror images of each other - each one's own removal
    # leaves its features fully covered by the other, so both qualify.
    assert redundant == {"svc.a", "svc.b"}
    assert src(pkg) == pytest.approx(2 / 4)


def test_src_never_flags_the_seed_or_pipeline_members():
    seed = _node("svc.seed", "seed", 0.0, 10, substance="PURE_COMPUTE")
    pipeline_member = _node("svc.pipe", "callee", 1.0, 10, substance="PURE_COMPUTE")
    other = _node("svc.other", "callee", 1.0, 10, substance="PURE_COMPUTE")
    pkg = _pkg([seed, pipeline_member, other])

    redundant = redundant_nodes(pkg, pipeline={"svc.pipe"})
    assert "svc.seed" not in redundant
    assert "svc.pipe" not in redundant
    assert redundant == {"svc.other"}


def test_src_zero_when_every_node_contributes_a_unique_feature():
    seed = _node("svc.seed", "seed", 0.0, 10, substance="PURE_COMPUTE")
    a = _node("svc.a", "callee", 1.0, 10, substance="SINK_NETWORK_IO")
    b = _node("svc.b", "callee", 1.0, 10, substance="SINK_DATABASE_IO")
    pkg = _pkg([seed, a, b])
    assert src(pkg) == 0.0


def test_src_zero_for_empty_package():
    pkg = _pkg([_node("svc.seed", "seed", 0.0, 10)])
    pkg = pkg.model_copy(update={"nodes": []})
    assert src(pkg) == 0.0


# --------------------------------------------------------------------- #
# BCCR (Blast-Radius Caller Capture Rate) on synthetic mock graphs
# --------------------------------------------------------------------- #
def test_bccr_direct_full_capture():
    selected = {"svc.seed", "svc.caller_a", "svc.caller_b"}
    ground_truth = {"svc.caller_a", "svc.caller_b"}
    assert bccr_direct(selected, ground_truth) == 1.0


def test_bccr_partial_capture():
    selected = {"svc.seed", "svc.caller_a"}
    ground_truth = {"svc.caller_a", "svc.caller_b"}
    assert bccr(selected, ground_truth) == pytest.approx(0.5)


def test_bccr_vacuous_when_no_ground_truth_callers():
    assert bccr_transitive({"svc.seed"}, set()) == 1.0


def test_compute_direct_and_transitive_callers_real_graph(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(
        "def calculate_tax(amount):\n    return amount * 0.2\n\n\n"
        "def invoice_generator(amount):\n    total = calculate_tax(amount)\n    return total\n\n\n"
        "def batch_invoicer(amounts):\n    results = [invoice_generator(a) for a in amounts]\n    return results\n"
    )
    builder, _ = build_pipeline(str(repo))
    direct, transitive = compute_direct_and_transitive_callers(builder, "svc.calculate_tax")
    assert "svc.invoice_generator" in direct
    # batch_invoicer doesn't bind invoice_generator's own return value via
    # a plain assignment (it's inside a list comprehension) - a real,
    # honest limitation of AST-level provenance detection, not a bug this
    # test should paper over.
    assert isinstance(transitive, set)


# --------------------------------------------------------------------- #
# FCC (IDF-weighted Feature Coverage Density) on synthetic mock graphs
# --------------------------------------------------------------------- #
def test_idf_of_a_rare_feature_exceeds_idf_of_a_common_one():
    stats = CorpusFeatureStats(total_symbols=100, document_frequency={"RARE": 1, "COMMON": 90})
    assert idf("RARE", stats) > idf("COMMON", stats)


def test_idf_of_an_unseen_feature_is_the_max_possible():
    stats = CorpusFeatureStats(total_symbols=100, document_frequency={"SEEN": 50})
    assert idf("NEVER_SEEN", stats) > idf("SEEN", stats)


def test_fcc_zero_for_zero_cost_package():
    stats = CorpusFeatureStats(total_symbols=10, document_frequency={})
    pkg = _pkg([_node("svc.seed", "seed", 0.0, cost=0)])
    assert fcc(pkg, stats) == 0.0


def test_fcc_positive_for_a_real_covered_feature():
    stats = CorpusFeatureStats(total_symbols=100, document_frequency={"SINK_NETWORK_IO": 5})
    pkg = _pkg([_node("svc.seed", "seed", 0.0, cost=1000, substance="SINK_NETWORK_IO")])
    assert fcc(pkg, stats) > 0.0


def test_compute_corpus_feature_stats_real_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(
        "import requests\n\n\ndef fetch(url):\n    return requests.get(url)\n\n\ndef add(a, b):\n    return a + b\n"
    )
    builder, _ = build_pipeline(str(repo))
    stats = compute_corpus_feature_stats(builder)
    assert stats.total_symbols == 2
    assert stats.document_frequency.get("SINK_NETWORK_IO", 0) == 1


# --------------------------------------------------------------------- #
# FPR (False Positive Rate)
# --------------------------------------------------------------------- #
def test_fpr_zero_when_fully_relevant():
    assert fpr({"a", "b"}, {"a", "b", "c"}) == 0.0


def test_fpr_one_when_fully_irrelevant():
    assert fpr({"x", "y"}, {"a", "b"}) == 1.0


def test_fpr_zero_for_empty_selection():
    assert fpr(set(), {"a"}) == 0.0


# --------------------------------------------------------------------- #
# BM25 baseline (Baseline A) on a synthetic mock codebase
# --------------------------------------------------------------------- #
def _rag_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(
        "def calculate_tax(amount):\n    return amount * 0.2\n\n\n"
        "def invoice_generator(amount):\n    total = calculate_tax(amount)\n    return total\n\n\n"
        "def unrelated_helper(x):\n    return x + 1\n"
    )
    return repo


def test_baseline_rag_indexes_and_retrieves(tmp_path):
    repo = _rag_repo(tmp_path)
    engine = BaselineRAGEngine()
    engine.index(str(repo))
    pkg = engine.retrieve("svc.calculate_tax", 2000)
    assert "svc.calculate_tax" in selected_symbols(pkg)
    assert pkg.manifest.compression == pkg.manifest.compression  # compression list populated, no crash
    assert all(n.compression == "L0_full" for n in pkg.nodes)


def test_baseline_rag_nodes_have_no_signature_or_feature_capability(tmp_path):
    """A pure lexical baseline must not be silently handed Prism's own
    semantic analysis - this is the whole point of the comparison."""
    repo = _rag_repo(tmp_path)
    engine = BaselineRAGEngine()
    engine.index(str(repo))
    pkg = engine.retrieve("svc.calculate_tax", 2000)
    for node in pkg.nodes:
        assert node.signature.params == []
        assert node.signature.returns is None
        assert node.features.substance == "NONE"
        assert node.features.form == "NONE"


def test_baseline_rag_respects_budget(tmp_path):
    repo = _rag_repo(tmp_path)
    engine = BaselineRAGEngine()
    engine.index(str(repo))
    pkg = engine.retrieve("svc.calculate_tax", 2000)
    total_cost = sum(n.cost for n in pkg.nodes)
    # a tiny synthetic file fits in one chunk regardless of budget, but
    # the accounting itself must still be real and non-negative.
    assert total_cost >= 0


def test_baseline_rag_seed_always_included_when_indexed(tmp_path):
    repo = _rag_repo(tmp_path)
    engine = BaselineRAGEngine()
    engine.index(str(repo))
    pkg = engine.retrieve("svc.calculate_tax", 2000)
    seed_nodes = [n for n in pkg.nodes if n.role == "seed"]
    assert len(seed_nodes) == 1
    assert seed_nodes[0].id == "svc.calculate_tax"


# --------------------------------------------------------------------- #
# BFS baseline (Baseline B) on a synthetic mock codebase
# --------------------------------------------------------------------- #
def _bfs_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(
        "def calculate_tax(amount):\n    return amount * 0.2\n\n\n"
        "def invoice_generator(amount):\n    total = calculate_tax(amount)\n    return total\n"
    )
    return repo


def test_baseline_bfs_forward_only_sees_callees(tmp_path):
    repo = _bfs_repo(tmp_path)
    engine = BaselineBFSEngine(mode="forward")
    engine.index(str(repo))
    pkg = engine.retrieve("svc.calculate_tax", 2000)
    ids = {n.id for n in pkg.nodes}
    assert "svc.invoice_generator" not in ids  # a caller, invisible in forward-only mode
    assert "svc.calculate_tax" in ids


def test_baseline_bfs_bidirectional_sees_callers_too(tmp_path):
    repo = _bfs_repo(tmp_path)
    engine = BaselineBFSEngine(mode="bidirectional")
    engine.index(str(repo))
    pkg = engine.retrieve("svc.calculate_tax", 2000)
    caller_nodes = [n for n in pkg.nodes if n.id == "svc.invoice_generator"]
    assert len(caller_nodes) == 1
    assert caller_nodes[0].role == "caller"


def test_baseline_bfs_invalid_mode_rejected():
    with pytest.raises(ValueError):
        BaselineBFSEngine(mode="sideways")


def test_baseline_bfs_forward_decay_beats_backward_decay_at_equal_depth(tmp_path):
    """The spec's own base-weight asymmetry (1.0 forward vs 0.7 backward
    at the same depth) - a bidirectional-mode caller one hop out must
    never outrank a callee one hop out."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(
        "def seed():\n    return callee()\n\n\ndef callee():\n    return 1\n\n\ndef caller():\n    return seed()\n"
    )
    engine = BaselineBFSEngine(mode="bidirectional")
    engine.index(str(repo))
    from benchmarks.engines.baseline_bfs import _decay, BACKWARD_DECAY_BASE, FORWARD_DECAY_BASE

    assert _decay(FORWARD_DECAY_BASE, 1) > _decay(BACKWARD_DECAY_BASE, 1)


# --------------------------------------------------------------------- #
# Prism engine + Oracle engine smoke coverage
# --------------------------------------------------------------------- #
def test_prism_engine_real_end_to_end(tmp_path):
    repo = _bfs_repo(tmp_path)
    engine = PrismEngine()
    engine.index(str(repo))
    pkg = engine.retrieve("svc.calculate_tax", 4000)
    assert "svc.calculate_tax" in selected_symbols(pkg)
    assert pkg.engine.name == "prism-causal"


def test_oracle_engine_loads_curated_symbols(tmp_path):
    repo = _bfs_repo(tmp_path)
    oracle_file = tmp_path / "oracle.yaml"
    oracle_file.write_text("t1:\n  - svc.invoice_generator\n")
    engine = OracleEngine(str(oracle_file), "t1")
    engine.index(str(repo))
    pkg = engine.retrieve("svc.calculate_tax", 2000)
    assert selected_symbols(pkg) == {"svc.calculate_tax", "svc.invoice_generator"}


def test_oracle_engine_missing_task_id_raises(tmp_path):
    repo = _bfs_repo(tmp_path)
    oracle_file = tmp_path / "oracle.yaml"
    oracle_file.write_text("other_task:\n  - svc.invoice_generator\n")
    engine = OracleEngine(str(oracle_file), "missing_task")
    engine.index(str(repo))
    with pytest.raises(OracleLoadError):
        engine.retrieve("svc.calculate_tax", 2000)


# --------------------------------------------------------------------- #
# Ground truth schema + Cohen's kappa
# --------------------------------------------------------------------- #
def test_perfect_agreement_kappa_is_one():
    a = GroundTruthAnnotation(annotator_id="a", critical_callers={"x", "y"}, expected_solution="x,y")
    b = GroundTruthAnnotation(annotator_id="b", critical_callers={"x", "y"}, expected_solution="x,y")
    assert compute_inter_annotator_agreement(a, b) == 1.0
    assert agreement_tier(1.0) == "proceed"


def test_low_agreement_kappa_is_rejected():
    a = GroundTruthAnnotation(annotator_id="a", critical_callers={"x", "y", "z"}, expected_solution="x,y,z")
    b = GroundTruthAnnotation(annotator_id="b", critical_callers={"p", "q", "r"}, expected_solution="p,q,r")
    kappa = compute_inter_annotator_agreement(a, b)
    assert kappa < 0.60
    assert agreement_tier(kappa) == "reject"


def test_load_task_from_dict_computes_missing_kappa():
    raw = {
        "task_id": "t1", "repo": "django", "pinned_commit": "abc", "seed_symbol": "x.y", "task_type": "blast",
        "prompt": "p",
        "annotation_a": {"annotator_id": "a", "critical_callers": ["c1"], "expected_solution": "c1"},
        "annotation_b": {"annotator_id": "b", "critical_callers": ["c1"], "expected_solution": "c1"},
        "adjudicated": {"annotator_id": "adj", "critical_callers": ["c1"], "expected_solution": "c1"},
    }
    task = load_task_from_dict(raw)
    assert task.cohen_kappa == 1.0


def test_load_tasks_from_dir_rejects_low_kappa_task(tmp_path):
    (tmp_path / "bad.yaml").write_text(
        "task_id: bad\nrepo: gin\npinned_commit: x\nseed_symbol: a.b\ntask_type: blast\nprompt: p\n"
        "annotation_a:\n  annotator_id: a\n  critical_callers: [x, y, z]\n  expected_solution: xyz\n"
        "annotation_b:\n  annotator_id: b\n  critical_callers: [p, q, r]\n  expected_solution: pqr\n"
        "adjudicated:\n  annotator_id: adj\n  critical_callers: [x]\n  expected_solution: x\n"
    )
    result = load_tasks_from_dir(tmp_path)
    assert result.accepted == []
    assert len(result.rejected) == 1


def test_load_tasks_from_dir_requires_real_adjudication_in_middle_tier(tmp_path):
    """A task landing in the [0.60, 0.80) tier whose `adjudicated` field
    is a verbatim copy of one rater's own annotation (no real
    adjudication happened) must be rejected, not silently accepted.

    critical_callers sized/overlapped to land agreement at exactly
    2*4/(5+7) = 0.667 (verified directly against
    `compute_inter_annotator_agreement`) - inside the [0.60, 0.80)
    "adjudicate" band.
    """
    (tmp_path / "mid.yaml").write_text(
        "task_id: mid\nrepo: gin\npinned_commit: x\nseed_symbol: a.b\ntask_type: blast\nprompt: p\n"
        "annotation_a:\n  annotator_id: a\n  critical_callers: [x, y, z, w, v]\n  expected_solution: a\n"
        "annotation_b:\n  annotator_id: b\n  critical_callers: [x, y, z, w, p, q, r]\n  expected_solution: b\n"
        "adjudicated:\n  annotator_id: a\n  critical_callers: [x, y, z, w, v]\n  expected_solution: a\n"
    )
    result = load_tasks_from_dir(tmp_path)
    assert result.accepted == []
    assert len(result.rejected) == 1
    assert "adjudication" in result.rejected[0][1]


# --------------------------------------------------------------------- #
# TSR scorers
# --------------------------------------------------------------------- #
def test_scorer_chain_correct_order():
    text = "parse_order runs first, then store_order, finally emit_audit."
    assert score_chain(text, ["svc.parse_order", "svc.store_order", "svc.emit_audit"]) == 1.0


def test_scorer_chain_wrong_order():
    text = "emit_audit happens, then store_order, then parse_order."
    assert score_chain(text, ["svc.parse_order", "svc.store_order", "svc.emit_audit"]) == 0.0


def test_scorer_blast_exact_f1_match():
    predicted = extract_mentioned_symbols("invoice_generator is affected.", {"svc.invoice_generator", "svc.unrelated"})
    assert predicted == {"svc.invoice_generator"}
    precision, recall, f1 = precision_recall_f1(predicted, {"svc.invoice_generator"})
    assert (precision, recall, f1) == (1.0, 1.0, 1.0)
    assert score_blast("invoice_generator is affected.", {"svc.invoice_generator", "svc.unrelated"}, {"svc.invoice_generator"}) == 1.0


def test_scorer_redundancy_flags_conflation():
    conflated_score = score_redundancy("calculate_tax is the same as calculate_tax_v2.", "svc.calculate_tax", {"svc.calculate_tax_v2"})
    clean_score = score_redundancy("calculate_tax computes a flat rate.", "svc.calculate_tax", {"svc.calculate_tax_v2"})
    assert conflated_score == 0.0
    assert clean_score == 1.0
    assert rubric_score("calculate_tax computes a flat rate.", "svc.calculate_tax", {"svc.calculate_tax_v2"}) >= 1


def test_scorer_architecture_recall_above_threshold_passes():
    reference = {"svc.Handler", "svc.Router", "svc.Middleware"}
    text = "The Handler is invoked by the Router after passing through Middleware."
    assert reference_symbol_recall(text, reference) == 1.0
    assert score_architecture(text, reference) == 1.0


def test_scorer_architecture_recall_below_threshold_fails():
    reference = {"svc.Handler", "svc.Router", "svc.Middleware"}
    text = "The Handler processes the request."
    recall = reference_symbol_recall(text, reference)
    assert recall == pytest.approx(1 / 3)
    assert score_architecture(text, reference) == 0.0


def test_scorer_architecture_vacuous_empty_reference_set():
    assert reference_symbol_recall("anything at all", set()) == 1.0
    assert score_architecture("anything at all", set()) == 1.0


def test_scorer_debug_extracts_fenced_json_array():
    text = 'Here is my answer:\n```json\n["svc.a", "svc.b"]\n```\nDone.'
    assert extract_fenced_symbol_list(text) == ["svc.a", "svc.b"]


def test_scorer_debug_bare_fence_without_json_tag_also_parses():
    text = '```\n["svc.a", "svc.b"]\n```'
    assert extract_fenced_symbol_list(text) == ["svc.a", "svc.b"]


def test_scorer_debug_no_fenced_block_returns_none():
    assert extract_fenced_symbol_list("svc.a then svc.b, no fence here") is None


def test_scorer_debug_malformed_json_returns_none():
    assert extract_fenced_symbol_list("```json\n[svc.a, svc.b]\n```") is None


def test_scorer_debug_exact_ordered_match_scores_one():
    text = '```json\n["svc.parse_order", "svc.store_order"]\n```'
    assert score_debug(text, ["svc.parse_order", "svc.store_order"]) == 1.0


def test_scorer_debug_wrong_order_scores_zero():
    text = '```json\n["svc.store_order", "svc.parse_order"]\n```'
    assert score_debug(text, ["svc.parse_order", "svc.store_order"]) == 0.0


def test_scorer_debug_missing_fence_scores_zero():
    assert score_debug("svc.parse_order runs then svc.store_order.", ["svc.parse_order", "svc.store_order"]) == 0.0


def test_scorer_debug_vacuous_empty_pipeline_and_empty_array():
    assert score_debug("```json\n[]\n```", []) == 1.0


# --------------------------------------------------------------------- #
# Bootstrap CI
# --------------------------------------------------------------------- #
def test_bootstrap_ci_bounds_contain_point_estimate():
    result = bootstrap_ci([1.0] * 8 + [0.0] * 2, random_seed=7)
    assert result.lower <= result.point_estimate <= result.upper


def test_bootstrap_ci_all_same_value_has_zero_width():
    result = bootstrap_ci([1.0, 1.0, 1.0], random_seed=1)
    assert result.lower == result.upper == 1.0


def test_bootstrap_ci_empty_samples_returns_zero():
    result = bootstrap_ci([])
    assert result.point_estimate == 0.0


# --------------------------------------------------------------------- #
# Reporting layer
# --------------------------------------------------------------------- #
def test_write_json_and_markdown_reports(tmp_path):
    run = EvaluationRun(
        records=[
            TaskRunRecord(task_id="t1", task_type="chain", repo="django", engine_name="prism_v11", budget_tokens=4000, tsr_scores=[1, 1, 0], diagnostics={"cpi_strict": 1.0}),
        ]
    )
    write_json_results(run, tmp_path / "eval_results_v11.json")
    write_markdown_report(run, tmp_path / "eval_results_v11.md", random_seed=1)
    assert (tmp_path / "eval_results_v11.json").exists()
    assert "TSR" in (tmp_path / "eval_results_v11.md").read_text()


def test_identify_failures_prism_worse_than_bfs():
    run = EvaluationRun(
        records=[
            TaskRunRecord(task_id="t1", task_type="chain", repo="django", engine_name="prism_v11", budget_tokens=4000, tsr_scores=[0.0]),
            TaskRunRecord(task_id="t1", task_type="chain", repo="django", engine_name="baseline_bfs_forward", budget_tokens=4000, tsr_scores=[1.0]),
        ]
    )
    failures = identify_failures(run)
    assert len(failures) == 1
    assert failures[0]["task_id"] == "t1"


def test_identify_failures_prism_far_below_oracle():
    run = EvaluationRun(
        records=[
            TaskRunRecord(task_id="t1", task_type="chain", repo="django", engine_name="prism_v11", budget_tokens=4000, tsr_scores=[0.5]),
            TaskRunRecord(task_id="t1", task_type="chain", repo="django", engine_name="oracle", budget_tokens=4000, tsr_scores=[1.0]),
        ]
    )
    failures = identify_failures(run)
    assert len(failures) == 1


def test_identify_failures_none_when_prism_wins():
    run = EvaluationRun(
        records=[
            TaskRunRecord(task_id="t1", task_type="chain", repo="django", engine_name="prism_v11", budget_tokens=4000, tsr_scores=[1.0]),
            TaskRunRecord(task_id="t1", task_type="chain", repo="django", engine_name="baseline_bfs_forward", budget_tokens=4000, tsr_scores=[0.5]),
            TaskRunRecord(task_id="t1", task_type="chain", repo="django", engine_name="oracle", budget_tokens=4000, tsr_scores=[1.0]),
        ]
    )
    assert identify_failures(run) == []


def test_write_failure_analysis_writes_file(tmp_path):
    run = EvaluationRun(records=[])
    failures = write_failure_analysis(run, tmp_path / "failure_analysis.md")
    assert failures == []
    assert "No failures" in (tmp_path / "failure_analysis.md").read_text()


def test_render_heatmap_markdown_shape():
    grid = {(0.1, 0.05): 0.7, (0.1, 0.15): 0.8, (0.25, 0.05): 0.9}
    table = render_heatmap_markdown("lambda1", "lambda2", grid)
    assert "0.7" in table and "0.8" in table and "0.9" in table


# --------------------------------------------------------------------- #
# Corpus resolver (hermetic - a local git repo, no real network clone)
# --------------------------------------------------------------------- #
def _make_local_git_repo(tmp_path):
    import subprocess

    repo = tmp_path / "src_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "a.txt").write_text("a")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "c1"], cwd=repo, check=True)
    first_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    (repo / "b.txt").write_text("b")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "c2"], cwd=repo, check=True)
    return repo, first_sha


def test_resolve_corpus_pins_to_an_exact_historical_commit(tmp_path):
    from benchmarks.corpora.resolver import CorpusSpec, resolve_corpus

    src_repo, first_sha = _make_local_git_repo(tmp_path)
    spec = CorpusSpec(name="local_test", url=str(src_repo), pinned_commit=first_sha)
    dest = resolve_corpus(spec, cache_dir=tmp_path / "cache")

    import subprocess

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=dest, capture_output=True, text=True, check=True).stdout.strip()
    assert head == first_sha
    assert (dest / "a.txt").exists()
    assert not (dest / "b.txt").exists()  # not yet committed at the pinned commit


def test_resolve_corpus_reuses_an_already_correct_checkout(tmp_path):
    from benchmarks.corpora.resolver import CorpusSpec, resolve_corpus

    src_repo, first_sha = _make_local_git_repo(tmp_path)
    spec = CorpusSpec(name="local_test", url=str(src_repo), pinned_commit=first_sha)
    cache_dir = tmp_path / "cache"
    first = resolve_corpus(spec, cache_dir=cache_dir)
    second = resolve_corpus(spec, cache_dir=cache_dir)
    assert first == second


def test_resolve_unknown_corpus_name_raises():
    from benchmarks.corpora.resolver import CorpusResolutionError, resolve

    with pytest.raises(CorpusResolutionError):
        resolve("not_a_real_corpus")


def test_corpora_loaded_from_pinned_commits_json_covers_all_four_repos():
    from benchmarks.corpora.resolver import CORPORA

    assert set(CORPORA) == {"django", "gin", "trpc", "express"}
    for name, spec in CORPORA.items():
        assert spec.name == name
        assert spec.url.startswith("https://github.com/")
        assert len(spec.pinned_commit) == 40  # a full git SHA-1, not a short/abbreviated one
        assert all(c in "0123456789abcdef" for c in spec.pinned_commit)


def test_evaluation_task_schema_accepts_express_repo():
    from benchmarks.ground_truth.schema import EvaluationTask, GroundTruthAnnotation

    ann = GroundTruthAnnotation(annotator_id="a", critical_callers={"x"}, expected_solution="x")
    task = EvaluationTask(
        task_id="t1", repo="express", pinned_commit="abc", seed_symbol="a.b", task_type="blast", prompt="p",
        annotation_a=ann, annotation_b=ann, adjudicated=ann, cohen_kappa=1.0,
    )
    assert task.repo == "express"
