"""P1: execute an EvidencePlan with the real MCP tool functions and score it
with the existing proof predicates (design/proof_eval.py `fact_state`).

The fact evaluation glue is the same as `proof_eval.evaluate`: parse the
rendered envelope (`envelope.parse_envelope`), take its warning codes, add
`CALLERS_TRUNCATED` when blast_radius truncated its caller list, and pass the
blast_radius caller set so a walked-but-unpacked caller is BUDGET_EXCEEDED.
`replay_matches_baseline` proves the glue equivalent by executing the P0
fixed-mode plans and comparing with the committed proof_baseline.json.

Combination: a requirement is evaluated only against the envelope of the
call that serves it (planner rule 6). Requirements with no call are
evaluated against an empty envelope, exactly as proof_eval does for
ambiguity/external requirements: no retrieval, so nothing is credited.
"""
from __future__ import annotations

import hashlib

from harness.experiments.query_to_evidence import envelope as E
from harness.experiments.query_to_evidence.design.proof_eval import ANSWERABLE, STATES, fact_state, worst
from harness.experiments.query_to_evidence.nlq import schema as S
from harness.experiments.query_to_evidence.nlq.planner import group_calls

_EMPTY = {"nodes": {}, "edges": [], "warnings": []}
PROVED_STATES = ("PROVED", "PROVED_WITH_WARNING")


def run_call(engine, call: dict) -> dict:
    """One real tool call through the MCP functions on the harness index."""
    with E.registered_repo(engine) as server:
        try:
            if call["op"] == "slice":
                out = server.prism_slice(repo_path=engine.repo_root, seed_symbol=call["seed"],
                                         budget_tokens=call["budget_tokens"], task_type=call.get("task_type"))
            else:
                out = server.prism_blast_radius(repo_path=engine.repo_root, seed_symbol=call["seed"],
                                                budget_tokens=call["budget_tokens"],
                                                include_tests=call["include_tests"])
        except server.MCPError as exc:
            err = getattr(exc, "error", exc)
            return {"error": {"code": getattr(err, "code", None), "message": str(exc)}}
    return out


def _call_record(call: dict, resp: dict) -> dict:
    rec = {k: call[k] for k in ("id", "op", "seed", "budget_tokens", "serves")}
    rec["params"] = {k: call[k] for k in ("task_type", "include_tests") if k in call}
    if "error" in resp:
        return {**rec, "ok": False, "error": resp["error"]}
    parsed = E.parse_envelope(resp["envelope"])
    warns = sorted({w["code"] for w in parsed["warnings"]})
    rec.update(ok=True, token_count=resp["token_count"], within_budget=resp["token_count"] <= call["budget_tokens"],
               warnings=warns, budget_overflow="BUDGET_OVERFLOW" in warns,
               nodes=len(parsed["nodes"]), edges=len(parsed["edges"]),
               envelope_sha256=hashlib.sha256(resp["envelope"].encode()).hexdigest())
    if call["op"] == "blast_radius":
        rec.update(callers_total=resp["callers_total"], callers_truncated=resp["callers_truncated"],
                   callers_in_context=len(resp["callers_in_context"]))
    return rec


def _evidence(call: dict, resp: dict) -> tuple[dict, set, set | None]:
    parsed = E.parse_envelope(resp["envelope"])
    warns = {w["code"] for w in parsed["warnings"]}
    callers = {c["symbol"] for c in resp.get("callers", [])} if call["op"] == "blast_radius" else None
    if callers is not None and resp.get("callers_truncated"):
        warns.add("CALLERS_TRUNCATED")
    return parsed, warns, callers


def _facts(mreq: dict, builder, parsed, warns, callers) -> list[dict]:
    return [{"id": f["id"], "type": f["type"], **fact_state(f, builder, parsed, warns, callers)} for f in mreq["facts"]]


def scope_compliance(note: dict | None, retrieved: bool) -> dict:
    """Scope-compliance record for one requirement from the planner's scope
    note (`planner._scope`): which limits the retrieval actually enforced."""
    if not retrieved:
        return {"status": "not_applicable", "not_enforced": []}
    if note is None:
        raise ValueError("a retrieved requirement needs the planner's scope note")
    if note["not_enforced"]:
        return {"status": "not_enforced", "not_enforced": list(note["not_enforced"])}
    return {"status": "enforced" if note["enforced"] else "no_limits", "not_enforced": []}


