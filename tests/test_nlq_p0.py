"""P0 evaluation substrate for question -> evidence planning
(`harness/experiments/query_to_evidence/nlq`).

Fast tests use hand-built inputs and the committed artifacts only. `slow`
tests build the real FastAPI and Django indexes and check that the extracted
lexical search reproduces the recorded probe output field for field.
"""
from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import networkx as nx
import pytest

from harness.experiments.query_to_evidence.nlq import baseline_records as B
from harness.experiments.query_to_evidence.nlq import gold_fixtures as G
from harness.experiments.query_to_evidence.nlq import lexical_eval as L
from harness.experiments.query_to_evidence.nlq import metrics as M
from harness.experiments.query_to_evidence.nlq import schema as S
from harness.experiments.query_to_evidence.nlq.lexical import BM25, LexicalIndex, stem, tokenize
from prism.graph.symbol_table import SymbolRole

REPO = Path(__file__).resolve().parents[1]
CANONICAL = ["harness/experiments/query_to_evidence/design/baseline/proof_baseline.json",
             "harness/experiments/query_to_evidence/design/baseline/requirements_matrix.json",
             "harness/experiments/query_to_evidence/dataset/gold.json",
             "harness/experiments/query_to_evidence/dataset/questions.json"]


@pytest.fixture(scope="module")
def fixtures():
    return G.load()


@pytest.fixture(scope="module")
def recorded():
    return L.load_recorded()


# --------------------------------------------------------------------------
# canonical artifacts are untouched
# --------------------------------------------------------------------------
@pytest.mark.parametrize("rel", CANONICAL)
def test_canonical_artifacts_are_byte_identical_to_head(rel):
    head = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=REPO, capture_output=True, check=True).stdout
    assert (REPO / rel).read_bytes() == head


# --------------------------------------------------------------------------
# lexical search: tokenizer, BM25, index, expansion
# --------------------------------------------------------------------------
def test_tokenizer_splits_identifiers_and_drops_stop_words():
    assert tokenize("HTTPBasic.__call__") == ["http", "basic", "call"]
    assert tokenize("getAuthorizationSchemeParam") == ["get", "authorization", "scheme", "param"]
    assert tokenize("What does get return in django and fastapi?") == ["get", "return"]   # `get` kept
    assert tokenize("a b x") == [] and tokenize("") == [] and tokenize("!!! 123") == []


def test_stemming_strips_one_suffix_and_keeps_short_words():
    assert [stem(w) for w in ("routes", "evicts", "running", "expired", "is", "bus", "gets")] == \
           ["rout", "evict", "runn", "expir", "is", "bus", "get"]


def test_bm25_ties_break_by_document_order_and_unmatched_are_unranked():
    ix = BM25([["cache", "get"], ["other"], ["cache", "get"], ["cache"]])
    assert ix.rank(["cache", "get"]) == [0, 2, 3]          # 0 and 2 tie: lower index first; 1 never ranked
    assert ix.rank([]) == [] and ix.rank(["nowhere"]) == []
    assert ix.rank(["get", "cache", "get"]) == ix.rank(["cache", "get"])     # query terms are a set


def _sym(name, kind="function", role=SymbolRole.IMPLEMENTATION, file="/r/m.py", lines=(1, 1)):
    return SimpleNamespace(qualified_name=name, kind=kind, role=role, file=file, line_range=lines)


class _Table(list):
    def get(self, name):
        return next((s for s in self if s.qualified_name == name), None)


