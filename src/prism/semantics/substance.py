"""v1.1+ Axis 1: Substance `S(v)` - the behavioral I/O sinks a symbol's
own body (and, one hop out, its thin wrapper callees) actually touches.

**Three-phase detection**, per the engineering spec:

  - **Phase 1 (Direct Qualified Matching)**: a call site's callee chain,
    resolved through the file's own `LocalImportMap`
    (`ConcreteGraphBuilder._build_import_map` - reused directly, never
    re-implemented, so import resolution never drifts from what Pass 2's
    own call linking already established as correct), matched against
    `prism.semantics.canonical_sinks.CANONICAL_SINKS` three ways: a bare
    single-segment entry (`requests`, `fs`) against the call's root or
    its import-resolved form; a dotted entry with no `/` (`time.sleep`,
    Go's own `time.Sleep`) against the call's own joined chain *and*
    against the chain with its root swapped for the resolved import path
    (so an aliased `import time as t; t.sleep()` still matches); a
    slash-containing entry (Go import paths only - `net/http.Get`,
    `gorm.io/gorm.DB.Create`) split into `(import_path, suffix_chain)`
    and matched against the call's import-resolved root plus its
    remaining segments.
  - **Phase 2 (Receiver Suffix & Driver Method Matching)**: most real
    sink calls are *instance* method calls (`conn.execute(...)`,
    `db.Query(...)`) that Phase 1 alone can never match without real type
    inference - a local variable's root identifier never resolves
    through an import map. Two real, bounded mechanisms instead of full
    type inference: (2a) **local constructor provenance** - a variable
    locally bound (`=`/`:=`/a declarator) directly from a Phase-1-matched
    constructor call (`db, err := sql.Open(...)`) propagates that same
    call's sink bit(s) to every later call whose receiver root is that
    variable, within the same function body only (never cross-function -
    that is Phase 3's job); (2b) **unambiguous driver-signature
    fallback** - a small, curated set of method names
    (`_UNAMBIGUOUS_DRIVER_SUFFIXES`) that are driver-internal signatures
    specific enough (`QueryRowContext`, `execute_driver_sql`,
    `storbinary`, ...) to name their own sink category regardless of
    receiver, matched against the call's own trailing segment as a last
    resort when nothing else matched.
  - **Phase 3 (Short Wrapper Unaliasing, `S_transitive`)**: if user
    function `A` calls user function `B`, and `B` has a statement count
    `<= 3` and a non-empty direct sink mask, that mask propagates into
    `A`'s own mask too - "an unaliased sink" per the spec: a function
    that's nothing but `def send(payload): requests.post(url, payload)`
    shouldn't force every one of its callers to also spell out
    `requests.post` before being recognized as network-touching.

A symbol with zero sink bits after all three phases, and no detected
state mutation (reusing `prism.graph.contracts.ContractExtractor`'s own
mutation check), gets `SINK_PURE_COMPUTE` instead.
"""
from __future__ import annotations

from tree_sitter import Node

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.parser.lang_config import CALL_NODE_TYPE, call_callee_segments, flatten_reference_chain, iter_scoped_nodes
from prism.parser.tree_sitter_loader import LanguageID, ParsedFile
from prism.semantics._ast_utils import count_statements
from prism.semantics.bitmask import FeatureBit
from prism.semantics.canonical_sinks import CANONICAL_SINKS
from prism.traversal._data_flow_common import _bindings, _decl_node_types, _node_key

#: The maximum statement count a callee `B` may have for its own direct
#: sink bits to still propagate up into a caller `A` (Phase 3) - raised
#: from 2 to 3 for v1.1+ per the spec's own wording ("statement count
#: <= 3"), still small enough that only genuine thin wrappers qualify,
#: not arbitrary multi-step business logic that merely happens to call a
#: sink partway through.
_TRANSITIVE_WRAPPER_MAX_STATEMENTS = 3

_CATEGORY_BIT: dict[str, FeatureBit] = {
    "network_io": FeatureBit.SINK_NETWORK_IO,
    "database_io": FeatureBit.SINK_DATABASE_IO,
    "filesystem_io": FeatureBit.SINK_FILESYSTEM_IO,
    "process_io": FeatureBit.SINK_PROCESS_IO,
    "time_io": FeatureBit.SINK_TIME_IO,
    "randomness": FeatureBit.SINK_RANDOMNESS,
}

