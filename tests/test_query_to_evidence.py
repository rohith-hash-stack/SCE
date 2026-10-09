"""Query-to-evidence evaluation harness (`harness/experiments/query_to_evidence`).

Two layers:
  - pure tests on hand-built cases (schema rules, stage accounting, first-loss
    attribution, full vs stub, edges in the graph but missing from the model
    input, claim grounding) - no index needed;
  - `slow` tests against the real, pinned FastAPI and Django corpora (same
    discipline as tests/test_two_pass_engine.py): dataset validation against
    the index, unanswerable vs retrieval failure, exact replay of the stored
    cells the container-class-first stubbing fix leaves unchanged (q03-q06,
    q08), explicit regression checks for the ones it changes (q01, q02, q07),
    and tracer on/off equivalence.
"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import networkx as nx
import pytest

from harness.experiments.query_to_evidence import dataset as D
from harness.experiments.query_to_evidence.accounting import (GraphProbe, edge_account, edge_graph_status,
                                                              requirement_account, symbol_account)
from harness.experiments.query_to_evidence.grading import grade_stored_answer, supported_impact_set
from harness.experiments.query_to_evidence.trace import AllRowsLLM, ReplayLLM, Trace, Tracer
from prism.graph.symbol_table import SymbolRole


# --------------------------------------------------------------------------
# hand-built fixtures
# --------------------------------------------------------------------------
class _Table(dict):
    def __iter__(self):
        return iter(self.values())


def _builder(edges, roles=None):
    """Minimal stand-in for ConcreteGraphBuilder: symbol_table + graph."""
    g = nx.DiGraph()
    names = {n for e in edges for n in e[:2] if not n.startswith("<")}
    for u, v, *attrs in edges:
        g.add_edge(u, v, **(attrs[0] if attrs else {"relation": "CALLS"}))
    table = _Table({n: SimpleNamespace(qualified_name=n, role=(roles or {}).get(n, SymbolRole.IMPLEMENTATION))
                    for n in names | set(roles or {})})
    return SimpleNamespace(graph=g, symbol_table=table)


def _case(anchor="m.seed", universe=(), requested=(), nodes=(), raw=(), kept=None, edges=(), stub=(), traced=True,
          dropped=()):
    """A run_case-shaped dict. `kept` defaults to `raw`; items in `stub` are
    signature stubs; the model input contains every kept item."""
    kept = list(raw) if kept is None else list(kept)
    item = lambda s: {"symbols": [s], "kind": "signature_stub" if s in stub else "code_chunk",
                      "source_id": s, "content": f"# {s}\nBODY_OF_{s}", "tokens": 1}
    trace = Trace()
    if traced and anchor:
        trace.manifests.append({"seed": anchor, "direction": "both", "text": "", "universe": sorted(universe), "rows": []})
        trace.hydrations.append({"seed": anchor, "budget": 100, "requested": list(requested), "skipped": [],
                                 "nodes": [{"id": n} for n in nodes], "edges": [{"from": a, "to": b, "type": "CALLS"}
                                                                                 for a, b in edges],
                                 "causal_path": None, "warnings": []})
    items = [item(s) for s in kept]
    return {"trace": trace if traced and anchor else None, "raw_items": [item(s) for s in raw], "items": items,
            "build_meta": {"anchor": anchor, "requested_symbols": list(requested), "budget_dropped": list(dropped)},
            "prompt": "\n".join(i["content"] for i in items),
            "model_input": "SYSTEM\n\n" + "\n".join(i["content"] for i in items)}


B = _builder([("m.a", "m.seed"), ("m.b", "m.a"), ("m.c", "m.seed"), ("m.d", "m.seed"), ("m.e", "m.seed"),
              ("m.f", "m.seed"), ("tests.t", "m.seed"), ("m.far3", "m.far2"), ("m.far2", "m.far1"),
              ("m.far1", "m.x"), ("m.x", "m.seed"), ("m.seed", "m.callee"),
              ("m.callee", "m.d2"), ("m.d2", "m.d3"), ("m.d3", "m.d4")],
             roles={"tests.t": SymbolRole.VERIFICATION, "m.lonely": SymbolRole.IMPLEMENTATION})
P = GraphProbe(B)


# --------------------------------------------------------------------------
# dataset schema
# --------------------------------------------------------------------------
def test_dataset_is_valid_and_has_the_specified_distribution():
    questions, gold = D.load_questions(), D.load_gold()
    assert D.validate(questions, gold) == []
    assert len(questions) == 24
    counts = {c: sum(1 for q in questions if q["category"] == c) for c in D.CATEGORIES}
    assert counts == {"seeded_control": 8, "unseeded_behavioral": 6, "compound": 4, "ambiguous": 3, "unanswerable": 3}
    assert {q["corpus"] for q in questions} == {"fastapi", "django"}


def test_query_file_carries_no_gold():
    raw = json.loads((D.DATASET_DIR / "questions.json").read_text())
    for q in raw["questions"]:
        assert set(q) <= {"id", "corpus", "category", "query", "inputs"}
        assert set(q["inputs"]) <= {"seed_symbol", "task_type"}
        assert bool(q["inputs"]) == (q["category"] == "seeded_control")


def test_expected_behaviour_and_category_fields():
    gold = D.load_gold()
    for q in D.load_questions():
        g = gold[q["id"]]
        if q["category"] == "compound":
            assert len(g["requirements"]) >= 2
        if q["category"] == "ambiguous":
            assert g["expected_behavior"] == "clarify" and g["ambiguity"]["resolving_information"]
        if q["category"] == "unanswerable":
            assert g["expected_behavior"] == "abstain" and g["unanswerable_reason"] and not g["requirements"]


@pytest.mark.parametrize("mutate, expected", [
    (lambda q, g: q["q09"].update(query="How does APIRouter.include_router copy routes?"), "query names gold symbol"),
    (lambda q, g: g["q15"]["requirements"].pop(), "compound question needs >= 2 requirements"),
    (lambda q, g: g["q22"].pop("unanswerable_reason"), "unanswerable question needs a reason"),
    (lambda q, g: g["q19"]["ambiguity"].pop("resolving_information"), "ambiguous question needs interpretations"),
    (lambda q, g: q["q10"].update(inputs={"seed_symbol": "x", "task_type": "T2_localization"}), "only seeded controls may carry inputs"),
    (lambda q, g: g["q17"]["requirements"][1].update(required_symbols=["x"]), "absence requirement must have no symbols"),
    (lambda q, g: g["q01"].update(expected_behavior="abstain"), "answerability/expected_behavior mismatch"),
])
def test_validator_rejects_broken_datasets(mutate, expected):
    questions = copy.deepcopy(D.load_questions())
    gold = copy.deepcopy(D.load_gold())
    mutate({q["id"]: q for q in questions}, gold)
    assert any(expected in p for p in D.validate(questions, gold))


# --------------------------------------------------------------------------
# stage accounting and first-loss attribution
# --------------------------------------------------------------------------
@pytest.mark.parametrize("case, sym, state, first_loss", [
    (_case(anchor=None), "m.a", "not_resolved_from_query", "resolution"),
    (_case(universe={"m.seed"}), "m.ghost", "not_indexed", "index"),
    (_case(universe={"m.seed"}), "m.a", "not_discovered_in_manifest", "manifest"),
    (_case(universe={"m.seed", "m.a"}), "m.a", "discovered_not_selected", "selection"),
    (_case(universe={"m.seed"}, requested={"m.a"}), "m.a", "requested_but_absent_from_manifest", "manifest"),
    (_case(universe={"m.seed", "m.a"}, requested={"m.a"}), "m.a", "selected_not_hydrated", "hydration"),
    (_case(universe={"m.a"}, requested={"m.a"}, nodes={"m.a"}), "m.a", "hydrated_then_excluded", "arm"),
    (_case(universe={"m.a"}, requested={"m.a"}, nodes={"m.a"}, raw=["m.a"], kept=[]), "m.a", "lost_to_budget", "budget"),
    (_case(universe={"m.a"}, requested={"m.a"}, nodes={"m.a"}, raw=["m.a"]), "m.a", "delivered_full", None),
    (_case(universe={"m.a"}, requested={"m.a"}, nodes={"m.a"}, raw=["m.a"], stub={"m.a"}), "m.a", "delivered_stub", None),
])
def test_each_stage_state_and_its_first_loss(case, sym, state, first_loss):
    acc = symbol_account(sym, case, B, P)
    assert (acc["state"], acc["first_loss"]) == (state, first_loss)


def test_first_loss_is_the_earliest_stage_and_secondary_causes_are_kept():
    # not in the manifest AND not delivered: the manifest is the first loss;
    # the reason it was not admitted (test code) is a secondary cause.
    acc = symbol_account("tests.t", _case(universe={"m.seed"}), B, P)
    assert acc["first_loss"] == "manifest"
    assert any(c.startswith("filtered_as_test_code") for c in acc["secondary_causes"])
    # a symbol with no call path to the subject at all
    acc = symbol_account("m.lonely", _case(universe={"m.seed"}), B, P)
    assert "no_call_path_to_or_from_subject" in acc["secondary_causes"]
    # a callee beyond the manifest's downstream bound (4 > 3 hops)
    acc = symbol_account("m.d4", _case(universe={"m.seed"}), B, P)
    assert any(c.startswith("callee_beyond_downstream_bound") for c in acc["secondary_causes"])
    # a caller within bounds that the manifest still did not admit
    acc = symbol_account("m.far3", _case(universe={"m.seed"}), B, P)
    assert any(c.startswith("within_bounds_but_not_admitted") for c in acc["secondary_causes"])


def test_auto_included_and_stub_are_recorded_as_secondary():
    acc = symbol_account("m.a", _case(universe={"m.a"}, nodes={"m.a"}, raw=["m.a"], stub={"m.a"}), B, P)
    assert acc["first_loss"] is None and acc["delivery"] == "stub"
    assert {"auto_included_by_hydration", "compressed_to_stub"} <= set(acc["secondary_causes"])


def test_unobservable_stages_are_not_measured_not_lost():
    # a stored cell has no manifest/hydration trace: those stages are None
    acc = symbol_account("m.a", _case(requested={"m.a"}, raw=["m.a"], traced=False), B, P)
    assert acc["reached"]["manifest"] is None and acc["reached"]["hydration"] is None
    assert acc["first_loss"] is None and acc["state"] == "delivered_full"


def test_full_vs_stub_recall_has_explicit_denominators():
    req = {"id": "r1", "kind": "impact", "required_symbols": ["m.a", "m.c", "m.d"], "relationship_paths": []}
    case = _case(universe={"m.a", "m.c", "m.d"}, requested={"m.a", "m.c", "m.d"}, nodes={"m.a", "m.c", "m.d"},
                 raw=["m.a", "m.c"], stub={"m.c"})
    r = requirement_account(req, case, B, P)["recall"]
    assert r["delivered"] == {"num": 2, "den": 3}
    assert r["delivered_full"] == {"num": 1, "den": 3}
    assert r["delivered_stub_only"] == {"num": 1, "den": 3}
    assert requirement_account({**req, "required_symbols": []}, case, B, P)["recall"]["delivered"] is None


# --------------------------------------------------------------------------
# relationship paths
# --------------------------------------------------------------------------
EDGE = {"from": "m.a", "to": "m.seed", "call_site": "m.py:1", "source_text": "BODY_OF_m.a"}


def test_relationship_in_graph_but_missing_from_model_input():
    # edge is in the graph and in the hydrated package, but the caller was
    # delivered as a stub: the call site never reaches the model.
    case = _case(universe={"m.a", "m.seed"}, requested={"m.a"}, nodes={"m.a", "m.seed"}, raw=["m.a", "m.seed"],
                 stub={"m.a"}, edges=[("m.a", "m.seed")])
    states = {s: symbol_account(s, case, B, P) for s in ("m.a", "m.seed")}
    e = edge_account(EDGE, case, B, states)
    assert e["stages"]["graph"] and e["stages"]["manifest"] and e["stages"]["hydrated_edge"]
    assert e["first_loss"] == "call_site_in_model_input"
    assert "relationship_constructed_in_package_but_not_delivered_as_relationship" in e["secondary_causes"]
    assert any(c.startswith("caller_delivered_only_as_stub") for c in e["secondary_causes"])
    assert e["relationship_record_delivered"] is False


def test_relationship_delivered_through_the_callers_body():
    case = _case(universe={"m.a", "m.seed"}, requested={"m.a"}, nodes={"m.a", "m.seed"}, raw=["m.a", "m.seed"],
                 edges=[("m.a", "m.seed")])
    states = {s: symbol_account(s, case, B, P) for s in ("m.a", "m.seed")}
    assert edge_account(EDGE, case, B, states)["first_loss"] is None


def test_edge_graph_status_keeps_tentative_and_sentinel_edges():
    b = _builder([("m.u", "m.v", {"relation": "CALLS", "kind": "TENTATIVE_CALL"}),
                  ("m.u", "m.w", {"relation": "CALLS", "confidence": "CONFIRMED_RUNTIME"}),
                  ("m.u", "<ambiguous:amb@m.py:3>"), ("m.u", "<dynamic:hazard@m.py:4>"),
                  ("m.u", "k.other"), ("m.amb", "m.z"), ("m.other", "m.z")])
    assert edge_graph_status(b, "m.u", "m.v")["status"] == "tentative"
    assert edge_graph_status(b, "m.u", "m.v")["kind"] == "TENTATIVE_CALL"
    assert edge_graph_status(b, "m.u", "m.w")["status"] == "confirmed_runtime"
    assert edge_graph_status(b, "m.u", "m.amb")["status"] == "ambiguous_sentinel"
    assert edge_graph_status(b, "m.u", "m.other") == {"status": "resolved_to_other_target", "targets": ["k.other"]}
    assert edge_graph_status(b, "m.u", "m.z")["status"] == "unresolved"
    assert edge_graph_status(b, None, "m.z")["status"] == "endpoint_not_indexed"


# --------------------------------------------------------------------------
# answer grading
# --------------------------------------------------------------------------
def _stored(items, names, task_type="T5_blast_radius"):
    return {"bundle": {"build_meta": {"task_type": task_type},
                       "items": [{"symbols": [s], "kind": k, "content": c} for s, k, c in items]},
            "completion": {"text": json.dumps({"reasoning": "r", "symbols": names})}}


def test_named_symbol_is_not_a_supported_claim_without_delivered_evidence():
    b = _builder([("m.a", "m.seed"), ("m.b", "m.a"), ("m.c", "m.seed")])
    gold = {"requirements": [{"required_symbols": ["m.a", "m.b", "m.c"]}]}
    stored = _stored([("m.a", "code_chunk", "def a():\n    seed()"), ("m.b", "code_chunk", "def b():\n    a()"),
                      ("m.c", "signature_stub", "def c(): ...")], ["m.seed", "m.a", "m.b", "m.c", "m.nope"])
    out = grade_stored_answer(stored, gold, b, "m.seed")
    claims = {c["symbol"]: c for c in out["claims"]}
    assert claims["m.a"]["supported"] and claims["m.b"]["support"] == "m.b -> m.a -> m.seed"
    assert claims["m.c"]["gold"] and not claims["m.c"]["supported"]           # gold, but only a stub reached the model
    assert claims["m.nope"]["evidence"] == "not_indexed"
    s = out["summary"]
    assert s["subject_named"] and s["named"] == 4 and s["gold_named"] == 3 and s["correct_but_unsupported"] == 1


def test_call_must_be_visible_in_the_delivered_body():
    b = _builder([("m.a", "m.seed")])
    assert supported_impact_set(b, "m.seed", {"m.a": "def a():\n    pass"}) == {}
    assert "m.a" in supported_impact_set(b, "m.seed", {"m.a": "def a():\n    seed()"})


def test_t2_claims_need_the_body_and_unparsed_answers_are_reported():
    b = _builder([("m.seed", "m.a")])
    out = grade_stored_answer(_stored([("m.a", "code_chunk", "x")], ["m.a"], "T2_localization"),
                              {"requirements": [{"required_symbols": ["m.a"]}]}, b, "m.seed")
    assert out["claims"][0]["supported"]
    bad = {"bundle": {"build_meta": {}, "items": []}, "completion": {"text": "no json here"}}
    assert grade_stored_answer(bad, {"requirements": []}, b, "m.seed")["status"] == "unparsed"
    assert grade_stored_answer({**bad, "completion": None}, {"requirements": []}, b, "m.seed")["status"] == "not_measured"


# --------------------------------------------------------------------------
# tracer mechanics
# --------------------------------------------------------------------------
class _Engine:
    def build_candidate_manifest(self, seed_id, direction="downstream", budget_tokens=None):
        return "<candidate_index>\nm.a|caller\n</candidate_index>", {seed_id, "m.a"}

    def retrieve_requested(self, seed_id, budget, requested, universe, task_type="debug"):
        node = SimpleNamespace(id="m.a", role="caller", compression="L0_full", cost=1, distance=1.0)
        edge = SimpleNamespace(from_node="m.a", to_node=seed_id, type="CALLS")
        return SimpleNamespace(nodes=[node], edges=[edge], causal_path=None, warnings=[]), []


def test_tracer_records_without_changing_results_and_detaches():
    eng = _Engine()
    plain = (eng.build_candidate_manifest("m.seed", direction="both"), eng.retrieve_requested("m.seed", 9, ["m.a"], set()))
    tracer = Tracer(eng)
    traced = (eng.build_candidate_manifest("m.seed", direction="both"), eng.retrieve_requested("m.seed", 9, ["m.a"], set()))
    tracer.detach()
    assert plain[0] == traced[0] and plain[1][1] == traced[1][1]
    assert [n.id for n in traced[1][0].nodes] == [n.id for n in plain[1][0].nodes]
    assert tracer.trace.manifest["universe"] == ["m.a", "m.seed"] and tracer.trace.manifest["direction"] == "both"
    assert tracer.trace.hydration["edges"] == [{"from": "m.a", "to": "m.seed", "type": "CALLS"}]
    assert "build_candidate_manifest" not in eng.__dict__ and "retrieve_requested" not in eng.__dict__


def test_turn1_stand_ins_are_labelled_and_replay_is_strict():
    out = AllRowsLLM()("sys", "x\n<candidate_index>\nm.a|caller|1\nm.b|callee|2\n</candidate_index>", purpose="turn1")
    assert json.loads(out.text)["requested_symbols"] == ["m.a", "m.b"] and out.model == "all_rows_upper_bound"
    replay = ReplayLLM([{"purpose": "turn1", "text": "T"}])
    assert replay("s", "u", purpose="turn1").text == "T"
    with pytest.raises(RuntimeError):
        replay("s", "u", purpose="turn2b")


# --------------------------------------------------------------------------
# real corpora: validation, outcomes, replay, equivalence
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def evaluation():
    from prism.slicer.tokenizer import is_exact
    if not is_exact():
        pytest.skip("exact cl100k tokenizer unavailable (set TIKTOKEN_CACHE_DIR)")
    from harness.experiments.query_to_evidence.run import Evaluation
    return Evaluation(["fastapi"])


@pytest.mark.slow
def test_fastapi_gold_matches_the_index_and_source(evaluation):
    # Django questions are schema-checked only here (no Django index built)
    assert D.validate(D.load_questions(), evaluation.gold, evaluation.builders()) == []


@pytest.mark.slow
def test_unanswerable_is_expected_absence_and_unseeded_is_a_resolution_loss(evaluation):
    by_id = {q["id"]: q for q in evaluation.questions}
    un = evaluation.evaluate_question(by_id["q22"])
    assert un["unanswerable"]["classification"].startswith("expected_absence")
    assert un["production"]["summary"]["no_seed"] and un["production"]["requirements"] == []
    beh = evaluation.evaluate_question(by_id["q09"])
    states = [s for r in beh["production"]["requirements"] for s in r["symbols"]]
    assert states and all(s["state"] == "not_resolved_from_query" for s in states)
    assert all(s["first_loss"] == "resolution" for s in states)
    # the gold edges are still in the graph: this is a resolution failure, not a missing graph
    assert all(e["stages"]["graph"] for r in beh["production"]["requirements"] for e in r["edges"])


def _exact_replay(ev, corpus, qid):
    from harness.experiments.query_to_evidence.run import compare_to_baseline
    q = next(x for x in ev.questions if x["id"] == qid)
    seed, stored, llm = ev.control_inputs(q)
    case = ev._run(corpus, q["query"], seed, llm)
    cmp_ = compare_to_baseline(case, stored)
    assert cmp_["equivalent"], cmp_
    again = ev._run(corpus, q["query"], seed, ev.control_inputs(q)[2])
    assert again["model_input"] == case["model_input"]


# Controls the container-class-first stubbing fix leaves untouched replay
# their stored s42 cell exactly (requested, delivered, stubs, item text,
# prompt string).
@pytest.mark.slow
@pytest.mark.parametrize("qid", ["q05", "q06"])
def test_controls_replay_the_stored_cell_exactly(evaluation, qid):
    _exact_replay(evaluation, "fastapi", qid)


def _run_affected_control(ev, corpus, qid, monkeypatch):
    """Run a control whose stored cell predates the container-class-first
    stubbing fix. Returns (case, stored, gold symbols, renders) where
    `renders` is the final rendered-package size and limit of every
    `_enforce_render_budget` call (test-only wrapper; result unchanged)."""
    import prism.surface.build as sb
    from prism.slicer.tokenizer import count_tokens

    renders = []
    real = sb._enforce_render_budget

    def recording(pkg, target_budget, builder):
        out = real(pkg, target_budget, builder)
        text = sb.render(out, sb.RenderOptions(include_timestamp=False, include_run_id=False))
        renders.append({"tokens": count_tokens(text), "limit": target_budget * (1 + sb._RENDER_BUDGET_TOLERANCE)})
        return out

    monkeypatch.setattr(sb, "_enforce_render_budget", recording)
    q = next(x for x in ev.questions if x["id"] == qid)
    seed, stored, llm = ev.control_inputs(q)
    case = ev._run(corpus, q["query"], seed, llm)
    gold = {s for r in ev.gold[qid]["requirements"] for s in r["required_symbols"]}
    return case, stored, gold, renders


def _assert_selection_unchanged_and_within_budget(case, stored, renders):
    syms = lambda items: {s for i in items for s in i["symbols"]}
    stored_items = stored["bundle"]["items"]
    assert case["build_meta"]["requested_symbols"] == stored["bundle"]["build_meta"]["requested_symbols"]
    assert syms(case["items"]) == syms(stored_items)
    assert renders and all(r["tokens"] <= r["limit"] for r in renders), renders


def _stubbed(case):
    return {s for i in case["items"] if i["kind"] == "signature_stub" for s in i["symbols"]}


def _full(case):
    return {s for i in case["items"] if i["kind"] == "code_chunk" for s in i["symbols"]}


@pytest.mark.slow
def test_q01_keeps_gold_callers_full_after_container_first_stubbing(evaluation, monkeypatch):
    case, stored, gold, renders = _run_affected_control(evaluation, "fastapi", "q01", monkeypatch)
    _assert_selection_unchanged_and_within_budget(case, stored, renders)
    assert _stubbed(case) & gold == set()
    assert "fastapi.dependencies.utils.get_parameterless_sub_dependant" in _full(case)
    assert "return get_sub_dependant(depends=depends, dependency=depends.dependency, path=path)" in case["model_input"]
    assert _stubbed(case) == {"fastapi.routing.APIRoute"}          # only the promoted container class


@pytest.mark.slow
def test_q02_stubs_only_the_promoted_container_class(evaluation, monkeypatch):
    case, stored, gold, renders = _run_affected_control(evaluation, "fastapi", "q02", monkeypatch)
    _assert_selection_unchanged_and_within_budget(case, stored, renders)
    assert _stubbed(case) & gold == set()
    assert _stubbed(case) == {"fastapi.applications.FastAPI"}


# ---- Django controls (one more index) -----------------------------------
@pytest.fixture(scope="module")
def django_evaluation():
    from prism.slicer.tokenizer import is_exact
    if not is_exact():
        pytest.skip("exact cl100k tokenizer unavailable (set TIKTOKEN_CACHE_DIR)")
    from harness.experiments.query_to_evidence.run import Evaluation
    return Evaluation(["django"])


@pytest.mark.slow
@pytest.mark.parametrize("qid", ["q03", "q04", "q08"])
def test_django_controls_replay_the_stored_cell_exactly(django_evaluation, qid):
    _exact_replay(django_evaluation, "django", qid)


@pytest.mark.slow
def test_q07_keeps_chain_and_clone_full_after_container_first_stubbing(django_evaluation, monkeypatch):
    case, stored, gold, renders = _run_affected_control(django_evaluation, "django", "q07", monkeypatch)
    _assert_selection_unchanged_and_within_budget(case, stored, renders)
    assert _stubbed(case) & gold == set()
    assert {"django.db.models.query.QuerySet._chain", "django.db.models.query.QuerySet._clone"} <= _full(case)
    assert "obj = self._clone()" in case["model_input"]
    assert _stubbed(case) == {"django.db.models.query.QuerySet"}   # only the promoted container class


@pytest.mark.slow
def test_tracer_on_and_off_give_identical_selection_and_model_input(evaluation):
    rows = evaluation.equivalence()
    assert rows and all(r["identical"] for r in rows), [r for r in rows if not r["identical"]]
