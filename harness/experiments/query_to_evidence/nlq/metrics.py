"""Metric scorers for the NLQ report's acceptance metrics (section 6.1, M1-M13).

Every scorer returns a `Metric` dict with an explicit `status`:
  measured      numerator / denominator from the given inputs (a denominator
                of 0 gives value None, shown "n/a" - never 0);
  not_measured  no numerator, no value, and a `reason`. A metric that was not
                measured is never reported as 0.
`method` separates what a program can decide (`deterministic`) from what needs
a human or model-assisted review (`judgment`).

Retrieval success is never used as a stand-in for proof or answer
correctness: M3 (candidate recall) only says a subject was *found*; proof
comes only from ProofReport states (M8), and answers are M11.
"""
from __future__ import annotations

import statistics
from collections import Counter

from harness.experiments.query_to_evidence.nlq import lexical_eval as L
from harness.experiments.query_to_evidence.nlq import schema as S

DETERMINISTIC, JUDGMENT = "deterministic", "judgment"
UNSEEDED_AND_COMPOUND = [f"q{i:02d}" for i in range(9, 19)]       # the 10 questions that name no symbol
AMBIGUOUS = ["q19", "q20", "q21"]
ANSWERED_STATES = ("PROVED", "PROVED_WITH_WARNING")


def measured(mid, name, method, num, den, *, detail=None, inputs=None) -> dict:
    return {"id": mid, "name": name, "method": method, "status": "measured", "numerator": num, "denominator": den,
            "value": (num / den) if den else None, "detail": detail or {}, "inputs": inputs}


def not_measured(mid, name, method, reason, *, detail=None) -> dict:
    return {"id": mid, "name": name, "method": method, "status": "not_measured", "numerator": None,
            "denominator": None, "value": None, "reason": reason, "detail": detail or {}}


#: Why a metric computed from gold-derived (oracle) interpretations says nothing
#: about natural-language understanding.
ORACLE_SELF_CHECK = ("ORACLE SELF-CHECK - not a capability measurement: the gold-derived interpretation already "
                     "states the answer (requirement kinds, subjects, ambiguity, evidence source), so this result "
                     "holds by construction and only checks that the scorer/planner rules are wired correctly")


def oracle_self_check(metric: dict) -> dict:
    """Mark a metric computed from oracle interpretations as a self-check."""
    return {**metric, "oracle_self_check": True, "caveat": ORACLE_SELF_CHECK}


def _by_qid(records):
    return {r["question_id"]: r for r in records}


# -- M1 requirement kind + subject ------------------------------------------------
def m1_requirement_kind(candidates: list[dict], fixtures: list[dict], inputs: str) -> dict:
    """Per gold requirement: the candidate interpretation of that question has
    a requirement of the same kind whose proposed names include the gold
    subject (or, for subject-less requirements, any of that kind). A missing
    interpretation counts as a miss, never as excluded."""
    cand = _by_qid(candidates)
    hits, den, misses = 0, 0, []
    for fx in fixtures:
        qid = fx["gold"]["query_id"]
        for g in fx["gold"]["requirements"]:
            den += 1
            ok = any(r["kind"] == g["kind"] and (not g.get("subject") or g["subject"] in r["subject"]["proposed_names"])
                     for r in cand.get(qid, {}).get("requirements", []))
            hits += ok
            if not ok:
                misses.append(f"{qid}/{g['id']}")
    return measured("M1", "requirement kind and subject", DETERMINISTIC, hits, den, detail={"misses": misses},
                    inputs=inputs)


# -- M2 decision / answerability -------------------------------------------------------
def m2_answerability(candidates: list[dict], fixtures: list[dict], inputs: str) -> dict:
    cand = _by_qid(candidates)
    hits, wrong = 0, []
    for fx in fixtures:
        qid, expected = fx["gold"]["query_id"], fx["gold"]["expected_behavior"]
        got = S.EXPECTED_BEHAVIOR.get(cand.get(qid, {}).get("answerability"))
        hits += got == expected
        if got != expected:
            wrong.append({"question": qid, "expected": expected, "got": got})
    return measured("M2", "answerability (answer / clarify / abstain)", DETERMINISTIC, hits, len(fixtures),
                    detail={"wrong": wrong}, inputs=inputs)


