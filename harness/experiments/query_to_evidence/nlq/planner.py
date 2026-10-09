"""P1 deterministic planner: QuestionInterpretation -> EvidencePlan.

Pure and rule-based; no model, no ranking. Every decision comes from the
tables below and the index lookup (`resolve`), and is written into the plan
with its reason.

Rules
  1. Operation by requirement kind (OPERATION_BY_KIND):
       behavior     -> prism.slice, task_type "debug"
       impact/tests -> prism.blast_radius
       concurrency  -> unsupported: state_reads_and_definition
       absence      -> unsupported: exhaustive_absence
       ambiguity    -> none (the question is clarified)
       external     -> none (the question is abstained)
  2. Every call uses CALL_BUDGET tokens (the blast_radius default and the
     harness retrieval budget). A question's cost is the sum of its distinct calls.
  3. Scope limits are applied only where a tool argument enforces them
     (verified against src/prism/mcp/server.py and packer/candidate_index.py):
       role=production on blast_radius -> include_tests=False (enforced:
                         test callers are neither walked nor packed);
       role=tests                       -> no tool argument; include_tests=True,
                         production callers are delivered too (not enforced);
       direct_only                      -> no tool argument (blast_radius walks 6
                         hops, slice has no caller filter; not enforced);
       any limit on slice               -> no tool argument (not enforced).
     An unenforced limit never changes a fact predicate; it is recorded.
  4. Subjects: a proposed name is resolved exactly as the MCP seed check does
     (`qualified_name_matches`: exact or whole-segment suffix). One match
     resolves; several make the requirement ambiguous; none, or no proposed
     name, is not_found. Nothing is guessed.
  5. Decision: clarify if any mandatory requirement is ambiguous or of kind
     ambiguity; else abstain if any mandatory requirement is external or its
     subject is not_found; else retrieve. A clarify / abstain plan retrieves
     nothing (schema rule).
  6. Combination: requirements whose calls are identical (op, subject,
     budget, task_type, include_tests) share one call; otherwise each has its
     own. A requirement is evaluated only against its own call's envelope -
     no evidence is credited across calls. The question's state is the worst
     requirement state (ProofReport rule).
"""
from __future__ import annotations

from typing import Callable

from harness.experiments.query_to_evidence.nlq import schema as S

PLANNER_VERSION = 1
CALL_BUDGET = 13_000
SLICE_TASK_TYPE = "debug"
OPERATION_BY_KIND = {
    "behavior": "slice", "impact": "blast_radius", "tests": "blast_radius",
    "concurrency": "unsupported", "absence": "unsupported", "ambiguity": "none", "external": "none",
}
UNSUPPORTED = {
    "concurrency": ("state_reads_and_definition",
                    "needs READS_STATE -> attribute -> defining method; READS_STATE is not traversed "
                    "(graph/concrete_builder.py TRAVERSABLE_RELATIONS) and EdgeEntry.type cannot render it "
                    "(surface/models.py), so no existing tool returns this evidence"),
    "absence": ("exhaustive_absence",
                "a negative claim needs an exhaustive search with recorded bounds; no tool reports search "
                "bounds and a bounded walk cannot prove absence (DESIGN.md section 5)"),
}

#: resolve(name) -> sorted list of indexed qualified names `name` denotes.
Resolver = Callable[[str], list]


def resolver_for(builder) -> Resolver:
    from prism.query.locate import qualified_name_matches
    return lambda name: [name] if name in builder.symbol_table else qualified_name_matches(builder, name)


def _resolve(req: dict, resolve: Resolver, oracle: bool, clarification: dict | None) -> dict:
    names = req["subject"]["proposed_names"]
    if req["kind"] == "ambiguity":
        # the candidates are the in-repo readings the interpretation lists, each checked against the index
        readings = [n for i in (clarification or {}).get("interpretations", []) for n in i["proposed_names"]]
        matches = sorted({m for n in readings for m in resolve(n)})
        if len(matches) < 2:
            raise ValueError(f"requirement {req['id']}: an ambiguity needs at least two indexed readings, "
                             f"found {matches}")
        return {"status": "ambiguous", "candidates": matches, "match_count": len(matches), "truncated": False}
    if req["kind"] == "external":
        return {"status": "not_applicable", "candidates": [], "match_count": 0, "truncated": False}
    if not names:
        return {"status": "not_found", "candidates": [], "match_count": 0, "truncated": False}
    matches = sorted({m for n in names for m in resolve(n)})
    if not matches:
        return {"status": "not_found", "candidates": [], "match_count": 0, "truncated": False}
    if len(matches) > 1:
        return {"status": "ambiguous", "candidates": matches, "match_count": len(matches), "truncated": False}
    # A name an oracle supplied came from gold: label it so (metrics.m4 then excludes it);
    # otherwise label how the index matched it.
    prov = "gold_counterfactual" if oracle else ("exact" if matches[0] in names else "partial_name")
    return {"status": "resolved", "subject": matches[0], "candidates": matches, "match_count": 1,
            "truncated": False, "provenance": prov}


