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

import re
from collections import deque

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.packer.blast_radius import compute_upstream_callers
from prism.graph.symbol_table import SymbolRole
from prism.packer.submodular_knapsack import (
    DEFAULT_UPSTREAM_MAX_HOPS,
    UPSTREAM_FRONTIER_CAP,
    SeedNotFoundError,
    _classify_role,
    _default_costs,
    _module_prefix3,
    _signature_stub,
    suggest_similar_seeds,
)
from prism.slicer.tokenizer import count_tokens
from prism.traversal.continuous_dijkstra import build_causal_graph, compute_topological_distances

#: The validated floor (see this module's own docstring) - hop=2 was
#: measured and rejected (drops a real pipeline symbol, `django_t02_009`'s
#: `QuerySet._clone`); 3 is not a guess.
CANDIDATE_INDEX_MAX_HOPS = 3.0

#: Blast-radius mode (`direction="both"`): the furthest number of caller
#: hops the upstream walk follows. A safety cap only - the shared token
#: budget is what normally stops the walk (same value as the packer's own
#: `DEFAULT_MAX_HOPS`).
UPSTREAM_WALK_MAX_HOPS = 6

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


#: A manifest signature longer than this many tokens (after long string
#: literals are collapsed) is cut and marked with `SIGNATURE_TRUNCATED_MARK`.
MANIFEST_SIGNATURE_MAX_TOKENS = 128
SIGNATURE_TRUNCATED_MARK = " …[signature truncated]"

#: String literals inside a declaration header (default values, or
#: documentation embedded in type annotations) longer than this many
#: characters are collapsed to `"…"` - they describe, they don't declare.
_LONG_STRING_LITERAL = re.compile(
    r'"""[\s\S]*?"""' r"|'''[\s\S]*?'''"
    r'|"(?:[^"\\\n]|\\.){25,}"' r"|'(?:[^'\\\n]|\\.){25,}'" r"|`(?:[^`\\]|\\.){25,}`"
)


def _full_declaration(builder: ConcreteGraphBuilder, qname: str, max_tokens: int = MANIFEST_SIGNATURE_MAX_TOKENS) -> str | None:
    """`qname`'s complete declaration header on one line: the source from
    the symbol's first line (decorators included) up to where its body
    starts, so a parameter list or return annotation spanning several lines
    is kept whole in every language. Long string literals are collapsed,
    whitespace is normalized, and `|` (the manifest's field separator) is
    replaced by `¦`. A header still longer than `max_tokens` is cut there
    and marked with `SIGNATURE_TRUNCATED_MARK`. Falls back to
    `_declaration_line` for a symbol without a body node."""
    info = builder.symbol_table.get(qname)
    node = builder.def_node(qname)
    parsed = builder.parsed_file(info.file) if info is not None else None
    body = node.child_by_field_name("body") if node is not None else None
    if info is None or parsed is None or body is None:
        raw = _declaration_line(builder, qname)
        return None if raw is None else " ".join(raw.split()).replace("|", "¦")
    source = parsed.source
    line_start = 0
    for _ in range(info.line_range[0] - 1):
        nl = source.find(b"\n", line_start)
        if nl < 0:
            break
        line_start = nl + 1
    start = min(line_start, node.start_byte)
    header = source[start:body.start_byte].decode("utf-8", errors="replace")
    header = _LONG_STRING_LITERAL.sub('"…"', header)
    header = " ".join(header.split()).replace("|", "¦")
    if count_tokens(header) <= max_tokens:
        return header
    words = header.split(" ")
    lo, hi = 0, len(words)
    while lo < hi:  # longest word prefix within the cap
        mid = (lo + hi + 1) // 2
        if count_tokens(" ".join(words[:mid])) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return " ".join(words[:lo]) + SIGNATURE_TRUNCATED_MARK


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


def _upstream_walk(
    builder: ConcreteGraphBuilder, seed_id: str, max_hops: int = UPSTREAM_WALK_MAX_HOPS, include_tests: bool = False
) -> dict[str, int]:
    """`{caller: hop}` for every transitive caller of `seed_id` within
    `max_hops`, breadth-first over `CALLS`/`INSTANTIATES` in-edges of the
    structural graph (tentative dispatch edges included). Callers that are
    test code (`SymbolRole.VERIFICATION`) are excluded and not walked
    through, unless the seed itself is test code or `include_tests`."""
    seed_info = builder.symbol_table.get(seed_id)
    allow_tests = include_tests or (seed_info is not None and seed_info.role == SymbolRole.VERIFICATION)
    hops = {seed_id: 0}
    queue = deque([seed_id])
    while queue:
        node = queue.popleft()
        depth = hops[node]
        if depth >= max_hops or node not in builder.graph:
            continue
        for pred in sorted(builder.graph.predecessors(node)):
            if pred in hops:
                continue
            edge = builder.graph.get_edge_data(pred, node) or {}
            if edge.get("relation") not in _CALL_RELATIONS:
                continue
            info = builder.symbol_table.get(pred)
            if info is None or info.kind not in ("function", "method"):
                continue
            if info.role == SymbolRole.VERIFICATION and not allow_tests:
                continue
            hops[pred] = depth + 1
            queue.append(pred)
    hops.pop(seed_id)
    return hops


def upstream_callers_by_hop(
    builder: ConcreteGraphBuilder, seed_id: str, max_hops: int = UPSTREAM_WALK_MAX_HOPS, include_tests: bool = True
) -> dict[str, int]:
    """Every transitive caller of `seed_id` within `max_hops` with its hop
    count - the walk blast-radius mode builds its caller rows from, with no
    token budget applied. Test code is included by default here: for "which
    tests does this change affect?" the tests are the answer."""
    return _upstream_walk(builder, seed_id, max_hops=max_hops, include_tests=include_tests)


