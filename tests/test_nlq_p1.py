"""P1 deterministic evidence planning (`nlq/planner.py`, `nlq/plan_exec.py`).

Fast tests: the planner on the 24 gold-derived interpretations with an
index stand-in, and the executor end to end on a five-symbol repository
(real `prism_slice` / `prism_blast_radius`, real proof predicates). `slow`
tests: the planned run on the real FastAPI and Django indexes, compared with
the committed proof baseline.
"""
from __future__ import annotations

import copy
import json

import pytest

from harness.experiments.query_to_evidence.nlq import baseline_records as B
from harness.experiments.query_to_evidence.nlq import gold_fixtures as G
from harness.experiments.query_to_evidence.nlq import plan_exec as X
from harness.experiments.query_to_evidence.nlq import planner as P
from harness.experiments.query_to_evidence.nlq import schema as S

IDENTITY = lambda name: [name]                 # every proposed name is indexed exactly as given


@pytest.fixture(scope="module")
def fixtures():
    return G.load()


@pytest.fixture(scope="module")
def plans(fixtures):
    return {fx["gold"]["query_id"]: P.plan(fx["interpretation"], IDENTITY) for fx in fixtures}


def _interp(fixtures, qid):
    return copy.deepcopy(next(fx["interpretation"] for fx in fixtures if fx["gold"]["query_id"] == qid))


# --------------------------------------------------------------------------
# planner rules
# --------------------------------------------------------------------------
def test_plans_are_deterministic_and_valid(fixtures, plans):
    again = {fx["gold"]["query_id"]: P.plan(fx["interpretation"], IDENTITY) for fx in fixtures}
    assert json.dumps(plans, sort_keys=True) == json.dumps(again, sort_keys=True)
    assert all(S.validate_plan(p["plan"]) == [] for p in plans.values())


def test_every_requirement_has_an_explicit_disposition(fixtures, plans):
    total = 0
    for fx in fixtures:
        qid = fx["gold"]["query_id"]
        ids = [r["id"] for r in fx["interpretation"]["requirements"]]
        assert [r["id"] for r in plans[qid]["plan"]["requirements"]] == ids
        assert list(plans[qid]["requirements"]) == ids
        assert all(plans[qid]["requirements"][i]["disposition"] in
                   ("retrieve", "unsupported", "clarify", "abstain") for i in ids)
        total += len(ids)
    assert total == 28


def test_operation_selected_by_requirement_kind(plans):
    ops = {}
    for p in plans.values():
        for r in p["plan"]["requirements"]:
            ops.setdefault(r["kind"], set()).add((r["retrieval"]["op"], r["retrieval"].get("task_type"),
                                                  r["retrieval"].get("unsupported_need")))
    assert ops == {"behavior": {("slice", "debug", None)}, "impact": {("blast_radius", None, None)},
                   "tests": {("blast_radius", None, None)},
                   "concurrency": {("unsupported", None, "state_reads_and_definition")},
                   "absence": {("unsupported", None, "exhaustive_absence")},
                   "ambiguity": {("none", None, None)}, "external": {("none", None, None)}}
    assert all(r["retrieval"].get("budget_tokens") in (None, P.CALL_BUDGET)
               for p in plans.values() for r in p["plan"]["requirements"])


def test_decisions_clarify_ambiguous_and_abstain_external(plans):
    by = {q: p["plan"]["decision"] for q, p in plans.items()}
    assert {q for q, d in by.items() if d == "clarify"} == {"q19", "q20", "q21"}
    assert {q for q, d in by.items() if d == "abstain"} == {"q22", "q23", "q24"}
    assert all(not plans[q]["calls"] for q in ("q19", "q20", "q21", "q22", "q23", "q24"))
    q19 = plans["q19"]["plan"]["requirements"][0]["resolution"]
    assert q19["status"] == "ambiguous" and q19["match_count"] == 6         # the in-repo readings, verified