def m2_plan_gate(plans: list[dict], fixtures: list[dict], inputs: str, label: str = "") -> dict:
    """Hard gate: plans that retrieve for an unanswerable question (the q24
    false-certainty trap). The numerator counts *violations*; 0/3 passes."""
    unans = [fx["gold"]["query_id"] for fx in fixtures if fx["gold"]["answerability"] == "unanswerable"]
    by = _by_qid(plans)
    missing = [q for q in unans if q not in by]
    viol = [q for q in unans if q in by and (by[q]["decision"] == "retrieve" or any(
        r["retrieval"]["op"] in ("slice", "blast_radius") for r in by[q]["requirements"]))]
    return measured(f"M2-gate{'-' + label if label else ''}",
                    "retrievals planned for unanswerable questions (violations)", DETERMINISTIC,
                    len(viol), len(unans) - len(missing), detail={"violations": viol, "missing_plans": missing},
                    inputs=inputs)


# -- M3 subject candidate recall (lexical floor) -------------------------------------------
def m3_lexical_recall(lex: dict, inputs: str) -> list[dict]:
    """From a lexical measurement (lexical_eval.measure shape): subject recall
    on the 10 questions that name no symbol, and interpretation recall on the
    3 ambiguous ones. Questions absent from `lex` make the metric not_measured."""
    out = []
    missing = [q for q in UNSEEDED_AND_COMPOUND + AMBIGUOUS if q not in lex["queries"]]
    if missing:
        return [not_measured("M3", "subject candidate recall (lexical)", DETERMINISTIC,
                             f"lexical measurement lacks questions {missing}")]
    for mode, ks in (("rank", (10,)), ("expanded", L.EXPANSION_K)):
        for k in ks:
            r = L.subject_recall(lex, UNSEEDED_AND_COMPOUND, k, mode)
            per_q = {}
            for qid in UNSEEDED_AND_COMPOUND:
                row = lex["queries"][qid]
                per_q[qid] = {t: ({v: row[v]["rank"][t] for v in L.VARIANTS} if mode == "rank" else
                                  {v: row["expansion"][f"{v}@{k}"]["targets_found"][t] for v in L.VARIANTS})
                              for t in row["targets"]}
                if mode == "expanded":
                    per_q[qid]["_size"] = {v: row["expansion"][f"{v}@{k}"]["size"] for v in L.VARIANTS}
            out.append(measured(f"M3-{mode}@{k}", f"questions with the gold subject {'ranked' if mode == 'rank' else 'in the 1-hop-expanded set'} at k={k} (either variant)",
                                DETERMINISTIC, r["questions"][0], r["questions"][1],
                                detail={"subjects": r["subjects"], "per_question": per_q}, inputs=inputs))
    hits = den = 0
    per = {}
    for qid in AMBIGUOUS:
        row = lex["queries"][qid]
        flags = [a or b for a, b in zip(row["N"]["interp_in_top20"], row["NB"]["interp_in_top20"])]
        hits += sum(flags)
        den += len(flags)
        per[qid] = flags
    out.append(measured("M3-interp@20", "in-repo interpretations with a symbol in the lexical top 20 (either variant)",
                        DETERMINISTIC, hits, den, detail={"per_question": per}, inputs=inputs))
    return out