def execute(ev: dict, engine, mq: dict, scope: dict | None = None) -> dict:
    """Run every distinct call of `ev` once and score each requirement of the
    matrix question `mq` (facts come from the matrix, unmodified).

    `scope`: requirement id -> the planner's scope note (or None). When given,
    every requirement records `scope_compliance` next to its proof state; the
    state itself is never changed by it. When omitted (fixed-mode replay, which
    has no scope information), the field is left out."""
    builder = engine.builder
    calls = group_calls(ev)
    responses = {c["id"]: run_call(engine, c) for c in calls}
    serving = {rid: c for c in calls for rid in c["serves"]}
    mreqs = {r["id"]: r for r in mq["requirements"]}
    reqs = []
    for pr in ev["requirements"]:
        mreq = mreqs[pr["id"]]
        c = serving.get(pr["id"])
        if c is not None and "error" in responses[c["id"]]:
            r = {"id": pr["id"], "kind": pr["kind"], "scope": "tool error", "state": "INDEX_MISSING", "facts": [],
                 "error": responses[c["id"]]["error"]["message"]}
        elif c is not None:
            resp = responses[c["id"]]
            parsed, warns, callers = _evidence(c, resp)
            facts = _facts(mreq, builder, parsed, warns, callers)
            r = {"id": pr["id"], "kind": pr["kind"], "scope": "retrieved", "facts": facts,
                 "state": worst(f["state"] for f in facts), "token_count": resp["token_count"],
                 "budget": c["budget_tokens"], "budget_overflow": "BUDGET_OVERFLOW" in warns}
        elif ev["decision"] == "retrieve" and pr["retrieval"]["op"] == "none":
            r = {"id": pr["id"], "kind": pr["kind"], "scope": "out of tool scope", "state": None, "facts": []}
        else:
            facts = _facts(mreq, builder, _EMPTY, set(), None)
            r = {"id": pr["id"], "kind": pr["kind"], "scope": "no retrieval", "facts": facts,
                 "state": worst(f["state"] for f in facts)}
        r["answer_contract"] = "n/a" if r["state"] is None else ANSWERABLE.get(r["state"], "abstain")
        if scope is not None:
            r["scope_compliance"] = scope_compliance(scope.get(pr["id"]), r["scope"] == "retrieved")
        reqs.append(r)
    in_scope = [r["state"] for r in reqs if r["state"] is not None]
    report = {"schema": S.PROOF, "schema_version": S.SCHEMA_VERSION, "question_id": ev["question_id"],
              "mode": ev.get("mode", "planned"), "requirements": reqs,
              "question_state": worst(in_scope) if in_scope else None, "complete_scope": len(in_scope) == len(reqs)}
    diagnostics = {}
    for pr in ev["requirements"]:
        if pr["retrieval"]["op"] == "unsupported" and calls:
            # NOT counted: what the question's other envelopes would give this requirement's facts
            best = {}
            for c in calls:
                if "error" in responses[c["id"]]:
                    continue
                for f in _facts(mreqs[pr["id"]], builder, *_evidence(c, responses[c["id"]])):
                    prev = best.get(f["id"])
                    if prev is None or STATES.index(f["state"]) > STATES.index(prev["state"]):
                        best[f["id"]] = {**f, "from_call": c["id"]}
            diagnostics[pr["id"]] = {"note": "diagnostic only, not counted: facts scored against the question's "
                                             "other envelopes", "facts": list(best.values()),
                                     "state": worst(f["state"] for f in best.values()) if best else None}
    return {"calls": [_call_record(c, responses[c["id"]]) for c in calls], "proof_report": report,
            "unsupported_diagnostics": diagnostics}


def disposition(plan_note: dict, rep_req: dict) -> str:
    d = plan_note["disposition"]
    if d != "retrieve":
        return d
    if rep_req["scope"] == "tool error":
        return "tool_error"
    return {"PROVED": "proved", "PROVED_WITH_WARNING": "proved_with_warning"}.get(
        rep_req["state"], f"retrieved_not_proved:{rep_req['state']}")


def compare(planned: dict[str, dict], baseline_reports: dict[str, list[dict]]) -> dict:
    """Per requirement: planned state vs each fixed mode and vs the best fixed
    mode (a per-requirement upper bound no single fixed mode achieves).
    `planned`: question id -> ProofReport. Severity order is proof_eval's STATES."""
    rank = {s: i for i, s in enumerate(STATES)}               # higher = better
    modes = {m: {(r["question_id"], q["id"]): q["state"] for r in reps for q in r["requirements"]}
             for m, reps in baseline_reports.items()}
    rows = []
    for qid in sorted(planned):
        for r in planned[qid]["requirements"]:
            key = (qid, r["id"])
            per_mode = {m: modes[m].get(key) for m in sorted(modes)}
            in_scope = {m: s for m, s in per_mode.items() if s is not None}
            best = max(in_scope.values(), key=lambda s: rank[s]) if in_scope else None
            best_modes = sorted(m for m, s in in_scope.items() if s == best)
            p = r["state"]
            if p is None or best is None:
                verdict = "not_comparable"
            else:
                verdict = ("improved" if rank[p] > rank[best] else "regressed" if rank[p] < rank[best]
                           else "unchanged")
            rows.append({"question": qid, "requirement": r["id"], "planned": p, "best": best,
                         "best_modes": best_modes, "per_mode": per_mode, "vs_best": verdict,
                         "vs_mode": {m: (None if s is None or p is None else
                                         "improved" if rank[p] > rank[s] else "regressed" if rank[p] < rank[s]
                                         else "unchanged") for m, s in per_mode.items()}})
    return {"rows": rows}


def replay_matches_baseline(engines: dict, matrix: dict, baseline: dict, records: dict) -> list[str]:
    """Execute the P0 fixed-mode plans (baseline_records) through `execute`
    and compare every requirement state, fact state/why, token count and
    overflow flag with proof_baseline.json. Empty list = equivalent glue."""
    problems = []
    for mode, per_q in sorted(records.items()):
        for qid, (ev, _) in sorted(per_q.items()):
            mq = matrix[qid]
            got = execute(ev, engines[mq["corpus"]], mq)["proof_report"]["requirements"]
            want = baseline["modes"][mode][qid]["requirements"]
            for g, w in zip(got, want):
                gk = (g["id"], g["state"], [(f["id"], f["state"], f["why"]) for f in g["facts"]],
                      g.get("token_count"), g.get("budget_overflow"))
                wk = (w["id"], w["state"], [(f["id"], f["state"], f["why"]) for f in w.get("facts", [])],
                      w.get("token_count"), w.get("budget_overflow"))
                if gk != wk:
                    problems.append(f"{mode}/{qid}/{w['id']}: replay {gk[1]} != baseline {wk[1]}")
            if len(got) != len(want):
                problems.append(f"{mode}/{qid}: {len(got)} requirements != {len(want)}")
    return problems
