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
absent - the capability leg is not scored as a mismatch: its weight moves
onto the physical-action leg, and the result is marked
`capability_source="opaque-native"` with the opaque entry points it
reached, so the provenance says exactly why it ranked where it did.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.parser.lang_config import CALL_NODE_TYPE, call_callee_segments, iter_scoped_nodes
from prism.semantics.bitmask import FeatureBit
from prism.semantics.capabilities import CapabilityProfile, capability_score
from prism.semantics.primitives import CompositeProfile

F = FeatureBit
CAPABILITY_WEIGHT = 0.25
ACTION_WEIGHT = 0.15


@dataclass(frozen=True)
class PhysicalAction:
    """What a function physically does, recognized by its verbs. `bits`
    are the primitives the action usually lands on (documentation and
    consistency checks; scoring uses the verbs)."""

    name: str
    verbs: tuple[str, ...]
    bits: int


PHYSICAL_ACTIONS: dict[str, PhysicalAction] = {
    a.name: a
    for a in (
        PhysicalAction("READ", ("read", "load", "get", "fetch", "find", "query", "select", "list", "scan", "lookup"),
                       int(F.PRIM_FS_READ | F.PRIM_DB_READ)),
        PhysicalAction("MUTATE", ("write", "save", "update", "insert", "delete", "remove", "put", "create", "commit",
                                  "store", "upsert", "persist", "drop", "truncate"),
                       int(F.PRIM_FS_WRITE | F.PRIM_FS_DELETE | F.PRIM_DB_WRITE)),
        PhysicalAction("SEND", ("send", "post", "upload", "publish", "emit", "push", "notify", "dispatch", "request"),
                       int(F.PRIM_NET_CONNECT)),
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

_WORD = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


def _words(identifier: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(identifier)]


def action_tokens(builder: ConcreteGraphBuilder, qualified_name: str) -> frozenset[str]:
    """The words a function's own name and every name it calls are made of
    (`save_user` + `cur.execute(...)` + `conn.commit()` -> save, user,
    execute, commit) - including calls on native objects no graph edge
    reaches."""
    words = set(_words(qualified_name.rsplit(".", 1)[-1]))
    def_node = builder.def_node(qualified_name)
    symbol = builder.symbol_table.get(qualified_name)
    parsed = builder.parsed_file(symbol.file) if symbol is not None else None
    call_type = CALL_NODE_TYPE.get(parsed.language_id) if parsed is not None else None
    if def_node is not None and call_type:
        for call in iter_scoped_nodes(def_node, {call_type}, parsed.language_id):
            segments = call_callee_segments(call, parsed.source, parsed.language_id)
            if segments:
                words.update(_words(segments[-1]))
    return frozenset(words)


def action_score(tokens: "frozenset[str] | set[str]", action: "PhysicalAction | str") -> float:
    """1.0 when any token is (or starts with) one of the action's verbs."""
    if isinstance(action, str):
        action = PHYSICAL_ACTIONS[action]
    return 1.0 if any(t.startswith(v) for t in tokens for v in action.verbs) else 0.0


@dataclass(frozen=True)
class TriadScore:
    """One re-ranked candidate and why it scored as it did."""

    qname: str
    score: float
    stems: float
    capability: float
    action: float
    capability_source: str  # "capability" | "opaque-native" | "none"
    opaque: tuple[str, ...] = ()


def triad_rerank(
    candidates: list[str],
    stem_scores: dict[str, float],
    profiles: dict[str, CapabilityProfile],
    tokens: dict[str, "frozenset[str]"],
    target: "CompositeProfile | str | int | None" = None,
    action: "PhysicalAction | str | None" = None,
    capability_weight: float = CAPABILITY_WEIGHT,
    action_weight: float = ACTION_WEIGHT,
) -> list[TriadScore]:
    """`candidates` re-ranked by the triad; every candidate is kept, the
    boost is at most `1 + capability_weight + action_weight`, and ties keep
    their input order (identity when no leg fires)."""
    if capability_weight < 0 or action_weight < 0:
        raise ValueError("weights must be >= 0")
    rows = []
    for i, qname in enumerate(candidates):
        stems = stem_scores.get(qname, 0.0)
        profile = profiles.get(qname)
        capability = capability_score(profile, target) if target is not None else 0.0
        act = action_score(tokens.get(qname, frozenset()), action) if action is not None else 0.0
        opaque = profile.opaque if profile is not None else ()
        if target is not None and capability == 0.0 and opaque:
            # Unknown, not absent: the capability leg's weight moves to the
            # physical-action leg instead of counting against the candidate.
            source = "opaque-native"
            boost = (capability_weight + action_weight) * act
        else:
            source = "capability" if capability > 0 else "none"
            boost = capability_weight * capability + action_weight * act
        rows.append((TriadScore(qname, stems * (1.0 + boost), stems, capability, act, source, opaque), i))
    rows.sort(key=lambda r: (-r[0].score, r[1]))
    return [row for row, _ in rows]