def test_unsupported_requirements_carry_their_reason(plans):
    for q, rid, need in (("q15", "r2", "state_reads_and_definition"), ("q17", "r2", "exhaustive_absence")):
        r = next(x for x in plans[q]["plan"]["requirements"] if x["id"] == rid)
        assert r["retrieval"]["op"] == "unsupported" and r["retrieval"]["unsupported_need"] == need
        assert r["retrieval"]["reason"] and plans[q]["requirements"][rid]["call"] is None
    assert plans["q15"]["plan"]["decision"] == "retrieve"                    # r1 is still retrieved
    assert [c["serves"] for c in plans["q15"]["calls"]] == [["r1"]]


def test_scope_limits_are_applied_only_where_a_tool_enforces_them(plans):
    q16 = plans["q16"]
    calls = {c["id"]: c for c in q16["calls"]}
    r1, r2 = q16["requirements"]["r1"], q16["requirements"]["r2"]
    assert calls[r1["call"]]["include_tests"] is False and r1["scope"]["enforced"] and not r1["scope"]["not_enforced"]
    assert calls[r2["call"]]["include_tests"] is True and not r2["scope"]["enforced"]
    assert any(n.startswith("role=tests") for n in r2["scope"]["not_enforced"])
    assert any(n.startswith("direct_only") for n in r2["scope"]["not_enforced"])
    # requirements without scope limits record nothing
    assert plans["q09"]["requirements"]["r1"]["scope"] == {"role": "any", "direct_only": False, "enforced": [],
                                                           "not_enforced": []}


def test_a_role_limit_on_slice_is_recorded_as_not_enforced(fixtures):
    it = _interp(fixtures, "q09")
    it["requirements"][0]["scope"] = {"role": "production", "direct_only": False,
                                      "evidence_span": "one FastAPI router"}
    note = P.plan(it, IDENTITY)["requirements"]["r1"]
    assert not note["scope"]["enforced"] and note["scope"]["not_enforced"]


def test_multi_requirement_questions_get_one_call_per_distinct_operation(plans):
    assert [(c["op"], c["serves"]) for c in plans["q18"]["calls"]] == [("slice", ["r1"]), ("blast_radius", ["r2"])]
    assert [(c["include_tests"], c["serves"]) for c in plans["q16"]["calls"]] == [(False, ["r1"]), (True, ["r2"])]


def test_identical_calls_are_shared_not_duplicated(fixtures):
    it = _interp(fixtures, "q18")
    it["requirements"][0]["kind"] = "impact"                  # r1 and r2 now need the same blast_radius call
    out = P.plan(it, IDENTITY)
    assert [(c["op"], c["serves"]) for c in out["calls"]] == [("blast_radius", ["r1", "r2"])]


def test_ambiguous_subject_clarifies_and_retrieves_nothing(fixtures):
    out = P.plan(_interp(fixtures, "q09"), lambda n: ["a.Router.include_router", "b.Router.include_router"])
    res = out["plan"]["requirements"][0]["resolution"]
    assert out["plan"]["decision"] == "clarify" and res["status"] == "ambiguous" and res["match_count"] == 2
    assert out["calls"] == [] and S.validate_plan(out["plan"]) == []


def test_missing_subject_abstains_and_is_never_guessed(fixtures):
    out = P.plan(_interp(fixtures, "q09"), lambda n: [])
    assert out["plan"]["decision"] == "abstain" and out["calls"] == []
    assert out["plan"]["requirements"][0]["resolution"]["status"] == "not_found"
    it = _interp(fixtures, "q10")
    it["requirements"][0]["subject"]["proposed_names"] = []
    assert P.plan(it, IDENTITY)["plan"]["requirements"][0]["resolution"]["status"] == "not_found"


def test_ambiguity_with_fewer_than_two_indexed_readings_is_an_error(fixtures):
    with pytest.raises(ValueError, match="at least two indexed readings"):
        P.plan(_interp(fixtures, "q21"), lambda n: [])


