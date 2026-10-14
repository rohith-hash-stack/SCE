"""v1.1: The Four-Axis Coordinate Space, encoded as a single unsigned
64-bit bitmask per symbol.

Every symbol `v` gets a 4-tuple coordinate `Phi(v) = (S(v), F(v), O(v),
R(v))` - Substance, Form, Output, Role (see `prism.semantics.substance`/
`form`/`output`/`role` for how each axis is extracted). To keep the
submodular knapsack's inner selection loop (`prism.packer.
submodular_knapsack`) sub-millisecond across 10,000+ nodes, no axis is
ever represented as a set of strings or an object hash in that loop -
every symbol's full coordinate is packed into one `int` (Python ints are
arbitrary-precision, but every value this module ever produces fits
inside 64 bits) so marginal-coverage comparisons reduce to bitwise `&`/`|`
and `int.bit_count()` (a single CPython opcode-level operation, not a
Python-level loop over a set).

Bit layout (bits 0-63), exactly as specified:

    Bits  0- 9  Substance (bits 0-6 used; 7 = HAS_REFLECTION modifier;
                8-9 reserved)
    Bits 10-24  Form (motifs)
    Bits 25-34  Output (return contract)
    Bits 35-44  Role (topological neighborhood)
    Bits 45-60  Atomic primitives (Phase 2): the physical operations the
                Layer 2 runtime exposes - see `PRIMITIVE_BITS`. Carried in
                capability profiles (`prism.semantics.capabilities`), not
                in the four-axis feature mask.
    Bits 61-63  Reserved
"""
from __future__ import annotations

from enum import IntFlag