@pytest.fixture
def tiny(tmp_path):
    src = tmp_path / "cache.py"
    src.write_text("def cull():\n    evict_entries()\n\ndef read_entry():\n    return expired\n"
                   "def put(): pass\ndef helper(): pass\ndef test_put(): pass\n")
    f = str(src)
    table = _Table([_sym("cache.cull", file=f, lines=(1, 2)), _sym("cache.read_entry", file=f, lines=(4, 5)),
                    _sym("cache.put", file=f, lines=(6, 6)), _sym("cache.helper", file=f, lines=(7, 7)),
                    _sym("cache.Store._lock", kind="attribute", file=f, lines=(2, 2)),
                    _sym("tests.test_put", role=SymbolRole.VERIFICATION, file=f, lines=(8, 8))])
    g = nx.DiGraph()
    g.add_edge("cache.put", "cache.cull", relation="CALLS")
    g.add_edge("cache.helper", "cache.put", relation="INSTANTIATES")
    g.add_edge("cache.read_entry", "cache.Store._lock", relation="READS_STATE")
    g.add_edge("tests.test_put", "cache.put", relation="CALLS")
    g.add_edge("cache.read_entry", "cache.helper", relation="EXTENDS")
    return LexicalIndex(SimpleNamespace(symbol_table=table, graph=g), str(tmp_path))


def test_pool_excludes_attributes_and_test_code(tiny):
    assert tiny.names == ["cache.cull", "cache.read_entry", "cache.put", "cache.helper"]


def test_search_limits_and_bad_inputs(tiny):
    assert tiny.search("cull", "N", 5) == ["cache.cull"]
    assert tiny.search("cache", "N", 2) == tiny.ranked("cache", "N")[:2] and len(tiny.ranked("cache", "N")) == 4
    assert tiny.search("cache", "N", 0) == [] and tiny.search("", "NB", 5) == []
    with pytest.raises(ValueError):
        tiny.search("cache", "N", -1)
    with pytest.raises(ValueError):
        tiny.search("cache", "BODY", 3)
    assert tiny.ranks("cull", "N", ["cache.cull", "cache.Store._lock", "nope"]) == \
           {"cache.cull": 1, "cache.Store._lock": None, "nope": None}


def test_body_variant_reaches_words_only_in_source(tiny):
    assert tiny.search("evict entries", "N", 5) == []
    assert tiny.search("evict entries", "NB", 5) == ["cache.cull"]


def test_vocabulary_gap_is_not_bridged(tiny):
    # the question's word never occurs in the name, path or body: no synonym table, no rank
    assert tiny.ranks("when it discards old items", "NB", ["cache.cull"]) == {"cache.cull": None}
    assert tiny.ranks("when it evicts old entries", "NB", ["cache.cull"])["cache.cull"] == 1   # only a literal word match


def test_expansion_follows_calls_instantiates_overrides_within_the_pool(tiny):
    assert tiny.neighbours("cache.put") == {"cache.cull", "cache.helper"}        # test caller excluded
    assert tiny.neighbours("cache.read_entry") == set()                           # READS_STATE / EXTENDS not followed
    small, big = tiny.expanded("cull", "N", 1), tiny.expanded("cache", "N", 4)
    assert small == {"cache.cull", "cache.put"} and small <= big
    assert tiny.expanded("", "N", 10) == set()


def test_search_is_deterministic(tiny):
    again = LexicalIndex(tiny.builder, tiny.root)
    for q in ("cache entry", "evict", "put helper"):
        for v in ("N", "NB"):
            assert tiny.ranked(q, v) == again.ranked(q, v)


# ---- F5: path tokens independent of the checkout location ----------------
def _checkout(root):
    """The same two-file corpus, checked out under `root`."""
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "cache.py").write_text("def cull():\n    evict_entries()\n")
    (root / "pkg" / "user.py").write_text("def load_user():\n    return 1\n")
    table = _Table([_sym("pkg.cache.cull", file=str(root / "pkg" / "cache.py"), lines=(1, 2)),
                    _sym("pkg.user.load_user", file=str(root / "pkg" / "user.py"), lines=(1, 2))])
    return SimpleNamespace(symbol_table=table, graph=nx.DiGraph())


def test_rebased_path_tokens_do_not_depend_on_the_checkout_location(tmp_path):
    a, b = tmp_path / "home" / "user" / "SCE", tmp_path / "workspace" / "elsewhere"
    ba, bb = _checkout(a), _checkout(b)
    # as indexed (absolute paths): the documents differ, and so can the ranks ("user" is in one checkout path)
    assert LexicalIndex(ba, str(a)).bm25["N"].tf != LexicalIndex(bb, str(b)).bm25["N"].tf
    # rebased onto the recording root: token-for-token identical documents, identical ranks
    ra = LexicalIndex(ba, str(a), path_tokens_root="/rec/root")
    rb = LexicalIndex(bb, str(b), path_tokens_root="/rec/root")
    assert ra.bm25["N"].tf == rb.bm25["N"].tf and ra.bm25["NB"].tf == rb.bm25["NB"].tf
    for q in ("user", "evict cache", "home"):
        assert ra.ranked(q, "NB") == rb.ranked(q, "NB")


