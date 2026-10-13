"""Capability profiles: atomic primitives per function, propagated a bounded
1-2 hops through the repo's own call graph, scored as soft signals
(Phase 2, Operation Groundwire).

- `compute_capability_profiles(builder)` gives every function/method its
  own primitive bits (calls matched against the Layer 2 table and the
  temporary Layer 3 stubs, `prism.semantics.primitives`), plus the bits its
  callees reach one and two hops away along repo-internal CALLS /
  INSTANTIATES edges. The bits that only a Layer 3 stub supplied are kept
  apart (`own_l3`), so Phase 3 can measure what dependency indexing
  replaces.
- `capability_score(profile, target)` turns a profile into a 0-1 signal for
  a primitive mask or a composite (`COMPOSITE_PROFILES`), decayed by hop
  distance.
- `soft_rerank(...)` applies that signal as a bounded multiplicative boost.
  It never removes a candidate and never reorders anything when every
  signal is zero: composite tags are soft scoring signals, never hard
  filters (a hard structural gate is what regressed F1 before).

Composites follow the distinct-source rule (`bitmask.distinct_source_
matches`): each part must come from its own source - one call site in the
function, or one callee as a whole - unless a callee already satisfies
the composite by itself. Calls into a summarized dependency (graph nodes
carrying `boundary_bits`, `prism.deps.host`) count at hop 1 with the
dependency's summary bits; TENTATIVE summary bits are valid soft signals
and are recorded in `CapabilityProfile.tentative`.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.semantics.bitmask import FeatureBit, distinct_source_matches
from prism.semantics.primitives import COMPOSITE_PROFILES, CompositeProfile, derive_primitive_bits, primitive_registry
from prism.semantics.substance import (
    _LANG_REGISTRY_KEY,
    _UNAMBIGUOUS_DRIVER_SUFFIXES,
    _call_shape,
    _direct_call_bits,
    _entry_matches,
)

#: Weight of a capability reached 0, 1 and 2 hops away. Bounded on purpose:
#: past two hops a callee's I/O says little about the caller's purpose.
HOP_DECAY: tuple[float, ...] = (1.0, 0.5, 0.25)
MAX_HOPS = len(HOP_DECAY) - 1
_PROPAGATING_RELATIONS = ("CALLS", "INSTANTIATES")


@dataclass(frozen=True)
class CapabilityProfile:
    """Primitive bits a function performs itself (`own`) and those its
    callees reach exactly 1 and 2 hops away (`by_hop[1]`, `by_hop[2]`).

    - `own_l3`: the part of `own` matched only through Layer 3 stubs.
    - `sources[h]`: the separate source masks at hop `h` - one per call
      site for hop 0, one per callee beyond - for the distinct-source rule.
      `None` (a hand-built profile) falls back to one shared mask per hop.
    - `established[h]`: composites a hop-`h` source satisfies on its own
      (hop 0: this function's own composites).
    - `tentative`: bits whose only evidence is a TENTATIVE dependency
      summary."""

    by_hop: tuple[int, ...]
    own_l3: int = 0
    sources: "tuple[tuple[int, ...], ...] | None" = None
    established: "tuple[frozenset[str], ...] | None" = None
    tentative: int = 0

    @property
    def own(self) -> int:
        return self.by_hop[0]

    def within(self, hops: int) -> int:
        """Every primitive reached within `hops` hops."""
        bits = 0
        for h in range(min(hops, len(self.by_hop) - 1) + 1):
            bits |= self.by_hop[h]
        return bits


@lru_cache(maxsize=None)
def _language_entries(lang_key: str, include_l3_stubs: bool = True) -> tuple[tuple[str, int, str], ...]:
    return tuple(
        (entry, bits, layer)
        for entry, (bits, layer) in sorted(primitive_registry().get(lang_key, {}).items())
        if include_l3_stubs or layer == "L2"
    )


#: Python file-open entries whose direction depends on the `mode` argument.
_PY_OPEN_ENTRIES = frozenset({"open", "builtins.open", "pathlib.Path.open"})
_FS_BOTH = int(FeatureBit.PRIM_FS_READ | FeatureBit.PRIM_FS_WRITE)


def _python_open_mode_bits(call_node, source: bytes, entry: str, default: int) -> int:
    """Narrow `open(path, mode)` / `path.open(mode)` to read or write from a
    literal mode: none or `r` reads, `w`/`a`/`x` write, `+` does both. A
    non-literal mode keeps `default` (both)."""
    if default != _FS_BOTH:
        return default
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return default
    positional = [a for a in args.named_children if a.type != "keyword_argument"]
    mode_node = None
    for a in args.named_children:
        if a.type == "keyword_argument" and (name := a.child_by_field_name("name")) is not None and name.text == b"mode":
            mode_node = a.child_by_field_name("value")
    if mode_node is None:
        index = 0 if entry == "pathlib.Path.open" else 1
        mode_node = positional[index] if len(positional) > index else None
    if mode_node is None:
        return int(FeatureBit.PRIM_FS_READ)  # default mode "r"
    if mode_node.type != "string":
        return default
    mode = "".join(c.text.decode(errors="replace") for c in mode_node.named_children if c.type == "string_content")
    if "+" in mode:
        return _FS_BOTH
    if any(ch in mode for ch in "wax"):
        return int(FeatureBit.PRIM_FS_WRITE)
    return int(FeatureBit.PRIM_FS_READ)


def _entry_root(entry: str) -> str:
    return entry.split("/")[0].split(".")[0]


def _direct_primitive_bits(
    def_node, parsed, import_map, include_l3_stubs: bool = True, summarized: frozenset[str] = frozenset()
) -> tuple[int, int, tuple[int, ...]]:
    """`(own primitive bits, bits only Layer 3 stubs supplied, per-call-site
    masks)` for one function body, through the same three-phase scan as the
    Substance axis (`prism.semantics.substance._direct_call_bits`). Unlike
    Substance's first-match-per-category rule, every matching entry
    contributes. Bits from the scan's fallbacks that no single call site
    explains form one extra trailing site. A Layer 3 stub of a package in
    `summarized` is skipped: that package's calls are read from its offline
    summary instead (boundary nodes), never counted twice."""
    lang_key = _LANG_REGISTRY_KEY.get(parsed.language_id)
    if lang_key is None:
        return 0, 0, ()
    entries = _language_entries(lang_key, include_l3_stubs)
    via_l2 = 0
    via_l3 = 0
    sites: list[int] = []

    def match(segments: list[str], node) -> FeatureBit:
        nonlocal via_l2, via_l3
        shape = _call_shape(segments, import_map)
        bits = 0
        for entry, entry_bits, layer in entries:
            if layer != "L2" and summarized and _entry_root(entry) in summarized:
                continue
            if _entry_matches(entry, *shape):
                if entry in _PY_OPEN_ENTRIES and lang_key == "python":
                    entry_bits = _python_open_mode_bits(node, parsed.source, entry, entry_bits)
                bits |= entry_bits
                if layer == "L2":
                    via_l2 |= entry_bits
                else:
                    via_l3 |= entry_bits
        if bits:
            sites.append(bits)
        return FeatureBit(bits)

    def fallback(method: str) -> FeatureBit:
        category = _UNAMBIGUOUS_DRIVER_SUFFIXES.get(method)
        return FeatureBit(derive_primitive_bits(category, method)) if category else FeatureBit(0)

    own = int(_direct_call_bits(def_node, parsed, match, fallback))
    explained = 0
    for site in sites:
        explained |= site
    if own & ~explained:
        sites.append(own & ~explained)
    return own, via_l3 & ~via_l2, tuple(sites)


def _own_composites(sources: "tuple[int, ...] | list[int]") -> frozenset[str]:
    return frozenset(
        name for name, c in COMPOSITE_PROFILES.items() if distinct_source_matches(c.groups, sources) == len(c.groups)
    )


def compute_capability_profiles(
    builder: ConcreteGraphBuilder, max_hops: int = MAX_HOPS, include_l3_stubs: bool = True
) -> dict[str, CapabilityProfile]:
    """`{qualified_name: CapabilityProfile}` for every function/method.
    `max_hops` (0-2) bounds propagation through repo callees; a summarized
    dependency entry point (a graph node with `boundary_bits`) is a leaf
    callee carrying its summary. `include_l3_stubs=False` matches calls
    against the Layer 2 table only (what the offline summarizer uses)."""
    if not 0 <= max_hops <= MAX_HOPS:
        raise ValueError(f"max_hops must be within 0..{MAX_HOPS}, got {max_hops}")
    own: dict[str, int] = {}
    own_l3: dict[str, int] = {}
    sites: dict[str, tuple[int, ...]] = {}
    import_maps: dict[str, object] = {}
    graph = builder.graph
    boundary = {
        n: (int(a.get("boundary_bits", 0)), int(a.get("boundary_tentative_bits", 0)))
        for n, a in graph.nodes(data=True)
        if a.get("boundary")
    }
    summarized = frozenset(a["boundary_package"] for _, a in graph.nodes(data=True) if a.get("boundary_package"))
    for symbol in builder.symbol_table:
        if symbol.kind not in ("function", "method"):
            continue
        q = symbol.qualified_name
        def_node = builder.def_node(q)
        parsed = builder.parsed_file(symbol.file)
        if def_node is None or parsed is None:
            own[q], own_l3[q], sites[q] = 0, 0, ()  # nothing to read, still profiled
            continue
        if symbol.module not in import_maps:
            import_maps[symbol.module] = builder._build_import_map(parsed, symbol.module)
        own[q], own_l3[q], sites[q] = _direct_primitive_bits(
            def_node, parsed, import_maps[symbol.module], include_l3_stubs, summarized
        )
    own_comps = {q: _own_composites(s) for q, s in sites.items()}

    def callees(qname: str) -> list[str]:
        if qname not in graph:
            return []
        return [
            t for _u, t, d in graph.out_edges(qname, data=True)
            if d.get("relation", "CALLS") in _PROPAGATING_RELATIONS and (t in own or t in boundary)
        ]

    profiles: dict[str, CapabilityProfile] = {}
    empty: frozenset[str] = frozenset()
    for qname, bits in own.items():
        by_hop = [bits] + [0] * MAX_HOPS
        hop_sources: list[list[int]] = [list(sites[qname])] + [[] for _ in range(MAX_HOPS)]
        hop_established: list[set[str]] = [set(own_comps[qname])] + [set() for _ in range(MAX_HOPS)]
        tentative = 0
        seen, frontier = {qname}, [qname]
        for hop in range(1, max_hops + 1):
            next_frontier = []
            for node in frontier:
                for callee in callees(node):
                    if callee in seen:
                        continue
                    seen.add(callee)
                    if callee in boundary:
                        mask, tentative_mask = boundary[callee]
                        tentative |= tentative_mask
                        comps = empty  # a summary is one source; its incidental bits never establish a composite
                    else:
                        next_frontier.append(callee)
                        mask, comps = own[callee], own_comps[callee]
                    by_hop[hop] |= mask
                    if mask:
                        hop_sources[hop].append(mask)
                    hop_established[hop] |= comps
            frontier = next_frontier
        confirmed = 0
        for b in by_hop:
            confirmed |= b
        profiles[qname] = CapabilityProfile(
            tuple(by_hop),
            own_l3.get(qname, 0),
            tuple(tuple(s) for s in hop_sources),
            tuple(frozenset(e) for e in hop_established),
            tentative & confirmed,
        )
    return profiles


def _groups(target: "CompositeProfile | str | int") -> tuple[int, ...]:
    if isinstance(target, str):
        target = COMPOSITE_PROFILES[target]
    if isinstance(target, CompositeProfile):
        return target.groups
    return (int(target),)  # a plain primitive mask is one any-of group


def _coverage(profile: CapabilityProfile, target: "CompositeProfile | str | int", hops: int) -> float:
    """Share of `target`'s groups covered within `hops` hops, under the
    distinct-source rule (a plain primitive mask is one any-of group)."""
    if isinstance(target, str):
        target = COMPOSITE_PROFILES[target]
    if not isinstance(target, CompositeProfile):
        return 1.0 if profile.within(hops) & int(target) else 0.0
    if profile.sources is None:  # hand-built profile: one shared mask per hop
        return target.coverage(profile.within(hops))
    last = min(hops, len(profile.by_hop) - 1)
    if profile.established is not None and any(target.name in profile.established[h] for h in range(last + 1)):
        return 1.0
    sources = [mask for h in range(last + 1) for mask in profile.sources[h]]
    return target.coverage_from_sources(sources)


def capability_score(profile: CapabilityProfile | None, target: "CompositeProfile | str | int") -> float:
    """0-1 soft signal: the best hop-decayed coverage of `target` by what
    the function reaches within each hop distance. A composite is covered
    in proportion to the share of its groups present, each from its own
    source; a primitive mask (any of its bits) counts as one group.
    TENTATIVE dependency bits count in full - this is a soft signal."""
    if profile is None:
        return 0.0
    return max(decay * _coverage(profile, target, hop) for hop, decay in enumerate(HOP_DECAY[: len(profile.by_hop)]))


def composites_of(profile: CapabilityProfile | None, max_hops: int = MAX_HOPS) -> list[str]:
    """The composite capabilities fully present within `max_hops` under the
    distinct-source rule - labels for display and for a Turn-1 shortlist,
    not a filter."""
    if profile is None:
        return []
    return [name for name, composite in COMPOSITE_PROFILES.items() if _coverage(profile, composite, max_hops) == 1.0]


def soft_rerank(
    candidates: list[str],
    base_scores: dict[str, float],
    profiles: dict[str, CapabilityProfile],
    target: "CompositeProfile | str | int",
    weight: float = 0.25,
) -> list[tuple[str, float]]:
    """`candidates` reordered by `base * (1 + weight * capability_score)`.

    Every candidate is kept, the boost is at most `1 + weight`, and equal
    adjusted scores keep their input order - so with `weight == 0` or no
    capability signal the input order is returned unchanged."""
    if weight < 0:
        raise ValueError("weight must be >= 0")
    scored = [
        (qname, base_scores.get(qname, 0.0) * (1.0 + weight * capability_score(profiles.get(qname), target)), i)
        for i, qname in enumerate(candidates)
    ]
    scored.sort(key=lambda row: (-row[1], row[2]))
    return [(qname, score) for qname, score, _ in scored]