class FeatureBit(IntFlag):
    # -- Substance: Bits 0-9 (behavioral sinks) --------------------------- #
    SINK_NETWORK_IO = 1 << 0
    SINK_DATABASE_IO = 1 << 1
    SINK_FILESYSTEM_IO = 1 << 2
    SINK_PROCESS_IO = 1 << 3
    SINK_TIME_IO = 1 << 4
    SINK_RANDOMNESS = 1 << 5
    SINK_PURE_COMPUTE = 1 << 6
    # -- Modifier (not an axis value): bit 7 ------------------------------- #
    #: The function's own body contains a dynamic-hazard construct
    #: (reflection, `eval`/`exec`, dynamic import or namespace lookup,
    #: subscript dispatch, JS `Reflect`/`Proxy`/`Function`) - see
    #: `prism.graph.call_site.has_dynamic_hazard_construct`. Mutually
    #: exclusive with `SINK_PURE_COMPUTE` (what such a body does can't be
    #: read statically); never propagated to callers. Deliberately not
    #: `SINK_`-prefixed, so it is no Substance value and no sink.
    HAS_REFLECTION = 1 << 7

    # -- Form (Motifs): Bits 10-24 ----------------------------------------- #
    FORM_LINEAR = 1 << 10
    FORM_RETRY_LOOP = 1 << 11
    FORM_BRANCH_DISPATCH = 1 << 12
    FORM_PIPELINE = 1 << 13
    FORM_GUARD_EARLY_EXIT = 1 << 14
    FORM_VALIDATOR = 1 << 15
    FORM_BATCH_LOOP = 1 << 16
    FORM_WRAPPED_TRY = 1 << 17
    FORM_RECURSIVE = 1 << 18
    FORM_ASYNC_CONCURRENT = 1 << 19

    # -- Output Categories: Bits 25-34 ------------------------------------- #
    OUTPUT_PREDICATE = 1 << 25  # bool
    OUTPUT_COMMAND = 1 << 26  # None / void
    OUTPUT_QUERY = 1 << 27  # returns existing T
    OUTPUT_FACTORY = 1 << 28  # fresh instance T(...)
    OUTPUT_TRANSFORMER = 1 << 29  # takes T, returns modified T
    OUTPUT_AGGREGATOR = 1 << 30  # collection/list/dict
    OUTPUT_FLUENT = 1 << 31  # returns self/this
    OUTPUT_ASYNC_DEFERRED = 1 << 32  # Promise/Future/Coroutine
    OUTPUT_GUARD = 1 << 33  # raises/panics unconditionally

    # -- Roles: Bits 35-44 -------------------------------------------------- #
    ROLE_ENTRYPOINT = 1 << 35  # in=0, out>0
    ROLE_ORCHESTRATOR = 1 << 36  # fan_out >= 4, fan_in <= 2
    ROLE_ADAPTER = 1 << 37  # bridges two distinct substance domains
    ROLE_LEAF_UTILITY = 1 << 38  # fan_in >= 5, fan_out <= 1, pure
    ROLE_BRIDGE = 1 << 39  # fan_in >= 5, fan_out >= 5
    ROLE_PUBLIC_API = 1 << 40  # exported, in=0
    ROLE_LEAF_SERVICE = 1 << 41  # fan_out <= 1, has side-effect sink

    # -- Atomic primitives: Bits 45-60 (Phase 2) ---------------------------- #
    # One bit per physical operation a Layer 2 runtime primitive performs
    # (`prism.semantics.primitives` derives them for every catalog entry).
    # Composite capabilities (`ASSET_EGRESS`, ...) are bitwise ORs of these.
    PRIM_NET_CONNECT = 1 << 45  # outbound connection / request
    PRIM_NET_LISTEN = 1 << 46  # bind, listen, accept, serve
    PRIM_NET_DNS = 1 << 47  # name resolution
    PRIM_NET_TLS = 1 << 48  # TLS/SSL session
    PRIM_FS_READ = 1 << 49  # read file content or metadata
    PRIM_FS_WRITE = 1 << 50  # create or modify files/directories
    PRIM_FS_DELETE = 1 << 51  # remove files/directories
    PRIM_DB_CONNECT = 1 << 52  # open a database/cache connection or pool
    PRIM_DB_READ = 1 << 53  # query/read stored data
    PRIM_DB_WRITE = 1 << 54  # insert/update/delete/commit
    PRIM_PROC_SPAWN = 1 << 55  # start a process/worker
    PRIM_PROC_SIGNAL = 1 << 56  # signal/kill a process
    PRIM_TIME_CLOCK = 1 << 57  # read a clock
    PRIM_TIME_WAIT = 1 << 58  # sleep, timer, scheduled callback
    PRIM_RAND_PRNG = 1 << 59  # pseudo-random generator
    PRIM_RAND_CSPRNG = 1 << 60  # cryptographic randomness / random UUIDs


#: Axis membership tables - which `FeatureBit` members belong to each of
#: the four axes, derived from `FeatureBit` itself (not hand-duplicated)
#: so a future bit addition can't silently fall out of sync with its own
#: axis grouping. Used by `prism.semantics.extractor` to compose one
#: symbol's four independently-extracted axis values into a single mask,
#: and by tests to assert axis isolation (setting a Substance bit never
#: touches a Form/Output/Role bit).
SUBSTANCE_BITS = frozenset(b for b in FeatureBit if b.name and b.name.startswith("SINK_"))
FORM_BITS = frozenset(b for b in FeatureBit if b.name and b.name.startswith("FORM_"))
OUTPUT_BITS = frozenset(b for b in FeatureBit if b.name and b.name.startswith("OUTPUT_"))
ROLE_BITS = frozenset(b for b in FeatureBit if b.name and b.name.startswith("ROLE_"))

