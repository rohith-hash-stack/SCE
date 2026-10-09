"""The committed proof baseline, expressed as EvidencePlan + ProofReport records.

`design/baseline/proof_baseline.json` already holds, per mode, what each tool
call retrieved for each requirement *given the gold subject* (a counterfactual,
not a production path) and the proof state of every fact. This adapter only
re-shapes it into the P0 schemas (read-only; nothing is re-run), so the
metric scorers and later planners consume one record format.
"""
from __future__ import annotations

import json
from pathlib import Path

from harness.experiments.query_to_evidence import envelope as E
from harness.experiments.query_to_evidence.design.proof_eval import ANSWERABLE, worst
from harness.experiments.query_to_evidence.nlq import schema as S

BASELINE = Path(__file__).resolve().parents[1] / "design" / "baseline" / "proof_baseline.json"
MATRIX = Path(__file__).resolve().parents[1] / "design" / "baseline" / "requirements_matrix.json"
SOURCE = {"kind": "proof_baseline_counterfactual",
          "note": "gold subject supplied as the seed (not a production path); re-shaped from proof_baseline.json"}


def load_inputs() -> tuple[dict, dict]:
    matrix = {q["query_id"]: q for q in json.loads(MATRIX.read_text())["questions"]}
    return json.loads(BASELINE.read_text()), matrix


def _tool_budget(mode: str) -> tuple[str, int]:
    tool, _, budget = mode.partition("@")
    return tool, int(budget.rstrip("k")) * 1000


def plan_for(mode: str, mq: dict, bq: dict) -> dict:
    tool, budget = _tool_budget(mode)
    by_id = {r["id"]: r for r in bq["requirements"]}
    reqs = []
    for r in mq["requirements"]:
        b = by_id[r["id"]]
        if r["kind"] == "ambiguity":
            cands = list(dict.fromkeys(mq["subject_candidates"]))
            res = {"status": "ambiguous", "candidates": cands, "match_count": len(cands), "truncated": False}
            ret = {"op": "none", "reason": "ambiguous subject: clarify, never guess"}
        elif r["kind"] == "external":
            res = {"status": "not_applicable", "candidates": [], "match_count": 0, "truncated": False}
            ret = {"op": "none", "reason": "evidence source is not the repository"}
        else:
            res = {"status": "resolved", "subject": r["subject"], "candidates": [r["subject"]], "match_count": 1,
                   "truncated": False, "provenance": "gold_counterfactual"}
            if b["scope"] == "out of tool scope":
                ret = {"op": "none", "reason": f"{r['kind']} requirement is outside {tool}'s scope"}
            else:
                ret = {"op": tool, "budget_tokens": budget}
                if tool == "slice":
                    tt = E.SLICE_TASK_TYPE.get(r.get("counterfactual_task_type"))
                    if tt:
                        ret["task_type"] = tt
                else:
                    ret["include_tests"] = True           # prism_blast_radius default, as envelope.call_tool runs it
        reqs.append({"id": r["id"], "kind": r["kind"], "mandatory": r["mandatory"], "resolution": res,
                     "retrieval": ret})
    decision = {"ambiguous": "clarify", "unanswerable": "abstain"}.get(mq["answerability"], "retrieve")
    return {"schema": S.PLAN, "schema_version": S.SCHEMA_VERSION, "question_id": mq["query_id"], "mode": mode,
            "source": dict(SOURCE), "decision": decision, "requirements": reqs}


def proof_report_for(mode: str, mq: dict, bq: dict) -> dict:
    fact_types = {f["id"]: f["type"] for r in mq["requirements"] for f in r["facts"]}
    reqs = []
    for b in bq["requirements"]:
        r = {"id": b["id"], "kind": b["kind"], "scope": b["scope"], "state": b["state"],
             "answer_contract": "n/a" if b["state"] is None else ANSWERABLE.get(b["state"], "abstain"),
             "facts": [{"id": f["id"], "type": f.get("type", fact_types[f["id"]]), "state": f["state"], "why": f["why"]}
                       for f in b.get("facts", [])]}
        for k in ("token_count", "budget", "budget_overflow", "error"):
            if k in b:
                r[k] = b[k]
        reqs.append(r)
    in_scope = [r["state"] for r in reqs if r["state"] is not None]
    return {"schema": S.PROOF, "schema_version": S.SCHEMA_VERSION, "question_id": mq["query_id"], "mode": mode,
            "requirements": reqs, "question_state": worst(in_scope) if in_scope else None,
            "complete_scope": len(in_scope) == len(reqs)}


def records(baseline: dict | None = None, matrix: dict | None = None) -> dict[str, dict[str, tuple[dict, dict]]]:
    """mode -> question id -> (EvidencePlan, ProofReport)."""
    if baseline is None or matrix is None:
        baseline, matrix = load_inputs()
    return {mode: {qid: (plan_for(mode, matrix[qid], bq), proof_report_for(mode, matrix[qid], bq))
                   for qid, bq in sorted(per_q.items())}
            for mode, per_q in sorted(baseline["modes"].items())}