_LANG_REGISTRY_KEY: dict[str, str] = {
    LanguageID.PYTHON: "python",
    LanguageID.JAVASCRIPT: "javascript",
    LanguageID.TYPESCRIPT: "javascript",
    LanguageID.TSX: "javascript",
    LanguageID.GO: "go",
}

#: JS/TS's own constructor-invocation node - a real, distinct grammar
#: shape from `call_expression` (confirmed directly), so a driver's own
#: constructor (`new Pool()`, `new Client()`) would otherwise never be
#: seen by a call-node-only walk at all. Python/Go have no `new` keyword
#: (both construct via a bare call, already covered by `CALL_NODE_TYPE`),
#: so this table only has JS/TS/TSX entries.
_NEW_EXPRESSION_NODE_TYPE: dict[str, str] = {
    LanguageID.JAVASCRIPT: "new_expression",
    LanguageID.TYPESCRIPT: "new_expression",
    LanguageID.TSX: "new_expression",
}

#: Phase 2b - method names specific enough to a single driver's own
#: internal API that matching them by trailing segment alone, with no
#: receiver/import resolution at all, is a safe (not merely convenient)
#: fallback: no other unrelated library in these three ecosystems ships a
#: same-named public method that means something else. Deliberately a
#: short, curated list, not a general "any Query*/Exec*-shaped name"
#: heuristic, which would be far too promiscuous (a domain object's own
#: `.Query()` method having nothing to do with a database is common).
_UNAMBIGUOUS_DRIVER_SUFFIXES: dict[str, str] = {
    # SQLAlchemy's own "raw SQL, bypass the ORM" escape hatches.
    "execute_driver_sql": "database_io",
    "exec_driver_sql": "database_io",
    # database/sql's context-aware driver methods - "Context" suffix is
    # itself part of the stdlib driver's own unique naming convention.
    "QueryRowContext": "database_io",
    "QueryContext": "database_io",
    "ExecContext": "database_io",
    # ftplib's own binary/line transfer primitives.
    "storbinary": "network_io",
    "storlines": "network_io",
    "retrbinary": "network_io",
    "retrlines": "network_io",
}


def _registry_for(lang: str) -> dict[str, set[str]] | None:
    key = _LANG_REGISTRY_KEY.get(lang)
    if key is None:
        return None
    return {category: entries.get(key, set()) for category, entries in CANONICAL_SINKS.items()}


def _normalize_go(path: str) -> str:
    return path.replace("/", ".")


def _split_go_import_entry(entry: str) -> tuple[str, str] | None:
    """For a Go registry entry shaped `<import/path>.<Suffix...>`
    (contains at least one `/`), split it into `(import_path,
    suffix_chain)`. The import path is everything up through the last
    `/` plus the single dotted segment immediately following it (the
    package's own locally-used identifier, which is not always the same
    as the URL path's own last component - `gorm.io/gorm`'s package name
    is `gorm`, matching its own last path segment here, but real Go code
    doesn't require that); `suffix_chain` is whatever dotted call-chain
    follows (a bare function name, or `Type.Method` for a receiver
    call). Returns `None` for a malformed entry (no `/`, or nothing after
    it) - never raises, since a registry-authoring mistake should degrade
    to "this one entry never matches", not crash extraction.
    """
    last_slash = entry.rfind("/")
    if last_slash == -1:
        return None
    after_slash = entry[last_slash + 1 :]
    dot_idx = after_slash.find(".")
    if dot_idx == -1:
        return None
    import_path = entry[: last_slash + 1] + after_slash[:dot_idx]
    suffix_chain = after_slash[dot_idx + 1 :]
    if not suffix_chain:
        return None
    return import_path, suffix_chain


