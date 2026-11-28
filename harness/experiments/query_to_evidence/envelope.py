"""Production-envelope delivery modes (evaluation only).

The Arm 5 mode in `run.py` measures the *benchmark* prompt: Arm 5 sends
each hydrated node as a header + body item and drops the rest of the
`<prism_context>` envelope (`<edges>`, `<contract>`, `<warnings>`,
`<causal_path>`). That is a benchmark serialization, not what the
production tools return.

These modes measure what the production MCP tools actually return. They
call the real `prism.mcp.server.prism_slice` and `prism_blast_radius`
functions - no re-implemented retrieval or serializer - against the
harness's own in-memory index, registered in the server's `GraphCache` for
the duration of the call (the two tools read only `builder`, `contracts`
and `repo_root` from it). The rendered XML is then parsed and every gold
edge is checked independently for:

  graph            the edge is in Prism's graph (static/tentative/runtime)
  selected         both endpoint nodes are in the envelope
  edge_record      an explicit `<edge from=".." to="..">` element is present
  contract         the callee's `<contract target_id=caller call_line=..>`
  call_site        the call-site text is inside the caller's full body
  warnings         the envelope's `<warning code=..>` list

Modes (seed = each requirement's gold subject, as in the counterfactual
mode of run.py; not a natural-language entry point):

  slice@4k / slice@13k                 every requirement with gold edges
  blast_radius@4k / blast_radius@13k   impact requirements only
                                       (counterfactual_task_type T5)

Tool arguments are the production defaults except `seed_symbol`,
`budget_tokens` and, for `prism.slice`, `task_type` (T5 -> "blast",
T2 -> "debug").
"""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import contextmanager

from harness.experiments.query_to_evidence import dataset as D
from harness.experiments.query_to_evidence.accounting import edge_graph_status

MODES = (("slice", 4000), ("slice", 13000), ("blast_radius", 4000), ("blast_radius", 13000))
SLICE_TASK_TYPE = {"T5_blast_radius": "blast", "T2_localization": "debug"}
IN_GRAPH = ("static", "tentative", "confirmed_runtime")


def mode_name(tool: str, budget: int) -> str:
    return f"{tool}@{budget // 1000}k"


# --------------------------------------------------------------------------
# parsing the rendered envelope
# --------------------------------------------------------------------------
def _bool(v: str | None) -> bool | None:
    return None if v is None else v == "true"


def parse_envelope(xml: str) -> dict:
    """Edges, nodes (with contract and body) and warnings of a rendered
    `<prism_context>` document, read from the XML itself."""
    root = ET.fromstring(xml)
    edges = []
    for e in root.iter("edge"):
        a = e.attrib
        edges.append({"from": a.get("from"), "to": a.get("to"), "type": a.get("type"),
                      "weight": float(a["weight"]) if "weight" in a else None,
                      "data_flow": _bool(a.get("data_flow")), "guard": _bool(a.get("guard")),
                      "back_edge": _bool(a.get("back_edge"))})
    nodes = {}
    for n in root.iter("node"):
        a = n.attrib
        c = n.find("contract")
        body = n.find("body")
        nodes[a["id"]] = {
            "role": a.get("role"), "compression": a.get("compression"), "file": a.get("file"),
            "line": int(a["line"]) if a.get("line") else None,
            "contract": ({"target_id": c.get("target_id"),
                          "call_line": int(c.get("call_line")) if c.get("call_line") else None}
                         if c is not None else None),
            "body": body.text if body is not None else None,
        }
    warnings = []
    for w in root.iter("warning"):
        details = {d.get("key"): d.get("value") for d in w.iter("detail")}
        warnings.append({"code": w.get("code"), "severity": w.get("severity"), "details": details})
    return {"edges": edges, "nodes": nodes, "warnings": warnings}


def edge_delivery(parsed: dict, frm: str | None, to: str | None, source_text: str) -> dict:
    """One gold edge against one parsed envelope. `first_loss` separates an
    unselected endpoint from an edge missing although both ends are in."""
    nodes = parsed["nodes"]
    from_sel, to_sel = frm in nodes, to in nodes
    record = next((e for e in parsed["edges"] if e["from"] == frm and e["to"] == to), None)
    callee = nodes.get(to)
    contract = callee["contract"] if callee and callee["contract"] and callee["contract"]["target_id"] == frm else None
    caller = nodes.get(frm)
    site = bool(caller and caller["compression"] == "L0_full" and caller["body"]
                and source_text.strip() in caller["body"])
    if record is not None:
        first_loss = None
    elif not from_sel:
        first_loss = "caller_not_selected"
    elif not to_sel:
        first_loss = "callee_not_selected"
    else:
        first_loss = "both_selected_no_record"
    return {"from_selected": from_sel, "to_selected": to_sel, "edge_record": record is not None,
            "edge_type": record["type"] if record else None,
            "contract": contract is not None, "contract_call_line": contract["call_line"] if contract else None,
            "call_site_in_full_caller_body": site, "first_loss": first_loss}


# --------------------------------------------------------------------------
# calling the real MCP tools
# --------------------------------------------------------------------------
@contextmanager
def registered_repo(engine):
    """Register the harness engine's index as the MCP server's RepoContext
    for its repo root; restore the previous entry afterwards."""
    from prism.mcp import server
    from prism.mcp.cache import GraphCache, RepoContext

    key = GraphCache.canonical_path(engine.repo_root)
    ctx = RepoContext(repo_root=engine.repo_root, builder=engine.builder, tag_matrix={}, metamodel=None,
                      distance_engine=None, runtime_state={}, contracts=engine._contracts)
    entries = server._cache._entries
    previous = entries.get(key)
    entries[key] = ctx
    try:
        yield server
    finally:
        if previous is None:
            entries.pop(key, None)
        else:
            entries[key] = previous


