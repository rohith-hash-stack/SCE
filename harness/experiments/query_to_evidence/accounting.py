"""Stage-by-stage loss accounting for required symbols and relationship edges.

Stages, in pipeline order (first observed loss wins; later probes are kept as
secondary causes):

    resolution   the pipeline had a subject (seed) to anchor on
    index        the symbol is in Prism's index
    manifest     the symbol is a Turn-1 manifest row
    selection    Turn 1 requested it (or hydration auto-included it)
    hydration    it is a node of the Turn-2 package
    arm          Arm 5 delivered it as an item
    budget       the harness budget trim kept it
    delivery     it is in the model input (full body or stub)

Edge statuses reuse Prism's own graph vocabulary: an edge's `kind`
(`TENTATIVE_CALL`/`TENTATIVE_DYNAMIC_CALL`), its `confidence`
(`CONFIRMED_RUNTIME`), and the sentinel nodes `<ambiguous:NAME@file:line>` /
`<dynamic:TYPE@file:line>` emitted where a call could not be resolved.
"""
from __future__ import annotations

import networkx as nx

from harness.experiments.query_to_evidence.dataset import resolve_endpoint
from prism.graph.symbol_table import SymbolRole
from prism.packer.candidate_index import CANDIDATE_INDEX_MAX_HOPS, UPSTREAM_WALK_MAX_HOPS

STAGES = ["resolution", "index", "manifest", "selection", "hydration", "arm", "budget", "delivery"]
STATE_FOR_LOSS = {
    "resolution": "not_resolved_from_query",
    "index": "not_indexed",
    "manifest": "not_discovered_in_manifest",
    "selection": "discovered_not_selected",
    "hydration": "selected_not_hydrated",
    "arm": "hydrated_then_excluded",
    "budget": "lost_to_budget",
    "delivery": "kept_but_not_in_model_input",
}
_CALL_RELATIONS = ("CALLS", "INSTANTIATES")


class GraphProbe:
    """Read-only reachability over CALLS/INSTANTIATES edges, for secondary
    causes. Built once per builder."""

    def __init__(self, builder) -> None:
        self.builder = builder
        g = nx.DiGraph()
        for u, v, d in builder.graph.edges(data=True):
            if d.get("relation", "CALLS") in _CALL_RELATIONS:
                g.add_edge(u, v)
        self.calls = g

    def hops(self, source: str, target: str) -> int | None:
        if source not in self.calls or target not in self.calls:
            return None
        try:
            return nx.shortest_path_length(self.calls, source, target)
        except nx.NetworkXNoPath:
            return None


def _delivery(sym: str, case: dict) -> str | None:
    for item in case["items"]:
        if sym in item["symbols"] and case.get("model_input") and item["content"] in case["model_input"]:
            return "full" if item["kind"] == "code_chunk" else "stub"
    return None


def symbol_account(sym: str, case: dict, builder, probe: GraphProbe, include_tests: bool = False) -> dict:
    trace = case.get("trace")
    meta = case["build_meta"]
    anchor = meta.get("anchor")
    manifest = trace.manifest if trace is not None else None
    hydration = trace.hydration if trace is not None else None
    universe = set(manifest["universe"]) if manifest else set()
    requested = set(meta.get("requested_symbols") or [])
    nodes = {n["id"]: n for n in hydration["nodes"]} if hydration else {}
    raw_syms = {s for i in case["raw_items"] for s in i["symbols"]}
    kept_syms = {s for i in case["items"] for s in i["symbols"]}
    delivery = _delivery(sym, case)
    traced = trace is not None
    # None = stage not observable for this case (a stored cell has no trace
    # of the manifest or the hydrated package): "not measured", never a loss.
    reached = {
        "resolution": bool(anchor),
        "index": sym in builder.symbol_table,
        "manifest": (sym in universe) if traced else None,
        "selection": (sym in requested or sym in nodes) if traced else (True if sym in requested else None),
        "hydration": (sym in nodes) if traced else None,
        "arm": sym in raw_syms,
        "budget": sym in kept_syms,
        "delivery": delivery is not None,
    }
    first_loss = next((s for s in STAGES if reached[s] is False), None)
    secondary: list[str] = []
    if first_loss is None:
        state = f"delivered_{delivery}"
    else:
        state = STATE_FOR_LOSS.get(first_loss, f"lost_at_{first_loss}")
        if first_loss == "manifest" and sym in requested:
            state = "requested_but_absent_from_manifest"
    if sym in nodes and sym not in requested and sym != anchor:
        secondary.append("auto_included_by_hydration")
    if delivery == "stub":
        secondary.append("compressed_to_stub")
    if first_loss == "manifest" and anchor and sym in builder.symbol_table:
        secondary.extend(_manifest_causes(sym, anchor, manifest, builder, probe, include_tests))
    return {"symbol": sym, "state": state, "first_loss": first_loss, "secondary_causes": secondary,
            "reached": reached, "requested": sym in requested, "delivery": delivery}