def test_rebasing_onto_the_index_root_is_the_identity(tmp_path):
    b = _checkout(tmp_path)
    plain, rebased = LexicalIndex(b, str(tmp_path)), LexicalIndex(b, str(tmp_path), path_tokens_root=str(tmp_path))
    assert plain.bm25["N"].tf == rebased.bm25["N"].tf and plain.bm25["NB"].tf == rebased.bm25["NB"].tf


def test_rebasing_rejects_files_outside_the_root_and_mixed_variants(tmp_path):
    from harness.experiments.query_to_evidence.nlq.lexical import rebase_path
    assert rebase_path(str(tmp_path / "a" / "b.py"), str(tmp_path), "/rec") == "/rec/a/b.py"
    assert rebase_path("a/b.py", str(tmp_path), "/rec/") == "/rec/a/b.py"
    with pytest.raises(ValueError, match="outside the corpus root"):
        rebase_path(str(tmp_path.parent / "other.py"), str(tmp_path), "/rec")
    with pytest.raises(ValueError, match="different variants"):
        LexicalIndex(_checkout(tmp_path), str(tmp_path), relative_paths=True, path_tokens_root="/rec")


def test_recorded_roots_cover_every_recorded_corpus(recorded):
    assert set(L.RECORDED_CORPUS_ROOTS) == set(recorded["corpus"])
    assert all(r.endswith("/" + c) for c, r in L.RECORDED_CORPUS_ROOTS.items())


# --------------------------------------------------------------------------
# recorded lexical results: the report's numbers, with denominators
# --------------------------------------------------------------------------
def test_recorded_subject_recall_matches_the_report(recorded):
    q10 = M.UNSEEDED_AND_COMPOUND
    assert L.subject_recall(recorded, q10, 10, "rank") == {"questions": [4, 10], "subjects": [4, 11]}
    assert L.subject_recall(recorded, q10, 10, "expanded") == {"questions": [6, 10], "subjects": [6, 11]}
    assert L.subject_recall(recorded, q10, 20, "expanded") == {"questions": [6, 10], "subjects": [6, 11]}
    assert L.subject_recall(recorded, q10, 50, "expanded") == {"questions": [9, 10], "subjects": [9, 11]}


def test_recorded_ranks_of_the_ten_unseeded_questions(recorded):
    ranks = {qid: {t: (recorded["queries"][qid]["N"]["rank"][t], recorded["queries"][qid]["NB"]["rank"][t])
                   for t in recorded["queries"][qid]["targets"]} for qid in M.UNSEEDED_AND_COMPOUND}
    flat = {qid: sorted(v.values()) for qid, v in ranks.items()}
    assert flat == {"q09": [(5, 3)], "q10": [(4, 2)], "q11": [(None, 50)], "q12": [(255, 24)], "q13": [(85, 34)],
                    "q14": [(2, 5)], "q15": [(325, 98), (331, 1435)], "q16": [(1090, 86)], "q17": [(2, 25)],
                    "q18": [(121, 93)]}


def test_recorded_vocabulary_gap_q15_is_never_found(recorded):
    exp = recorded["queries"]["q15"]["expansion"]
    assert all(not any(v["targets_found"].values()) for v in exp.values())
    assert set(exp) == {f"{v}@{k}" for v in ("N", "NB") for k in (10, 20, 50)}