def _scope(req: dict, op: str) -> dict:
    sc = req["scope"]
    out = {"role": sc["role"], "direct_only": sc["direct_only"], "enforced": [], "not_enforced": []}
    if sc["role"] == "production" and op == "blast_radius":
        out["enforced"].append("role=production via include_tests=False")
    elif sc["role"] != "any":
        out["not_enforced"].append(f"role={sc['role']}: {op} has no argument that restricts callers to "
                                   f"{sc['role']} code" + (" (include_tests=True also returns production callers)"
                                                           if op == "blast_radius" else ""))
    if sc["direct_only"]:
        out["not_enforced"].append(f"direct_only: {op} has no hop limit (blast_radius walks 6 hops)")
    return out


def plan(interp: dict, resolve: Resolver) -> dict:
    """{plan: EvidencePlan, calls: [...], requirements: {id: notes}} for one question."""
    reqs, notes = [], {}
    oracle = interp["interpreter"]["kind"] == "oracle_gold_derived"
    for r in interp["requirements"]:
        res = _resolve(r, resolve, oracle, interp.get("clarification"))
        reqs.append({"id": r["id"], "kind": r["kind"], "mandatory": r["mandatory"], "resolution": res,
                     "retrieval": {"op": "none"}})
        notes[r["id"]] = {"intended_op": OPERATION_BY_KIND[r["kind"]]}
    mand = [x for x in reqs if x["mandatory"]]
    if any(x["kind"] == "ambiguity" or x["resolution"]["status"] == "ambiguous" for x in mand):
        decision, why = "clarify", "a mandatory subject is ambiguous: clarify, never guess"
    elif any(x["kind"] == "external" for x in mand):
        decision, why = "abstain", "the evidence source is not the repository"
    elif any(x["resolution"]["status"] == "not_found" for x in mand):
        decision, why = "abstain", "a mandatory subject is not in the index"
    else:
        decision, why = "retrieve", "every mandatory subject resolved"

    for x, r in zip(reqs, interp["requirements"]):
        n = notes[x["id"]]
        op = n["intended_op"]
        if decision != "retrieve":
            x["retrieval"] = {"op": "none", "reason": f"question decision is {decision}: {why}"}
            n.update(disposition=decision, scope=None)
            continue
        if op == "unsupported":
            need, reason = UNSUPPORTED[x["kind"]]
            x["retrieval"] = {"op": "unsupported", "unsupported_need": need, "reason": reason}
            n.update(disposition="unsupported", scope=None)
            continue
        ret = {"op": op, "budget_tokens": CALL_BUDGET}
        if op == "slice":
            ret["task_type"] = SLICE_TASK_TYPE
        else:
            ret["include_tests"] = r["scope"]["role"] != "production"
        x["retrieval"] = ret
        n.update(disposition="retrieve", scope=_scope(r, op))
    ev = {"schema": S.PLAN, "schema_version": S.SCHEMA_VERSION, "question_id": interp["question_id"],
          "mode": "planned", "source": {"kind": "planner",
                                        "note": f"P1 planner v{PLANNER_VERSION} over {interp['interpreter']['kind']} "
                                                "interpretations"},
          "decision": decision, "requirements": reqs}
    calls = group_calls(ev)
    for c in calls:
        for rid in c["serves"]:
            notes[rid]["call"] = c["id"]
    for n in notes.values():
        n.setdefault("call", None)
    return {"plan": ev, "decision_reason": why, "calls": calls, "requirements": notes}


def call_key(req: dict) -> tuple | None:
    """Identity of the tool call a requirement needs; None if it needs none."""
    ret = req["retrieval"]
    if ret["op"] not in ("slice", "blast_radius"):
        return None
    return (ret["op"], req["resolution"]["subject"], ret["budget_tokens"], ret.get("task_type"),
            ret.get("include_tests"))


def group_calls(ev: dict) -> list[dict]:
    """Distinct calls of an EvidencePlan, in requirement order; identical calls are shared."""
    calls, ids = [], {}
    for r in ev["requirements"]:
        key = call_key(r)
        if key is None:
            continue
        if key not in ids:
            ids[key] = len(calls)
            op, seed, budget, tt, it = key
            calls.append({"id": f"c{len(calls) + 1}", "op": op, "seed": seed, "budget_tokens": budget,
                          **({"task_type": tt} if op == "slice" else {"include_tests": it}), "serves": []})
        calls[ids[key]]["serves"].append(r["id"])
    return calls