def _manifest_causes(sym, anchor, manifest, builder, probe, include_tests) -> list[str]:
    causes = []
    info = builder.symbol_table.get(sym)
    if info is not None and info.role == SymbolRole.VERIFICATION and not include_tests:
        causes.append("filtered_as_test_code (SymbolRole.VERIFICATION; manifest include_tests=False)")
    down, up = probe.hops(anchor, sym), probe.hops(sym, anchor)
    direction = manifest.get("direction") if manifest else None
    if down is None and up is None:
        causes.append("no_call_path_to_or_from_subject")
    else:
        if up is not None and direction != "both":
            causes.append(f"caller_at_{up}_hops_but_manifest_direction_is_{direction}")
        if up is not None and up > UPSTREAM_WALK_MAX_HOPS:
            causes.append(f"caller_beyond_upstream_bound ({up} > {UPSTREAM_WALK_MAX_HOPS} hops)")
        if down is not None and down > CANDIDATE_INDEX_MAX_HOPS:
            causes.append(f"callee_beyond_downstream_bound ({down} > {CANDIDATE_INDEX_MAX_HOPS:g} hops)")
        if not any(c.startswith(("caller_at", "caller_beyond", "callee_beyond", "filtered")) for c in causes):
            causes.append(f"within_bounds_but_not_admitted (down={down}, up={up}; scope filter or manifest budget)")
    return causes


def edge_graph_status(builder, frm: str | None, to: str | None) -> dict:
    if frm is None or to is None:
        return {"status": "endpoint_not_indexed"}
    data = builder.graph.get_edge_data(frm, to)
    if data and data.get("relation", "CALLS") in _CALL_RELATIONS:
        kind = data.get("kind")
        if kind and kind.startswith("TENTATIVE"):
            status = "tentative"
        elif data.get("confidence") == "CONFIRMED_RUNTIME":
            status = "confirmed_runtime"
        else:
            status = "static"
        out = {"status": status}
        if kind:
            out["kind"] = kind
        if data.get("dispatch_of"):
            out["dispatch_of"] = data["dispatch_of"]
        return out
    leaf = to.rsplit(".", 1)[-1]
    succs = list(builder.graph.successors(frm)) if frm in builder.graph else []
    if any(s.startswith(f"<ambiguous:{leaf}@") for s in succs):
        return {"status": "ambiguous_sentinel"}
    other = sorted(s for s in succs if not s.startswith("<") and s.rsplit(".", 1)[-1] == leaf)
    if other:
        return {"status": "resolved_to_other_target", "targets": other}
    if any(s.startswith("<dynamic:") for s in succs):
        return {"status": "unresolved", "note": "caller has dynamic-dispatch sentinels"}
    return {"status": "unresolved"}