def test_recorded_candidate_list_growth(recorded):
    for qid in M.UNSEEDED_AND_COMPOUND:
        exp = recorded["queries"][qid]["expansion"]
        for v in ("N", "NB"):
            sizes = [exp[f"{v}@{k}"]["size"] for k in (10, 20, 50)]
            assert sizes == sorted(sizes) and sizes[0] >= 10           # k seeds, plus neighbours
    largest = {qid: max(recorded["queries"][qid]["expansion"][f"{v}@50"]["size"] for v in ("N", "NB"))
               for qid in M.UNSEEDED_AND_COMPOUND}
    assert largest == {"q09": 85, "q10": 210, "q11": 541, "q12": 181, "q13": 166, "q14": 197, "q15": 164,
                       "q16": 140, "q17": 388, "q18": 819}


def test_recorded_false_subjects_for_unanswerable_and_ambiguous(recorded):
    q = recorded["queries"]
    assert q["q24"]["N"]["top10"][0] == "django.db.models.query.QuerySet.get"
    assert all(n.startswith("docs_src.") for n in q["q22"]["NB"]["top10"][:6])
    assert q["q20"]["NB"]["interp_best_rank"] == [10, 4, 6, 7, 1, 2]
    assert not any(q["q19"][v]["interp_in_top20"][i] for v in ("N", "NB") for i in range(3))
    assert not any(q["q21"][v]["interp_in_top20"][i] for v in ("N", "NB") for i in range(3))


def test_diff_against_recorded_reports_every_changed_field(recorded):
    m = {"corpus": {"fastapi": dict(ranked_pool=recorded["corpus"]["fastapi"]["ranked_pool"])},
         "queries": {"q09": copy.deepcopy(recorded["queries"]["q09"])}}
    assert L.diff_against_recorded(m, recorded) == []
    m["queries"]["q09"]["N"]["rank"]["fastapi.routing.APIRouter.include_router"] = 6
    m["corpus"]["fastapi"]["ranked_pool"] += 1
    assert len(L.diff_against_recorded(m, recorded)) == 2


# --------------------------------------------------------------------------
# gold-derived fixtures
# --------------------------------------------------------------------------
def test_fixture_file_is_the_builder_output():
    assert G.FIXTURE.read_text() == G.render(G.build())


def test_fixtures_cover_all_24_questions_and_every_matrix_requirement(fixtures):
    assert len(fixtures) == 24
    matrix = json.loads(G.MATRIX.read_text())["questions"]
    gold_reqs = {(q["query_id"], r["id"], r["kind"], r.get("subject"), r["mandatory"]) for q in matrix
                 for r in q["requirements"]}
    fx_reqs = {(fx["interpretation"]["question_id"], r["id"], r["kind"],
                (r["subject"]["proposed_names"] or [None])[0], r["mandatory"])
               for fx in fixtures for r in fx["interpretation"]["requirements"]}
    assert len(gold_reqs) == 28 and fx_reqs == gold_reqs
    # facts are not copied: the join hands back the matrix entry itself, all 83 facts
    assert [fx["gold"] for fx in fixtures] == sorted(matrix, key=lambda q: q["query_id"])
    assert sum(len(r["facts"]) for fx in fixtures for r in fx["gold"]["requirements"]) == 83


def test_fixtures_are_valid_and_labelled(fixtures):
    for fx in fixtures:
        it = fx["interpretation"]
        assert S.validate_interpretation(it) == []
        assert it["interpreter"] == {"kind": "oracle_gold_derived", "note": G.LABEL}
        assert "not evidence of natural-language understanding" in it["interpreter"]["note"]


def test_fixture_scope_limits_and_evidence_sources_are_the_annotated_ones(fixtures):
    limits = {(fx["gold"]["query_id"], r["id"]): (r["scope"]["role"], r["scope"]["direct_only"])
              for fx in fixtures for r in fx["interpretation"]["requirements"] if r["scope"]["role"] != "any"}
    assert limits == {("q16", "r1"): ("production", False), ("q16", "r2"): ("tests", True),
                      ("q17", "r2"): ("tests", True)}
    sources = {fx["gold"]["query_id"]: r["evidence_source"] for fx in fixtures
               for r in fx["interpretation"]["requirements"] if r["evidence_source"] != "static_code"}
    assert sources == {"q22": "runtime_telemetry", "q23": "deployment_or_business_data", "q24": "runtime_telemetry"}
    q21 = next(fx for fx in fixtures if fx["gold"]["query_id"] == "q21")["interpretation"]["clarification"]
    assert [i["evidence_source"] for i in q21["interpretations"]] == ["static_code"] * 3 + ["external_service"]


