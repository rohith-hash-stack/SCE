"""Track 2 (Phase B Two-Pass Engine Integration): the production
Candidate Index / Manifest Generator.

Graduated verbatim from the noise-reduction spike's Approach A v3 (hop=3
+ scope-filtered candidate universe - `benchmarks/experiments/
hydration_loop.py` on `experiment/noise-filtering-spike`, commit
7c352a5). Two prior manifest shapes were tried and rejected before v3:
v1 (name-only) collapsed `cpi_strict` to 0.333; v2 (full unbounded/
6-hop reach) recovered recall but cost ~44K Turn-1 tokens on average
(up to 73K). The offline sizing analysis (`benchmarks/experiments/
inspect_manifest_sizing.py`, commit 465474b, zero LLM spend, run before
any live grid) found recall=1.000 on all 3 target tasks at exactly 3
hops, and that combining a 3-hop cap with a same-module-or-real-call-
chain scope filter beats either lever alone by a wide margin (93-99%
Turn-1 token reduction vs. v2) on every task measured - confirmed live
afterward (tsr=0.500, the best of the entire spike; fpr_gt=0.000; mean
tokens=4167, 91% below v2's Turn-1 cost alone). `docs/design_formalism.
md` Sec 10.5 and `reports/spike_noise_reduction_debrief.md` record the
full validation.

Ported, not reimplemented: the same discipline the `index_cache.py`
`_rehydrate_def_nodes` bugfix already established for this codebase -
production code changes only what's proven, not what's convenient to
rewrite while porting. Every helper below is the spike's own, unchanged
except for import paths (`prism.packer.submodular_knapsack`'s already-
public `_classify_role`/`_module_prefix3`/`_signature_stub`/
`SeedNotFoundError`/`suggest_similar_seeds` are reused directly, exactly
as the spike itself reused them from production rather than forking).

This module deliberately stops at the manifest/candidate-universe
boundary (Turn 1's own output) - Turn 2's own hydration (resolving a
caller's requested symbols against this candidate universe into a real,
rendered `ContextPackage`) is `prism.engine.PrismEngine.retrieve_two_pass`
(Track 2 Item 3), which calls back into this module for the manifest
before running the caller-supplied selection.
"""
from __future__ import annotations

from collections import deque

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.packer.blast_radius import compute_upstream_callers
from prism.packer.submodular_knapsack import (
    DEFAULT_UPSTREAM_MAX_HOPS,
    UPSTREAM_FRONTIER_CAP,
    SeedNotFoundError,
    _classify_role,
    _module_prefix3,
    _signature_stub,
    suggest_similar_seeds,
)
from prism.traversal.continuous_dijkstra import build_causal_graph, compute_topological_distances

#: The validated floor (see this module's own docstring) - hop=2 was
#: measured and rejected (drops a real pipeline symbol, `django_t02_009`'s
#: `QuerySet._clone`); 3 is not a guess.
CANDIDATE_INDEX_MAX_HOPS = 3.0

#: The relations `ConcreteGraphBuilder.graph` (the real, directly
#: AST-derived structural graph - not `build_causal_graph`'s own
#: augmented one, which also mixes in synthetic causal-coupling edges)
#: uses for an actual invocation/instantiation, as opposed to
#: `READS_STATE` or any other non-call structural edge - so a manifest
#: row's own `calls=[...]` reflects only what a symbol's own AST body
#: literally invokes, language-agnostically, never a synthetic/inferred
#: coupling.
_CALL_RELATIONS = ("CALLS", "INSTANTIATES")

#: Caps one symbol's own `calls=[...]` list - a real hub symbol (e.g.
#: `HttpRequest`) can have dozens of real outgoing calls; capping keeps
#: one row from dominating the manifest.
_MAX_CALLS_PER_SYMBOL = 12


def _declaration_line(builder: ConcreteGraphBuilder, qname: str) -> str | None:
    """`qname`'s own raw declaration line(s) - reuses `_signature_stub`
    (production, unmodified) and drops its trailing `...` body
    placeholder, since a manifest row only wants the real signature
    text, not a renderable stub. Returns `None` under the same "no real
    source to describe" conditions `_signature_stub` itself does."""
    stub = _signature_stub(builder, qname)
    if stub is None:
        return None
    return stub.rsplit("\n", 1)[0]


