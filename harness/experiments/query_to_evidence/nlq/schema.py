"""Versioned record schemas for question -> evidence evaluation (P0).

Three records, produced by different stages; only the first may ever come
from a model, and none of the others may be set by one:

  QuestionInterpretation  what the question asks: requirements, subject
                          *mentions*, scope limits, evidence source,
                          clarification. Never carries a proof state.
  EvidencePlan            how each requirement is resolved against the index
                          and which retrieval op serves it; the
                          retrieve / clarify / abstain decision.
  ProofReport             the deterministic proof state of every fact and
                          requirement (design/DESIGN.md section 5 states,
                          computed by design/proof_eval.py predicates).

Vocabulary is reused, not reinvented: requirement kinds and fact types are
the ones in design/baseline/requirements_matrix.json; proof states, the
worst-state rule and the state -> answer mapping are imported from
design/proof_eval.py. Two concepts are new because gold questions need them
(see README): `scope` (q16 "production code", q16/q17 "directly") and
`evidence_source` (q22-q24 need runtime / business data, not code).

Validators return a list of problems (empty = valid), like dataset.validate.
Every record is a closed JSON object: unknown keys are problems, so a field
that is not in the schema (e.g. a proof state inside an interpretation)
cannot slip through.
"""
from __future__ import annotations

from harness.experiments.query_to_evidence.design.proof_eval import ANSWERABLE, STATES, worst

SCHEMA_VERSION = 1
INTERPRETATION = "nlq.question_interpretation"
PLAN = "nlq.evidence_plan"
PROOF = "nlq.proof_report"

# -- vocabularies -------------------------------------------------------------
#: requirements_matrix.json `kind` values (dataset.REQUIREMENT_KINDS + the two
#: the matrix adds for ambiguous and unanswerable questions).
REQUIREMENT_KINDS = ("behavior", "impact", "tests", "concurrency", "absence", "ambiguity", "external")
#: requirements_matrix.json fact `type` values.
FACT_TYPES = ("call_edge", "symbol_body", "source_text", "negative_claim", "subject_resolution", "external")
ANSWERABILITY = ("answerable", "needs_clarification", "unanswerable")
#: gold `expected_behavior` for each interpretation-level answerability.
EXPECTED_BEHAVIOR = {"answerable": "answer", "needs_clarification": "clarify", "unanswerable": "abstain"}
EVIDENCE_SOURCES = ("static_code", "runtime_telemetry", "deployment_or_business_data", "external_service")
ROLES = ("any", "production", "tests")
#: `direct_only` has a demonstrated use only for these kinds (q16 r2 tests, q17 r2 absence).
DIRECT_ONLY_KINDS = ("tests", "absence")
INTERPRETER_KINDS = ("oracle_gold_derived", "agent", "model")

DECISIONS = ("retrieve", "clarify", "abstain")
RESOLUTION_STATUS = ("resolved", "ambiguous", "not_found", "not_applicable")
PROVENANCE = ("exact", "partial_name", "bare_name", "lexical", "graph_neighbor", "gold_counterfactual")
#: Retrieval ops: the two existing MCP tools, `none`, or `unsupported` when no
#: existing op can retrieve what the requirement needs (P2 work).
OPS = ("slice", "blast_radius", "none", "unsupported")
UNSUPPORTED_NEEDS = ("state_reads_and_definition", "exhaustive_absence")
BUDGET_RANGE = (500, 128_000)          # the MCP tools' own bounds (server.py)
PROOF_SCOPES = ("retrieved", "no retrieval", "out of tool scope", "tool error")
#: Whether the question's scope limits (role / direct_only) were enforced by the
#: retrieval that produced a requirement's evidence. Recorded next to - never
#: folded into - the proof state: the fact predicates are unchanged, but a
#: PROVED requirement whose scope was not enforced did not establish the scoped
#: claim (e.g. q16 r2 "which tests ... directly": the envelope also holds
#: production and indirect callers). `not_applicable`: nothing was retrieved.
SCOPE_COMPLIANCE = ("no_limits", "enforced", "not_enforced", "not_applicable")


# -- helpers --------------------------------------------------------------------
def _closed(obj, where: str, required: dict, optional: dict | None = None) -> list[str]:
    """Type-check a closed object: `required`/`optional` map key -> type(s)."""
    if not isinstance(obj, dict):
        return [f"{where}: expected an object"]
    optional = optional or {}
    p = [f"{where}: missing {k}" for k in required if k not in obj]
    p += [f"{where}: unknown field {k}" for k in sorted(set(obj) - set(required) - set(optional))]
    for k, t in {**required, **optional}.items():
        if k in obj and not isinstance(obj[k], t):
            p.append(f"{where}.{k}: expected {t}")
        if k in obj and t is int and isinstance(obj[k], bool):
            p.append(f"{where}.{k}: expected int, got bool")
    return p