def call_tool(server, tool: str, root: str, seed: str, budget: int, task_type: str | None) -> dict:
    try:
        if tool == "slice":
            out = server.prism_slice(repo_path=root, seed_symbol=seed, budget_tokens=budget, task_type=task_type)
        else:
            out = server.prism_blast_radius(repo_path=root, seed_symbol=seed, budget_tokens=budget)
    except server.MCPError as exc:
        return {"error": {"code": exc.code, "message": str(exc)}}
    return out


# --------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------
def _requirements(questions, gold, tool):
    for q in questions:
        for req in gold[q["id"]].get("requirements", []):
            if not req.get("relationship_paths") or not req.get("subject"):
                continue
            if tool == "blast_radius" and req.get("counterfactual_task_type") != "T5_blast_radius":
                continue
            yield q, req


def evaluate_modes(engines: dict, questions: list[dict] | None = None, gold: dict | None = None,
                   modes=MODES) -> dict:
    """`engines`: corpus -> PrismEngine (the harness's in-memory index)."""
    questions = questions if questions is not None else D.load_questions()
    gold = gold if gold is not None else D.load_gold()
    out = {}
    for tool, budget in modes:
        rows, envelopes = [], {}
        for q, req in _requirements(questions, gold, tool):
            engine = engines[q["corpus"]]
            b = engine.builder
            task_type = SLICE_TASK_TYPE.get(req.get("counterfactual_task_type")) if tool == "slice" else None
            with registered_repo(engine) as server:
                resp = call_tool(server, tool, engine.repo_root, req["subject"], budget, task_type)
            if "error" in resp:
                parsed = {"edges": [], "nodes": {}, "warnings": []}
                env_meta = {"error": resp["error"]}
            else:
                parsed = parse_envelope(resp["envelope"])
                env_meta = {"token_count": resp["token_count"], "truncated": resp["truncated"],
                            "sha": hashlib.sha256(resp["envelope"].encode()).hexdigest()[:16]}
                if tool == "blast_radius":
                    env_meta["callers_total"] = resp["callers_total"]
            envelopes[f"{q['id']}/{req['id']}"] = {**env_meta, "warnings": [w["code"] for w in parsed["warnings"]]}
            walked = {c["symbol"] for c in resp.get("callers", [])} if tool == "blast_radius" else None
            for e in req["relationship_paths"]:
                frm, to = D.resolve_endpoint(b, e["from"]), D.resolve_endpoint(b, e["to"])
                row = {"q": q["id"], "req": req["id"], "category": q["category"], "from": e["from"], "to": e["to"],
                       "call_site": e["call_site"], "graph": edge_graph_status(b, frm, to)["status"],
                       "tool_error": "error" in resp, "warnings": [w["code"] for w in parsed["warnings"]],
                       **edge_delivery(parsed, frm, to, e["source_text"])}
                if row["first_loss"] and row["graph"] not in IN_GRAPH:
                    row["first_loss"] = "not_in_graph"
                if row["tool_error"]:
                    row["first_loss"] = "tool_error"
                if walked is not None:
                    row["in_callers_list"] = frm in walked
                rows.append(row)
        out[mode_name(tool, budget)] = {"rows": rows, "envelopes": envelopes, "summary": summarize(rows)}
    return out


def summarize(rows: list[dict]) -> dict:
    """Counts over gold edges (denominator = len(rows)); envelope-level
    warnings are counted once per envelope."""
    n = len(rows)
    c = Counter()
    for r in rows:
        c["in_graph"] += r["graph"] in IN_GRAPH
        c["both_selected"] += r["from_selected"] and r["to_selected"]
        c["edge_record"] += r["edge_record"]
        c["contract"] += r["contract"]
        c["call_site_in_full_caller_body"] += r["call_site_in_full_caller_body"]
        c["record_or_call_site"] += r["edge_record"] or r["call_site_in_full_caller_body"]
        if "in_callers_list" in r:
            c["in_callers_list"] += r["in_callers_list"]
    envs = {(r["q"], r["req"]): r for r in rows}
    out = {"edges": n, **{k: {"num": v, "den": n} for k, v in sorted(c.items())},
           "first_loss": dict(Counter(r["first_loss"] or "none (edge record delivered)" for r in rows)),
           "envelopes": len(envs),
           "envelopes_with_warning": dict(Counter(code for r in envs.values() for code in set(r["warnings"]))),
           "tool_errors": sum(1 for r in envs.values() if r["tool_error"])}
    return out


def arm5_summary(results: list[dict], mode: str) -> dict:
    """The benchmark (Arm 5 prompt) counterpart, from run.py's results:
    `mode` is "production" or "counterfactual"."""
    edges = []
    for q in results:
        reqs = q["production"]["requirements"] if mode == "production" else q["counterfactual"]
        for r in reqs:
            if isinstance(r, dict) and r.get("edges"):
                edges.extend(r["edges"])
    n = len(edges)
    return {"edges": n,
            "in_graph": {"num": sum(e["stages"]["graph"] for e in edges), "den": n},
            "both_selected": {"num": sum(bool(e["endpoint_delivery"]["from"] and e["endpoint_delivery"]["to"])
                                         for e in edges), "den": n},
            "edge_record": {"num": sum(e["relationship_record_delivered"] for e in edges), "den": n},
            "call_site_in_full_caller_body": {"num": sum(e["stages"]["call_site_in_model_input"] for e in edges),
                                              "den": n}}