def _outgoing_call_names(builder: ConcreteGraphBuilder, qname: str) -> list[str]:
    """The bare (unqualified) names `qname`'s own AST body directly
    calls or instantiates, deterministic and sorted. Qualified (the
    same dotted id a manifest row's own identity uses), not the bare
    simple name: two different classes in a large candidate universe
    can easily share a method name (`.get()`), and a bare name here
    would be genuinely ambiguous against which of several same-named
    candidate rows it means. An unresolved/ambiguous call target
    (`<ambiguous:...>`, `<dynamic:...>`) is dropped rather than
    rendered as a real name."""
    if qname not in builder.graph:
        return []
    names: set[str] = set()
    for succ in builder.graph.successors(qname):
        if succ.startswith("<"):
            continue
        edge = builder.graph.get_edge_data(qname, succ) or {}
        if edge.get("relation") not in _CALL_RELATIONS:
            continue
        names.add(succ)
    return sorted(names)[:_MAX_CALLS_PER_SYMBOL]


def _real_call_chain_reachable(
    builder: ConcreteGraphBuilder, seed_id: str, max_hops: int = 3, relations: tuple[str, ...] = _CALL_RELATIONS
) -> dict[str, int]:
    """`{node: hop_distance}` for every node reachable from `seed_id` via
    a chain of concrete `relations` edges in `builder.graph` (the real
    structural graph, not the causal graph's synthetic-coupling-
    augmented one), capped at `max_hops` unweighted hops - what the
    scope rule's "directly targeted by a concrete CALLS/INSTANTIATES
    edge" means, applied transitively."""
    visited = {seed_id: 0}
    queue = deque([seed_id])
    while queue:
        node = queue.popleft()
        depth = visited[node]
        if depth >= max_hops or node not in builder.graph:
            continue
        for succ in builder.graph.successors(node):
            if succ in visited:
                continue
            edge = builder.graph.get_edge_data(node, succ) or {}
            if edge.get("relation") not in relations:
                continue
            visited[succ] = depth + 1
            queue.append(succ)
    return visited


def _combined_hop_scope_filtered(
    builder: ConcreteGraphBuilder, seed_id: str, candidates: set[str], max_hops: int = 3
) -> set[str]:
    """Keep a (already hop-limited) candidate if it shares the seed's own
    top-level-3 module namespace, or sits within a `max_hops`-long chain
    of concrete `CALLS`/`INSTANTIATES` edges from the seed
    (`_real_call_chain_reachable`) - the scope rule: a candidate symbol
    must reside within the seed's package namespace unless reached via a
    concrete structural edge."""
    seed_info = builder.symbol_table.get(seed_id)
    seed_prefix = _module_prefix3(seed_info.module) if seed_info is not None else ""
    real_chain = _real_call_chain_reachable(builder, seed_id, max_hops=max_hops)
    kept = set()
    for qname in candidates:
        if qname == seed_id or qname in real_chain:
            kept.add(qname)
            continue
        info = builder.symbol_table.get(qname)
        module = info.module if info is not None else ""
        if _module_prefix3(module) == seed_prefix:
            kept.add(qname)
    return kept


