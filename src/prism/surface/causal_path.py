"""v1.1+ Agent Surface: the `<causal_path>` envelope block - a forward
causal chain from a T02-style (chain/debug) task's seed to whichever
reachable *sink* explains why the seed's own behavior matters, so a
model reading the envelope doesn't have to re-derive call order from the
unordered `<nodes>`/`<edges>` sets itself.

Computed strictly over the **already-packed** subgraph (`pkg.nodes`/
`pkg.edges`, i.e. `packed_ids` and CALLS/INSTANTIATES edges between two
packed nodes) - never the full underlying repo graph. A stage naming a
symbol the envelope doesn't otherwise carry a `<node>` for would be
useless (nothing for the reader to look up), so the path can only ever
walk through what's actually in the pack, in whichever order that
subgraph gives.

Algorithm (spec-mandated):

  1. **Sinks**: a packed node (other than the seed) is a sink if its
     feature mask intersects the substance sink set (`SINK_NETWORK_IO`,
     `SINK_DATABASE_IO`, `SINK_FILESYSTEM_IO`, `SINK_PROCESS_IO` -
     `SINK_TIME_IO`/`SINK_RANDOMNESS`/`SINK_PURE_COMPUTE` are
     deliberately excluded, per spec) OR its output category is Command
     with no outgoing packed CALLS/INSTANTIATES edge of its own.
  2. If any sink is reachable: the shortest path (fewest hops) from the
     seed to a sink, via a deterministic sorted-adjacency BFS (so the
     same `(seed, packed_ids, edges)` triple always yields the same
     path, in-process or across a fresh one). Multiple equally-short
     sink paths are broken first by the sink's own `dist_w` (smallest
     first - "earliest in the seed's distance-ordered candidate list"),
     then by qualified name, for full determinism.
  3. If no sink is reachable: the seed's longest forward chain - the
     most-hops-away reachable packed node, same tie-break.
  4. Capped at `MAX_PATH_LENGTH` (6) stages including the seed; a longer
     path is truncated to the first 6 and flagged.

`compute_causal_path_stages` returns the plain ordered list of qualified
names (never `None` - if the seed has no packed successor at all, the
path is just `[seed_id]`) plus the truncation flag; `stage_role` derives
each stage's rendered role from its position and sink status.
"""
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Callable

from prism.semantics.bitmask import FeatureBit

if TYPE_CHECKING:
    from prism.graph.symbol_table import SymbolInfo

MAX_PATH_LENGTH = 6

#: Phase D (Issues #24/#42 subsystem-scope rule; #37/#38 sink ranking)
#: reuses exactly the same 4-bit "hard I/O" subset `_is_sink` already
#: used, NOT the full 7-bit Substance axis (SUBSTANCE_BITS in
#: prism.semantics.bitmask, which also includes SINK_TIME_IO/
#: SINK_RANDOMNESS/SINK_PURE_COMPUTE). Confirmed empirically against the
#: real t02_006 regression this phase exists to fix: every symbol on the
#: real drift chain (django.contrib.auth.middleware.get_user ->
#: django.contrib.auth.get_user -> django.utils.crypto.
#: constant_time_compare -> django.utils.encoding.force_bytes ->
#: django.contrib.gis.geos.geometry.GEOSGeometryBase._from_wkt ->
#: django.contrib.gis.geos.prototypes.io.wkt_r) carries the *same one*
#: substance bit, SINK_PURE_COMPUTE, and nothing else - it is the
#: catch-all "does no special I/O" default nearly every plain utility
#: function gets, not a meaningful "these two symbols serve the same
#: real purpose" signal. Using the full 7-bit axis as the scope rule's
#: cross-package escape hatch made it pass at every single hop of the
#: real bug this phase is supposed to close, since PURE_COMPUTE trivially
#: overlaps between almost any two ordinary functions. The narrower
#: 4-bit "hard" I/O mask is a real, selective signal precisely because
#: it excludes that catch-all case.
_SUBSTANCE_SINK_MASK = int(
    FeatureBit.SINK_NETWORK_IO
    | FeatureBit.SINK_DATABASE_IO
    | FeatureBit.SINK_FILESYSTEM_IO
    | FeatureBit.SINK_PROCESS_IO
)
_OUTPUT_COMMAND = int(FeatureBit.OUTPUT_COMMAND)

#: A symbol's own `SymbolInfo`, or `None` if unknown - the same shape
#: `GlobalSymbolTable.get` already has, so a real caller (`prism.surface.
#: build.build_context_package`) passes `builder.symbol_table.get`
#: directly with no wrapper needed.
SymbolInfoLookup = Callable[[str], "SymbolInfo | None"]


def _substance_bits(mask: int) -> int:
    return mask & _SUBSTANCE_SINK_MASK