def _enum(value, allowed, where: str) -> list[str]:
    return [] if value in allowed else [f"{where}: {value!r} not in {list(allowed)}"]


def _header(rec, kind: str) -> list[str]:
    p = []
    if rec.get("schema") != kind:
        p.append(f"schema must be {kind!r}")
    if rec.get("schema_version") != SCHEMA_VERSION:
        p.append(f"schema_version must be {SCHEMA_VERSION}")
    return p


def _names(lst, where: str) -> list[str]:
    if not all(isinstance(x, str) and x for x in lst):
        return [f"{where}: names must be non-empty strings"]
    return [f"{where}: duplicate names"] if len(set(lst)) != len(lst) else []


# -- QuestionInterpretation ---------------------------------------------------------
def validate_interpretation(rec: dict) -> list[str]:
    p = _closed(rec, "interpretation",
                {"schema": str, "schema_version": int, "question_id": str, "corpus": str, "question_text": str,
                 "interpreter": dict, "answerability": str, "requirements": list},
                {"clarification": dict})
    if p:
        return p
    p += _header(rec, INTERPRETATION)
    p += _enum(rec["answerability"], ANSWERABILITY, "answerability")
    it = rec["interpreter"]
    p += _closed(it, "interpreter", {"kind": str}, {"model_id": str, "prompt_version": str, "note": str})
    if not p:
        p += _enum(it["kind"], INTERPRETER_KINDS, "interpreter.kind")
        if it["kind"] != "oracle_gold_derived" and not (it.get("model_id") and it.get("prompt_version")):
            p.append("interpreter: agent/model interpretations must record model_id and prompt_version")
    text = rec["question_text"]
    if not rec["requirements"]:
        p.append("requirements: at least one requirement is required")
    seen: list[str] = []
    for i, r in enumerate(rec["requirements"]):
        w = f"requirements[{i}]"
        rp = _closed(r, w, {"id": str, "kind": str, "mandatory": bool, "subject": dict, "scope": dict,
                            "evidence_source": str})
        if rp:
            p += rp
            continue
        w = f"requirement {r['id']}"
        if r["id"] in seen:
            p.append(f"{w}: duplicate id")
        p += _enum(r["kind"], REQUIREMENT_KINDS, f"{w}.kind")
        p += _enum(r["evidence_source"], EVIDENCE_SOURCES, f"{w}.evidence_source")
        s = r["subject"]
        sp = _closed(s, f"{w}.subject", {"mention": str, "proposed_names": list}, {"refers_to": str})
        p += sp
        if not sp:
            if not s["mention"] or s["mention"] not in text:
                p.append(f"{w}.subject.mention must be a non-empty verbatim span of the question text")
            p += _names(s["proposed_names"], f"{w}.subject.proposed_names")
            if "refers_to" in s and s["refers_to"] not in seen:
                p.append(f"{w}.subject.refers_to must name an earlier requirement of this question")
            if r["kind"] == "ambiguity" and s["proposed_names"]:
                p.append(f"{w}: an ambiguity requirement lists its candidates under clarification, not proposed_names")
        sc = r["scope"]
        scp = _closed(sc, f"{w}.scope", {"role": str, "direct_only": bool}, {"evidence_span": str})
        p += scp
        if not scp:
            p += _enum(sc["role"], ROLES, f"{w}.scope.role")
            restricted = sc["role"] != "any" or sc["direct_only"]
            if restricted and not (sc.get("evidence_span") and sc["evidence_span"] in text):
                p.append(f"{w}.scope: a scope limit needs an evidence_span quoted verbatim from the question")
            if not restricted and "evidence_span" in sc:
                p.append(f"{w}.scope: evidence_span without a scope limit")
            if sc["direct_only"] and r["kind"] not in DIRECT_ONLY_KINDS:
                p.append(f"{w}.scope.direct_only only applies to {list(DIRECT_ONLY_KINDS)}")
        if (r["kind"] == "external") != (r["evidence_source"] != "static_code"):
            p.append(f"{w}: kind 'external' if and only if the evidence source is not static_code")
        seen.append(r["id"])
    p += _check_answerability(rec)
    return p


