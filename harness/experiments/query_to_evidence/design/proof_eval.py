"""Research prototype (evaluation only; not production code).

Derives the query requirement model for every gold question
(`dataset/gold.json`, read-only) and applies the *proposed* deterministic
proof-state predicates (design/DESIGN.md section 5) to the envelopes the
real production tools return today (`envelope.py`: `prism_slice` /
`prism_blast_radius`). Nothing here changes retrieval; it only measures
whether today's delivered evidence would satisfy each predicate.

    TIKTOKEN_CACHE_DIR=<cl100k dir> python -m harness.experiments.query_to_evidence.design.proof_eval --out DIR

Writes DIR/requirements_matrix.json (static: question -> requirement ->
atomic facts -> predicates; no index needed) and DIR/proof_baseline.json
(measured: per fact/requirement/question proof state in each mode).
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from harness.experiments.query_to_evidence import dataset as D
from harness.experiments.query_to_evidence import envelope as E
from harness.experiments.query_to_evidence.accounting import edge_graph_status

# Severity order: the first state present among a requirement's mandatory
# facts is the requirement's state (worst wins); same rule for questions.
STATES = ["INDEX_MISSING", "STRUCTURAL_AMBIGUOUS", "DYNAMIC_UNRESOLVED", "BOUNDED_MISSING", "BUDGET_EXCEEDED",
          "PROVED_WITH_WARNING", "PROVED"]
ANSWERABLE = {"PROVED": "state", "PROVED_WITH_WARNING": "qualify"}   # everything else: abstain on that claim
WARNING_CODES = {"BUDGET_OVERFLOW", "TOKENIZER_FALLBACK", "LANGUAGE_TIER_2", "LANGUAGE_TIER_3"}
MODES = [m for m in E.MODES]


def worst(states):
    states = list(states)
    return min(states, key=STATES.index) if states else None


# --------------------------------------------------------------------------
# requirement model (static, from gold)
# --------------------------------------------------------------------------
def requirement_model(q: dict, g: dict) -> dict:
    """Question -> requirements -> atomic facts. Each fact names the
    evidence that can establish it and the predicate that must hold."""
    model = {"query_id": q["id"], "corpus": q["corpus"], "category": q["category"],
             "question_type": None, "answerability": g["answerability"], "expected_behavior": g["expected_behavior"],
             "subject_candidates": [], "requirements": []}
    if g["answerability"] == "ambiguous":
        model["question_type"] = "ambiguous"
        model["subject_candidates"] = [s for i in g["ambiguity"]["interpretations"] for s in i["symbols"]]
        model["requirements"].append({
            "id": "disambiguate", "kind": "ambiguity", "mandatory": True,
            "facts": [{"id": "single_subject", "type": "subject_resolution",
                       "candidates": model["subject_candidates"],
                       "predicate": "exactly one candidate survives the query's constraints",
                       "evidence": "symbol table (locate_symbol_by_name ambiguity)"}],
            "resolving_information": g["ambiguity"]["resolving_information"]})
        return model
    if g["answerability"] == "unanswerable":
        model["question_type"] = "external_evidence"
        model["requirements"].append({
            "id": "external", "kind": "external", "mandatory": True,
            "facts": [{"id": "external_data", "type": "external",
                       "predicate": "evidence source is indexed by Prism", "evidence": g["unanswerable_reason"]}]})
        return model
    kinds = {r["kind"] for r in g["requirements"]}
    model["question_type"] = ("compound" if len(g["requirements"]) > 1 else
                              {"impact": "multi_hop_impact", "behavior": "behavioral_multi_path"}.get(next(iter(kinds)), "other"))
    for r in g["requirements"]:
        if r.get("subject"):
            model["subject_candidates"].append(r["subject"])
        facts = []
        for e in r.get("relationship_paths", []):
            facts.append({"id": f"edge:{e['from']}->{e['to']}", "type": "call_edge", "from": e["from"], "to": e["to"],
                          "call_site": e["call_site"], "source_text": e["source_text"],
                          "predicate": "edge in graph AND both endpoints selected AND <edge> record rendered "
                                       "AND call-site text inside the caller's full body at the gold line",
                          "evidence": "<edge from to type> + caller <node> L0_full body + file:line"})
        edge_syms = {x for e in r.get("relationship_paths", []) for x in (e["from"], e["to"])}
        for s in D.required_symbols(r):
            if s not in edge_syms:
                facts.append({"id": f"symbol:{s}", "type": "symbol_body", "symbol": s,
                              "predicate": "symbol indexed AND selected AND delivered L0_full",
                              "evidence": "<node> L0_full body"})
        for ev in r.get("required_source_evidence", []):
            facts.append({"id": f"source:{ev['location']}", "type": "source_text", "symbol": ev["symbol"],
                          "location": ev["location"], "text": ev["text"],
                          "predicate": "text present in the symbol's full body AND the location lies in its range",
                          "evidence": "<node> L0_full body + file:line; class state may need READS_STATE -> attribute"})
        if r["kind"] == "absence":
            facts.append({"id": "negative", "type": "negative_claim", "claim": r.get("expected_conclusion"),
                          "search": r.get("absence_evidence"),
                          "predicate": "exhaustive search over a complete index (never satisfied by a bounded walk)",
                          "evidence": "complete caller/test enumeration with no truncation and no dynamic sentinels"})
        model["requirements"].append({"id": r["id"], "kind": r["kind"], "mandatory": True, "subject": r.get("subject"),
                                      "counterfactual_task_type": r.get("counterfactual_task_type"),
                                      "supporting_symbols": list(r.get("context_symbols", [])), "facts": facts})
    return model


# --------------------------------------------------------------------------
# fact predicates against a parsed production envelope
# --------------------------------------------------------------------------
def _in_range(builder, sym, location):
    info = builder.symbol_table.get(sym) if sym else None
    rel, _, line = location.partition(":")
    return bool(info and info.file.endswith("/" + rel) and info.line_range[0] <= int(line) <= info.line_range[1])


def fact_state(fact: dict, builder, parsed: dict, envelope_warnings: set, callers: set | None) -> dict:
    nodes = parsed["nodes"]
    warn = sorted(envelope_warnings & WARNING_CODES)
    if fact["type"] == "call_edge":
        frm, to = D.resolve_endpoint(builder, fact["from"]), D.resolve_endpoint(builder, fact["to"])
        if frm is None or to is None:
            return {"state": "INDEX_MISSING", "why": "endpoint not indexed"}
        g = edge_graph_status(builder, frm, to)
        if g["status"] == "ambiguous_sentinel":
            return {"state": "STRUCTURAL_AMBIGUOUS", "why": "call resolved to an <ambiguous:...> sentinel"}
        if g["status"] == "unresolved" and g.get("note"):
            return {"state": "DYNAMIC_UNRESOLVED", "why": "caller has dynamic-dispatch sentinels; edge not resolved"}
        if g["status"] in ("unresolved", "resolved_to_other_target", "endpoint_not_indexed"):
            return {"state": "INDEX_MISSING", "why": f"relationship not in graph ({g['status']})"}
        d = E.edge_delivery(parsed, frm, to, fact["source_text"])
        if not (d["from_selected"] and d["to_selected"]):
            missing = frm if not d["from_selected"] else to
            if callers is not None and missing == frm and frm in callers:
                return {"state": "BUDGET_EXCEEDED", "why": "caller enumerated by the walk but not packed"}
            return {"state": "BOUNDED_MISSING", "why": f"{'caller' if missing == frm else 'callee'} outside the selected set"}
        line_ok = _in_range(builder, frm, fact["call_site"])
        if d["edge_record"] and d["call_site_in_full_caller_body"] and line_ok:
            tentative = g["status"] == "tentative"
            return {"state": "PROVED_WITH_WARNING" if (tentative or warn) else "PROVED",
                    "why": ("tentative edge; " if tentative else "") + (",".join(warn) if warn else "edge+call site+line")}
        if d["edge_record"]:
            return {"state": "PROVED_WITH_WARNING", "why": "edge record only; call site not visible (caller stubbed)"}
        return {"state": "BUDGET_EXCEEDED", "why": "endpoints selected but no edge record / call site"}
    if fact["type"] == "symbol_body":
        sym = D.resolve_endpoint(builder, fact["symbol"]) if fact["symbol"].startswith("@") else fact["symbol"]
        if sym is None or sym not in builder.symbol_table:
            return {"state": "INDEX_MISSING", "why": "symbol not indexed"}
        n = nodes.get(sym)
        if n is None:
            return {"state": "BOUNDED_MISSING", "why": "symbol outside the selected set"}
        if n["compression"] != "L0_full":
            return {"state": "BUDGET_EXCEEDED", "why": "delivered only as a stub"}
        return {"state": "PROVED_WITH_WARNING" if warn else "PROVED", "why": ",".join(warn) or "full body"}
    if fact["type"] == "source_text":
        sym = fact["symbol"]
        if sym not in builder.symbol_table:
            return {"state": "INDEX_MISSING", "why": "symbol not indexed"}
        n = nodes.get(sym)
        if n is None:
            return {"state": "BOUNDED_MISSING", "why": "owning symbol outside the selected set"}
        if n["compression"] != "L0_full" or fact["text"] not in (n["body"] or ""):
            return {"state": "BUDGET_EXCEEDED", "why": "owning symbol stubbed or text absent"}
        ok = _in_range(builder, sym, fact["location"])
        return {"state": ("PROVED_WITH_WARNING" if warn else "PROVED") if ok else "PROVED_WITH_WARNING",
                "why": "text+location" if ok else "text present, location outside symbol range"}
    if fact["type"] == "negative_claim":
        return {"state": "BOUNDED_MISSING", "why": "absence cannot be proved by a bounded search"}
    if fact["type"] == "subject_resolution":
        return {"state": "STRUCTURAL_AMBIGUOUS", "why": f"{len(fact['candidates'])} candidate subjects"}
    if fact["type"] == "external":
        return {"state": "INDEX_MISSING", "why": "evidence source is not indexed by Prism"}
    raise ValueError(fact["type"])


# --------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------
def evaluate(engines: dict, questions: list[dict], gold: dict) -> dict:
    models = {q["id"]: requirement_model(q, gold[q["id"]]) for q in questions}
    out = {"modes": {}}
    cache = {}
    for tool, budget in MODES:
        name = E.mode_name(tool, budget)
        per_q = {}
        for q in questions:
            m = models[q["id"]]
            engine = engines[q["corpus"]]
            b = engine.builder
            reqs = []
            for r in m["requirements"]:
                if r["kind"] in ("ambiguity", "external") or r.get("subject") is None:
                    facts = [{"id": f["id"], **fact_state(f, b, {"nodes": {}, "edges": [], "warnings": []}, set(), None)}
                             for f in r["facts"]]
                    reqs.append({"id": r["id"], "kind": r["kind"], "scope": "no retrieval", "facts": facts,
                                 "state": worst(f["state"] for f in facts)})
                    continue
                if tool == "blast_radius" and r.get("counterfactual_task_type") != "T5_blast_radius":
                    reqs.append({"id": r["id"], "kind": r["kind"], "scope": "out of tool scope", "state": None})
                    continue
                tt = E.SLICE_TASK_TYPE.get(r.get("counterfactual_task_type")) if tool == "slice" else None
                key = (name, q["corpus"], r["subject"], tt)
                if key not in cache:
                    with E.registered_repo(engine) as server:
                        resp = E.call_tool(server, tool, engine.repo_root, r["subject"], budget, tt)
                    cache[key] = resp
                resp = cache[key]
                if "error" in resp:
                    reqs.append({"id": r["id"], "kind": r["kind"], "scope": "tool error", "error": resp["error"],
                                 "state": "INDEX_MISSING"})
                    continue
                parsed = E.parse_envelope(resp["envelope"])
                warns = {w["code"] for w in parsed["warnings"]}
                callers = {c["symbol"] for c in resp.get("callers", [])} if tool == "blast_radius" else None
                if callers is not None and resp.get("callers_truncated"):
                    warns.add("CALLERS_TRUNCATED")
                facts = [{"id": f["id"], "type": f["type"], **fact_state(f, b, parsed, warns, callers)} for f in r["facts"]]
                reqs.append({"id": r["id"], "kind": r["kind"], "scope": "retrieved", "facts": facts,
                             "state": worst(f["state"] for f in facts),
                             "token_count": resp["token_count"], "budget": budget,
                             "budget_overflow": "BUDGET_OVERFLOW" in warns})
            in_scope = [x["state"] for x in reqs if x["state"] is not None]
            per_q[q["id"]] = {"category": q["category"], "requirements": reqs,
                              "state_given_subject": worst(in_scope) if in_scope and len(in_scope) == len(reqs) else
                              (worst(in_scope) + " (partial scope)" if in_scope else None)}
        out["modes"][name] = per_q
    # production inputs: only seeded controls have a subject; everything else stops at resolution
    out["production_inputs"] = {q["id"]: ("resolved (seed given)" if q["category"] == "seeded_control"
                                          else "subject_unresolved (no natural-language resolver)") for q in questions}
    out["summary"] = summarize(out, questions)
    return out


def summarize(out: dict, questions: list[dict]) -> dict:
    s = {}
    for name, per_q in out["modes"].items():
        req_states = Counter(r["state"] for v in per_q.values() for r in v["requirements"] if r["state"])
        fact_states = Counter(f["state"] for v in per_q.values() for r in v["requirements"] for f in r.get("facts", []))
        s[name] = {"requirement_states": dict(req_states), "fact_states": dict(fact_states)}
    # classification across modes
    cls = {}
    for q in questions:
        qid = q["id"]
        best = {}
        for name, per_q in out["modes"].items():
            for r in per_q[qid]["requirements"]:
                if r["state"] is None:
                    continue
                prev = best.get(r["id"])
                best[r["id"]] = r["state"] if prev is None else min(prev, r["state"], key=lambda x: -STATES.index(x))
        req_best = list(best.values())
        all_ok = req_best and all(x in ANSWERABLE for x in req_best)
        some_ok = any(x in ANSWERABLE for x in req_best)
        if all_ok and q["category"] == "seeded_control":
            c = "provable_today"
        elif all_ok:
            c = "conditionally_provable (needs subject resolution)"
        elif some_ok:
            c = "partially_provable"
        else:
            c = "not_provable"
        cls[qid] = {"class": c, "best_requirement_states": best}
    s["classification"] = cls
    s["classification_counts"] = dict(Counter(v["class"] for v in cls.values()))
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--static-only", action="store_true", help="write the requirement matrix only (no index)")
    args = ap.parse_args(argv)
    questions, gold = D.load_questions(), D.load_gold()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    matrix = {"version": 1, "questions": [requirement_model(q, gold[q["id"]]) for q in questions]}
    (out / "requirements_matrix.json").write_text(json.dumps(matrix, indent=1) + "\n")
    if args.static_only:
        return 0
    from harness.experiments.query_to_evidence.run import build_engine
    from harness.tasks.loaders import _repo_root
    engines = {c: build_engine(str(_repo_root(c))) for c in sorted({q["corpus"] for q in questions})}
    result = evaluate(engines, questions, gold)
    (out / "proof_baseline.json").write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result["summary"]["classification_counts"]))
    for name in result["modes"]:
        print(name, result["summary"][name])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
