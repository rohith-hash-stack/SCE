"""Prototype P1: payload compression with v2 summaries (docs/retrieval_pivot_investigation.md).

Offline, no model. For each case (a seed symbol plus gold nodes Prism did not produce) and each
budget, the production packer selects the payload (`pack_symbol_context`, called exactly as
`prism.surface.build` calls it), with node costs re-priced per arm: what a node costs is what it
is sent as. Every arm is then rendered by one renderer here and token-counted, so arms compare
like for like.

Representations
  full      the node's source (its L0 body), as the packer sends it today
  sig       signature stub + compact contract (params / returns / effects / raises / mutates)
  sig+sum   sig + its v2 summary line
  full+sum  full + its v2 summary line

Arms (the seed is always `full`; ring = hops from the seed over the graph's traversable edges)
  A0   baseline: every node full (the packer's own stub fallback still applies)
  A1   ring >= 2 -> sig+sum
  A1n  ring >= 2 -> sig          (control: A1 without the summary)
  A2   ring >= 1 -> sig+sum      (immediate helpers compressed too)
  A3   ring 1 -> full+sum, ring >= 2 -> sig+sum   (helpers enriched with their summary)

Gold (independent of Prism)
  q2e/<q>/<r>   query_to_evidence requirements: seed = gold subject; gold nodes =
                required_symbols; gold edges = relationship_paths
  l4/<task>     Layer 4 runtime traces (django, fastapi): seed = focal; gold nodes = repo
                functions observed at call depth <= 2 above or below it

Metrics per case x arm x budget: tokens used, nodes by representation, gold nodes present (any
representation / full / only compressed), gold edges with both ends present, tokens on nodes that
are neither seed nor gold (noise proxy), summary tokens.

Usage:
  python -m harness.experiments.retrieval_pivot.p1 needed OUT_DIR       # symbols to summarize
  python -m harness.experiments.retrieval_pivot.p1 run SUMMARIES_DIR OUT_DIR
SUMMARIES_DIR/<corpus>.json: a `prism summarize` index (v2) of each corpus.
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from collections import Counter, deque
from contextlib import contextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CORPORA = ("fastapi", "django")
BUDGETS = (2000, 4000, 8000)
ARMS = ("A0", "A1", "A1n", "A2", "A3")
L4_DEPTH = 2
NODE_HEADER = "### {kind} {qname} ({rel}:{line})"


def arm_rep(arm: str, ring: int) -> str:
    """The representation a non-seed node at `ring` hops gets under `arm`."""
    if arm == "A0" or ring == 0:
        return "full"
    if arm == "A1":
        return "sig+sum" if ring >= 2 else "full"
    if arm == "A1n":
        return "sig" if ring >= 2 else "full"
    if arm == "A2":
        return "sig+sum"
    if arm == "A3":
        return "full+sum" if ring == 1 else "sig+sum"
    raise ValueError(arm)


# ---------------------------------------------------------------- cases
def load_cases(builders: dict) -> list[dict]:
    from harness.experiments.query_to_evidence import dataset as D
    cases = []
    questions = {q["id"]: q for q in D.load_questions()}
    for qid, g in D.load_gold().items():
        corpus = questions[qid]["corpus"]
        b = builders.get(corpus)
        if b is None:
            continue
        for req in g.get("requirements", []):
            seed = req.get("subject")
            if not seed or seed not in b.symbol_table:
                continue
            nodes = {D.resolve_endpoint(b, s) for s in req.get("required_symbols", [])} - {None, seed}
            edges = [(D.resolve_endpoint(b, e["from"]), D.resolve_endpoint(b, e["to"])) for e in req.get("relationship_paths", [])]
            if nodes or edges:
                cases.append({"id": f"q2e/{qid}/{req['id']}", "corpus": corpus, "seed": seed, "gold_nodes": sorted(nodes),
                              "gold_edges": [e for e in edges if None not in e]})
    for path in sorted((REPO / "benchmarks/layer4/manifests").glob("l4_*.json")):
        task = json.loads((REPO / "benchmarks/layer4/tasks" / path.name).read_text())
        b = builders.get(task["corpus"])
        if b is None:
            continue
        m = json.loads(path.read_text())
        seed = m["focal"][0]
        nodes = {q for depth in (m["ancestor_depth"], m["descendant_depth"]) for q, d in depth.items() if d <= L4_DEPTH}
        nodes = {q for q in nodes if q in b.symbol_table} - {seed}
        if seed in b.symbol_table and nodes:
            cases.append({"id": f"l4/{task['task_id']}", "corpus": task["corpus"], "seed": seed,
                          "gold_nodes": sorted(nodes), "gold_edges": []})
    return cases


def rings(builder, seed: str) -> dict[str, int]:
    from prism.graph.concrete_builder import TRAVERSABLE_RELATIONS
    G = builder.graph
    dist, todo = {seed: 0}, deque([seed])
    while todo:
        u = todo.popleft()
        for v in list(G.successors(u)) + list(G.predecessors(u)):
            rel = (G.edges[u, v] if G.has_edge(u, v) else G.edges[v, u]).get("relation")
            if v not in dist and rel in TRAVERSABLE_RELATIONS:
                dist[v] = dist[u] + 1
                todo.append(v)
    return dist


# ---------------------------------------------------------------- rendering
def contract_text(contract) -> str:
    if contract is None:
        return ""
    parts = []
    if contract.params:
        parts.append("params: " + ", ".join(p.render() for p in contract.params))
    if contract.return_type:
        parts.append(f"returns: {contract.return_type}")
    for label, values in (("effects", contract.effects), ("raises", contract.thrown_exceptions),
                          ("mutates", contract.state_mutations)):
        if values:
            parts.append(f"{label}: {', '.join(values)}")
    return "[contract] " + "; ".join(parts) if parts else ""


class Renderer:
    def __init__(self, builder, contracts: dict, summaries: dict[str, str]):
        from prism.packer.submodular_knapsack import _signature_stub
        self.b, self.contracts, self.summaries, self._stub = builder, contracts, summaries, _signature_stub

    def body(self, q: str) -> str:
        info = self.b.symbol_table.get(q)
        source = self.b.source_text(info.file) if info else None
        if not source:
            return ""
        lines = source.splitlines()
        return "\n".join(lines[max(info.line_range[0] - 1, 0):info.line_range[1]])

    def header(self, q: str) -> str:
        info = self.b.symbol_table.get(q)
        rel = str(Path(info.file).relative_to(self.b.repo_root)) if info and str(info.file).startswith(str(self.b.repo_root)) else (info.file if info else "?")
        return NODE_HEADER.format(kind=info.kind if info else "?", qname=q, rel=rel, line=info.line_range[0] if info else 0)

    def render(self, q: str, rep: str) -> tuple[str, str]:
        """(text, the representation actually used): `sig` falls back to `full` when there is no
        stub (a class, a binding), and a summary part is dropped when there is no summary."""
        summary = self.summaries.get(q)
        if rep in ("sig", "sig+sum"):
            stub = self._stub(self.b, q)
            if stub is None:
                rep = "full+sum" if rep == "sig+sum" else "full"
            else:
                core = "\n".join(x for x in (stub, contract_text(self.contracts.get(q))) if x)
        if rep in ("full", "full+sum"):
            core = "\n".join(x for x in (self.body(q), contract_text(self.contracts.get(q))) if x)
        if rep.endswith("+sum"):
            if summary:
                core += f"\n[summary] {summary}"
            else:
                rep = rep[:-4]
        return f"{self.header(q)}\n{core}", rep


# ---------------------------------------------------------------- packing
@contextmanager
def repriced(renderer: Renderer, seed: str, arm: str, ring: dict[str, int], record: set | None = None):
    """Inside the block, the packer's node costs are the token counts of the arm's renderings."""
    import prism.packer.submodular_knapsack as K
    original = K._default_costs

    def costs(builder, symbols, contracts=None):
        if record is not None:
            record.update(symbols)
        out = {}
        for q in symbols:
            rep = "full" if q == seed else arm_rep(arm, ring.get(q, 99))
            out[q] = max(K.count_tokens(renderer.render(q, rep)[0]), 1)
        return out
    K._default_costs = costs
    try:
        yield
    finally:
        K._default_costs = original


def pack(builder, contracts: dict, seed: str, budget: int):
    """`pack_symbol_context` exactly as `prism.surface.build.build_context_package` calls it."""
    from prism.mcp.server import DEFAULT_D_MAX
    from prism.packer.submodular_knapsack import pack_symbol_context
    referenced = {q: c.referenced_constants for q, c in contracts.items() if c.referenced_constants}
    return pack_symbol_context(builder, seed, budget, max_hops=DEFAULT_D_MAX, referenced_constants=referenced)


def run_case(case: dict, engine, summaries: dict[str, str], arms=ARMS, budgets=BUDGETS) -> list[dict]:
    from prism.slicer.tokenizer import count_tokens
    b, contracts = engine.builder, engine._contracts
    r = Renderer(b, contracts, summaries)
    ring = rings(b, case["seed"])
    gold = set(case["gold_nodes"])
    rows = []
    production = {budget: set(pack(b, contracts, case["seed"], budget).selected) for budget in budgets}
    pool: set[str] = set()  # every symbol the packer may choose from (budget-independent)
    with repriced(r, case["seed"], "A0", ring, record=pool):
        pack(b, contracts, case["seed"], min(budgets))
    for arm in arms:
        for budget in budgets:
            with repriced(r, case["seed"], arm, ring):
                res = pack(b, contracts, case["seed"], budget)
            rendered, reps = [], {}
            for item in res.items:
                rep = "full" if item.symbol == case["seed"] else arm_rep(arm, ring.get(item.symbol, 99))
                if item.compression == "L2_skeleton" and rep in ("full", "full+sum"):
                    rep = "sig"  # the packer's own stub fallback
                text, reps[item.symbol] = r.render(item.symbol, rep)
                rendered.append((item.symbol, text))
            tok = {q: count_tokens(t) for q, t in rendered}
            summary_tok = sum(count_tokens(f"[summary] {summaries[q]}") for q in reps if reps[q].endswith("+sum"))
            present = gold & set(reps)
            rows.append({
                "case": case["id"], "corpus": case["corpus"], "arm": arm, "budget": budget,
                "tokens": sum(tok.values()), "nodes": len(reps), "reps": dict(Counter(reps.values())),
                "gold": len(gold), "gold_present": len(present), "gold_reachable": len(gold & (pool | {case["seed"]})),
                "gold_full": sum(1 for q in present if reps[q].startswith("full")),
                "gold_compressed_only": sum(1 for q in present if reps[q].startswith("sig")),
                "gold_with_summary": sum(1 for q in present if reps[q].endswith("+sum")),
                "edges": len(case["gold_edges"]),
                "edges_both_present": sum(1 for f, t in case["gold_edges"] if f in reps and t in reps),
                "noise_tokens": sum(v for q, v in tok.items() if q != case["seed"] and q not in gold),
                "summary_tokens": summary_tok,
                "production_overlap": round(len(production[budget] & set(reps)) / max(1, len(production[budget] | set(reps))), 3),
                "selected": sorted(reps),
            })
    return rows


# ---------------------------------------------------------------- report
def report(rows: list[dict]) -> str:
    L = ["# P1: payload compression with v2 summaries (offline, no model)", "",
         "Per arm and budget, pooled over cases: gold nodes present (any representation / at full body),",
         "gold edges with both ends present, mean tokens used, noise share (tokens on nodes that are",
         "neither seed nor gold), summary tokens. Arms: see p1.py.", "",
         "Ceiling: gold nodes inside the packer's candidate pool at all (reachable within its hop limits):",
         f"{sum(x['gold_reachable'] for x in rows if x['arm'] == 'A0' and x['budget'] == BUDGETS[0])}/"
         f"{sum(x['gold'] for x in rows if x['arm'] == 'A0' and x['budget'] == BUDGETS[0])}.", "",
         "| budget | arm | gold present | gold at full body | gold edges | tokens (mean) | noise share | summary tokens (mean) |",
         "|---|---|---|---|---|---|---|---|"]
    for budget in BUDGETS:
        for arm in ARMS:
            rs = [x for x in rows if x["arm"] == arm and x["budget"] == budget]
            if not rs:
                continue
            g, gp, gf = sum(x["gold"] for x in rs), sum(x["gold_present"] for x in rs), sum(x["gold_full"] for x in rs)
            e, ep = sum(x["edges"] for x in rs), sum(x["edges_both_present"] for x in rs)
            tokens = st.mean(x["tokens"] for x in rs)
            noise = sum(x["noise_tokens"] for x in rs) / max(1, sum(x["tokens"] for x in rs))
            L.append(f"| {budget} | {arm} | {gp}/{g} ({gp / max(1, g):.0%}) | {gf}/{g} | {ep}/{e} | {tokens:.0f} | "
                     f"{noise:.0%} | {st.mean(x['summary_tokens'] for x in rs):.0f} |")
    a0 = [x["production_overlap"] for x in rows if x["arm"] == "A0"]
    L += ["", f"Faithfulness: A0's selection vs the production packer's own (body-only costs), Jaccard mean "
          f"{st.mean(a0):.2f}, min {min(a0):.2f}." if a0 else ""]
    L += ["", "Per case, gold present at 4k (A0 -> A1 / A2 / A3):", ""]
    by = {(x["case"], x["arm"]): x for x in rows if x["budget"] == 4000}
    for case in sorted({x["case"] for x in rows}):
        a = [by.get((case, arm)) for arm in ("A0", "A1", "A2", "A3")]
        if all(a):
            L.append(f"- {case} ({a[0]['gold']} gold): " + " -> ".join(str(x["gold_present"]) for x in a))
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- main
def engines(corpora=CORPORA) -> dict:
    from benchmarks.corpora.resolver import resolve
    from harness.experiments.query_to_evidence.run import build_engine
    return {c: build_engine(str(resolve(c))) for c in corpora}


def needed(out: Path) -> dict:
    """Every symbol the packer prices for any case at the largest budget: the summaries P1 needs."""
    eng = engines()
    cases = load_cases({c: e.builder for c, e in eng.items()})
    need = {c: set() for c in eng}
    for case in cases:
        e = eng[case["corpus"]]
        r = Renderer(e.builder, e._contracts, {})
        with repriced(r, case["seed"], "A0", rings(e.builder, case["seed"]), record=need[case["corpus"]]):
            pack(e.builder, e._contracts, case["seed"], max(BUDGETS))
    out.mkdir(parents=True, exist_ok=True)
    fns = {c: sorted(q for q in s if (info := eng[c].builder.symbol_table.get(q)) is not None
                     and info.kind in ("function", "method")) for c, s in need.items()}
    for c, names in fns.items():
        (out / f"needed_{c}.txt").write_text("\n".join(names) + "\n")
    (out / "cases.json").write_text(json.dumps(cases, indent=1) + "\n")
    return {c: len(v) for c, v in fns.items()} | {"cases": len(cases)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("needed", "run"))
    ap.add_argument("paths", nargs="+", type=Path)
    a = ap.parse_args(argv)
    if a.command == "needed":
        print(json.dumps(needed(a.paths[0]), indent=1))
        return 0
    from prism.summaries.index import read_index
    summaries_dir, out = a.paths
    eng = engines()
    cases = load_cases({c: e.builder for c, e in eng.items()})
    rows = []
    for case in cases:
        idx = read_index(summaries_dir / f"{case['corpus']}.json") or {"functions": {}}
        summaries = {q: e["summary"] for q, e in idx["functions"].items()}
        rows += run_case(case, eng[case["corpus"]], summaries)
    out.mkdir(parents=True, exist_ok=True)
    (out / "p1.json").write_text(json.dumps({"cases": cases, "rows": rows}, indent=1) + "\n")
    (out / "P1.md").write_text(report(rows))
    print(report(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