def _check_answerability(rec: dict) -> list[str]:
    p = []
    a = rec["answerability"]
    kinds = [r.get("kind") for r in rec["requirements"] if isinstance(r, dict) and r.get("mandatory")]
    clar = rec.get("clarification")
    if a == "needs_clarification":
        if clar is None:
            p.append("needs_clarification requires a clarification block")
        if "ambiguity" not in kinds:
            p.append("needs_clarification requires a mandatory ambiguity requirement")
    elif clar is not None:
        p.append("a clarification block is only allowed when answerability is needs_clarification")
    if a == "unanswerable" and (not kinds or any(k != "external" for k in kinds)):
        p.append("unanswerable requires every mandatory requirement to be external")
    if a == "answerable" and any(k in ("external", "ambiguity") for k in kinds):
        p.append("answerable cannot have a mandatory external or ambiguity requirement")
    if clar is not None:
        cp = _closed(clar, "clarification", {"interpretations": list, "resolving_information": str})
        p += cp
        if not cp:
            if len(clar["interpretations"]) < 2:
                p.append("clarification: at least two interpretations are required")
            for j, i in enumerate(clar["interpretations"]):
                w = f"clarification.interpretations[{j}]"
                ip = _closed(i, w, {"label": str, "proposed_names": list, "evidence_source": str})
                p += ip
                if not ip:
                    p += _enum(i["evidence_source"], EVIDENCE_SOURCES, f"{w}.evidence_source")
                    p += _names(i["proposed_names"], f"{w}.proposed_names")
                    if (i["evidence_source"] == "static_code") != bool(i["proposed_names"]):
                        p.append(f"{w}: an in-repo reading names symbols; an external one names none")
    return p


# -- EvidencePlan ----------------------------------------------------------------------
def validate_plan(rec: dict) -> list[str]:
    p = _closed(rec, "plan", {"schema": str, "schema_version": int, "question_id": str, "source": dict,
                              "decision": str, "requirements": list}, {"mode": str})
    if p:
        return p
    p += _header(rec, PLAN)
    p += _enum(rec["decision"], DECISIONS, "decision")
    p += _closed(rec["source"], "source", {"kind": str}, {"note": str})
    statuses, ops = [], []
    for i, r in enumerate(rec["requirements"]):
        w = f"requirements[{i}]"
        rp = _closed(r, w, {"id": str, "kind": str, "mandatory": bool, "resolution": dict, "retrieval": dict})
        if rp:
            p += rp
            continue
        w = f"requirement {r['id']}"
        p += _enum(r["kind"], REQUIREMENT_KINDS, f"{w}.kind")
        res, ret = r["resolution"], r["retrieval"]
        resp = _closed(res, f"{w}.resolution", {"status": str, "candidates": list, "match_count": int,
                                                "truncated": bool}, {"subject": str, "provenance": str})
        p += resp
        if not resp:
            st = res["status"]
            p += _enum(st, RESOLUTION_STATUS, f"{w}.resolution.status")
            p += _names(res["candidates"], f"{w}.resolution.candidates")
            if "provenance" in res:
                p += _enum(res["provenance"], PROVENANCE, f"{w}.resolution.provenance")
            if res["match_count"] < len(res["candidates"]):
                p.append(f"{w}.resolution: match_count below the number of listed candidates")
            if res["truncated"] != (res["match_count"] > len(res["candidates"])):
                p.append(f"{w}.resolution: truncated must be true exactly when match_count exceeds the listed candidates")
            if (st == "resolved") != ("subject" in res):
                p.append(f"{w}.resolution: a subject is required exactly when status is resolved")
            if st == "resolved" and (res["match_count"] != 1 or res["candidates"] != [res.get("subject")]):
                p.append(f"{w}.resolution: resolved means exactly one match, the subject")
            if st == "ambiguous" and res["match_count"] < 2:
                p.append(f"{w}.resolution: ambiguous needs at least two matches")
            if st in ("not_found", "not_applicable") and (res["match_count"] or res["candidates"]):
                p.append(f"{w}.resolution: {st} has no matches")
            if r["mandatory"]:
                statuses.append(st)
        retp = _closed(ret, f"{w}.retrieval", {"op": str},
                       {"budget_tokens": int, "task_type": str, "include_tests": bool, "unsupported_need": str,
                        "reason": str})
        p += retp
        if not retp:
            op = ret["op"]
            p += _enum(op, OPS, f"{w}.retrieval.op")
            if op in ("slice", "blast_radius"):
                if res.get("status") != "resolved":
                    p.append(f"{w}: a {op} retrieval needs a resolved subject (never a guess among candidates)")
                b = ret.get("budget_tokens")
                if b is None or not BUDGET_RANGE[0] <= b <= BUDGET_RANGE[1]:
                    p.append(f"{w}.retrieval.budget_tokens must be in {list(BUDGET_RANGE)}")
            if op == "unsupported" and ret.get("unsupported_need") not in UNSUPPORTED_NEEDS:
                p.append(f"{w}.retrieval.unsupported_need must be one of {list(UNSUPPORTED_NEEDS)}")
            if op != "unsupported" and "unsupported_need" in ret:
                p.append(f"{w}.retrieval: unsupported_need only with op 'unsupported'")
            if r["mandatory"]:
                ops.append(op)
    d = rec["decision"]
    if d == "clarify" and "ambiguous" not in statuses:
        p.append("decision clarify requires a mandatory ambiguous requirement")
    if d == "retrieve" and any(s != "resolved" for s in statuses):
        p.append("decision retrieve requires every mandatory requirement resolved")
    if d in ("clarify", "abstain") and any(o in ("slice", "blast_radius") for o in ops):
        p.append(f"decision {d} must not retrieve")
    return p