def _chain_matches(chain: str, entry_suffix: str) -> bool:
    """Symmetric prefix match between a call's own dotted suffix
    (everything after its root segment) and a registry entry's own
    suffix chain - `chain == entry_suffix` for the common case
    (`Command` vs `Command`), `chain` a longer, more specific path than
    the entry (`entry_suffix` a prefix of `chain`), or `entry_suffix`
    longer than what the call site can actually observe without receiver
    type-tracking (`chain` a prefix of `entry_suffix` - e.g. matching a
    bare `.Do` call against a registered `Client.Do`).
    """
    return chain == entry_suffix or chain.startswith(entry_suffix + ".") or entry_suffix.startswith(chain + ".")


def _match_sink_bits_for_call(segments: list[str], import_map, lang: str, registry: dict[str, set[str]]) -> FeatureBit:
    """Phase 1 - direct qualified matching only (no receiver/provenance
    tracking; that is `_direct_sink_bits`'s own job, Phase 2). See this
    module's own docstring for the three entry shapes this checks.
    """
    if not segments:
        return FeatureBit(0)
    joined = ".".join(segments)
    root = segments[0]
    resolved_root = import_map.resolve(root) if import_map is not None else None
    resolved_joined = ".".join([resolved_root, *segments[1:]]) if resolved_root is not None else None
    suffix_after_root = ".".join(segments[1:]) if len(segments) > 1 else None

    bits = FeatureBit(0)
    for category, entries in registry.items():
        bit = _CATEGORY_BIT[category]
        for entry in entries:
            if "/" in entry:
                split = _split_go_import_entry(entry)
                if split is None:
                    continue
                import_path, entry_suffix = split
                normalized_import_path = _normalize_go(import_path)
                if (
                    resolved_root is not None
                    and resolved_root == normalized_import_path
                    and suffix_after_root is not None
                    and _chain_matches(suffix_after_root, entry_suffix)
                ):
                    bits |= bit
                    break
            elif "." in entry:
                if joined == entry or joined.startswith(entry + "."):
                    bits |= bit
                    break
                if resolved_joined is not None and (resolved_joined == entry or resolved_joined.startswith(entry + ".")):
                    bits |= bit
                    break
            else:
                if root == entry or resolved_root == entry:
                    bits |= bit
                    break
    return bits


def _suffix_fallback_bits(method_name: str) -> FeatureBit:
    """Phase 2b - see `_UNAMBIGUOUS_DRIVER_SUFFIXES`."""
    category = _UNAMBIGUOUS_DRIVER_SUFFIXES.get(method_name)
    if category is None:
        return FeatureBit(0)
    return _CATEGORY_BIT[category]


def _callable_segments(node: Node, new_expr_type: str | None, parsed: ParsedFile, lang: str) -> list[str] | None:
    """The dotted callee/constructor segments for either an ordinary call
    (`requests.get(url)`) or a JS/TS `new X(...)` instantiation - the
    latter has its own `"constructor"` field rather than `call_
    callee_segments`' `"function"` field, so it needs its own extraction,
    not a variant of the same helper.
    """
    if new_expr_type is not None and node.type == new_expr_type:
        ctor = node.child_by_field_name("constructor")
        return flatten_reference_chain(ctor, parsed.source, lang) if ctor is not None else None
    return call_callee_segments(node, parsed.source, lang)