def test_load_rejects_a_dropped_or_weakened_requirement(tmp_path):
    data = json.loads(G.FIXTURE.read_text())
    dropped = copy.deepcopy(data)
    q15 = next(i for i in dropped["interpretations"] if i["question_id"] == "q15")
    q15["requirements"] = q15["requirements"][:1]
    (tmp_path / "a.json").write_text(json.dumps(dropped))
    with pytest.raises(ValueError, match="q15"):
        G.load(tmp_path / "a.json")
    weakened = copy.deepcopy(data)
    q17 = next(i for i in weakened["interpretations"] if i["question_id"] == "q17")
    q17["requirements"][1]["mandatory"] = False
    (tmp_path / "b.json").write_text(json.dumps(weakened))
    with pytest.raises(ValueError, match="q17"):
        G.load(tmp_path / "b.json")


# --------------------------------------------------------------------------
# schema validation rules
# --------------------------------------------------------------------------
def _interp(fixtures, qid):
    return copy.deepcopy(next(fx["interpretation"] for fx in fixtures if fx["gold"]["query_id"] == qid))


@pytest.mark.parametrize("qid, mutate, expected", [
    ("q09", lambda i: i["requirements"][0].update(state="PROVED"), "unknown field state"),
    ("q09", lambda i: i.update(schema_version=2), "schema_version"),
    ("q09", lambda i: i["requirements"][0]["subject"].update(mention="router nesting"), "verbatim span"),
    ("q09", lambda i: i["requirements"][0]["scope"].update(role="production"), "evidence_span"),
    ("q09", lambda i: i["requirements"][0]["scope"].update(direct_only=True, evidence_span="inside another"),
     "direct_only only applies"),
    ("q09", lambda i: i["requirements"][0].update(evidence_source="runtime_telemetry"), "kind 'external'"),
    ("q09", lambda i: i["requirements"].append(copy.deepcopy(i["requirements"][0])), "duplicate id"),
    ("q09", lambda i: i.update(requirements=[]), "at least one requirement"),
    ("q09", lambda i: i.update(interpreter={"kind": "model"}), "model_id and prompt_version"),
    ("q15", lambda i: i["requirements"][0]["subject"].update(refers_to="r2"), "earlier requirement"),
    ("q19", lambda i: i.pop("clarification"), "requires a clarification block"),
    ("q19", lambda i: i["requirements"][0]["subject"].update(proposed_names=["get"]), "under clarification"),
    ("q19", lambda i: i["clarification"].update(interpretations=i["clarification"]["interpretations"][:1]),
     "at least two interpretations"),
    ("q21", lambda i: i["clarification"]["interpretations"][3].update(evidence_source="static_code"),
     "external one names none"),
    ("q24", lambda i: i.update(answerability="answerable"), "answerable cannot have"),
    ("q09", lambda i: i.update(answerability="unanswerable"), "unanswerable requires"),
])
def test_interpretation_rules(fixtures, qid, mutate, expected):
    rec = _interp(fixtures, qid)
    mutate(rec)
    assert any(expected in p for p in S.validate_interpretation(rec)), S.validate_interpretation(rec)


@pytest.fixture(scope="module")
def baseline():
    return B.records()


def _plan(baseline, qid, mode="slice@13k"):
    return copy.deepcopy(baseline[mode][qid][0])