def _module_of(qname: str, symbol_info: SymbolInfoLookup) -> str:
    info = symbol_info(qname)
    return info.module if info is not None else ""


def _prefix3(module: str) -> str:
    return ".".join(module.split(".")[:3])


def _canonical_key(qname: str, symbol_info: SymbolInfoLookup) -> tuple[str, int, str]:
    """`(file_path, line_no, symbol_name)` - Invariant 3's tertiary
    tie-break, and also used for the pre-existing no-sink "longest
    chain" fallback's own tie-break (upgraded from a bare qname string
    for consistency; every synthetic single-file test fixture this
    module's own test suite uses has at most one node at the max hop
    distance, so this upgrade changes no existing test's outcome - there
    is no actual tie to break in any of them)."""
    info = symbol_info(qname)
    if info is None:
        return ("", 0, qname)
    return (info.file, info.line_range[0], qname.rsplit(".", 1)[-1])


def _edge_in_scope(u: str, v: str, feature_masks: dict[str, int], symbol_info: SymbolInfoLookup) -> bool:
    """Phase D Invariant 2 (Issues #24/#42 - the t02_006 GIS-drift bug:
    a causal_path wandered from django.contrib.auth into django.contrib.
    gis via a shared, widely-used utility function with no real semantic
    connection to the seed's own subsystem). An edge (u, v) is only ever
    traversed for causal-path synthesis if u and v share the same
    top-3-segment module prefix (the ordinary case - a same-subsystem
    call chain), OR their Substance tag sets overlap (a legitimate
    cross-package call - e.g. auth calling a shared crypto/pure-compute
    utility both tag the same way). Neither holding means the edge
    crosses into a genuinely unrelated subsystem - refused outright,
    regardless of what real graph edge otherwise connects them. An
    unresolvable module on either side (only possible for a synthetic/
    sentinel node, which causal-path edges should never contain in
    practice - see `build_context_package`'s own edge filtering) falls
    through to the substance-overlap check alone rather than treating
    two unknowns as trivially "the same prefix"."""
    u_module = _module_of(u, symbol_info)
    v_module = _module_of(v, symbol_info)
    if u_module and v_module and _prefix3(u_module) == _prefix3(v_module):
        return True
    u_substance = _substance_bits(feature_masks.get(u, 0))
    v_substance = _substance_bits(feature_masks.get(v, 0))
    return bool(u_substance & v_substance)


def _build_adjacency(
    edges: list[tuple[str, str]], feature_masks: dict[str, int], symbol_info: SymbolInfoLookup
) -> dict[str, list[str]]:
    adjacency: dict[str, set[str]] = {}
    for u, v in edges:
        if u == v:
            continue
        if not _edge_in_scope(u, v, feature_masks, symbol_info):
            continue
        adjacency.setdefault(u, set()).add(v)
    return {u: sorted(succs) for u, succs in adjacency.items()}


def _is_sink(qname: str, feature_masks: dict[str, int], adjacency: dict[str, list[str]], output_kind_fn: Callable[[int], str]) -> bool:
    mask = feature_masks.get(qname, 0)
    if mask & _SUBSTANCE_SINK_MASK:
        return True
    return bool(mask & _OUTPUT_COMMAND) and not adjacency.get(qname)


def _bfs(seed_id: str, adjacency: dict[str, list[str]]) -> tuple[dict[str, int], dict[str, str]]:
    """Deterministic shortest-hop BFS: `adjacency`'s own successor lists
    are pre-sorted, so traversal order - and therefore which predecessor
    "wins" a tie - is fixed independent of set/dict iteration order or
    process."""
    hop_dist = {seed_id: 0}
    predecessor: dict[str, str] = {}
    queue = deque([seed_id])
    while queue:
        node = queue.popleft()
        for succ in adjacency.get(node, ()):
            if succ not in hop_dist:
                hop_dist[succ] = hop_dist[node] + 1
                predecessor[succ] = node
                queue.append(succ)
    return hop_dist, predecessor


def _reconstruct(seed_id: str, target: str, predecessor: dict[str, str]) -> list[str]:
    path = [target]
    while path[-1] != seed_id:
        path.append(predecessor[path[-1]])
    path.reverse()
    return path


