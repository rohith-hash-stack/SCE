"""Item 12 (second post-implementation audit): Incremental File Caching.

`build_pipeline` (`prism.cli`) re-parses and re-links every source file on
every single invocation - real, but bounded, work
(`prism.runtime.contract_cache`'s own docstring already documents this
tradeoff for the separate contract-extraction step: "caching *that*
safely would mean invalidating on a per-file basis with a much larger
blast radius"). This module applies the exact same scope discipline to
the index itself: a **whole-repository content-hash fingerprint**, not
per-file partial invalidation.

Why not partial/incremental linking: Pass 2 (`ConcreteGraphBuilder.
pass2_resolve_calls`) resolves calls across file boundaries (imports,
cross-module instance binding, Go's repo-wide type registries) - a single
changed file can, in the general case, change what an *unrelated* file's
call sites resolve to (a renamed re-export, a newly-ambiguous simple
name). Nothing in this codebase tracks that dependency graph today, so a
cache that tried to re-link only the changed files' own edges would risk
silently stale results elsewhere - a correctness regression this module
refuses to introduce for a speed win. What it *does* give is real and
common: the no-op case (an agent re-running `prism query` repeatedly
against a snapshot nothing has touched since the last `prism index`/
`prism query` call) skips the two-pass linker and tag inference entirely -
the parts that actually scale with cross-file reference resolution.

One thing a cache hit still redoes: re-parsing every file with
tree-sitter into a `ParsedFile` (source bytes + CST). A tree-sitter
`Tree`/`Node` is a native, non-picklable, non-JSON-serializable object -
there is no sane way to persist it across processes - and `prism.slicer`
needs the *real* parsed source (`ConcreteGraphBuilder.parsed_file`) to
render L0-L3 context, not just the graph/symbol-table shape this module
does cache. Re-parsing alone is real but comparatively small work next to
what a cache hit skips (no query traversal, no cross-file symbol/type
resolution, no tag inference passes) - and it reuses the very same file
bytes this module already read to compute the content hash, rather than
reading each file from disk twice.

Storage: SQLite at `.prism/cache/index.db` (not `.prism/*.json` like
`contract_cache.py`/`reconciler.py` - Item 12 asks for SQLite specifically,
and a real per-file table is a more natural fit for "list every file and
its hash" than one big JSON blob), keyed by **content hash** (SHA-256),
not `(mtime, size)` the way `contract_cache.py` fingerprints - a checkout,
a touched-but-unchanged file, or a build tool that rewrites timestamps
must not cost a full re-index, and a hash catches a content change a
`(mtime, size)` pair could in principle miss (same size, coincidentally
close mtime).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.symbol_table import GlobalSymbolTable, SymbolInfo
from prism.parser.queries import run_query
from prism.parser.tree_sitter_loader import ParsedFile, parse_source

try:
    import networkx as nx
except ImportError:  # pragma: no cover - networkx is a hard dependency elsewhere
    nx = None  # type: ignore[assignment]

#: Bumped whenever the serialized snapshot's shape changes - an old cache
#: written by a prior schema version is just another cache miss (rebuild
#: and overwrite), never a crash trying to deserialize a shape this
#: version doesn't expect.
_SCHEMA_VERSION = 1


def index_cache_path(repo_root: str) -> Path:
    return Path(repo_root) / ".prism" / "cache" / "index.db"


def _read_and_hash(path: str) -> tuple[str, bytes] | None:
    """The file's raw bytes plus their SHA-256 hex digest in one read, or
    `None` if it can't be read right now (removed mid-scan, a permissions
    error) - treated as "definitely not a cache hit" by the caller, never
    as a crash."""
    try:
        with open(path, "rb") as f:
            data = f.read()
        return hashlib.sha256(data).hexdigest(), data
    except OSError:
        return None


def compute_file_hashes(files: list[str]) -> dict[str, str]:
    """`{path: sha256_hex}` for every file in `files` that could be read -
    a file that failed to hash is simply omitted, which makes it differ
    from any previously-cached entry for that same path (the cache lookup
    below requires an *exact* key set match), correctly forcing a cache
    miss rather than matching on a partial/guessed hash.

    Keyed by `path` exactly as given (never re-normalized through
    `os.path.abspath`) - `files` already comes from `discover_files`
    joined onto an already-absolute `repo_root`, and this exact string is
    also what ends up as `SymbolInfo.file`/`ParsedFile.path` throughout
    the rest of the pipeline (`ConcreteGraphBuilder.parsed_file` looks
    files up by that same string) - introducing a second, independently
    "normalized" path form here would risk a silent key mismatch between
    a cache-hydrated builder and a freshly-built one.
    """
    hashes: dict[str, str] = {}
    for path in files:
        result = _read_and_hash(path)
        if result is not None:
            hashes[path] = result[0]
    return hashes


def _compute_file_hashes_and_bytes(files: list[str]) -> dict[str, tuple[str, bytes]]:
    """Like `compute_file_hashes`, but also keeps each file's bytes around
    - used only on the cache-hit path, where those same bytes get reused
    to re-parse the file (see this module's own docstring) instead of
    reading it from disk a second time.
    """
    out: dict[str, tuple[str, bytes]] = {}
    for path in files:
        result = _read_and_hash(path)
        if result is not None:
            out[path] = result
    return out


def _connect(repo_root: str) -> sqlite3.Connection:
    path = index_cache_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, content_hash TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS snapshot ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), "
        "schema_version INTEGER NOT NULL, "
        "language_tier TEXT NOT NULL, "
        "graph_json TEXT NOT NULL, "
        "symbols_json TEXT NOT NULL, "
        "tag_matrix_json TEXT NOT NULL, "
        "index_errors_json TEXT NOT NULL, "
        "go_receiver_total INTEGER NOT NULL, "
        "go_receiver_resolved INTEGER NOT NULL"
        ")"
    )
    return conn


def _node_link_data_compat(graph: "nx.DiGraph") -> dict:
    try:
        return nx.node_link_data(graph, edges="edges")
    except TypeError:
        # Older networkx (< 3.4) doesn't accept `edges=`; its default
        # ("links") is a fine, equally round-trippable key name - the
        # loader side below reads whichever key `node_link_graph` itself
        # expects for the installed version, so this stays symmetric.
        return nx.node_link_data(graph)


def _node_link_graph_compat(data: dict) -> "nx.DiGraph":
    try:
        return nx.node_link_graph(data, directed=True, multigraph=False, edges="edges")
    except TypeError:
        return nx.node_link_graph(data, directed=True, multigraph=False)


def _jsonable_attrs(attrs: dict) -> dict:
    """`tags` (a `set[str]`) is the one node-attribute value this
    codebase's build_pipeline stages ever put on a node that isn't
    already a JSON-native type - see this module's own docstring survey.
    Converted to a sorted list for a deterministic, diffable cache file;
    restored to a `set` by `_restore_attrs` on load.
    """
    out = dict(attrs)
    if isinstance(out.get("tags"), set):
        out["tags"] = sorted(out["tags"])
    return out


def _restore_attrs(attrs: dict) -> dict:
    out = dict(attrs)
    if "tags" in out and isinstance(out["tags"], list):
        out["tags"] = set(out["tags"])
    if "line_range" in out and isinstance(out["line_range"], list):
        out["line_range"] = tuple(out["line_range"])
    return out


def save_pipeline_to_cache(
    repo_root: str,
    files: list[str],
    language_tier: str,
    builder: ConcreteGraphBuilder,
    tag_matrix: dict[str, set[str]],
) -> None:
    """Persists the just-built `(builder, tag_matrix)` plus the exact set
    of `(file, content_hash)` pairs it was built from. Never raises on a
    write failure (a read-only filesystem, a full disk) - caching is an
    optimization, never a correctness dependency, so a failed save just
    means the next invocation pays full indexing cost again, exactly like
    today without this module at all.
    """
    if nx is None:
        return
    try:
        hashes = compute_file_hashes(files)

        graph_data = _node_link_data_compat(builder.graph)
        graph_data["nodes"] = [_jsonable_attrs(n) for n in graph_data["nodes"]]
        edge_key = "edges" if "edges" in graph_data else "links"
        graph_data[edge_key] = [_jsonable_attrs(e) for e in graph_data[edge_key]]

        symbols = [
            {
                "qualified_name": s.qualified_name,
                "kind": s.kind,
                "file": s.file,
                "line_range": list(s.line_range),
                "language_id": s.language_id,
                "module": s.module,
                "enclosing_class": s.enclosing_class,
            }
            for s in builder.symbol_table
        ]

        conn = _connect(repo_root)
        try:
            with conn:
                conn.execute("DELETE FROM files")
                conn.executemany(
                    "INSERT INTO files (path, content_hash) VALUES (?, ?)",
                    list(hashes.items()),
                )
                conn.execute("DELETE FROM snapshot")
                conn.execute(
                    "INSERT INTO snapshot (id, schema_version, language_tier, graph_json, symbols_json, "
                    "tag_matrix_json, index_errors_json, go_receiver_total, go_receiver_resolved) "
                    "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        _SCHEMA_VERSION,
                        language_tier,
                        json.dumps(graph_data),
                        json.dumps(symbols),
                        json.dumps({k: sorted(v) for k, v in tag_matrix.items()}),
                        json.dumps(builder.index_errors),
                        builder._go_receiver_call_sites_total,
                        builder._go_receiver_call_sites_resolved,
                    ),
                )
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return


def _rehydrate_def_nodes(builder: ConcreteGraphBuilder, parsed: ParsedFile, symbols: list) -> None:
    """Repopulates `builder._def_nodes` for one freshly-re-parsed file's
    own symbols on a cache hit.

    `_def_nodes` (`ConcreteGraphBuilder.def_node`, a *public* accessor
    `prism.graph.contracts`/`prism.graph.blueprint`/`prism.semantics`'s
    four-axis extractors all call on every symbol) is Pass 1's own
    bookkeeping dict, entirely separate from the cached graph/symbol-table
    snapshot this module persists - a real gap caught only once
    `prism.semantics` started exercising `def_node()` against a
    cache-hit builder for the *second* time a fixture repo was indexed in
    one process (the first index is always a cold miss, so this was
    invisible to every test that only builds a repo once).

    Rather than re-deriving each symbol's qualified name from scratch
    (duplicating `ConcreteGraphBuilder._register_definition`'s own
    enclosing-class/module logic a second, driftable way), this reuses
    the *cached* `SymbolInfo.line_range` as the join key: the same
    `definitions` tree-sitter query Pass 1 itself runs is run again here
    against the fresh parse tree, and each candidate node is matched back
    to its cached `SymbolInfo` purely by 1-indexed start line - a symbol
    whose line moved (the file changed) is definitionally a cache miss
    already handled upstream, so an exact line match is always available
    for anything that reaches this function.
    """
    if not symbols:
        return
    by_start_line = {s.line_range[0]: s for s in symbols}
    captures = run_query(parsed.language_id, "definitions", parsed.root_node)
    for key in ("def.class", "def.interface", "def.function"):
        for node in captures.get(key, []):
            start_line = node.start_point[0] + 1
            symbol = by_start_line.get(start_line)
            if symbol is not None:
                builder._def_nodes[symbol.qualified_name] = node


def load_pipeline_from_cache(
    repo_root: str, files: list[str], language_tier: str
) -> tuple[ConcreteGraphBuilder, dict[str, set[str]]] | None:
    """Returns a rehydrated `(builder, tag_matrix)` iff every file in
    `files` is present in the cache with a matching content hash, no
    *extra* cached file is left over (a deletion also invalidates), and
    the cached snapshot was built under the same `language_tier` -
    `None` on any miss, corrupt cache, or read failure, never raises (same
    "cache is an optimization" contract as `save_pipeline_to_cache`).
    """
    if nx is None:
        return None
    path = index_cache_path(repo_root)
    if not path.exists():
        return None
    try:
        current_with_bytes = _compute_file_hashes_and_bytes(files)
        current = {path: h for path, (h, _data) in current_with_bytes.items()}
        conn = _connect(repo_root)
        try:
            cached = dict(conn.execute("SELECT path, content_hash FROM files").fetchall())
            if cached != current:
                return None
            row = conn.execute(
                "SELECT schema_version, language_tier, graph_json, symbols_json, tag_matrix_json, "
                "index_errors_json, go_receiver_total, go_receiver_resolved FROM snapshot WHERE id = 1"
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        (
            schema_version, cached_tier, graph_json, symbols_json,
            tag_matrix_json, index_errors_json, go_total, go_resolved,
        ) = row
        if schema_version != _SCHEMA_VERSION or cached_tier != language_tier:
            return None

        graph_data = json.loads(graph_json)
        graph_data["nodes"] = [_restore_attrs(n) for n in graph_data["nodes"]]
        edge_key = "edges" if "edges" in graph_data else "links"
        graph_data[edge_key] = [_restore_attrs(e) for e in graph_data[edge_key]]
        graph = _node_link_graph_compat(graph_data)

        symbol_table = GlobalSymbolTable()
        for entry in json.loads(symbols_json):
            symbol_table.add(
                SymbolInfo(
                    qualified_name=entry["qualified_name"],
                    kind=entry["kind"],
                    file=entry["file"],
                    line_range=tuple(entry["line_range"]),
                    language_id=entry["language_id"],
                    module=entry["module"],
                    enclosing_class=entry["enclosing_class"],
                )
            )

        builder = ConcreteGraphBuilder(repo_root, symbol_table)
        builder.graph = graph
        builder.index_errors = json.loads(index_errors_json)
        builder._go_receiver_call_sites_total = go_total
        builder._go_receiver_call_sites_resolved = go_resolved

        # `ParsedFile` (tree-sitter CST + source bytes) can't be cached -
        # see this module's own docstring - so it's rebuilt here from the
        # exact bytes this function already read to confirm the cache hit,
        # not read from disk a second time. A file the *cached snapshot*
        # never indexed in the first place (skipped by `parse_file`
        # itself - an unsupported extension slipping through
        # `discover_files`, vanishingly rare) or one Item 4's own error
        # boundary skipped that run is simply left absent here too,
        # exactly matching what a full rebuild would have left in
        # `_parsed_files`.
        error_files = {e["file"] for e in builder.index_errors}
        symbols_by_file: dict[str, list] = {}
        for symbol in symbol_table:
            symbols_by_file.setdefault(symbol.file, []).append(symbol)

        for file_path, (_hash, data) in current_with_bytes.items():
            if file_path in error_files:
                continue
            parsed = parse_source(file_path, data)
            if parsed is not None:
                builder._parsed_files[file_path] = parsed
                _rehydrate_def_nodes(builder, parsed, symbols_by_file.get(file_path, []))

        tag_matrix = {k: set(v) for k, v in json.loads(tag_matrix_json).items()}
        return builder, tag_matrix
    except (OSError, sqlite3.Error, json.JSONDecodeError, KeyError, TypeError):
        return None
