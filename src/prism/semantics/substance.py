"""v1.1 Axis 1: Substance `S(v)` - the behavioral I/O sinks a symbol's own
body (and, one hop out, its thin wrapper callees) actually touches.

Two-tier detection:
  - `S_direct(v)`: call sites inside `v`'s own body whose resolved target
    matches `CANONICAL_SINKS` - either a stdlib/dotted builtin
    (`time.sleep`, `os.path`, `Math.random`) matched against the call's
    full dotted chain, or a third-party import matched by resolving the
    call's *root* identifier through the file's own `LocalImportMap`
    (`ConcreteGraphBuilder._build_import_map` - reused directly rather
    than re-implemented, so import resolution never drifts from what
    Pass 2's own call linking already established as correct).
  - `S_transitive(v)`: if `v` calls `u`, and `u` is a thin (<= 2
    statement) wrapper that itself has a direct sink bit, that bit is
    folded into `v`'s own mask too - "an unaliased sink" per the spec: a
    function that's nothing but `def send(payload): requests.post(url,
    payload)` shouldn't force every one of its callers to also spell out
    `requests.post` before being recognized as network-touching.

A symbol with zero sink bits after both tiers, and no detected state
mutation (reusing `prism.graph.contracts.ContractExtractor`'s own
mutation check when a `contracts` map is supplied), gets
`SINK_PURE_COMPUTE` instead.
"""
from __future__ import annotations

from tree_sitter import Node

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.symbol_table import SymbolInfo
from prism.parser.lang_config import CALL_NODE_TYPE, call_callee_segments, iter_scoped_nodes
from prism.parser.tree_sitter_loader import LanguageID, ParsedFile
from prism.semantics._ast_utils import count_statements
from prism.semantics.bitmask import FeatureBit

#: The audit's own literal registry, keyed by sink category then by this
#: codebase's own `LanguageID` values. TypeScript/TSX share the
#: JavaScript entry (the runtime/stdlib surface being matched - `fetch`,
#: `fs`, `child_process` - is identical; only the type system differs,
#: which this axis never inspects).
CANONICAL_SINKS: dict[str, dict[str, set[str]]] = {
    "network_io": {
        "python": {"requests", "httpx", "urllib", "aiohttp", "socket"},
        "javascript": {"fetch", "axios", "http", "https", "got"},
        "go": {"net/http", "net", "grpc"},
    },
    "database_io": {
        "python": {"psycopg2", "sqlalchemy", "django.db", "pymongo", "redis"},
        "javascript": {"pg", "mysql2", "mongoose", "sequelize", "prisma"},
        "go": {"database/sql", "gorm.io", "mongo-driver"},
    },
    "filesystem_io": {
        "python": {"open", "pathlib", "os.path", "shutil"},
        "javascript": {"fs", "fs/promises", "path"},
        "go": {"os", "io/ioutil", "path/filepath"},
    },
    "process_io": {
        "python": {"subprocess", "os.system", "multiprocessing"},
        "javascript": {"child_process", "worker_threads"},
        "go": {"os/exec"},
    },
    "time_io": {
        "python": {"time.sleep", "asyncio.sleep"},
        "javascript": {"setTimeout", "setInterval"},
        "go": {"time.Sleep", "time.After"},
    },
    "randomness": {
        "python": {"random", "secrets", "uuid"},
        "javascript": {"Math.random", "crypto"},
        "go": {"math/rand", "crypto/rand"},
    },
}

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


def _registry_for(lang: str) -> dict[str, set[str]] | None:
    key = _LANG_REGISTRY_KEY.get(lang)
    if key is None:
        return None
    return {category: entries.get(key, set()) for category, entries in CANONICAL_SINKS.items()}


def _normalize_go(entry: str) -> str:
    return entry.replace("/", ".")


def _match_sink_bits_for_call(segments: list[str], import_map, lang: str, registry: dict[str, set[str]]) -> FeatureBit:
    """Matches one call site's resolved dotted chain against every sink
    category, three ways (any can fire independently, a call can in
    principle match more than one axis - e.g. a hypothetical
    `db.http_export()` - though no entry in the literal registry actually
    does today):

      1. Full dotted chain (`os.path.join` -> `"os.path"`) against a
         multi-segment registry entry (`time.sleep`, `os.path`,
         `Math.random`, `time.Sleep`) - checked as a *prefix* match on
         the joined chain, since `os.path.join(...)`'s chain is
         `["os", "path", "join"]`, three segments, against a two-segment
         registry entry.
      2. The call's root segment, resolved through the file's own
         `LocalImportMap` to its fully-qualified import target, against
         a plain package/module registry entry (`requests`, `fs`,
         `net/http` normalized to `net.http`).
      3. The bare root segment itself (no import needed - Python
         builtins like `open`, or a single-segment registry entry that
         happens to equal an unimported global like JS's `fetch`).
    """
    if not segments:
        return FeatureBit(0)
    joined = ".".join(segments)
    root = segments[0]
    resolved_root = import_map.resolve(root) if import_map is not None else None

    bits = FeatureBit(0)
    for category, entries in registry.items():
        bit = _CATEGORY_BIT[category]
        for entry in entries:
            normalized_entry = _normalize_go(entry) if lang == LanguageID.GO else entry
            if "." in normalized_entry:
                if joined == normalized_entry or joined.startswith(normalized_entry + "."):
                    bits |= bit
                    break
            else:
                if root == normalized_entry or resolved_root == normalized_entry:
                    bits |= bit
                    break
                if resolved_root is not None and (
                    resolved_root == normalized_entry or resolved_root.startswith(normalized_entry + ".")
                ):
                    bits |= bit
                    break
    return bits


def _direct_sink_bits(def_node: Node, parsed: ParsedFile, import_map) -> FeatureBit:
    lang = parsed.language_id
    registry = _registry_for(lang)
    if registry is None:
        return FeatureBit(0)
    call_type = CALL_NODE_TYPE.get(lang)
    if not call_type:
        return FeatureBit(0)
    bits = FeatureBit(0)
    for call_node in iter_scoped_nodes(def_node, {call_type}, lang):
        segments = call_callee_segments(call_node, parsed.source, lang)
        if not segments:
            continue
        bits |= _match_sink_bits_for_call(segments, import_map, lang, registry)
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
    calls. Two passes: direct sinks first (needed to know which callees
    are themselves sink-touching before propagation can run), then a
    second pass folds in one-hop transitive wrapper propagation.
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
                if wrapper_statement_counts.get(callee, 99) <= 2:
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
