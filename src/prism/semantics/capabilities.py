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
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.semantics.bitmask import FeatureBit
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
    `own_l3`: the part of `own` matched only through Layer 3 stubs."""

    by_hop: tuple[int, ...]
    own_l3: int = 0

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
def _language_entries(lang_key: str) -> tuple[tuple[str, int, str], ...]:
    return tuple((entry, bits, layer) for entry, (bits, layer) in sorted(primitive_registry().get(lang_key, {}).items()))


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


def _direct_primitive_bits(def_node, parsed, import_map) -> tuple[int, int]:
    """`(own primitive bits, bits only Layer 3 stubs supplied)` for one
    function body, through the same three-phase scan as the Substance axis
    (`prism.semantics.substance._direct_call_bits`). Unlike Substance's
    first-match-per-category rule, every matching entry contributes."""
    lang_key = _LANG_REGISTRY_KEY.get(parsed.language_id)
    if lang_key is None:
        return 0, 0
    entries = _language_entries(lang_key)
    via_l2 = 0
    via_l3 = 0

    def match(segments: list[str], node) -> FeatureBit:
        nonlocal via_l2, via_l3
        shape = _call_shape(segments, import_map)
        bits = 0
        for entry, entry_bits, layer in entries:
            if _entry_matches(entry, *shape):
                if entry in _PY_OPEN_ENTRIES and lang_key == "python":
                    entry_bits = _python_open_mode_bits(node, parsed.source, entry, entry_bits)
                bits |= entry_bits
                if layer == "L2":
                    via_l2 |= entry_bits
                else:
                    via_l3 |= entry_bits
        return FeatureBit(bits)

    def fallback(method: str) -> FeatureBit:
        category = _UNAMBIGUOUS_DRIVER_SUFFIXES.get(method)
        return FeatureBit(derive_primitive_bits(category, method)) if category else FeatureBit(0)

    own = int(_direct_call_bits(def_node, parsed, match, fallback))
    return own, via_l3 & ~via_l2


def compute_capability_profiles(builder: ConcreteGraphBuilder, max_hops: int = MAX_HOPS) -> dict[str, CapabilityProfile]:
    """`{qualified_name: CapabilityProfile}` for every function/method.
    `max_hops` (0-2) bounds propagation; callees are only repo symbols, so
    nothing propagates out of an external or sentinel node."""
    if not 0 <= max_hops <= MAX_HOPS:
        raise ValueError(f"max_hops must be within 0..{MAX_HOPS}, got {max_hops}")
    own: dict[str, int] = {}
    own_l3: dict[str, int] = {}
    import_maps: dict[str, object] = {}
    for symbol in builder.symbol_table:
        if symbol.kind not in ("function", "method"):
            continue
        def_node = builder.def_node(symbol.qualified_name)
        parsed = builder.parsed_file(symbol.file)
        if def_node is None or parsed is None:
            own[symbol.qualified_name] = own_l3[symbol.qualified_name] = 0  # nothing to read, still profiled
            continue
        if symbol.module not in import_maps:
            import_maps[symbol.module] = builder._build_import_map(parsed, symbol.module)
        own[symbol.qualified_name], own_l3[symbol.qualified_name] = _direct_primitive_bits(
            def_node, parsed, import_maps[symbol.module]
        )

    graph = builder.graph

    def callees(qname: str) -> list[str]:
        if qname not in graph:
            return []
        return [
            t for _u, t, d in graph.out_edges(qname, data=True)
            if d.get("relation", "CALLS") in _PROPAGATING_RELATIONS and t in own
        ]

    profiles: dict[str, CapabilityProfile] = {}
    for qname, bits in own.items():
        by_hop = [bits] + [0] * MAX_HOPS
        seen, frontier = {qname}, [qname]
        for hop in range(1, max_hops + 1):
            next_frontier = []
            for node in frontier:
                for callee in callees(node):
                    if callee not in seen:
                        seen.add(callee)
                        next_frontier.append(callee)
                        by_hop[hop] |= own[callee]
            frontier = next_frontier
        profiles[qname] = CapabilityProfile(tuple(by_hop), own_l3.get(qname, 0))
    return profiles


def _groups(target: "CompositeProfile | str | int") -> tuple[int, ...]:
    if isinstance(target, str):
        target = COMPOSITE_PROFILES[target]
    if isinstance(target, CompositeProfile):
        return target.groups
    return (int(target),)  # a plain primitive mask is one any-of group


def capability_score(profile: CapabilityProfile | None, target: "CompositeProfile | str | int") -> float:
    """0-1 soft signal: the best hop-decayed coverage of `target` by what
    the function reaches within each hop distance. A composite is covered
    in proportion to the share of its groups present; a primitive mask
    (any of its bits) counts as one group."""
    if profile is None:
        return 0.0
    groups = _groups(target)
    best = 0.0
    for hop, decay in enumerate(HOP_DECAY[: len(profile.by_hop)]):
        bits = profile.within(hop)
        coverage = sum(1 for g in groups if bits & g) / len(groups)
        best = max(best, decay * coverage)
    return best


def composites_of(profile: CapabilityProfile | None, max_hops: int = MAX_HOPS) -> list[str]:
    """The composite capabilities fully present within `max_hops` - labels
    for display and for a Turn-1 shortlist, not a filter."""
    if profile is None:
        return []
    bits = profile.within(max_hops)
    return [name for name, composite in COMPOSITE_PROFILES.items() if composite.coverage(bits) == 1.0]


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