@pytest.mark.parametrize("qid, mutate, expected", [
    ("q19", lambda p: p.update(decision="retrieve"), "every mandatory requirement resolved"),
    ("q24", lambda p: p["requirements"][0].update(retrieval={"op": "slice", "budget_tokens": 4000}),
     "needs a resolved subject"),
    ("q19", lambda p: p["requirements"][0]["resolution"].update(truncated=True), "truncated must be true exactly"),
    ("q19", lambda p: p["requirements"][0]["resolution"].update(match_count=60), "truncated must be true exactly"),
    ("q09", lambda p: p["requirements"][0]["retrieval"].update(budget_tokens=100), "budget_tokens must be in"),
    ("q09", lambda p: p["requirements"][0]["resolution"].update(match_count=2), "exactly one match"),
    ("q09", lambda p: p["requirements"][0]["resolution"].pop("subject"), "subject is required exactly"),
    ("q09", lambda p: p["requirements"][0].update(retrieval={"op": "unsupported"}), "unsupported_need"),
    ("q09", lambda p: p.update(decision="clarify"), "decision clarify requires"),
    ("q22", lambda p: p["requirements"][0]["resolution"].update(match_count=1, candidates=["x.y"]), "has no matches"),
])
def test_plan_rules(baseline, qid, mutate, expected):
    rec = _plan(baseline, qid)
    mutate(rec)
    assert any(expected in p for p in S.validate_plan(rec)), S.validate_plan(rec)


def test_abstain_plan_must_not_retrieve(baseline):
    rec = _plan(baseline, "q24")
    rec["requirements"][0]["resolution"] = {"status": "resolved", "subject": "a.b", "candidates": ["a.b"],
                                            "match_count": 1, "truncated": False}
    rec["requirements"][0]["retrieval"] = {"op": "slice", "budget_tokens": 4000}
    assert "decision abstain must not retrieve" in S.validate_plan(rec)


@pytest.mark.parametrize("mutate, expected", [
    (lambda r: r["requirements"][0].update(state="PROVED", answer_contract="state"), "worst fact state"),
    (lambda r: r["requirements"][0].update(answer_contract="state"), "answer_contract must be"),
    (lambda r: r.update(question_state="PROVED"), "question_state"),
    (lambda r: r.update(complete_scope=False), "complete_scope"),
    (lambda r: r["requirements"][0]["facts"][0].update(state="MAYBE"), "not in"),
    (lambda r: r["requirements"][0].pop("token_count"), "records token_count"),
])
def test_proof_report_rules(baseline, mutate, expected):
    rec = copy.deepcopy(baseline["slice@4k"]["q01"][1])        # q01 r1: BOUNDED_MISSING, retrieved
    mutate(rec)
    assert any(expected in p for p in S.validate_proof_report(rec)), S.validate_proof_report(rec)


def test_out_of_scope_requirement_has_no_state(baseline):
    rec = copy.deepcopy(baseline["blast_radius@13k"]["q05"][1])
    assert rec["requirements"][0]["state"] is None and S.validate_proof_report(rec) == []
    rec["requirements"][0]["state"] = "PROVED"
    assert any("out of the tool's scope" in p for p in S.validate_proof_report(rec))


# --------------------------------------------------------------------------
# baseline records: a lossless re-shaping of proof_baseline.json
# --------------------------------------------------------------------------
def test_baseline_records_are_valid_and_keep_every_state(baseline):
    raw, _ = B.load_inputs()
    assert sorted(baseline) == sorted(raw["modes"]) and all(len(v) == 24 for v in baseline.values())
    for mode, per_q in baseline.items():
        for qid, (plan, rep) in per_q.items():
            assert S.validate_plan(plan) == [] and S.validate_proof_report(rep) == []
            src = raw["modes"][mode][qid]["requirements"]
            assert [(r["id"], r["state"]) for r in rep["requirements"]] == [(r["id"], r["state"]) for r in src]
            assert [(f["id"], f["state"], f["why"]) for r in rep["requirements"] for f in r["facts"]] == \
                   [(f["id"], f["state"], f["why"]) for r in src for f in r.get("facts", [])]


def test_baseline_records_decide_clarify_and_abstain_without_retrieval(baseline):
    for per_q in baseline.values():
        assert {q for q, (p, _) in per_q.items() if p["decision"] == "clarify"} == {"q19", "q20", "q21"}
        assert {q for q, (p, _) in per_q.items() if p["decision"] == "abstain"} == {"q22", "q23", "q24"}


# --------------------------------------------------------------------------
# metrics: denominators, unmeasured, violations
# --------------------------------------------------------------------------
def _reports(baseline):
    return {m: [r for _, r in qs.values()] for m, qs in baseline.items()}