# -- ProofReport -----------------------------------------------------------------------
def validate_proof_report(rec: dict) -> list[str]:
    p = _closed(rec, "proof_report", {"schema": str, "schema_version": int, "question_id": str,
                                      "requirements": list, "question_state": (str, type(None)),
                                      "complete_scope": bool}, {"mode": str})
    if p:
        return p
    p += _header(rec, PROOF)
    in_scope = []
    for i, r in enumerate(rec["requirements"]):
        w = f"requirements[{i}]"
        rp = _closed(r, w, {"id": str, "kind": str, "scope": str, "state": (str, type(None)),
                            "answer_contract": str, "facts": list},
                     {"token_count": int, "budget": int, "budget_overflow": bool, "error": str,
                      "scope_compliance": dict})
        if rp:
            p += rp
            continue
        w = f"requirement {r['id']}"
        p += _enum(r["kind"], REQUIREMENT_KINDS, f"{w}.kind")
        p += _enum(r["scope"], PROOF_SCOPES, f"{w}.scope")
        if (r["state"] is None) != (r["scope"] == "out of tool scope"):
            p.append(f"{w}: state is null exactly when the requirement is out of the tool's scope")
        if r["state"] is not None:
            p += _enum(r["state"], STATES, f"{w}.state")
            in_scope.append(r["state"])
        expected_contract = "n/a" if r["state"] is None else ANSWERABLE.get(r["state"], "abstain")
        if r["answer_contract"] != expected_contract:
            p.append(f"{w}.answer_contract must be {expected_contract!r} for state {r['state']!r}")
        fstates = []
        for j, f in enumerate(r["facts"]):
            fw = f"{w}.facts[{j}]"
            fp = _closed(f, fw, {"id": str, "type": str, "state": str, "why": str})
            p += fp
            if not fp:
                p += _enum(f["type"], FACT_TYPES, f"{fw}.type") + _enum(f["state"], STATES, f"{fw}.state")
                fstates.append(f["state"])
        if fstates and all(s in STATES for s in fstates) and r["state"] != worst(fstates):
            p.append(f"{w}: state must be the worst fact state ({worst(fstates)})")
        if r["scope"] == "retrieved" and not {"token_count", "budget", "budget_overflow"} <= set(r):
            p.append(f"{w}: a retrieved requirement records token_count, budget and budget_overflow")
        if "scope_compliance" in r:
            p += _check_scope_compliance(r["scope_compliance"], r["scope"], f"{w}.scope_compliance")
    if rec["question_state"] != (worst(in_scope) if in_scope else None):
        p.append("question_state must be the worst in-scope requirement state")
    if rec["complete_scope"] != (len(in_scope) == len(rec["requirements"])):
        p.append("complete_scope must say whether every requirement was in scope")
    return p


def _check_scope_compliance(sc, scope: str, where: str) -> list[str]:
    p = _closed(sc, where, {"status": str, "not_enforced": list})
    if p:
        return p
    p += _enum(sc["status"], SCOPE_COMPLIANCE, f"{where}.status")
    if not all(isinstance(x, str) and x for x in sc["not_enforced"]):
        p.append(f"{where}.not_enforced: entries must be non-empty strings")
    if (sc["status"] == "not_enforced") != bool(sc["not_enforced"]):
        p.append(f"{where}: status is not_enforced exactly when an unenforced limit is listed")
    if (sc["status"] == "not_applicable") != (scope != "retrieved"):
        p.append(f"{where}: not_applicable exactly when nothing was retrieved")
    return p