def test_non_oracle_subjects_record_how_the_index_matched_them(fixtures):
    it = _interp(fixtures, "q09")
    it["interpreter"] = {"kind": "agent", "model_id": "m", "prompt_version": "v"}
    it["requirements"][0]["subject"]["proposed_names"] = ["APIRouter.include_router"]
    res = P.plan(it, lambda n: ["fastapi.routing.APIRouter.include_router"])["plan"]["requirements"][0]["resolution"]
    assert (res["subject"], res["provenance"]) == ("fastapi.routing.APIRouter.include_router", "partial_name")
    assert P.plan(_interp(fixtures, "q09"), IDENTITY)["plan"]["requirements"][0]["resolution"]["provenance"] == \
           "gold_counterfactual"


# --------------------------------------------------------------------------
# execution on a real five-symbol repository
# --------------------------------------------------------------------------
_MOD = ("def leaf(x):\n    return x + 1\n\n\ndef seed(x):\n    return leaf(x) * 2\n\n\n"
        "def caller(y):\n    return seed(y) - 3\n\n\ndef top(z):\n    return caller(z)\n")
_TEST = "from mod import seed\n\n\ndef test_seed():\n    assert seed(1) == 4\n"


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    from harness.experiments.query_to_evidence.run import build_engine
    root = tmp_path_factory.mktemp("tiny")
    (root / "mod.py").write_text(_MOD)
    (root / "tests").mkdir()
    (root / "tests" / "test_mod.py").write_text(_TEST)
    return build_engine(str(root))


def _edge(frm, to, line, text):
    return {"id": f"edge:{frm}->{to}", "type": "call_edge", "from": frm, "to": to, "call_site": line,
            "source_text": text}


_MQ = {"query_id": "t1", "corpus": "tiny", "requirements": [
    {"id": "r1", "kind": "impact", "mandatory": True, "subject": "mod.seed",
     "facts": [_edge("mod.caller", "mod.seed", "mod.py:10", "seed(y)")]},
    {"id": "r2", "kind": "tests", "mandatory": True, "subject": "mod.seed",
     "facts": [_edge("tests.test_mod.test_seed", "mod.seed", "tests/test_mod.py:5", "seed(1)")]},
    {"id": "r3", "kind": "behavior", "mandatory": True, "subject": "mod.seed",
     "facts": [_edge("mod.seed", "mod.leaf", "mod.py:6", "leaf(x)")]},
    {"id": "r4", "kind": "concurrency", "mandatory": True, "subject": "mod.seed",
     "facts": [{"id": "symbol:mod.top", "type": "symbol_body", "symbol": "mod.top"}]}]}
_TEXT = "Which production code calls seed, which tests call seed directly, what does seed do, is it thread safe?"


def _tiny_interp():
    def req(rid, kind, scope=None):
        return {"id": rid, "kind": kind, "mandatory": True,
                "subject": {"mention": "seed", "proposed_names": ["mod.seed"]},
                "scope": scope or {"role": "any", "direct_only": False}, "evidence_source": "static_code"}
    return {"schema": S.INTERPRETATION, "schema_version": 1, "question_id": "t1", "corpus": "tiny",
            "question_text": _TEXT, "interpreter": {"kind": "oracle_gold_derived"}, "answerability": "answerable",
            "requirements": [
                req("r1", "impact", {"role": "production", "direct_only": False, "evidence_span": "production code"}),
                req("r2", "tests", {"role": "tests", "direct_only": True, "evidence_span": "tests call seed directly"}),
                req("r3", "behavior"), req("r4", "concurrency")]}


@pytest.fixture(scope="module")
def tiny_run(tiny):
    it = _tiny_interp()
    assert S.validate_interpretation(it) == []
    planned = P.plan(it, P.resolver_for(tiny.builder))
    return planned, X.execute(planned["plan"], tiny, _MQ, scope=_scope_notes(planned))


def _scope_notes(planned):
    return {rid: n["scope"] for rid, n in planned["requirements"].items()}


