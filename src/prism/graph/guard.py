"""Self-healing AST structural-hash guard: reconciles a symbol rename
(`db.user.get` renamed to `db.user.fetch_record`, with its body otherwise
untouched) across re-indexing runs, so persisted, name-keyed state -
today, `.prism/runtime_state.json`'s runtime-confirmed edges and sink tags
(see `prism.runtime.reconciler`) - doesn't silently go dangling and get
dropped the moment its qualified name changes.

**The problem this solves**: `prism.runtime.reconciler.apply_runtime_state`
re-validates every persisted edge against the freshly-built static graph
and, by design, *skips* any edge whose caller/callee no longer exists
there - correct when the code genuinely changed, wrong when the only
change was a rename. Without this guard, a rename silently discards every
runtime-confirmed edge, sink tag, and invocation count that pointed at the
old name - exactly the kind of stale-reference problem a manual alias
file would otherwise be needed to work around. This module avoids that
file entirely: it reads the rename directly off the AST.

**How a rename is detected**: for every function/method symbol, a
structural hash `H(v)` is computed from its normalized parameter *types*
(names stripped), its return signature, and a "skeleton" sequence of AST
node types across its body with every identifier/name leaf excluded - so
`H(v)` is invariant to renaming the function itself, renaming its
parameters, or renaming any local variable inside it, but changes the
moment its actual shape (branch structure, call structure, argument
count/types) changes. A symbol that vanishes between two fingerprint
snapshots (an old one persisted from the previous index, a fresh one
computed this run) is treated as *renamed* - not deleted - when exactly
one symbol in the same scope (module, or enclosing class for a method)
and of the same kind appears in the new snapshot with an identical hash
and wasn't already present in the old one. More than one such candidate
is left unresolved rather than guessed at (see `GuardReconciliation.ambiguous`).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.contracts import ContractExtractor

# A single, module-level extractor is safe to share - `_extract_params`/
# `_extract_return_type` are pure AST readers with no per-call state of
# their own (see `prism.graph.contracts.ContractExtractor`).
_PARAM_RETURN_EXTRACTOR = ContractExtractor()

# Leaf node types that name something (an identifier, a literal's own
# text) rather than shape something - excluded from the skeleton walk so
# renaming a function/parameter/local variable, or changing a literal's
# *value* (not its kind), never changes the hash. A literal's node TYPE
# ("string", "integer", ...) is still included below - only its text is
# dropped - since "this branch compares an int" is structural, but the
# literal's actual value is not.
_NAME_LEAF_TYPES = frozenset({
    "identifier", "type_identifier", "property_identifier", "field_identifier",
    "this", "this_expression", "shorthand_property_identifier",
})


def _skeleton_token_sequence(node) -> list[str]:
    """Pre-order walk of `node`'s own type plus every descendant's type,
    dropping name-leaf node types (see `_NAME_LEAF_TYPES`) - the resulting
    sequence describes shape (statement/expression kinds, nesting, branch
    count) with every name scrubbed out."""
    tokens: list[str] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type not in _NAME_LEAF_TYPES:
            tokens.append(current.type)
        # Reverse so children are visited in source order despite the
        # stack's LIFO pop order.
        stack.extend(reversed(current.children))
    return tokens


def _normalized_param_types(def_node, parsed) -> tuple[str, ...]:
    params = _PARAM_RETURN_EXTRACTOR._extract_params(def_node, parsed)
    return tuple(p.type or "?" for p in params)


def compute_structural_hash(def_node, parsed) -> str:
    """`H(v)`: a short, stable hex digest over normalized parameter types,
    return signature, and the name-scrubbed skeleton token sequence -
    truncated to 16 hex characters (64 bits), plenty of collision
    resistance for reconciling renames within one repository's symbol
    count while staying compact enough to persist and diff cheaply."""
    param_types = _normalized_param_types(def_node, parsed)
    return_type = _PARAM_RETURN_EXTRACTOR._extract_return_type(def_node, parsed) or "?"
    skeleton = _skeleton_token_sequence(def_node)
    payload = "|".join([",".join(param_types), return_type, ",".join(skeleton)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class SymbolFingerprint:
    qualified_name: str
    scope: str  # enclosing class if a method, else the module
    kind: str
    structural_hash: str

    def to_dict(self) -> dict:
        return {
            "qualified_name": self.qualified_name,
            "scope": self.scope,
            "kind": self.kind,
            "structural_hash": self.structural_hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SymbolFingerprint":
        return cls(
            qualified_name=d["qualified_name"], scope=d["scope"],
            kind=d["kind"], structural_hash=d["structural_hash"],
        )


def build_fingerprints(builder: ConcreteGraphBuilder) -> dict[str, SymbolFingerprint]:
    """One `SymbolFingerprint` per function/method the builder indexed -
    computed straight from each symbol's own AST definition node, no
    contract extraction required (this can run before or independently of
    `prism.graph.contracts.compute_contracts`)."""
    fingerprints: dict[str, SymbolFingerprint] = {}
    for symbol in builder.symbol_table:
        if symbol.kind not in ("function", "method"):
            continue
        def_node = builder.def_node(symbol.qualified_name)
        parsed = builder.parsed_file(symbol.file)
        if def_node is None or parsed is None:
            continue
        fingerprints[symbol.qualified_name] = SymbolFingerprint(
            qualified_name=symbol.qualified_name,
            scope=symbol.enclosing_class or symbol.module,
            kind=symbol.kind,
            structural_hash=compute_structural_hash(def_node, parsed),
        )
    return fingerprints


@dataclass
class GuardReconciliation:
    #: old qualified name -> the single new qualified name it structurally
    #: matches (safe to heal automatically).
    renamed: dict[str, str] = field(default_factory=dict)
    #: old qualified name -> every new qualified name that matched (more
    #: than one candidate - deliberately left unresolved rather than
    #: guessed at; a caller can surface these for a human to disambiguate).
    ambiguous: dict[str, list[str]] = field(default_factory=dict)


def reconcile(old: dict[str, SymbolFingerprint], new: dict[str, SymbolFingerprint]) -> GuardReconciliation:
    """Diffs two fingerprint snapshots and proposes a rename map. Never
    mutates either snapshot or any graph - purely a pure function over two
    dicts, so it's trivial to test and to call speculatively (a caller
    decides whether/how to apply the result)."""
    result = GuardReconciliation()
    vanished = [name for name in old if name not in new]
    appeared = {name: fp for name, fp in new.items() if name not in old}

    for old_name in vanished:
        old_fp = old[old_name]
        candidates = sorted(
            new_name for new_name, new_fp in appeared.items()
            if new_fp.scope == old_fp.scope
            and new_fp.kind == old_fp.kind
            and new_fp.structural_hash == old_fp.structural_hash
        )
        if len(candidates) == 1:
            result.renamed[old_name] = candidates[0]
        elif len(candidates) > 1:
            result.ambiguous[old_name] = candidates
    return result


def heal_qualified_names(value, rename_map: dict[str, str]):
    """Recursively rewrites any string in a JSON-shaped structure (nested
    dicts/lists/tuples of `str`/`int`/...) that exactly matches a
    `rename_map` key - used to migrate `.prism/runtime_state.json`'s
    persisted, name-keyed edges/tags/counts onto a renamed symbol's new
    qualified name before `prism.runtime.reconciler.apply_runtime_state`
    re-validates them against the fresh graph. A no-op (returns `value`
    unchanged, not a copy) when `rename_map` is empty, so calling this
    unconditionally costs nothing on the far more common "nothing was
    renamed" path.
    """
    if not rename_map:
        return value
    if isinstance(value, str):
        return rename_map.get(value, value)
    if isinstance(value, dict):
        return {heal_qualified_names(k, rename_map): heal_qualified_names(v, rename_map) for k, v in value.items()}
    if isinstance(value, list):
        return [heal_qualified_names(item, rename_map) for item in value]
    if isinstance(value, tuple):
        return tuple(heal_qualified_names(item, rename_map) for item in value)
    return value


# --------------------------------------------------------------------- #
# Fingerprint snapshot persistence - the "old" side of the diff `reconcile`
# needs, carried across separate `prism` invocations the same way
# `.prism/runtime_state.json` itself is (see `prism.runtime.reconciler`).
# --------------------------------------------------------------------- #
def fingerprints_path(repo_root: str) -> Path:
    return Path(repo_root) / ".prism" / "symbol_fingerprints.json"


def load_fingerprints(repo_root: str) -> dict[str, SymbolFingerprint]:
    path = fingerprints_path(repo_root)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    try:
        return {name: SymbolFingerprint.from_dict(d) for name, d in payload.items()}
    except (KeyError, TypeError):
        return {}


def save_fingerprints(repo_root: str, fingerprints: dict[str, SymbolFingerprint]) -> None:
    path = fingerprints_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {name: fp.to_dict() for name, fp in fingerprints.items()}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