def m4_subject_precision(plans: list[dict], fixtures: list[dict], inputs: str) -> dict:
    """Resolved subjects that equal the gold subject. Plans whose subjects came
    from gold (counterfactual) are excluded: precision would be 1 by construction."""
    gold = {(fx["gold"]["query_id"], r["id"]): r.get("subject") for fx in fixtures for r in fx["gold"]["requirements"]}
    hits = den = 0
    wrong = []
    for p in plans:
        for r in p["requirements"]:
            res = r["resolution"]
            if res["status"] != "resolved" or res.get("provenance") == "gold_counterfactual":
                continue
            den += 1
            ok = res["subject"] == gold.get((p["question_id"], r["id"]))
            hits += ok
            if not ok:
                wrong.append(f"{p['question_id']}/{r['id']}: {res['subject']}")
    if den == 0:
        return not_measured("M4", "subject precision", DETERMINISTIC,
                            "no plan resolved a subject by itself (all resolutions are gold counterfactuals)")
    return measured("M4", "subject precision", DETERMINISTIC, hits, den, detail={"wrong": wrong}, inputs=inputs)


# -- M5 clarification ------------------------------------------------------------------------
def m5_clarification(candidates: list[dict], fixtures: list[dict], payloads: dict | None, inputs: str) -> list[dict]:
    """(a) clarify when gold is ambiguous, (b) no clarification on answerable
    questions, (c) coverage of in-repo gold interpretations by a clarification
    payload. `payloads`: question id -> list of candidate names, or None when no
    payload could be produced for that question (reported, not counted)."""
    cand = _by_qid(candidates)
    amb = [fx for fx in fixtures if fx["gold"]["answerability"] == "ambiguous"]
    ans = [fx for fx in fixtures if fx["gold"]["answerability"] == "answerable"]
    a = sum(cand.get(fx["gold"]["query_id"], {}).get("answerability") == "needs_clarification" for fx in amb)
    b = [fx["gold"]["query_id"] for fx in ans
         if cand.get(fx["gold"]["query_id"], {}).get("answerability") == "needs_clarification"]
    out = [measured("M5a", "clarify on ambiguous questions", DETERMINISTIC, a, len(amb), inputs=inputs),
           measured("M5b", "false clarifications on answerable questions (violations)", DETERMINISTIC, len(b), len(ans),
                    detail={"violations": b}, inputs=inputs)]
    if payloads is None:
        out.append(not_measured("M5c", "clarification payload covers each in-repo interpretation", DETERMINISTIC,
                                "no clarification payloads supplied"))
        return out
    hits = den = 0
    per, unmeasurable = {}, []
    for fx in amb:
        qid = fx["gold"]["query_id"]
        groups = [i["proposed_names"] for i in fx["interpretation"]["clarification"]["interpretations"]
                  if i["proposed_names"]]
        if payloads.get(qid) is None:
            unmeasurable.append(qid)
            continue
        shown = set(payloads[qid])
        flags = [bool(shown & set(g)) for g in groups]
        hits += sum(flags)
        den += len(flags)
        per[qid] = flags
    if den == 0:
        out.append(not_measured("M5c", "clarification payload covers each in-repo interpretation", DETERMINISTIC,
                                f"no payload for any ambiguous question ({unmeasurable})"))
    else:
        out.append(measured("M5c", "clarification payload covers each in-repo interpretation", DETERMINISTIC, hits, den,
                            detail={"per_question": per, "no_payload": unmeasurable}, inputs=inputs))
    return out


# -- M6 decomposition ----------------------------------------------------------------------------
def m6_decomposition(candidates: list[dict], fixtures: list[dict], inputs: str) -> dict:
    cand = _by_qid(candidates)
    comp = [fx for fx in fixtures if fx["gold"]["category"] == "compound"]
    wrong = []
    for fx in comp:
        qid = fx["gold"]["query_id"]
        got = Counter(r["kind"] for r in cand.get(qid, {}).get("requirements", []))
        if got != Counter(r["kind"] for r in fx["gold"]["requirements"]):
            wrong.append({"question": qid, "got": dict(got)})
    return measured("M6", "compound decomposition (requirement count and kinds)", DETERMINISTIC,
                    len(comp) - len(wrong), len(comp), detail={"wrong": wrong}, inputs=inputs)