def edge_account(edge: dict, case: dict, builder, symbol_states: dict[str, dict]) -> dict:
    frm, to = resolve_endpoint(builder, edge["from"]), resolve_endpoint(builder, edge["to"])
    graph = edge_graph_status(builder, frm, to)
    trace = case.get("trace")
    anchor = case["build_meta"].get("anchor")
    universe = set(trace.manifest["universe"]) if trace is not None and trace.manifest else set()
    hydrated_edges = {(e["from"], e["to"]) for e in trace.hydration["edges"]} if trace is not None and trace.hydration else set()
    model_input = case.get("model_input") or ""
    from_delivery = symbol_states.get(frm, {}).get("delivery") if frm else None
    to_delivery = symbol_states.get(to, {}).get("delivery") if to else None
    call_site_delivered = from_delivery == "full" and edge["source_text"].strip() in model_input
    record_delivered = bool(frm and to and (f"{frm} -> {to}" in model_input or f'from="{frm}" to="{to}"' in model_input
                                            or f'from_node="{frm}"' in model_input))
    traced = trace is not None
    stages = {
        "resolution": bool(anchor),
        "graph": graph["status"] in ("static", "tentative", "confirmed_runtime"),
        "manifest": bool(frm and to and frm in universe and to in universe) if traced else None,
        "hydrated_edge": ((frm, to) in hydrated_edges) if traced else None,
        "call_site_in_model_input": call_site_delivered,
    }
    order = ["resolution", "graph", "manifest", "call_site_in_model_input"]
    first_loss = next((s for s in order if stages[s] is False), None)
    secondary = []
    if stages["hydrated_edge"] and not record_delivered:
        secondary.append("relationship_constructed_in_package_but_not_delivered_as_relationship")
    if from_delivery == "stub":
        secondary.append("caller_delivered_only_as_stub (call site not visible)")
    if graph["status"] == "tentative":
        secondary.append("edge_is_tentative")
    return {"from": edge["from"], "to": edge["to"], "call_site": edge["call_site"], "graph": graph, "stages": stages,
            "first_loss": first_loss, "secondary_causes": secondary, "relationship_record_delivered": record_delivered,
            "endpoint_delivery": {"from": from_delivery, "to": to_delivery}}


def requirement_account(req: dict, case: dict, builder, probe: GraphProbe, include_tests: bool = False) -> dict:
    from harness.experiments.query_to_evidence.dataset import required_symbols
    syms = [s for s in required_symbols(req, builder) if s]
    states = {s: symbol_account(s, case, builder, probe, include_tests) for s in syms}
    endpoints = {resolve_endpoint(builder, e[k]) for e in req.get("relationship_paths", []) for k in ("from", "to")}
    endpoint_states = {s: symbol_account(s, case, builder, probe, include_tests) for s in endpoints if s and s not in states}
    edges = [edge_account(e, case, builder, {**states, **endpoint_states}) for e in req.get("relationship_paths", [])]
    n = len(syms)

    def frac(pred):
        return {"num": sum(1 for s in syms if pred(states[s])), "den": n} if n else None

    evidence = []
    for ev in req.get("required_source_evidence", []):
        sym = ev["symbol"]
        st = states.get(sym) or symbol_account(sym, case, builder, probe, include_tests)
        evidence.append({**ev, "in_model_input": bool(st["delivery"] == "full" and ev["text"] in (case.get("model_input") or ""))})
    return {
        "id": req["id"], "kind": req["kind"],
        "symbols": list(states.values()),
        "edges": edges,
        "source_evidence": evidence,
        "recall": {
            "manifest": frac(lambda s: bool(s["reached"]["manifest"])),
            "requested": frac(lambda s: s["requested"]),
            "selected": frac(lambda s: bool(s["reached"]["selection"])),
            "delivered": frac(lambda s: s["delivery"] is not None),
            "delivered_full": frac(lambda s: s["delivery"] == "full"),
            "delivered_stub_only": frac(lambda s: s["delivery"] == "stub"),
        },
        "path": {
            "edges": len(edges),
            "in_graph": sum(1 for e in edges if e["stages"]["graph"]),
            "endpoints_in_manifest": sum(1 for e in edges if e["stages"]["manifest"]),
            "constructed_in_package": sum(1 for e in edges if e["stages"]["hydrated_edge"]),
            "call_site_in_model_input": sum(1 for e in edges if e["stages"]["call_site_in_model_input"]),
            "relationship_record_delivered": sum(1 for e in edges if e["relationship_record_delivered"]),
            "complete_path_delivered": bool(edges) and all(e["stages"]["call_site_in_model_input"] for e in edges),
        } if edges else None,
        "absence": ({"expected": req.get("expected_conclusion"), "search": req.get("absence_evidence"),
                     "pipeline_can_report_absence": False} if req["kind"] == "absence" else None),
    }