def test_execution_runs_each_distinct_call_once_with_its_parameters(tiny_run):
    planned, out = tiny_run
    assert [(c["op"], c["params"], c["serves"], c["ok"]) for c in out["calls"]] == [
        ("blast_radius", {"include_tests": False}, ["r1"], True),
        ("blast_radius", {"include_tests": True}, ["r2"], True),
        ("slice", {"task_type": "debug"}, ["r3"], True)]
    assert all(c["within_budget"] and not c["budget_overflow"] for c in out["calls"])


def test_proof_states_propagate_from_the_existing_predicates(tiny_run):
    _, out = tiny_run
    rep = out["proof_report"]
    assert S.validate_proof_report(rep) == []
    states = {r["id"]: (r["scope"], r["state"]) for r in rep["requirements"]}
    assert states == {"r1": ("retrieved", "PROVED"), "r2": ("retrieved", "PROVED"), "r3": ("retrieved", "PROVED"),
                      "r4": ("no retrieval", "BOUNDED_MISSING")}
    assert rep["question_state"] == "BOUNDED_MISSING"                       # worst requirement wins


def test_production_only_is_enforced_but_tests_only_and_direct_only_are_not(tiny):
    with X.E.registered_repo(tiny) as server:
        prod = server.prism_blast_radius(repo_path=tiny.repo_root, seed_symbol="mod.seed", budget_tokens=13000,
                                         include_tests=False)
        all_ = server.prism_blast_radius(repo_path=tiny.repo_root, seed_symbol="mod.seed", budget_tokens=13000,
                                         include_tests=True)
    assert {c["symbol"] for c in prod["callers"]} == {"mod.caller", "mod.top"}
    hops = {c["symbol"]: (c["hop"], c["is_test"]) for c in all_["callers"]}
    # include_tests=True still returns production callers (tests-only is not a tool option) and 2-hop callers
    assert hops == {"mod.caller": (1, False), "tests.test_mod.test_seed": (1, True), "mod.top": (2, False)}


def test_unsupported_requirement_is_not_credited_with_other_envelopes(tiny_run):
    _, out = tiny_run
    r4 = next(r for r in out["proof_report"]["requirements"] if r["id"] == "r4")
    assert r4["state"] == "BOUNDED_MISSING" and r4["answer_contract"] == "abstain"
    diag = out["unsupported_diagnostics"]["r4"]
    assert diag["state"] == "PROVED" and "not counted" in diag["note"]       # mod.top IS in r1's envelope


def test_execution_is_deterministic(tiny, tiny_run):
    planned, out = tiny_run
    again = X.execute(planned["plan"], tiny, _MQ, scope=_scope_notes(planned))
    assert json.dumps(again, sort_keys=True) == json.dumps(out, sort_keys=True)


# ---- F1: scope compliance is recorded next to the proof state ----------
def test_scope_compliance_is_recorded_without_changing_the_proof_state(tiny_run):
    _, out = tiny_run
    rep = {r["id"]: r for r in out["proof_report"]["requirements"]}
    assert {rid: (r["state"], r["scope_compliance"]["status"]) for rid, r in rep.items()} == {
        "r1": ("PROVED", "enforced"),            # role=production via include_tests=False
        "r2": ("PROVED", "not_enforced"),        # tests-only and direct-only have no tool argument
        "r3": ("PROVED", "no_limits"),
        "r4": ("BOUNDED_MISSING", "not_applicable")}
    assert [n.split(":")[0] for n in rep["r2"]["scope_compliance"]["not_enforced"]] == ["role=tests", "direct_only"]
    assert S.validate_proof_report(out["proof_report"]) == []


def test_without_scope_notes_the_field_is_absent_and_the_report_still_valid(tiny, tiny_run):
    planned, _ = tiny_run
    plain = X.execute(planned["plan"], tiny, _MQ)                       # e.g. fixed-mode replay
    assert all("scope_compliance" not in r for r in plain["proof_report"]["requirements"])
    assert S.validate_proof_report(plain["proof_report"]) == []