def test_proof_state_and_budget_metrics_match_the_committed_baseline(baseline):
    by = {m["id"]: m for m in M.m8_proof_states(_reports(baseline), "t") + M.m12_budget(_reports(baseline), "t")}
    got = {k: (by[k]["numerator"], by[k]["denominator"]) for k in by if by[k]["status"] == "measured"}
    assert got == {"M8-slice@4k": (10, 28), "M8-slice@13k": (13, 28), "M8-blast_radius@4k": (7, 16),
                   "M8-blast_radius@13k": (9, 16), "M8-best-mode": (20, 28),
                   "M12-overflow-slice@4k": (14, 22), "M12-overflow-slice@13k": (6, 22),
                   "M12-overflow-blast_radius@4k": (9, 10), "M12-overflow-blast_radius@13k": (0, 10)}
    assert set(by["M8-best-mode"]["detail"]["not_proved"]) == {
        "q15/r2", "q17/r2", "q19/disambiguate", "q20/disambiguate", "q21/disambiguate",
        "q22/external", "q23/external", "q24/external"}
    assert by["M8-planned"]["status"] == "not_measured"


def test_unmeasured_metrics_are_never_zero():
    for m in M.unmeasured_in_p0() + [M.not_measured("X", "x", M.JUDGMENT, "because")]:
        assert m["status"] == "not_measured" and m["reason"]
        assert m["numerator"] is None and m["denominator"] is None and m["value"] is None
    assert M.measured("Y", "y", M.DETERMINISTIC, 0, 0)["value"] is None          # n/a, not 0


def test_oracle_self_check_and_missing_interpretations_count_as_misses(fixtures):
    interps = [fx["interpretation"] for fx in fixtures]
    assert M.m1_requirement_kind(interps, fixtures, "o")["numerator"] == 28
    partial = [i for i in interps if i["question_id"] != "q16"]
    m1 = M.m1_requirement_kind(partial, fixtures, "o")
    assert (m1["numerator"], m1["denominator"]) == (26, 28) and m1["detail"]["misses"] == ["q16/r1", "q16/r2"]
    m2 = M.m2_answerability(partial, fixtures, "o")
    assert (m2["numerator"], m2["denominator"]) == (23, 24)
    wrong = copy.deepcopy(interps)
    next(i for i in wrong if i["question_id"] == "q16")["requirements"][1]["kind"] = "impact"
    assert M.m6_decomposition(wrong, fixtures, "o")["detail"]["wrong"][0]["question"] == "q16"


def test_unanswerable_retrieval_is_a_gate_violation(fixtures, baseline):
    plans = [p for p, _ in baseline["slice@13k"].values()]
    assert M.m2_plan_gate(plans, fixtures, "b")["numerator"] == 0
    bad = copy.deepcopy(plans)
    q24 = next(p for p in bad if p["question_id"] == "q24")
    q24["decision"] = "retrieve"
    gate = M.m2_plan_gate(bad, fixtures, "b")
    assert (gate["numerator"], gate["denominator"], gate["detail"]["violations"]) == (1, 3, ["q24"])
    missing = M.m2_plan_gate([p for p in plans if p["question_id"] != "q23"], fixtures, "b")
    assert missing["denominator"] == 2 and missing["detail"]["missing_plans"] == ["q23"]


def test_subject_precision_ignores_gold_counterfactuals(fixtures, baseline):
    plans = [p for p, _ in baseline["slice@13k"].values()]
    assert M.m4_subject_precision(plans, fixtures, "b")["status"] == "not_measured"
    own = copy.deepcopy(plans)
    q12 = next(p for p in own if p["question_id"] == "q12")["requirements"][0]["resolution"]
    q12.update(subject="django.core.cache.backends.base.BaseCache.get",
               candidates=["django.core.cache.backends.base.BaseCache.get"], provenance="lexical")
    m4 = M.m4_subject_precision(own, fixtures, "b")
    assert (m4["numerator"], m4["denominator"]) == (0, 1)


