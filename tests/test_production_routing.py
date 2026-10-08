"""Production-routing experiment (harness/experiments/production_routing):
the query-intent classifier, pool shaping, the routed and rule-selector Arm 5
variants, the config wiring, and the B0M4 baseline read from stored cells."""
import glob
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from harness import config as C
from harness.arms import build_arm
from harness.arms.arm5_prism import Arm5Prism
from harness.experiments.production_routing.arms import ProductionRoutingArm5, RuleSelectorArm5
from harness.experiments.production_routing.intent import classify
from harness.experiments.production_routing.routing import route_manifest
from harness.llm import Completion

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "harness" / "experiments" / "production_routing"
WANT = {"T5_blast_radius": "blast_radius", "T2_localization": "localization"}


class Words:
    name = "words"

    def count(self, text):
        return len(text.split())


class PickLLM:
    """Turn 1 requests the given names; records every call's purpose."""

    def __init__(self, names=()):
        self.names, self.calls = list(names), []

    def __call__(self, system, user, max_tokens=0, seed=None, purpose=""):
        self.calls.append(purpose)
        return Completion(json.dumps({"requested_symbols": self.names}), 1, 1, 0.0, purpose=purpose)


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    from prism.engine import PrismEngine
    root = tmp_path_factory.mktemp("repo")
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "m.py").write_text(textwrap.dedent("""
        def seed(x):
            return helper(x)
        def helper(x):
            return deep(x)
        def deep(x):
            return x
        def c1():
            value = seed(1)
            return value
        def c2():
            return c1()
    """))
    return PrismEngine.from_repo(str(root))


def test_classifier_on_every_m4_query():
    queries = {}
    for f in glob.glob(str(REPO / "reports" / "harness_m4" / "*" / "bundles" / "arm5_*_s42.json")):
        meta = json.load(open(f))["bundle"]["build_meta"]
        queries[f] = (meta["task_type"], meta["query"])
    assert len(queries) == 121
    wrong = [(tt, q[:60]) for tt, q in queries.values() if classify(q)[0] != WANT[tt]]
    assert wrong == []


def test_classifier_on_the_held_out_queries():
    data = json.loads((PKG / "heldout_queries.json").read_text())["queries"]
    results = [(classify(q)[0], exp) for q, exp in data]
    correct = sum(got == exp or (exp == "ambiguous" and got in ("mixed", "unknown")) for got, exp in results)
    opposite = [(g, e) for g, e in results if {g, e} == {"blast_radius", "localization"}]
    assert correct >= 33 and opposite == []


def test_pool_shaping_blast_keeps_callers_and_hop1_downstream_only(engine):
    routed = route_manifest(engine, "pkg.m.seed", "What breaks if `pkg.m.seed` changes?", 13000)
    assert routed.intent == "blast_radius" and routed.policy == "upstream+hop1"
    assert routed.universe == {"pkg.m.seed", "pkg.m.c1", "pkg.m.c2", "pkg.m.helper"}     # no hop-2 pkg.m.deep
    assert dict(routed.rows())["pkg.m.c2"] == "caller"


def test_pool_shaping_localization_is_the_default_manifest(engine):
    routed = route_manifest(engine, "pkg.m.seed", "How does `pkg.m.seed` compute its result? Trace it.", 13000)
    assert routed.intent == "localization" and routed.policy == "downstream"
    assert (routed.text, routed.universe) == engine.build_candidate_manifest("pkg.m.seed")


def test_routed_arm_reads_the_query_not_the_label_and_logs(engine):
    arm = ProductionRoutingArm5(llm=PickLLM(["pkg.m.c1", "pkg.m.helper"]), tokenizer=Words(), engine=engine)
    # a T2-labelled seed with an impact question is routed upstream, and vice versa
    ctx = arm.retrieve("What breaks if `pkg.m.seed` changes?",
                       {"task_id": "t", "task_type": "T2_localization", "seed_symbol": "pkg.m.seed"})
    meta = ctx.build_meta
    assert meta["routing_intent"] == "blast_radius" and meta["routing_policy"] == "upstream+hop1"
    assert meta["manifest_caller_fraction"] == pytest.approx(2 / 3) and meta["turn1_picks_caller_fraction"] == 0.5
    ctx = arm.retrieve("Trace how `pkg.m.seed` works step by step.",
                       {"task_id": "t", "task_type": "T5_blast_radius", "seed_symbol": "pkg.m.seed"})
    assert ctx.build_meta["routing_intent"] == "localization" and ctx.build_meta["routing_policy"] == "downstream"