#: The 64-bit-clean mask of every bit any axis actually assigns - a sanity
#: bound (`compose_mask`'s own output is always a subset of this), and the
#: definitive answer to "is bit N reserved" without re-deriving it.
#: Modifier bits: flags about a symbol that belong to no axis.
MODIFIER_BITS = frozenset({FeatureBit.HAS_REFLECTION})
#: Atomic primitive bits (Phase 2) - capability-profile bits, no axis.
PRIMITIVE_BITS = frozenset(b for b in FeatureBit if b.name and b.name.startswith("PRIM_"))
#: Ambient primitives: clocks, timers and randomness that almost every
#: client library touches incidentally (timeouts, backoff jitter, request
#: ids) - not physical external I/O.
AMBIENT_PRIMITIVE_MASK = int(
    FeatureBit.PRIM_TIME_CLOCK | FeatureBit.PRIM_TIME_WAIT | FeatureBit.PRIM_RAND_PRNG | FeatureBit.PRIM_RAND_CSPRNG
)
#: Operational I/O primitives: network (connect, listen, DNS, TLS),
#: filesystem, database and process - every primitive that is not ambient.
OPERATIONAL_IO_MASK = int(
    FeatureBit.PRIM_NET_CONNECT | FeatureBit.PRIM_NET_LISTEN | FeatureBit.PRIM_NET_DNS | FeatureBit.PRIM_NET_TLS
    | FeatureBit.PRIM_FS_READ | FeatureBit.PRIM_FS_WRITE | FeatureBit.PRIM_FS_DELETE
    | FeatureBit.PRIM_DB_CONNECT | FeatureBit.PRIM_DB_READ | FeatureBit.PRIM_DB_WRITE
    | FeatureBit.PRIM_PROC_SPAWN | FeatureBit.PRIM_PROC_SIGNAL
)
ALL_KNOWN_BITS = SUBSTANCE_BITS | FORM_BITS | OUTPUT_BITS | ROLE_BITS | MODIFIER_BITS | PRIMITIVE_BITS
#: Bits a context pack can "cover" (four axes only) - packing and coverage
#: reports never count modifier or primitive bits as features.
AXIS_BITS = SUBSTANCE_BITS | FORM_BITS | OUTPUT_BITS | ROLE_BITS


def compose_mask(*axis_values: "FeatureBit | int") -> int:
    """OR every axis's own `FeatureBit` value together into one plain
    `int` bitmask - the canonical way `prism.semantics.extractor` builds
    `Phi(v)`. Plain `int` (not `FeatureBit`) is returned deliberately:
    every downstream consumer (`prism.packer.submodular_knapsack`'s
    marginal-gain loop, the SQLite cache's `feature_bitmasks` blob) wants
    a bare integer, not an `IntFlag` wrapper, for the fastest possible
    `&`/`|`/`bit_count()` in the hot path.
    """
    mask = 0
    for value in axis_values:
        mask |= int(value)
    return mask


def describe_mask(mask: int) -> list[str]:
    """Every known `FeatureBit` name set in `mask`, sorted by bit
    position - a debugging/test-assertion convenience, never used in the
    knapsack's own hot path (which only ever needs the raw int)."""
    return [b.name for b in sorted(ALL_KNOWN_BITS, key=lambda f: int(f)) if mask & int(b) and b.name]


def distinct_source_matches(groups: "tuple[int, ...] | list[int]", sources: "tuple[int, ...] | list[int]") -> int:
    """The distinct-source rule for composite capabilities: how many of
    `groups` (each an any-of primitive mask) can be satisfied when every
    group must be met by a *different* source mask (one call site, or one
    callee's whole summary).

    A composite like ASSET_EGRESS (read a file, send over the network)
    means two operations; a single library call that *incidentally*
    touches both - `requests.post` reads TLS key files and sleeps between
    retries - must not count as both. Maximum bipartite matching between
    groups and sources (augmenting paths; both sides are tiny)."""
    owner: dict[int, int] = {}  # source index -> group index

    def assign(g: int, seen: set[int]) -> bool:
        for s, mask in enumerate(sources):
            if mask & groups[g] and s not in seen:
                seen.add(s)
                if s not in owner or assign(owner[s], seen):
                    owner[s] = g
                    return True
        return False

    return sum(1 for g in range(len(groups)) if assign(g, set()))