def test_clarification_payload_coverage_and_missing_payloads(fixtures):
    interps = [fx["interpretation"] for fx in fixtures]
    assert M.m5_clarification(interps, fixtures, None, "o")[2]["status"] == "not_measured"
    payloads = {"q19": ["django.db.models.query.QuerySet.get", "django.core.cache.backends.base.BaseCache.get"],
                "q20": None, "q21": None}
    c = M.m5_clarification(interps, fixtures, payloads, "o")[2]
    assert (c["numerator"], c["denominator"]) == (2, 3) and c["detail"]["no_payload"] == ["q20", "q21"]
    assert M.m5_clarification(interps, fixtures, {"q19": None, "q20": None, "q21": None}, "o")[2]["status"] == \
           "not_measured"


def test_lexical_recall_metric_requires_every_question(recorded):
    partial = {"queries": {k: v for k, v in recorded["queries"].items() if k != "q15"}}
    assert M.m3_lexical_recall(partial, "r")[0]["status"] == "not_measured"
    by = {m["id"]: m for m in M.m3_lexical_recall(recorded, "r")}
    assert (by["M3-rank@10"]["numerator"], by["M3-rank@10"]["denominator"]) == (4, 10)
    assert (by["M3-interp@20"]["numerator"], by["M3-interp@20"]["denominator"]) == (6, 12)


def test_runner_labels_oracle_derived_metrics_as_self_checks(tmp_path):
    from harness.experiments.query_to_evidence.nlq import run as R
    res = R.run(tmp_path / "out", with_index=False)
    flagged = {m["id"] for m in res["metrics"] if m.get("oracle_self_check")}
    assert flagged == {"M1", "M2", "M5a", "M5b", "M6"}
    assert all("ORACLE SELF-CHECK" in m["caveat"] for m in res["metrics"] if m["id"] in flagged)
    # measured on real artifacts, not decided by the oracle: never flagged
    assert not any(m.get("oracle_self_check") for m in res["metrics"]
                   if m["id"].startswith(("M3", "M8", "M12", "M13", "M5c")))
    saved = json.loads((tmp_path / "out" / "p0_metrics.json").read_text())
    assert {m["id"] for m in saved["metrics"] if m.get("oracle_self_check")} == flagged


def test_runner_refuses_canonical_output(tmp_path):
    from harness.experiments.query_to_evidence.nlq import run as R
    assert R.main(["--out", str(REPO / "harness/experiments/query_to_evidence/design/baseline/x")]) == 2
    assert R.main(["--out", str(REPO / "reports/nlq")]) == 2


# --------------------------------------------------------------------------
# real corpora: the extracted search reproduces the recorded probe output
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def indexes():
    from harness.experiments.query_to_evidence.run import build_engine
    from harness.tasks.loaders import _repo_root
    engines = {c: build_engine(str(_repo_root(c))) for c in ("fastapi", "django")}
    return engines, {c: L.recorded_index(e.builder, e.repo_root, c) for c, e in engines.items()}


@pytest.mark.slow
def test_lexical_search_reproduces_the_recorded_probe_output(indexes):
    from harness.experiments.query_to_evidence import dataset as D
    _, ix = indexes
    first = L.measure(ix, D.load_questions(), D.load_gold())
    assert L.diff_against_recorded(first) == []
    assert L.measure(ix, D.load_questions(), D.load_gold()) == first            # deterministic


@pytest.mark.slow
def test_todays_mcp_clarification_payload_is_truncated_and_misses_an_interpretation(indexes, fixtures):
    from harness.experiments.query_to_evidence.nlq.run import mcp_payloads
    from prism.query.locate import qualified_name_matches
    engines, _ = indexes
    payloads = mcp_payloads({c: e.builder for c, e in engines.items()}, fixtures)
    assert payloads["q20"] is None and payloads["q21"] is None                # mentions are not identifiers
    assert len(qualified_name_matches(engines["django"].builder, "get")) == 51 and len(payloads["q19"]) == 20
    interps = [fx["interpretation"] for fx in fixtures]
    c = M.m5_clarification(interps, fixtures, payloads, "mcp")[2]
    assert (c["numerator"], c["denominator"], c["detail"]["per_question"]["q19"]) == (2, 3, [True, True, False])