def test_rule_selector_requests_every_caller_on_t5_and_leaves_t2_to_the_model(engine):
    llm = PickLLM(["pkg.m.helper"])
    arm = RuleSelectorArm5(llm=llm, tokenizer=Words(), engine=engine)
    ctx = arm.retrieve("What breaks if `pkg.m.seed` changes?",
                       {"task_id": "t5", "task_type": "T5_blast_radius", "seed_symbol": "pkg.m.seed"})
    assert "turn1" not in llm.calls                                       # no model call at Turn 1
    assert set(ctx.build_meta["requested_symbols"]) == {"pkg.m.c1", "pkg.m.c2"}
    assert ctx.build_meta["turn1_selector"] == "rule_callers" and ctx.build_meta["turn1_picks_caller_fraction"] == 1.0
    ctx = arm.retrieve("How does `pkg.m.seed` work?",
                       {"task_id": "t2", "task_type": "T2_localization", "seed_symbol": "pkg.m.seed"})
    assert llm.calls == ["turn1"] and "turn1_selector" not in ctx.build_meta


def test_flags_select_the_arm_class_and_off_is_plain_arm5(monkeypatch):
    for routing, rule, cls in ((False, False, Arm5Prism), (True, False, ProductionRoutingArm5), (False, True, RuleSelectorArm5)):
        monkeypatch.setattr(C, "PRISM_PRODUCTION_ROUTING", routing)
        monkeypatch.setattr(C, "PRISM_T5_RULE_SELECTOR", rule)
        assert type(build_arm("arm5", tokenizer=Words())) is cls
    env = {**os.environ, "HARNESS_PRISM_PRODUCTION_ROUTING": "1", "HARNESS_PRISM_T5_RULE_SELECTOR": "1"}
    proc = subprocess.run([sys.executable, "-c", "import harness.config"], cwd=REPO, env=env, capture_output=True, text=True)
    assert proc.returncode != 0 and "enable at most one" in proc.stderr


def test_rule_selector_is_the_default_and_opt_outs_work():
    probe = ("from harness import config as C; from harness.arms import build_arm; "
             "arm = build_arm('arm5', tokenizer=type('W', (), {'name': 'w', 'count': lambda s, t: len(t.split())})()); "
             "print(C.PRISM_T5_RULE_SELECTOR, C.PRISM_PRODUCTION_ROUTING, type(arm).__name__)")
    base = {k: v for k, v in os.environ.items()
            if k not in ("HARNESS_PRISM_T5_RULE_SELECTOR", "HARNESS_PRISM_PRODUCTION_ROUTING")}
    cases = (({}, "True False RuleSelectorArm5"),                                        # default: R0
             ({"HARNESS_PRISM_T5_RULE_SELECTOR": "0"}, "False False Arm5Prism"),         # R1
             ({"HARNESS_PRISM_PRODUCTION_ROUTING": "1"}, "False True ProductionRoutingArm5"))  # R2
    for extra, want in cases:
        proc = subprocess.run([sys.executable, "-c", probe], cwd=REPO, env={**base, **extra},
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip().splitlines()[-1] == want


def test_b0m4_from_stored_cells_matches_the_published_m4_matrices():
    from harness.experiments.production_routing.b0m4 import check_against_published, summarize
    summary = summarize()
    assert len(summary) == 8 and check_against_published(summary) == []


def test_ablation_report_reproduces_the_published_m4_t5_matrix_for_b0m4():
    from harness.experiments.production_routing.ablation_report import CORPORA, M4, _gold, analyse, load_config
    gold = {c: _gold(c) for c in CORPORA}
    res = analyse(load_config("B0M4", M4, gold), ["B0M4"])
    published = json.loads((M4 / "analysis" / "summary.json").read_text())["t5_matrix"]
    for corpus in CORPORA:
        mine, pub = res["matrix"][corpus]["B0M4"], published[corpus]["arm5"]
        assert (mine["n_tasks"], mine["n_cells"]) == (pub["n_tasks"], pub["n_cells"])
        for k, pk in (("mean_tsr", "mean"), ("ci_low", "ci_low"), ("ci_high", "ci_high")):
            assert mine[k] == pytest.approx(pub[pk], abs=1e-12)
    assert res["anomalies"] == []


def test_cell_metrics_use_resolved_picks_and_report_the_turn1_source():
    from harness.experiments.production_routing.ablation_report import cell_metrics
    bundle = {"items": [{"symbols": ["s"]}, {"symbols": ["a"]}, {"symbols": ["x"]}],
              "build_meta": {"requested_symbols": ["a", "x", "ghost"], "skipped_hallucinated": ["ghost"],
                             "retrieval_turns": [{"model": "rule_callers", "purpose": "turn1"}]}}
    m = cell_metrics(bundle, "s", {"s", "a", "b"})
    assert (m["gold_coverage"], m["selection_precision"], m["selection_recall"]) == (0.5, 0.5, 0.5)
    assert m["turn1_source"] == "rule" and m["n_picks"] == 2
    bundle["build_meta"]["retrieval_turns"] = [{"model": "qwen", "purpose": "turn1"}]
    assert cell_metrics(bundle, "s", {"s", "a", "b"})["turn1_source"] == "llm"
