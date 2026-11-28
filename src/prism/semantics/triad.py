"""Turn-1 triad scoring: lexical stems x physical action x capability
(Operation Groundwire).

A Turn-1 interpretation names up to three things about the code it wants:
stems ("upload", "avatar"), a physical action (MUTATE, SEND, ...) and a
capability target (a primitive mask or composite such as ASSET_EGRESS).
`triad_rerank` combines them as soft, bounded boosts over the caller's
lexical (stem) scores - never a filter, never a removal:

    score = stems * (1 + w_cap * capability + w_act * action)

Capability comes from `prism.semantics.capabilities`. When it is requested
but a candidate's dependency evidence is OPAQUE - it calls into compiled
native code (`psycopg2`'s C core), whose capability is unknown rather than
absent - the capability leg is neutral (0.0, never a penalty) and the
candidate is scored by its stems and its own action verbs alone; the result
is marked `capability_source="opaque-native"` with the opaque entry points
it reached, so the provenance says exactly why it ranked where it did. A
call through a runtime proxy (`django.db.connection`, `flask.request`) is
the same kind of unknown, reported as `"runtime-proxy"`. (Moving the
capability weight onto the action leg instead - `unknown_to_action=True` -
rewarded any function that merely touches a proxy: on held-out Flask code a
route reading `current_user` outranked the sink it should not beat.)

`DATABASE_IO` is synthesized by the triad rather than read off a catalog
of driver names. A pure-Python driver bottoms out at a socket
(`PRIM_NET_CONNECT`) - that is the ground truth of what it physically does;
what makes the connection a database is the action dispatched over it. So
a candidate is database I/O when it reaches a DB primitive, or when it
reaches a network connection *and* its own name or a call it makes is a
database verb (`DATABASE_VERBS`, or a whole-word key-value command from
`DATASTORE_VERBS`), or when it reaches opaque native code
with such a verb (a compiled driver).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.parser.lang_config import (
    ATTR_PROPERTY_FIELD,
    ATTRIBUTE_NODE_TYPE,
    CALL_NODE_TYPE,
    call_callee_segments,
    iter_scoped_nodes,
)
from prism.semantics.bitmask import FeatureBit
from prism.semantics.capabilities import CapabilityProfile, capability_score
from prism.semantics.primitives import CompositeProfile

F = FeatureBit
CAPABILITY_WEIGHT = 0.25
ACTION_WEIGHT = 0.15


@dataclass(frozen=True)
class PhysicalAction:
    """What a function physically does, recognized by its verbs (a token
    that is, or starts with, one) or by whole-word commands (`words`: short
    datastore commands such as `hset` or `decr`, which as prefixes would
    match `decrypt`). `bits` are the primitives the action usually lands on
    (documentation and consistency checks; scoring uses the verbs)."""

    name: str
    verbs: tuple[str, ...]
    bits: int
    words: frozenset[str] = frozenset()


PHYSICAL_ACTIONS: dict[str, PhysicalAction] = {
    a.name: a
    for a in (
        PhysicalAction("READ", ("read", "load", "get", "fetch", "find", "query", "select", "list", "scan", "lookup"),
                       int(F.PRIM_FS_READ | F.PRIM_DB_READ), frozenset({"hget", "mget"})),
        PhysicalAction("MUTATE", ("write", "save", "update", "insert", "delete", "remove", "put", "create", "commit",
                                  "store", "upsert", "persist", "drop", "truncate"),
                       int(F.PRIM_FS_WRITE | F.PRIM_FS_DELETE | F.PRIM_DB_WRITE),
                       frozenset({"hset", "lpush", "rpush", "sadd", "srem", "zadd", "incr", "decr", "mset", "expire"})),
        PhysicalAction("SEND", ("send", "post", "upload", "publish", "emit", "push", "notify", "dispatch", "request",
                                "produce", "enqueue", "broadcast"),
                       int(F.PRIM_NET_CONNECT), frozenset({"apply_async"})),
        PhysicalAction("RECEIVE", ("listen", "serve", "accept", "receive", "subscribe", "consume", "handle", "recv"),
                       int(F.PRIM_NET_LISTEN)),
        PhysicalAction("EXECUTE", ("run", "exec", "spawn", "launch", "kill", "fork", "popen", "system"),
                       int(F.PRIM_PROC_SPAWN | F.PRIM_PROC_SIGNAL)),
        PhysicalAction("WAIT", ("sleep", "wait", "schedule", "poll", "retry", "tick", "delay", "timeout"),
                       int(F.PRIM_TIME_WAIT | F.PRIM_TIME_CLOCK)),
        PhysicalAction("CONNECT", ("connect", "dial", "resolve", "handshake", "bind"),
                       int(F.PRIM_NET_CONNECT | F.PRIM_DB_CONNECT | F.PRIM_NET_DNS)),
    )
}

#: Verbs that dispatch a database action (a call site's callee or the
#: function's own name): `cur.execute(...)`, `conn.commit()`.
DATABASE_VERBS = ("query", "execute", "select", "insert", "commit", "find", "cursor")
#: Datastore (key-value) commands with no HTTP or collection meaning,
#: matched as whole words only (a prefix `decr` would match `decrypt`).
#: Bare `get`/`set`/`delete` are deliberately absent: they collide with
#: HTTP verbs, accessors and collection helpers.
DATASTORE_VERBS = frozenset({"hset", "hget", "lpush", "rpush", "sadd", "srem", "zadd", "expire", "incr", "decr", "mget", "mset"})
#: The triad-synthesized database capability target.
DATABASE_IO = "DATABASE_IO"
_DB_BITS = int(F.PRIM_DB_CONNECT | F.PRIM_DB_READ | F.PRIM_DB_WRITE)
_NET_SOCKET = int(F.PRIM_NET_CONNECT)

_WORD = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


def _words(identifier: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(identifier)]


def action_tokens(builder: ConcreteGraphBuilder, qualified_name: str) -> frozenset[str]:
    """The words a function's own name and every name it calls are made of
    (`save_user` + `cur.execute(...)` + `conn.commit()` -> save, user,
    execute, commit), plus those names whole (`save_user`, `apply_async`)
    - including calls on native objects no graph edge reaches."""
    own = qualified_name.rsplit(".", 1)[-1]
    words = set(_words(own)) | {own.lower()}
    def_node = builder.def_node(qualified_name)
    symbol = builder.symbol_table.get(qualified_name)
    parsed = builder.parsed_file(symbol.file) if symbol is not None else None
    call_type = CALL_NODE_TYPE.get(parsed.language_id) if parsed is not None else None
    if def_node is not None and call_type:
        for call in iter_scoped_nodes(def_node, {call_type}, parsed.language_id):
            segments = call_callee_segments(call, parsed.source, parsed.language_id)
            name = segments[-1] if segments else _chained_method(call, parsed)
            if name:
                words.update(_words(name))
                words.add(name.lower())  # whole identifiers too: `apply_async`
    return frozenset(words)


def _chained_method(call, parsed) -> str | None:
    """The method of a chained call (`cursor().execute(...)` -> `execute`),
    which has no nameable callee chain."""
    func = call.child_by_field_name("function")
    if func is None or func.type != ATTRIBUTE_NODE_TYPE.get(parsed.language_id):
        return None
    prop = func.child_by_field_name(ATTR_PROPERTY_FIELD[parsed.language_id])
    return parsed.source[prop.start_byte:prop.end_byte].decode(errors="replace") if prop is not None else None


def action_score(tokens: "frozenset[str] | set[str]", action: "PhysicalAction | str") -> float:
    """1.0 when any token is (or starts with) one of the action's verbs, or
    is one of its whole-word commands."""
    if isinstance(action, str):
        action = PHYSICAL_ACTIONS[action]
    if not action.words.isdisjoint(tokens):
        return 1.0
    return 1.0 if any(t.startswith(v) for t in tokens for v in action.verbs) else 0.0


def has_database_verb(tokens: "frozenset[str] | set[str]") -> bool:
    return any(t.startswith(v) for t in tokens for v in DATABASE_VERBS) or not DATASTORE_VERBS.isdisjoint(tokens)


def database_io(
    profile: CapabilityProfile | None, tokens: "frozenset[str] | set[str]", gamma: float | None = None
) -> tuple[float, str]:
    """`(capability, source)` for the synthesized `DATABASE_IO` target:

    - `"capability"`: a DB primitive is reached (hop-decayed score);
    - `"net-socket+db-verb"`: a network connection is reached and a
      database verb is dispatched (score of the network leg);
    - `"opaque-native"` / `"runtime-proxy"`: only opaque native code (or a
      runtime proxy) is reached, with a database verb - capability unknown
      (0.0), matched by the action;
    - `"none"`: no match."""
    if profile is None:
        return 0.0, "none"
    direct = capability_score(profile, _DB_BITS, gamma)
    if direct > 0:
        return direct, "capability"
    if not has_database_verb(tokens):
        return 0.0, "none"
    network = capability_score(profile, _NET_SOCKET, gamma)
    if network > 0:
        return network, "net-socket+db-verb"
    unknown = _unknown_source(profile)
    return (0.0, unknown) if unknown else (0.0, "none")


def _unknown_source(profile: CapabilityProfile | None) -> str | None:
    """Why `profile`'s capability is unknown rather than absent: compiled
    native code reached (`"opaque-native"`), else a runtime proxy reached
    (`"runtime-proxy"`), else `None`."""
    if profile is None:
        return None
    if profile.opaque:
        return "opaque-native"
    return "runtime-proxy" if profile.proxy else None


@dataclass(frozen=True)
class TriadScore:
    """One re-ranked candidate and why it scored as it did."""

    qname: str
    score: float
    stems: float
    capability: float
    action: float
    capability_source: str  # "capability" | "net-socket+db-verb" | "opaque-native" | "runtime-proxy" | "none"
    opaque: tuple[str, ...] = ()
    proxy: tuple[str, ...] = ()


def triad_rerank(
    candidates: list[str],
    stem_scores: dict[str, float],
    profiles: dict[str, CapabilityProfile],
    tokens: dict[str, "frozenset[str]"],
    target: "CompositeProfile | str | int | None" = None,
    action: "PhysicalAction | str | None" = None,
    capability_weight: float = CAPABILITY_WEIGHT,
    action_weight: float = ACTION_WEIGHT,
    hop_gamma: float | None = None,
    unknown_to_action: bool = False,
) -> list[TriadScore]:
    """`candidates` re-ranked by the triad; every candidate is kept, the
    boost is at most `1 + capability_weight + action_weight`, and ties keep
    their input order (identity when no leg fires). `hop_gamma` (opt-in)
    attenuates the capability leg by repo-internal hops: the function that
    makes the I/O call scores in full, each caller above it `hop_gamma`
    times less (`capability_score`). `unknown_to_action` restores the
    former treatment of OPAQUE/PROXY capability (its weight moved onto the
    action leg, a compiled driver's database verb counted as the action)."""
    if capability_weight < 0 or action_weight < 0:
        raise ValueError("weights must be >= 0")
    rows = []
    for i, qname in enumerate(candidates):
        stems = stem_scores.get(qname, 0.0)
        profile = profiles.get(qname)
        words = tokens.get(qname, frozenset())
        act = action_score(words, action) if action is not None else 0.0
        opaque = profile.opaque if profile is not None else ()
        proxy = profile.proxy if profile is not None else ()
        unknown = _unknown_source(profile)
        if target == DATABASE_IO:
            capability, db_source = database_io(profile, words, hop_gamma)
            if unknown_to_action and db_source in ("opaque-native", "runtime-proxy"):
                # A compiled driver (or a proxied connection) dispatching a
                # database verb: the verb is the matching action.
                act = max(act, 1.0)
        else:
            capability = capability_score(profile, target, hop_gamma) if target is not None else 0.0
            db_source = None
        if db_source == "net-socket+db-verb":
            source = db_source
            boost = capability_weight * capability + action_weight * act
        elif target is not None and capability == 0.0 and unknown:
            # Unknown, not absent: neutral - recorded, never a penalty, and
            # (unless `unknown_to_action`) never a boost either.
            source = unknown
            boost = (capability_weight + action_weight) * act if unknown_to_action else action_weight * act
        else:
            source = "capability" if capability > 0 else "none"
            boost = capability_weight * capability + action_weight * act
        rows.append((TriadScore(qname, stems * (1.0 + boost), stems, capability, act, source, opaque, proxy), i))
    rows.sort(key=lambda r: (-r[0].score, r[1]))
    return [row for row, _ in rows]