# -- M8 / M12 from proof reports ------------------------------------------------------------------
def m8_proof_states(reports_by_mode: dict[str, list[dict]], inputs: str) -> list[dict]:
    """Per mode: requirements PROVED or PROVED_WITH_WARNING over in-scope
    requirements; and the best state per requirement across modes over all 28.
    (The single-planned-run version of M8 needs a planner: not measured here.)"""
    from harness.experiments.query_to_evidence.design.proof_eval import STATES
    out = []
    best: dict[tuple, str] = {}
    total = set()
    for mode, reports in sorted(reports_by_mode.items()):
        states = Counter()
        for rep in reports:
            for r in rep["requirements"]:
                total.add((rep["question_id"], r["id"]))
                if r["state"] is None:
                    continue
                states[r["state"]] += 1
                key = (rep["question_id"], r["id"])
                prev = best.get(key)
                best[key] = r["state"] if prev is None else min(prev, r["state"], key=lambda s: -STATES.index(s))
        den = sum(states.values())
        out.append(measured(f"M8-{mode}", f"in-scope requirements proved ({mode})", DETERMINISTIC,
                            states["PROVED"] + states["PROVED_WITH_WARNING"], den,
                            detail={"states": dict(sorted(states.items()))}, inputs=inputs))
    ok = sorted(k for k, s in best.items() if s in ANSWERED_STATES)
    not_ok = {f"{q}/{r}": best.get((q, r)) for q, r in sorted(total) if best.get((q, r)) not in ANSWERED_STATES}
    out.append(measured("M8-best-mode", "requirements proved in their best single mode", DETERMINISTIC, len(ok),
                        len(total), detail={"not_proved": not_ok}, inputs=inputs))
    out.append(not_measured("M8-planned", "requirements proved in one planned run", DETERMINISTIC,
                            "needs the P1 planner; the baseline runs one tool per mode"))
    return out


def m12_budget(reports_by_mode: dict[str, list[dict]], inputs: str) -> list[dict]:
    out = []
    for mode, reports in sorted(reports_by_mode.items()):
        retrieved = [r for rep in reports for r in rep["requirements"] if r["scope"] == "retrieved"]
        over = sum(r["budget_overflow"] for r in retrieved)
        toks = [r["token_count"] for r in retrieved]
        out.append(measured(f"M12-overflow-{mode}", f"retrieved requirements whose envelope overflowed ({mode})",
                            DETERMINISTIC, over, len(retrieved),
                            detail={"median_tokens": statistics.median(toks) if toks else None,
                                    "max_tokens": max(toks) if toks else None}, inputs=inputs))
    out.append(not_measured("M12-interpretation", "interpretation tokens and latency", DETERMINISTIC,
                            "no interpreter exists in P0 (oracle fixtures cost nothing)"))
    return out


# -- M13 reproducibility ----------------------------------------------------------------------------
def m13_reproducible(pairs: dict[str, tuple[str, str]]) -> dict:
    """`pairs`: artifact name -> (first serialization, second serialization)."""
    same = sorted(k for k, (a, b) in pairs.items() if a == b)
    return measured("M13", "artifacts byte-identical across two runs", DETERMINISTIC, len(same), len(pairs),
                    detail={"identical": same, "differ": sorted(set(pairs) - set(same))})


# -- not measurable in P0 --------------------------------------------------------------------------------
def unmeasured_in_p0() -> list[dict]:
    return [
        not_measured("M7", "fact recall of derived facts", DETERMINISTIC,
                     "no fact-derivation stage exists yet (P1/P2); facts are gold-enumerated in the matrix"),
        not_measured("M9", "evidence precision / irrelevant-context rate", DETERMINISTIC,
                     "proof_baseline.json does not record per-item tokens of delivered evidence; needs an envelope "
                     "re-run with item accounting (P1)"),
        not_measured("M9-review", "irrelevant-context calibration sample", JUDGMENT, "needs human review"),
        not_measured("M10", "proof-state correctness vs human adjudication", JUDGMENT, "needs human adjudication"),
        not_measured("M11", "end-to-end answer support", JUDGMENT,
                     "needs model answers (paid runs not authorised); existing controls-only grading in "
                     "reports/query_to_evidence is not recomputed or extended here"),
    ]