def test_a_retrieved_requirement_without_a_scope_note_is_an_error(tiny, tiny_run):
    planned, _ = tiny_run
    notes = _scope_notes(planned)
    notes.pop("r1")
    with pytest.raises(ValueError, match="scope note"):
        X.execute(planned["plan"], tiny, _MQ, scope=notes)


@pytest.mark.parametrize("rid, compliance, expected", [
    ("r2", {"status": "not_enforced", "not_enforced": []}, "exactly when an unenforced limit"),
    ("r3", {"status": "no_limits", "not_enforced": ["direct_only: x"]}, "exactly when an unenforced limit"),
    ("r3", {"status": "not_applicable", "not_enforced": []}, "not_applicable exactly when"),
    ("r4", {"status": "no_limits", "not_enforced": []}, "not_applicable exactly when"),
    ("r1", {"status": "compliant", "not_enforced": []}, "not in"),
    ("r1", {"status": "enforced", "not_enforced": [], "proved": True}, "unknown field proved"),
    ("r1", {"status": "enforced"}, "missing not_enforced"),
])
def test_scope_compliance_rules(tiny_run, rid, compliance, expected):
    _, out = tiny_run
    rep = copy.deepcopy(out["proof_report"])
    next(r for r in rep["requirements"] if r["id"] == rid)["scope_compliance"] = compliance
    assert any(expected in p for p in S.validate_proof_report(rep)), S.validate_proof_report(rep)


# ---- F1 + F2 in the run summary ------------------------------------------
@pytest.fixture(scope="module")
def offline_summary(fixtures, plans):
    """`p1_run.summarize` over the committed slice@13k proof reports standing in
    for an execution (no index needed). Those reports carry no scope_compliance."""
    from harness.experiments.query_to_evidence.nlq import p1_run as R
    recs = B.records()
    executed = {q: {"calls": [], "proof_report": rep} for q, (_, rep) in recs["slice@13k"].items()}
    return R.summarize(plans, executed, fixtures, {m: [r for _, r in qs.values()] for m, qs in recs.items()})


def test_missing_scope_compliance_is_never_counted_as_compliant(offline_summary):
    by = {m["id"]: m for m in offline_summary["metrics"]}
    proved, compliant = by["M8-planned"], by["M8-planned-scope-compliant"]
    assert proved["numerator"] == 13 and compliant["numerator"] == 0          # nothing recorded, nothing compliant
    assert len(compliant["detail"]["excluded_scope_not_recorded"]) == 13
    assert compliant["denominator"] == proved["denominator"] == 28


def test_oracle_derived_metrics_and_routing_are_labelled_self_checks(offline_summary):
    from harness.experiments.query_to_evidence.nlq import p1_run as R
    by = {m["id"]: m for m in offline_summary["metrics"]}
    flagged = {i for i, m in by.items() if m.get("oracle_self_check")}
    assert flagged == {"M2-gate-planned", "M5a-planned", "M5b-planned", "M2-planned-abstain"}
    assert all("ORACLE SELF-CHECK" in by[i]["caveat"] for i in flagged)
    # proof-state metrics are measured, not self-checks, but still name their oracle input
    assert not by["M8-planned"].get("oracle_self_check")
    assert all(m["interpretation_source"] == "oracle_gold_derived" for m in by.values())
    assert offline_summary["comparison"]["routing"]["oracle_self_check"] is True
    assert "not end-to-end natural-language question understanding" in offline_summary["characterization"]
    assert offline_summary["characterization"] == R.CHARACTERIZATION


def test_tool_errors_are_reported_per_requirement(tiny):
    it = _tiny_interp()
    planned = P.plan(it, IDENTITY)
    for r in planned["plan"]["requirements"]:
        if r["resolution"]["status"] == "resolved":
            r["resolution"].update(subject="mod.nope", candidates=["mod.nope"])
    out = X.execute(planned["plan"], tiny, _MQ)
    assert all(not c["ok"] and c["error"]["code"] == -32002 for c in out["calls"])
    assert {r["id"]: r["scope"] for r in out["proof_report"]["requirements"]}["r1"] == "tool error"