def build_candidate_manifest(
    builder: ConcreteGraphBuilder,
    seed_id: str,
    max_hops: float = CANDIDATE_INDEX_MAX_HOPS,
    upstream_max_hops: float = DEFAULT_UPSTREAM_MAX_HOPS,
) -> tuple[str, set[str]]:
    """`(manifest_text, candidate_universe)`: one compact
    `qualified_name|role|kind|signature|calls=[...]` line per real
    (symbol-table-resolved) downstream candidate reachable from `seed_id`
    within `max_hops` (default `CANDIDATE_INDEX_MAX_HOPS=3.0`), plus up
    to `UPSTREAM_FRONTIER_CAP` upstream callers - a `role == "caller"`
    line additionally carries two contract-protection flags:
    `|binds_return=true/false|nontrivial_args=true/false` (`prism.packer.
    blast_radius.UpstreamCaller.unpacks_return`/`.supplies_nontrivial_args`).

    Upstream admission (pilot-4 finding): `compute_upstream_callers`
    returns every direct caller, unranked and uncapped - on a hub seed
    (e.g. `django.urls.base.reverse`, 709 real callers in the pilot-4
    corpus) `_combined_hop_scope_filtered`'s module-prefix rule alone
    left exactly 1 of 709 in the manifest, not because the other 708
    were unimportant but because almost none of them share the seed's
    own narrow module prefix - the same rule that correctly bounds
    downstream candidate explosion accidentally hides the cross-package
    consumers upstream blast-radius protection exists to surface. Here,
    upstream candidates are instead ranked by `UpstreamCaller.weight`
    (already boosts a return-unpacking/nontrivial-arg caller) and the
    top `UPSTREAM_FRONTIER_CAP` are admitted unconditionally - the same
    cap `submodular_knapsack.select_submodular_context` already uses for
    its own upstream competitive frontier, reused rather than a second,
    possibly-drifting constant - independent of module-prefix scope.
    Downstream candidates keep the original `_combined_hop_scope_
    filtered` rule unchanged.

    Budget-independent by construction, same as `pack_symbol_context`'s
    own `dist_w_map` - what a downstream budget affects is Turn 2's own
    render cap, not which symbols are reachable in the first place.

    Raises `SeedNotFoundError` if `seed_id` isn't in `builder.
    symbol_table`, carrying up to 5 fuzzy-matched suggestions - the same
    contract `pack_symbol_context` itself already gives every other
    production retrieval path.
    """
    if seed_id not in builder.symbol_table:
        raise SeedNotFoundError(seed_id, suggest_similar_seeds(builder, seed_id))

    graph = build_causal_graph(builder)
    dist_w_map = compute_topological_distances(builder, seed_id, d_max=max_hops)
    upstream_callers = compute_upstream_callers(builder, seed_id)
    dist_w_upstream_map = {symbol: caller.dist_w_upstream for symbol, caller in upstream_callers.items()}
    direct_successors = set(graph.successors(seed_id)) if seed_id in graph else set()

    downstream_candidates = {seed_id} | {n for n in dist_w_map if dist_w_map[n] <= max_hops}
    downstream_candidates = _combined_hop_scope_filtered(builder, seed_id, downstream_candidates, max_hops=int(max_hops))

    ranked_upstream = sorted(upstream_callers.values(), key=lambda c: -c.weight)
    upstream_candidates = {
        c.symbol for c in ranked_upstream[:UPSTREAM_FRONTIER_CAP] if dist_w_upstream_map[c.symbol] <= upstream_max_hops
    }

    candidates = downstream_candidates | upstream_candidates

    lines = []
    resolved: set[str] = set()
    for qname in sorted(candidates):
        info = builder.symbol_table.get(qname)
        if info is None:
            continue
        role = _classify_role(qname, seed_id, direct_successors, dist_w_map, dist_w_upstream_map)
        # A wrapped multi-line signature (_declaration_line can return
        # several header lines joined by "\n") must collapse to one
        # physical text line here, or it silently breaks this manifest's
        # own "one candidate per line" contract - the spike found this
        # directly via a real num_lines/candidate_universe count mismatch
        # during validation, not assumed away.
        raw_signature = _declaration_line(builder, qname) or ""
        signature = " ".join(raw_signature.split())
        calls = _outgoing_call_names(builder, qname)
        line = f"{qname}|{role}|{info.kind}|{signature}|calls=[{','.join(calls)}]"
        if role == "caller" and qname in upstream_callers:
            caller = upstream_callers[qname]
            line += f"|binds_return={'true' if caller.unpacks_return else 'false'}"
            line += f"|nontrivial_args={'true' if caller.supplies_nontrivial_args else 'false'}"
        lines.append(line)
        resolved.add(qname)
    manifest = "<candidate_index>\n" + "\n".join(lines) + "\n</candidate_index>"
    return manifest, resolved
