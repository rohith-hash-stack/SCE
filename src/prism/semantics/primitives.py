"""Atomic primitives and composite capability profiles (Phase 2, Operation
Groundwire).

Every entry of the Layer 2 primitive table
(`prism.semantics.primitives_l2`) and of the temporary Layer 3 stub table
(`prism.semantics.boundary_stubs_l3`) is mapped to the atomic
`FeatureBit.PRIM_*` operations it performs, from its Substance category plus
the name of the operation (`derive_primitive_bits`). The mapping is
deterministic and table-free: adding an entry to either layer table needs
no second edit here.

Composite capabilities (`COMPOSITE_PROFILES`) are not stored anywhere; they
are bitwise ORs of primitives, matched against a symbol's capability
profile as soft scoring signals (`prism.semantics.capabilities`), never as
hard filters.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from prism.semantics.bitmask import FeatureBit, distinct_source_matches
from prism.semantics.boundary_stubs_l3 import L3_BOUNDARY_STUBS
from prism.semantics.primitives_l2 import L2_PRIMITIVE_ENTRIES

F = FeatureBit

#: Every primitive bit belonging to one legacy Substance category.
CATEGORY_PRIMITIVES: dict[str, int] = {
    "network_io": int(F.PRIM_NET_CONNECT | F.PRIM_NET_LISTEN | F.PRIM_NET_DNS | F.PRIM_NET_TLS),
    "filesystem_io": int(F.PRIM_FS_READ | F.PRIM_FS_WRITE | F.PRIM_FS_DELETE),
    "database_io": int(F.PRIM_DB_CONNECT | F.PRIM_DB_READ | F.PRIM_DB_WRITE),
    "process_io": int(F.PRIM_PROC_SPAWN | F.PRIM_PROC_SIGNAL),
    "time_io": int(F.PRIM_TIME_CLOCK | F.PRIM_TIME_WAIT),
    "randomness": int(F.PRIM_RAND_PRNG | F.PRIM_RAND_CSPRNG),
}

# -- Derivation rules ---------------------------------------------------- #
_NET_LISTEN = frozenset({
    "accept", "createserver", "create_server", "listen", "listentcp", "listenudp", "listenpacket", "server", "newserver",
    "start_server", "start_unix_server", "create_unix_server", "sock_accept",
})
#: Name resolution by leaf name (beside `dns.*` and `resolve*` / `lookup*`).
_NET_DNS = frozenset({"getaddrinfo", "getnameinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"})
_NET_TLS_ROOTS = frozenset({"ssl", "tls", "crypto/tls", "https"})

_DB_CONNECT = frozenset({
    "connect", "connectconfig", "create_engine", "create_pool", "createconnection", "createpool", "createclient",
    "open", "opendb", "ping", "pingcontext", "cursor", "mongoclient", "newclient", "database", "collection",
    "getrepository", "getconnection", "datasource", "client", "pool", "redis", "redisclient", "sequelize",
    "prismaclient", "begin", "begintx", "prepare", "preparecontext",
})
_DB_READ = frozenset({"get", "hget", "all", "first", "queryrow", "queryrowcontext", "$queryraw"})
_DB_READ_WRITE = frozenset({"execute", "execute_command", "exec_driver_sql", "execute_driver_sql", "query", "querycontext", "raw"})
_DB_WRITE_PREFIXES = (
    "insert", "update", "delete", "set", "hset", "lpush", "rpush", "sadd", "srem", "expire", "del", "destroy", "save",
    "create", "add", "commit", "rollback", "remove", "exec", "run", "$executeraw",
)

_FS_DELETE = frozenset({"remove", "removeall", "rm", "rmdir", "rmtree", "unlink"})
#: Generic handles and copies touch both directions.
_FS_READ_WRITE = frozenset({"open", "openfile", "mmap", "zipfile", "close", "seek", "extract", "extractall"})
_FS_READ_WRITE_PREFIXES = ("copy", "cp")
#: Prefix-matched against the operation name (after dropping Node's `Sync`
#: suffix) - never substrings: `readdir` contains "add".
_FS_READ_PREFIXES = ("read", "pread", "stat", "lstat", "listdir", "scandir", "walk", "watch")
_FS_WRITE_PREFIXES = (
    "write", "pwrite", "append", "mkdir", "makedirs", "move", "rename", "replace", "chmod", "chown", "link",
    "symlink", "hardlink", "touch", "truncate", "create", "mkstemp", "mkdtemp", "temp", "namedtemporary",
    "spooledtemporary", "add", "sync",
)

_TIME_CLOCK = frozenset({"monotonic", "perf_counter", "time", "now", "since"})
_PROC_SIGNAL = frozenset({"kill", "signal", "findprocess"})
_CSPRNG_ROOTS = ("secrets", "crypto", "globalThis.crypto")
_NON_RANDOM_UUIDS = frozenset({"uuid1", "uuid3", "uuid5", "uuid6"})


def _leaf(entry: str) -> str:
    return entry.rsplit("/", 1)[-1].rsplit(".", 1)[-1].lower()


def _root(entry: str) -> str:
    if "/" in entry and "." in entry.split("/")[0]:
        return entry.rsplit(".", 1)[0]  # Go module path: the import path itself
    return entry.split(".")[0]


def derive_primitive_bits(category: str, entry: str) -> int:
    """The atomic operations one catalog entry (or a bare method name)
    performs. Always a non-empty subset of its category's primitives: an
    operation the rules can't narrow down gets every plausible bit, never
    none."""
    leaf, root = _leaf(entry), _root(entry)
    lowered = entry.lower()
    if category == "network_io":
        if root == "dns" or leaf.startswith(("resolve", "lookup")) or leaf in _NET_DNS:
            return int(F.PRIM_NET_DNS)
        tls = root in _NET_TLS_ROOTS or "ssl" in leaf or ("secure" in leaf and "insecure" not in leaf) or "https" in lowered
        if leaf in ("wrap_socket", "wrap_bio"):
            return int(F.PRIM_NET_TLS)
        bits = F.PRIM_NET_LISTEN if leaf in _NET_LISTEN else F.PRIM_NET_CONNECT
        return int(bits | F.PRIM_NET_TLS) if tls else int(bits)
    if category == "database_io":
        if leaf in _DB_CONNECT or "." not in entry:  # a bare library entry (`knex`, `ioredis`) is its client
            return int(F.PRIM_DB_CONNECT)
        if leaf in _DB_READ or leaf.startswith("find"):
            return int(F.PRIM_DB_READ)
        if leaf in _DB_READ_WRITE:
            return int(F.PRIM_DB_READ | F.PRIM_DB_WRITE)
        if leaf.startswith(_DB_WRITE_PREFIXES):
            return int(F.PRIM_DB_WRITE)
        return int(F.PRIM_DB_READ | F.PRIM_DB_WRITE)
    if category == "filesystem_io":
        if leaf.endswith("sync") and leaf != "sync":
            leaf = leaf[: -len("sync")]  # Node's `readFileSync` is `readFile`
        if leaf in _FS_DELETE:
            return int(F.PRIM_FS_DELETE)
        if leaf in _FS_READ_WRITE or leaf.startswith(_FS_READ_WRITE_PREFIXES):
            return int(F.PRIM_FS_READ | F.PRIM_FS_WRITE)
        if leaf == "readfrom":  # Go `File.ReadFrom` reads *into* the file
            return int(F.PRIM_FS_WRITE)
        if leaf.startswith(_FS_READ_PREFIXES):
            return int(F.PRIM_FS_READ)
        if leaf.startswith(_FS_WRITE_PREFIXES):
            return int(F.PRIM_FS_WRITE)
        return int(F.PRIM_FS_READ | F.PRIM_FS_WRITE)
    if category == "process_io":
        return int(F.PRIM_PROC_SIGNAL if leaf in _PROC_SIGNAL else F.PRIM_PROC_SPAWN)
    if category == "time_io":
        return int(F.PRIM_TIME_CLOCK if leaf in _TIME_CLOCK else F.PRIM_TIME_WAIT)
    if category == "randomness":
        if leaf in _NON_RANDOM_UUIDS:
            return int(F.PRIM_RAND_PRNG)  # time-based or name-hashed, not cryptographic
        if entry.startswith(_CSPRNG_ROOTS) or "urandom" in lowered or "systemrandom" in lowered or "uuid" in lowered or root == "crypto/rand":
            return int(F.PRIM_RAND_CSPRNG)
        return int(F.PRIM_RAND_PRNG)
    return 0


def _entry_bits(table: dict[str, dict[str, set[str]]], layer: str) -> dict[str, dict[str, tuple[int, str]]]:
    out: dict[str, dict[str, tuple[int, str]]] = {}
    for category, languages in table.items():
        for language, entries in languages.items():
            for entry in entries:
                bits, _ = out.setdefault(language, {}).get(entry, (0, layer))
                out[language][entry] = (bits | derive_primitive_bits(category, entry), layer)
    return out


@lru_cache(maxsize=1)
def primitive_registry() -> dict[str, dict[str, tuple[int, str]]]:
    """`{language: {entry: (primitive bits, "L2" | "L3")}}` over both layer
    tables. A Layer 2 entry wins over a same-named Layer 3 stub."""
    registry = _entry_bits(L3_BOUNDARY_STUBS, "L3")
    for language, entries in _entry_bits(L2_PRIMITIVE_ENTRIES, "L2").items():
        registry.setdefault(language, {}).update(entries)
    return registry


# -- Composite capability profiles --------------------------------------- #
@dataclass(frozen=True)
class CompositeProfile:
    """A capability made of several primitive operations. Each group is an
    any-of set of primitives (`NET_CONNECT | NET_LISTEN` = "any socket");
    the composite is fully present when every group is."""

    name: str
    groups: tuple[int, ...]

    @property
    def mask(self) -> int:
        """The composite as one bitwise OR of all its primitives."""
        mask = 0
        for group in self.groups:
            mask |= group
        return mask

    def coverage(self, bits: int) -> float:
        """Fraction of groups `bits` satisfies (0.0-1.0), with every group
        allowed to come from the same mask - see `coverage_from_sources`
        for the distinct-source rule capability scoring uses."""
        return sum(1 for g in self.groups if bits & g) / len(self.groups)

    def coverage_from_sources(self, sources: "tuple[int, ...] | list[int]") -> float:
        """Fraction of groups satisfiable when each group needs its own
        source (`bitmask.distinct_source_matches`)."""
        return distinct_source_matches(self.groups, sources) / len(self.groups)


_NET_ANY = int(F.PRIM_NET_CONNECT | F.PRIM_NET_LISTEN)
COMPOSITE_PROFILES: dict[str, CompositeProfile] = {
    p.name: p
    for p in (
        # Reads local files and ships them over the network.
        CompositeProfile("ASSET_EGRESS", (int(F.PRIM_FS_READ), int(F.PRIM_NET_CONNECT))),
        # Takes data off the network and persists it.
        CompositeProfile("STREAMING_INGEST", (_NET_ANY, int(F.PRIM_FS_WRITE | F.PRIM_DB_WRITE))),
        # Talks to the network on a clock or timer.
        CompositeProfile("PERIODIC_HEARTBEAT", (int(F.PRIM_TIME_WAIT | F.PRIM_TIME_CLOCK), int(F.PRIM_NET_CONNECT))),
        # Writes files and drives external processes.
        CompositeProfile("OFFLINE_BATCH", (int(F.PRIM_FS_WRITE), int(F.PRIM_PROC_SPAWN))),
        # Accepts connections and starts processes.
        CompositeProfile("REMOTE_COMMAND", (int(F.PRIM_NET_LISTEN), int(F.PRIM_PROC_SPAWN))),
        # Generates secrets and stores them.
        CompositeProfile("SECRET_MINTING", (int(F.PRIM_RAND_CSPRNG), int(F.PRIM_FS_WRITE | F.PRIM_DB_WRITE))),
    )
}