def _interleave_within_budget(
    builder: ConcreteGraphBuilder, seed_id: str, upstream: list[str], downstream: list[str], budget_tokens: int | None
) -> set[str]:
    """Admit candidates alternately from the two ordered lists (upstream
    first) until the shared `budget_tokens` - priced with the packer's own
    per-symbol costs, the same costs Turn 2 renders with - is spent. A
    candidate that does not fit is skipped and the next one tried. `None`
    admits everything."""
    if budget_tokens is None:
        return set(upstream) | set(downstream)
    costs = _default_costs(builder, [seed_id, *upstream, *downstream])
    spent = costs.get(seed_id, 0)
    admitted: set[str] = set()
    queues = [deque(upstream), deque(downstream)]
    turn = 0
    while queues[0] or queues[1]:
        q = queues[turn % 2] if queues[turn % 2] else queues[(turn + 1) % 2]
        turn += 1
        symbol = q.popleft()
        cost = costs.get(symbol, 0)
        if spent + cost <= budget_tokens:
            admitted.add(symbol)
            spent += cost
    return admitted


def build_candidate_manifest(
    builder: ConcreteGraphBuilder,
    seed_id: str,
    max_hops: float = CANDIDATE_INDEX_MAX_HOPS,
    upstream_max_hops: float = DEFAULT_UPSTREAM_MAX_HOPS,
    direction: str = "downstream",
    budget_tokens: int | None = None,
    include_tests: bool = False,
) -> tuple[str, set[str]]:
    """`(manifest_text, candidate_universe)`: one compact
    `qualified_name|role|kind|signature|calls=[...]|lines=N` line per real
    (symbol-table-resolved) downstream candidate reachable from `seed_id`
    within `max_hops` (default `CANDIDATE_INDEX_MAX_HOPS=3.0`), plus up
    to `UPSTREAM_FRONTIER_CAP` upstream callers - a `role == "caller"`
    line additionally carries two contract-protection flags:
    `|binds_return=true/false|nontrivial_args=true/false` (`prism.packer.
    blast_radius.UpstreamCaller.unpacks_return`/`.supplies_nontrivial_args`)
    before the final `|lines=N` (the symbol's body length in lines). The
    signature is the full declaration header on one line
    (`_full_declaration`, capped at `MANIFEST_SIGNATURE_MAX_TOKENS`).

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

    `direction="both"` (blast-radius mode, Design C): upstream admission
    is replaced by a hop-ordered walk over the seed's transitive callers
    (`_upstream_walk`, test code excluded), interleaved with the
    downstream candidates under a shared `budget_tokens` (no cap when
    `None`). The default `"downstream"` leaves every existing caller's
    manifest unchanged. `include_tests` keeps test code among the walked
    callers (the MCP `prism.blast_radius` tool sets it; the benchmark
    harness, whose gold is production callers, does not).

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
    # Hop-count admission: a symbol within `max_hops` structural CALLS/
    # INSTANTIATES hops is a candidate even when its weighted distance is
    # larger (a chain of best-effort `TENTATIVE_CALL` links is priced above
    # one hop each); the weighted distance still orders candidates.
    downstream_candidates |= set(_real_call_chain_reachable(builder, seed_id, max_hops=int(max_hops)))
    downstream_candidates = _combined_hop_scope_filtered(builder, seed_id, downstream_candidates, max_hops=int(max_hops))

    ranked_upstream = sorted(upstream_callers.values(), key=lambda c: -c.weight)
    upstream_candidates = {
        c.symbol for c in ranked_upstream[:UPSTREAM_FRONTIER_CAP] if dist_w_upstream_map[c.symbol] <= upstream_max_hops
    }

    if direction == "both":
        # Blast-radius mode (Design C): the seed's transitive callers, not
        # only its strongest direct ones, walked upstream in hop order
        # (direct callers ranked by W_upstream, then name) and interleaved
        # with the downstream candidates in distance order until the shared
        # token budget is spent. Every upstream candidate is labelled
        # `caller` (hop distance recorded for _classify_role).
        walked = _upstream_walk(builder, seed_id, include_tests=include_tests)
        upstream_order = sorted(
            walked,
            key=lambda q: (walked[q], -(upstream_callers[q].weight if q in upstream_callers else 0.0), q),
        )
        downstream_order = sorted(downstream_candidates - {seed_id}, key=lambda q: (dist_w_map.get(q, float("inf")), q))
        admitted = _interleave_within_budget(builder, seed_id, upstream_order, downstream_order, budget_tokens)
        upstream_candidates = {q for q in walked if q in admitted}
        downstream_candidates = {seed_id} | {q for q in downstream_candidates if q in admitted}
        dist_w_upstream_map = {**{q: float(h) for q, h in walked.items()}, **dist_w_upstream_map}
    elif direction != "downstream":
        raise ValueError(f"direction must be 'downstream' or 'both', got {direction!r}")

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
        signature = _full_declaration(builder, qname) or ""
        calls = _outgoing_call_names(builder, qname)
        line = f"{qname}|{role}|{info.kind}|{signature}|calls=[{','.join(calls)}]"
        if role == "caller" and qname in upstream_callers:
            caller = upstream_callers[qname]
            line += f"|binds_return={'true' if caller.unpacks_return else 'false'}"
            line += f"|nontrivial_args={'true' if caller.supplies_nontrivial_args else 'false'}"
        line += f"|lines={info.line_range[1] - info.line_range[0] + 1}"
        lines.append(line)
        resolved.add(qname)
    manifest = "<candidate_index>\n" + "\n".join(lines) + "\n</candidate_index>"
    return manifest, resolved