def compute_causal_path_stages(
    seed_id: str,
    packed_ids: set[str],
    edges: list[tuple[str, str]],
    dist_w_map: dict[str, float],
    feature_masks: dict[str, int],
    output_kind_fn: Callable[[int], str],
    symbol_info: SymbolInfoLookup,
    max_path_length: int = MAX_PATH_LENGTH,
) -> tuple[list[str], bool]:
    """Returns `(stage_qualified_names, truncated)` - `stage_qualified_
    names[0]` is always `seed_id`. `edges` must already be restricted to
    CALLS/INSTANTIATES pairs where both endpoints are in `packed_ids`
    (exactly what `build_context_package` already computes for
    `pkg.edges`) - `_build_adjacency` further restricts them to
    in-subsystem-scope pairs (Phase D Invariant 2) before any BFS runs,
    so `reachable`/`hop_dist`/`predecessor` below are already "reachable
    within scope", not "reachable at all".

    Fallback hierarchy (Phase D Invariant 3), a direct consequence of
    that scope-filtered BFS rather than separate logic: (1) the
    top-ranked in-scope sink, if any is reachable; (2) the seed's own
    longest in-scope chain, if no sink is reachable but something is;
    (3) `[seed_id]` alone (no further stages) if nothing survives the
    scope filter at all - the closest a `causal_path` that must always
    carry at least one stage (see `prism.surface.renderer`'s own
    "never rendered empty" invariant) can come to Invariant 3's own
    "emit an empty path" wording.
    """
    if seed_id not in packed_ids:
        return [seed_id], False

    adjacency = _build_adjacency(edges, feature_masks, symbol_info)
    hop_dist, predecessor = _bfs(seed_id, adjacency)
    reachable = [n for n in hop_dist if n != seed_id]

    if not reachable:
        return [seed_id], False

    sinks = [n for n in reachable if _is_sink(n, feature_masks, adjacency, output_kind_fn)]

    if sinks:
        # Invariant 3 ranking: substance overlap with the seed DESC,
        # then dijkstra distance from the seed ASC, then the canonical
        # (file, line, name) key - hop count is no longer part of the
        # ranking at all (a like-for-like replacement of the prior
        # hop-count-primary ranking, not an additional tie-break layered
        # on top of it).
        seed_substance = _substance_bits(feature_masks.get(seed_id, 0))

        def _sink_rank_key(n: str) -> tuple[int, float, tuple[str, int, str]]:
            overlap = bin(_substance_bits(feature_masks.get(n, 0)) & seed_substance).count("1")
            return (-overlap, dist_w_map.get(n, float("inf")), _canonical_key(n, symbol_info))

        target = min(sinks, key=_sink_rank_key)
    else:
        max_hops = max(hop_dist[n] for n in reachable)
        deepest = [n for n in reachable if hop_dist[n] == max_hops]
        target = min(deepest, key=lambda n: (dist_w_map.get(n, float("inf")), _canonical_key(n, symbol_info)))

    path = _reconstruct(seed_id, target, predecessor)
    truncated = len(path) > max_path_length
    if truncated:
        path = path[:max_path_length]
    return path, truncated


def _stage_role(index: int, stage_count: int, qname: str, feature_masks: dict[str, int], adjacency: dict[str, list[str]], output_kind_fn: Callable[[int], str]) -> str:
    """`entry` for the first stage (the seed), `sink`/`return` for the
    last (whichever fits `_is_sink`), `transform` for everything between."""
    if index == 0:
        return "entry"
    if index == stage_count - 1:
        return "sink" if _is_sink(qname, feature_masks, adjacency, output_kind_fn) else "return"
    return "transform"


def compute_causal_path(
    seed_id: str,
    packed_ids: set[str],
    edges: list[tuple[str, str]],
    dist_w_map: dict[str, float],
    feature_masks: dict[str, int],
    output_kind_fn: Callable[[int], str],
    symbol_info: SymbolInfoLookup,
    max_path_length: int = MAX_PATH_LENGTH,
) -> tuple[list[tuple[str, float, str]], bool]:
    """The single public entry point: `(stages, truncated)`, where each
    stage is `(symbol, distance, role)` - `distance` is `dist_w` rounded
    to 2 decimals (`0.0` for the seed itself), `role` already resolved
    per `_stage_role`. `order` is deliberately not included here - it's
    exactly `enumerate(stages, start=1)`, the caller's own concern
    (rendering, model construction) rather than this module's.

    `symbol_info` (Phase D): a `qname -> SymbolInfo | None` lookup (a
    real caller passes `builder.symbol_table.get` directly) - the source
    of both the subsystem-scope filter's module prefixes (Invariant 2)
    and the sink-ranking/no-sink-fallback canonical tie-break
    (Invariant 3)."""
    path, truncated = compute_causal_path_stages(
        seed_id, packed_ids, edges, dist_w_map, feature_masks, output_kind_fn, symbol_info, max_path_length
    )
    adjacency = _build_adjacency(edges, feature_masks, symbol_info)
    stages = [
        (
            qname,
            round(0.0 if qname == seed_id else dist_w_map.get(qname, 0.0), 2),
            _stage_role(i, len(path), qname, feature_masks, adjacency, output_kind_fn),
        )
        for i, qname in enumerate(path)
    ]
    return stages, truncated
