"""Answer correctness and claim grounding for stored answers.

Only stored completions are graded (no model is called), always against the
stored model input that produced them. Automated, claim-level where the
answer is structured: each symbol the answer names is one claim ("X is in the
blast radius of the subject" on T5, "X implements the behaviour" on T2).

A named symbol is not proof of a supported claim. A claim counts as
supported only if the model input carried the evidence for it:

  T5 impact claim   a chain X -> ... -> subject of Prism CALLS/INSTANTIATES
                    edges in which every caller's full body was delivered
                    and shows the call (the callee's name followed by "(").
  T2 behaviour      X's full body was delivered.

Claims about X whose body arrived only as a signature stub, or not at all,
are unsupported by the input even when X is gold. The prose `reasoning`
field is not graded automatically; see `manual_grounding.json` for the
labelled manual subset.
"""
from __future__ import annotations

import re
from collections import deque

from harness.scoring.adapters import _json_symbols

_CALL_RELATIONS = ("CALLS", "INSTANTIATES")


def _leaf(sym: str) -> str:
    leaf = sym.rsplit(".", 1)[-1]
    return sym.rsplit(".", 2)[-2] if leaf == "__init__" and sym.count(".") >= 1 else leaf


def _shows_call(content: str, callee: str) -> bool:
    """The callee's name followed by "(" (for a constructor: the class name,
    or `super().__init__(` / `__init__(`)."""
    names = [_leaf(callee)] + (["__init__"] if callee.endswith(".__init__") else [])
    return any(re.search(rf"\b{re.escape(n)}\s*\(", content) for n in names)


def supported_impact_set(builder, subject: str, full_bodies: dict[str, str]) -> dict[str, list[str]]:
    """Symbols with a delivered, visible call chain to `subject`, mapped to
    that chain. Walks callers backwards from the subject."""
    chains = {subject: [subject]}
    queue = deque([subject])
    while queue:
        callee = queue.popleft()
        if callee not in builder.graph:
            continue
        for caller in builder.graph.predecessors(callee):
            if caller in chains or caller not in full_bodies:
                continue
            if builder.graph.get_edge_data(caller, callee, {}).get("relation", "CALLS") not in _CALL_RELATIONS:
                continue
            if _shows_call(full_bodies[caller], callee):
                chains[caller] = [caller, *chains[callee]]
                queue.append(caller)
    chains.pop(subject)
    return chains


def grade_stored_answer(stored: dict, gold: dict, builder, subject: str) -> dict:
    comp = stored.get("completion")
    if comp is None:
        return {"status": "not_measured", "reason": "no stored completion"}
    task_type = stored["bundle"]["build_meta"].get("task_type")
    parsed = _json_symbols(comp["text"])
    if parsed is None:
        return {"status": "unparsed", "reason": "answer is not the JSON contract"}
    named, reasoning = parsed
    named = list(dict.fromkeys(named))
    # The subject itself (the seed) is neither gold nor a claim about the
    # relationship; it is reported but kept out of the claim counts.
    subject_named = subject in named
    named = [s for s in named if s != subject]
    gold_syms = {s for r in gold["requirements"] for s in r.get("required_symbols", [])}
    items = stored["bundle"]["items"]
    full = {s: i["content"] for i in items if i["kind"] == "code_chunk" for s in i["symbols"]}
    stub = {s for i in items if i["kind"] == "signature_stub" for s in i["symbols"]}
    chains = supported_impact_set(builder, subject, full) if task_type == "T5_blast_radius" else {}
    claims = []
    for sym in named:
        if sym not in builder.symbol_table:
            evidence = "not_indexed"
        elif sym in full:
            evidence = "full"
        elif sym in stub:
            evidence = "stub"
        else:
            evidence = "not_delivered"
        if task_type == "T5_blast_radius":
            supported = sym in chains
            basis = " -> ".join(chains[sym]) if supported else None
        else:
            supported, basis = evidence == "full", ("body delivered" if evidence == "full" else None)
        claims.append({"symbol": sym, "gold": sym in gold_syms, "evidence": evidence, "supported": supported,
                       "support": basis})
    summary = {
        "subject_named": subject_named, "named": len(named), "gold_total": len(gold_syms), "gold_named": sum(1 for c in claims if c["gold"]),
        "named_full": sum(1 for c in claims if c["evidence"] == "full"),
        "named_stub": sum(1 for c in claims if c["evidence"] == "stub"),
        "named_absent": sum(1 for c in claims if c["evidence"] in ("not_delivered", "not_indexed")),
        "path_claims": len(claims), "path_supported": sum(1 for c in claims if c["supported"]),
        "correct_and_supported": sum(1 for c in claims if c["gold"] and c["supported"]),
        "correct_but_unsupported": sum(1 for c in claims if c["gold"] and not c["supported"]),
        "supported_but_not_gold": sum(1 for c in claims if c["supported"] and not c["gold"]),
    }
    return {"status": "graded", "task_type": task_type, "claims": claims, "summary": summary,
            "reasoning": reasoning, "reasoning_grounding": "not graded automatically (see manual subset)",
            "model_tokens": {"prompt_server": comp.get("prompt_tokens_server"), "generated": comp.get("generation_tokens"),
                             "latency_s": comp.get("latency_seconds")}}
