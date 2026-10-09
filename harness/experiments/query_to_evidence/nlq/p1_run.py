"""P1 run: plan every gold-derived interpretation, execute the plans with the
real tools, score them with the existing proof predicates, and compare with
the four fixed modes of the committed proof baseline.

    TIKTOKEN_CACHE_DIR=... python -m harness.experiments.query_to_evidence.nlq.p1_run --out <scratch dir> [--replay]

--replay additionally re-executes the P0 fixed-mode plans through the same
executor and requires them to equal proof_baseline.json (glue equivalence).
Writes only under --out; never into design/baseline, dataset or reports.
No model is called.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from harness.experiments.query_to_evidence.nlq import baseline_records as B
from harness.experiments.query_to_evidence.nlq import gold_fixtures as G
from harness.experiments.query_to_evidence.nlq import metrics as M
from harness.experiments.query_to_evidence.nlq import plan_exec as X
from harness.experiments.query_to_evidence.nlq import planner as P
from harness.experiments.query_to_evidence.nlq import schema as S
from harness.experiments.query_to_evidence.nlq.run import PROTECTED

TOTAL_REQUIREMENTS = 28
#: What a P1 result is - and is not.
CHARACTERIZATION = ("deterministic evidence planning and retrieval GIVEN gold-derived (oracle) interpretations: "
                    "subjects, requirement kinds, scope limits, ambiguity and evidence sources all come from gold. "
                    "Proof states are measured with the real tools and the existing predicates; decision, "
                    "clarification, abstention and routing results are oracle self-checks. This is not "
                    "end-to-end natural-language question understanding.")
#: Metrics decided entirely by the oracle interpretation (true by construction).
ORACLE_SELF_CHECK_IDS = ("M2-gate-planned", "M5a-planned", "M5b-planned", "M2-planned-abstain")
SCOPE_COMPLIANT = ("no_limits", "enforced")


def _dump(x) -> str:
    return json.dumps(x, indent=1, sort_keys=True) + "\n"


def make_plans(fixtures: list[dict], builders: dict) -> dict:
    resolvers = {c: P.resolver_for(b) for c, b in builders.items()}
    return {fx["gold"]["query_id"]: P.plan(fx["interpretation"], resolvers[fx["interpretation"]["corpus"]])
            for fx in fixtures}


def execute_all(plans: dict, engines: dict, fixtures: list[dict]) -> dict:
    gold = {fx["gold"]["query_id"]: fx["gold"] for fx in fixtures}
    return {qid: X.execute(p["plan"], engines[gold[qid]["corpus"]], gold[qid],
                           scope={rid: n["scope"] for rid, n in p["requirements"].items()})
            for qid, p in sorted(plans.items())}


def summarize(plans: dict, executed: dict, fixtures: list[dict], baseline_reports: dict) -> dict:
    gold = {fx["gold"]["query_id"]: fx["gold"] for fx in fixtures}
    rows = []
    for qid in sorted(plans):
        rep = {r["id"]: r for r in executed[qid]["proof_report"]["requirements"]}
        for pr in plans[qid]["plan"]["requirements"]:
            note = plans[qid]["requirements"][pr["id"]]
            r = rep[pr["id"]]
            call = next((c for c in executed[qid]["calls"] if pr["id"] in c["serves"]), None)
            rows.append({"question": qid, "requirement": pr["id"], "kind": pr["kind"],
                         "decision": plans[qid]["plan"]["decision"],
                         "op": pr["retrieval"]["op"], "subject": pr["resolution"].get("subject"),
                         "resolution": pr["resolution"]["status"],
                         "params": {k: v for k, v in pr["retrieval"].items() if k != "op"},
                         "scope": note.get("scope"), "call": note.get("call"),
                         "executed": None if call is None else call["ok"],
                         "token_count": None if call is None else call.get("token_count"),
                         "budget_overflow": None if call is None else call.get("budget_overflow"),
                         "callers_truncated": None if call is None else call.get("callers_truncated"),
                         "state": r["state"], "disposition": X.disposition(note, r),
                         # None = not recorded (e.g. a report without scope information): never counted as compliant
                         "scope_compliance": (r.get("scope_compliance") or {}).get("status"),
                         "facts": {"total": len(r["facts"]),
                                   "proved": sum(f["state"] in X.PROVED_STATES for f in r["facts"]),
                                   "states": dict(Counter(f["state"] for f in r["facts"]))}})
    disp = Counter(r["disposition"] for r in rows)
    proved = [r for r in rows if r["state"] in X.PROVED_STATES]
    proved_compliant = [r for r in proved if r["scope_compliance"] in SCOPE_COMPLIANT]
    proved_unenforced = sorted(f"{r['question']}/{r['requirement']}" for r in proved
                               if r["scope_compliance"] == "not_enforced")
    proved_unknown = sorted(f"{r['question']}/{r['requirement']}" for r in proved
                            if r["scope_compliance"] not in SCOPE_COMPLIANT + ("not_enforced",))
    planned_reports = {qid: e["proof_report"] for qid, e in executed.items()}
    cmp = X.compare(planned_reports, baseline_reports)
    calls = [c for e in executed.values() for c in e["calls"]]
    q_tokens = {qid: sum(c.get("token_count", 0) for c in e["calls"]) for qid, e in executed.items() if e["calls"]}
    gate_plans = [p["plan"] for p in plans.values()]
    metrics = [
        M.measured("M8-planned", "requirements proved (PROVED or PROVED_WITH_WARNING) in one planned run",
                   M.DETERMINISTIC, len(proved), len(rows), detail={
                       "PROVED": sum(r["state"] == "PROVED" for r in rows),
                       "PROVED_WITH_WARNING": sum(r["state"] == "PROVED_WITH_WARNING" for r in rows),
                       "proved_scope_not_enforced": proved_unenforced,
                       "proved_scope_not_recorded": proved_unknown},
                   inputs="planned run over oracle interpretations"),
        M.measured("M8-planned-scope-compliant",
                   "requirements proved AND retrieved with every scope limit enforced (or none stated)",
                   M.DETERMINISTIC, len(proved_compliant), len(rows),
                   detail={"excluded_scope_not_enforced": proved_unenforced,
                           "excluded_scope_not_recorded": proved_unknown},
                   inputs="planned run over oracle interpretations"),
        M.m2_plan_gate(gate_plans, fixtures, "planned plans", label="planned"),
        M.measured("M5a-planned", "plans that clarify ambiguous questions", M.DETERMINISTIC,
                   sum(plans[q]["plan"]["decision"] == "clarify" for q in plans if gold[q]["answerability"] == "ambiguous"),
                   sum(gold[q]["answerability"] == "ambiguous" for q in plans), inputs="planned plans"),
        M.measured("M5b-planned", "plans that clarify an answerable question (violations)", M.DETERMINISTIC,
                   sum(plans[q]["plan"]["decision"] == "clarify" for q in plans if gold[q]["answerability"] == "answerable"),
                   sum(gold[q]["answerability"] == "answerable" for q in plans), inputs="planned plans"),
        M.measured("M2-planned-abstain", "plans that abstain on unanswerable questions", M.DETERMINISTIC,
                   sum(plans[q]["plan"]["decision"] == "abstain" for q in plans if gold[q]["answerability"] == "unanswerable"),
                   sum(gold[q]["answerability"] == "unanswerable" for q in plans), inputs="planned plans"),
        M.measured("M12-overflow-planned", "planned calls whose envelope overflowed its budget", M.DETERMINISTIC,
                   sum(bool(c.get("budget_overflow")) for c in calls), len(calls),
                   detail={"calls_over_budget_tokens": sum(not c.get("within_budget", True) for c in calls),
                           "question_tokens": q_tokens,
                           "questions_over_13k_total": sorted(q for q, t in q_tokens.items() if t > P.CALL_BUDGET)},
                   inputs="planned run"),
        M.m4_subject_precision(gate_plans, fixtures, "planned plans"),
    ]
    metrics = [{**(M.oracle_self_check(m) if m["id"] in ORACLE_SELF_CHECK_IDS else m),
                "interpretation_source": "oracle_gold_derived"} for m in metrics]
    cmp["routing"] = {"oracle_self_check": True, "caveat": M.ORACLE_SELF_CHECK,
                      "note": "each requirement is routed by its gold kind (planner.OPERATION_BY_KIND), the same "
                              "kind-to-tool correspondence that scoped the fixed modes, so matching the best fixed "
                              "mode is expected by construction; proof states themselves are measured"}
    vs = Counter(r["vs_best"] for r in cmp["rows"])
    per_mode = {}
    for m in sorted(baseline_reports):
        c = Counter(r["vs_mode"][m] or "not_comparable" for r in cmp["rows"])
        per_mode[m] = dict(c)
    return {"characterization": CHARACTERIZATION,
            "rows": rows, "dispositions": dict(sorted(disp.items())), "metrics": metrics,
            "proved_with_unenforced_scope": proved_unenforced,
            "comparison": cmp, "vs_best": dict(vs), "vs_mode": per_mode,
            "accounted": {"requirements": len(rows), "expected": TOTAL_REQUIREMENTS,
                          "all_accounted": len(rows) == TOTAL_REQUIREMENTS and all(r["disposition"] for r in rows)}}


def run(out: Path, replay: bool) -> dict:
    from harness.experiments.query_to_evidence.run import build_engine
    from harness.tasks.loaders import _repo_root
    fixtures = G.load()
    engines = {c: build_engine(str(_repo_root(c))) for c in ("fastapi", "django")}
    builders = {c: e.builder for c, e in engines.items()}
    plans = make_plans(fixtures, builders)
    plans_again = make_plans(fixtures, builders)
    problems = {qid: S.validate_plan(p["plan"]) for qid, p in plans.items()}
    executed = execute_all(plans, engines, fixtures)
    executed_again = execute_all(plans_again, engines, fixtures)
    problems.update({f"{qid}/report": S.validate_proof_report(e["proof_report"]) for qid, e in executed.items()})
    recs = B.records()
    baseline_reports = {m: [r for _, r in qs.values()] for m, qs in recs.items()}
    summary = summarize(plans, executed, fixtures, baseline_reports)
    summary["metrics"].append(M.m13_reproducible({"plans": (_dump(plans), _dump(plans_again)),
                                                   "execution": (_dump(executed), _dump(executed_again))}))
    result = {"characterization": CHARACTERIZATION,
              "problems": {k: v for k, v in problems.items() if v}, "planner_version": P.PLANNER_VERSION,
              "call_budget": P.CALL_BUDGET, "summary": summary}
    if replay:
        baseline, matrix = B.load_inputs()
        result["replay_vs_proof_baseline"] = X.replay_matches_baseline(engines, matrix, baseline, recs)
    out.mkdir(parents=True, exist_ok=True)
    (out / "p1_plans.json").write_text(_dump(plans))
    (out / "p1_execution.json").write_text(_dump(executed))
    (out / "p1_result.json").write_text(_dump(result))
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="P1 deterministic evidence planning run")
    ap.add_argument("--out", required=True)
    ap.add_argument("--replay", action="store_true", help="also replay the fixed modes through the executor")
    args = ap.parse_args(argv)
    out = Path(args.out).resolve()
    if any(out == p or p in out.parents for p in PROTECTED):
        print(f"refusing to write into a canonical location: {out}", file=sys.stderr)
        return 2
    res = run(out, args.replay)
    s = res["summary"]
    print("NOTE:", CHARACTERIZATION)
    for r in s["rows"]:
        print(f"{r['question']}/{r['requirement']:<13} {r['kind']:<11} {r['op']:<12} {str(r['state']):<20} "
              f"{r['disposition']:<28} scope={str(r['scope_compliance']):<15} "
              f"best={next(x for x in s['comparison']['rows'] if x['question'] == r['question'] and x['requirement'] == r['requirement'])['best']}")
    for m in s["metrics"]:
        tag = " [ORACLE SELF-CHECK]" if m.get("oracle_self_check") else ""
        print(m["id"] + tag, f"{m['numerator']}/{m['denominator']}" if m["status"] == "measured" else f"NOT MEASURED ({m['reason']})")
    print("comparison vs fixed modes: routing is an [ORACLE SELF-CHECK];", s["comparison"]["routing"]["note"])
    print("dispositions:", s["dispositions"], "| vs best:", s["vs_best"], "| accounted:", s["accounted"])
    print("problems:", res["problems"] or "none")
    if "replay_vs_proof_baseline" in res:
        print("replay vs proof_baseline:", len(res["replay_vs_proof_baseline"]), "differences")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