def test_disposition_and_comparison_verdicts():
    assert X.disposition({"disposition": "retrieve"}, {"scope": "retrieved", "state": "PROVED"}) == "proved"
    assert X.disposition({"disposition": "retrieve"}, {"scope": "retrieved", "state": "BUDGET_EXCEEDED"}) == \
           "retrieved_not_proved:BUDGET_EXCEEDED"
    assert X.disposition({"disposition": "unsupported"}, {"scope": "no retrieval", "state": "BOUNDED_MISSING"}) == \
           "unsupported"
    rep = lambda s: {"question_id": "q", "requirements": [{"id": "r", "state": s}]}
    cmp = X.compare({"q": rep("PROVED_WITH_WARNING")},
                    {"a": [rep("BOUNDED_MISSING")], "b": [rep("PROVED")], "c": [rep(None)]})["rows"][0]
    assert (cmp["best"], cmp["best_modes"], cmp["vs_best"]) == ("PROVED", ["b"], "regressed")
    assert cmp["vs_mode"] == {"a": "improved", "b": "regressed", "c": None}


# --------------------------------------------------------------------------
# real corpora
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def corpora():
    from prism.slicer.tokenizer import is_exact
    if not is_exact():
        pytest.skip("exact cl100k tokenizer unavailable (set TIKTOKEN_CACHE_DIR)")
    from harness.experiments.query_to_evidence.run import build_engine
    from harness.tasks.loaders import _repo_root
    return {c: build_engine(str(_repo_root(c))) for c in ("fastapi", "django")}


@pytest.mark.slow
def test_planned_run_on_the_gold_set(corpora, fixtures):
    from harness.experiments.query_to_evidence.nlq import p1_run as R
    plans = R.make_plans(fixtures, {c: e.builder for c, e in corpora.items()})
    executed = R.execute_all(plans, corpora, fixtures)
    recs = B.records()
    s = R.summarize(plans, executed, fixtures, {m: [r for _, r in qs.values()] for m, qs in recs.items()})
    m8 = next(m for m in s["metrics"] if m["id"] == "M8-planned")
    assert (m8["numerator"], m8["denominator"]) == (20, 28)
    assert s["dispositions"] == {"proved": 16, "proved_with_warning": 4, "unsupported": 2, "clarify": 3, "abstain": 3}
    assert s["accounted"]["all_accounted"] and s["vs_best"] == {"unchanged": 28}
    assert {(r["question"], r["requirement"]) for r in s["rows"] if r["disposition"] == "unsupported"} == \
           {("q15", "r2"), ("q17", "r2")}
    overflow = next(m for m in s["metrics"] if m["id"] == "M12-overflow-planned")
    assert (overflow["numerator"], overflow["denominator"]) == (4, 20)
    # F1: q16 r2 is PROVED, but its tests-only / direct-only limits were not enforced
    compliant = next(m for m in s["metrics"] if m["id"] == "M8-planned-scope-compliant")
    assert (compliant["numerator"], compliant["denominator"]) == (19, 28)
    assert s["proved_with_unenforced_scope"] == ["q16/r2"]
    status = {(r["question"], r["requirement"]): r["scope_compliance"] for r in s["rows"]}
    assert (status[("q16", "r1")], status[("q16", "r2")], status[("q15", "r2")]) == \
           ("enforced", "not_enforced", "not_applicable")
    assert all(S.validate_proof_report(e["proof_report"]) == [] for e in executed.values())
    assert json.dumps(R.execute_all(plans, corpora, fixtures), sort_keys=True) == json.dumps(executed, sort_keys=True)


@pytest.mark.slow
def test_executor_replays_the_fixed_modes_exactly(corpora):
    baseline, matrix = B.load_inputs()
    recs = B.records()
    fastapi = {m: {q: v for q, v in qs.items() if matrix[q]["corpus"] == "fastapi"} for m, qs in recs.items()}
    assert X.replay_matches_baseline(corpora, matrix, baseline, fastapi) == []
