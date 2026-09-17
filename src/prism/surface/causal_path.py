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
from typing import Callable

from prism.semantics.bitmask import FeatureBit

MAX_PATH_LENGTH = 6

_SUBSTANCE_SINK_MASK = int(
    FeatureBit.SINK_NETWORK_IO
    | FeatureBit.SINK_DATABASE_IO
    | FeatureBit.SINK_FILESYSTEM_IO
    | FeatureBit.SINK_PROCESS_IO
)
_OUTPUT_COMMAND = int(FeatureBit.OUTPUT_COMMAND)


def _build_adjacency(edges: list[tuple[str, str]]) -> dict[str, list[str]]:
    adjacency: dict[str, set[str]] = {}
    for u, v in edges:
        if u == v:
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
    max_path_length: int = MAX_PATH_LENGTH,
) -> tuple[list[str], bool]:
    """Returns `(stage_qualified_names, truncated)` - `stage_qualified_
    names[0]` is always `seed_id`. `edges` must already be restricted to
    CALLS/INSTANTIATES pairs where both endpoints are in `packed_ids`
    (exactly what `build_context_package` already computes for
    `pkg.edges`)."""
    if seed_id not in packed_ids:
        return [seed_id], False

    adjacency = _build_adjacency(edges)
    hop_dist, predecessor = _bfs(seed_id, adjacency)
    reachable = [n for n in hop_dist if n != seed_id]

    if not reachable:
        return [seed_id], False

    sinks = [n for n in reachable if _is_sink(n, feature_masks, adjacency, output_kind_fn)]

    if sinks:
        target = min(sinks, key=lambda n: (hop_dist[n], dist_w_map.get(n, float("inf")), n))
    else:
        max_hops = max(hop_dist[n] for n in reachable)
        deepest = [n for n in reachable if hop_dist[n] == max_hops]
        target = min(deepest, key=lambda n: (dist_w_map.get(n, float("inf")), n))

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
    max_path_length: int = MAX_PATH_LENGTH,
) -> tuple[list[tuple[str, float, str]], bool]:
    """The single public entry point: `(stages, truncated)`, where each
    stage is `(symbol, distance, role)` - `distance` is `dist_w` rounded
    to 2 decimals (`0.0` for the seed itself), `role` already resolved
    per `_stage_role`. `order` is deliberately not included here - it's
    exactly `enumerate(stages, start=1)`, the caller's own concern
    (rendering, model construction) rather than this module's."""
    path, truncated = compute_causal_path_stages(seed_id, packed_ids, edges, dist_w_map, feature_masks, output_kind_fn, max_path_length)
    adjacency = _build_adjacency(edges)
    stages = [
        (
            qname,
            round(0.0 if qname == seed_id else dist_w_map.get(qname, 0.0), 2),
            _stage_role(i, len(path), qname, feature_masks, adjacency, output_kind_fn),
        )
        for i, qname in enumerate(path)
    ]
    return stages, truncated