def _direct_sink_bits(def_node: Node, parsed: ParsedFile, import_map) -> FeatureBit:
    """`S_direct(v)` - Phases 1 and 2 combined, scoped to `def_node`'s own
    body. Two passes over the same call/instantiation-site set rather
    than one: Phase 2a (local constructor provenance) needs every site's
    own Phase-1 bits already computed before it can decide what a
    locally-bound variable "means", and a document-order single pass
    would see many receiver calls before the constructor they were bound
    from was itself resolved.
    """
    lang = parsed.language_id
    registry = _registry_for(lang)
    if registry is None:
        return FeatureBit(0)
    call_type = CALL_NODE_TYPE.get(lang)
    new_expr_type = _NEW_EXPRESSION_NODE_TYPE.get(lang)
    if not call_type and not new_expr_type:
        return FeatureBit(0)

    target_types = {t for t in (call_type, new_expr_type) if t}
    phase1_bits: dict[tuple[int, int], FeatureBit] = {}
    segments_by_key: dict[tuple[int, int], list[str]] = {}
    for node in iter_scoped_nodes(def_node, target_types, lang):
        segments = _callable_segments(node, new_expr_type, parsed, lang)
        if not segments:
            continue
        key = _node_key(node)
        segments_by_key[key] = segments
        phase1_bits[key] = _match_sink_bits_for_call(segments, import_map, lang, registry)

    # Phase 2a: local constructor provenance - a variable bound directly
    # from a Phase-1-matched call/instantiation inherits its sink bits
    # for every later same-function call on it.
    local_bindings: dict[str, FeatureBit] = {}
    decl_types = _decl_node_types(lang)
    if decl_types:
        for decl_node in iter_scoped_nodes(def_node, decl_types, lang):
            for name, value in _bindings(decl_node, lang, parsed.source):
                if value is None or value.type not in target_types:
                    continue
                call_bits = phase1_bits.get(_node_key(value))
                if call_bits:
                    local_bindings[name] = local_bindings.get(name, FeatureBit(0)) | call_bits

    bits = FeatureBit(0)
    for key, segments in segments_by_key.items():
        call_bits = phase1_bits[key]
        if not call_bits and len(segments) >= 2 and segments[0] in local_bindings:
            call_bits = local_bindings[segments[0]]  # Phase 2a
        if not call_bits:
            call_bits = _suffix_fallback_bits(segments[-1])  # Phase 2b
        bits |= call_bits
    return bits


def _has_state_mutation(def_node: Node, parsed: ParsedFile) -> bool:
    """Best-effort, dependency-light reuse of `ContractExtractor`'s own
    mutation check - imported lazily (function-local) to avoid a
    module-level import cycle (`prism.graph.contracts` itself imports
    `prism.graph.concrete_builder`, which this module also imports).
    """
    from prism.graph.contracts import ContractExtractor

    return bool(ContractExtractor()._state_mutations(def_node, parsed))


def compute_substance_bits(builder: ConcreteGraphBuilder) -> dict[str, int]:
    """`{qualified_name: Substance bits}` for every function/method
    `builder` indexed - the one entry point `prism.semantics.extractor`
    calls. Two passes: direct sinks first (Phases 1-2, needed to know
    which callees are themselves sink-touching before Phase 3 can run),
    then a second pass folds in Phase 3's one-hop transitive wrapper
    propagation.
    """
    direct: dict[str, FeatureBit] = {}
    wrapper_statement_counts: dict[str, int] = {}
    import_maps: dict[str, object] = {}

    for symbol in builder.symbol_table:
        if symbol.kind not in ("function", "method"):
            continue
        def_node = builder.def_node(symbol.qualified_name)
        parsed = builder.parsed_file(symbol.file)
        if def_node is None or parsed is None:
            continue
        if symbol.module not in import_maps:
            import_maps[symbol.module] = builder._build_import_map(parsed, symbol.module)
        import_map = import_maps[symbol.module]
        direct[symbol.qualified_name] = _direct_sink_bits(def_node, parsed, import_map)
        wrapper_statement_counts[symbol.qualified_name] = count_statements(def_node, parsed.language_id)

    result: dict[str, int] = {}
    for symbol in builder.symbol_table:
        if symbol.kind not in ("function", "method"):
            continue
        qname = symbol.qualified_name
        own_bits = direct.get(qname, FeatureBit(0))
        transitive_bits = FeatureBit(0)
        if qname in builder.graph:
            for _u, callee, data in builder.graph.out_edges(qname, data=True):
                if data.get("relation", "CALLS") not in ("CALLS", "INSTANTIATES"):
                    continue
                callee_bits = direct.get(callee)
                if not callee_bits:
                    continue
                if wrapper_statement_counts.get(callee, 99) <= _TRANSITIVE_WRAPPER_MAX_STATEMENTS:
                    transitive_bits |= callee_bits
        combined = own_bits | transitive_bits

        if not combined:
            def_node = builder.def_node(qname)
            parsed = builder.parsed_file(symbol.file)
            is_pure = True
            if def_node is not None and parsed is not None:
                is_pure = not _has_state_mutation(def_node, parsed)
            if is_pure:
                combined = FeatureBit.SINK_PURE_COMPUTE

        result[qname] = int(combined)

    return result
