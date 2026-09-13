"""v1.1: the one entry point most callers need - composes all four axes
(`prism.semantics.substance`/`form`/`output`/`role`) into a single
`{qualified_name: uint64 mask}` map, mirroring `prism.graph.contracts.
compute_contracts`'s/`prism.tagger.engine.TaggingEngine.tag_graph`'s own
"one function does the whole pass" shape rather than making every caller
re-orchestrate axis ordering (Role needs Substance's output; the other
three are independent) itself.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from prism.cache.sqlite_cache import load_file_cache_entry, save_file_cache_entry
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.semantics._ast_utils import count_statements
from prism.semantics.bitmask import SUBSTANCE_BITS, FeatureBit, compose_mask
from prism.semantics.form import compute_form_bits, extract_form
from prism.semantics.output import compute_output_bits, extract_output
from prism.semantics.role import compute_role_bits
from prism.semantics.substance import (
    _TRANSITIVE_WRAPPER_MAX_STATEMENTS,
    _direct_sink_bits,
    _has_state_mutation,
    compute_substance_bits,
)
from prism.traversal._cache_keys import _LRUCache, engine_commit_hash, target_repo_file_signature

_SUBSTANCE_MASK = compose_mask(*SUBSTANCE_BITS)

#: Step-1c in-memory layer on top of `compute_feature_masks_cached`'s own
#: per-file disk cache (`prism.cache.sqlite_cache`). That disk cache
#: still pays a real file-read + sha256 + sqlite-query cost per source
#: file on *every* call, even on a hit (Milestone 1 closure finding,
#: see `reports/pilot/methodology.md`) - this dict skips the whole
#: function body entirely on a repeat call within the same process, the
#: same in-memory treatment Steps 2/4 already gave `build_causal_graph`/
#: `compute_topological_distances`/the causal-edge functions.
#: `digest(repo_path, engine_commit_hash, file_hash_set) -> masks`.
#:
#: Bookmark 1 Item 3: bounded at 10 entries (LRU-evicted) - the
#: slowest-growing of the five caches, one entry per repo (not per
#: seed), same cap as `_GRAPH_CACHE`.
_FEATURE_MASKS_CACHE: _LRUCache[dict[str, int]] = _LRUCache(maxsize=10)


def _feature_masks_cache_key(repo_root: str) -> str:
    """`file_hash_set` here is `target_repo_file_signature(repo_root)` -
    not itself session-cached (no `@lru_cache`), but measured directly
    against the real Django corpus at ~2-3ms/call (a single `git
    rev-parse HEAD` for a git-tracked repo, not a full per-file rehash)
    - cheap enough to call fresh on every `compute_feature_masks_cached`
    invocation without threatening the <100ms warm-call target."""
    raw = "|".join((str(Path(repo_root).resolve()), engine_commit_hash(), target_repo_file_signature(repo_root)))
    return hashlib.sha256(raw.encode()).hexdigest()


def compute_feature_masks(builder: ConcreteGraphBuilder) -> dict[str, int]:
    """`Phi(v) = (S(v), F(v), O(v), R(v))`, composed into one plain `int`
    bitmask per function/method symbol `builder` indexed. Role is
    computed last since it depends on Substance's own output (a symbol's
    - and its callers'/callees' - sink domain); Substance/Form/Output are
    otherwise independent of each other and could run in any order.
    """
    substance = compute_substance_bits(builder)
    form = compute_form_bits(builder)
    output = compute_output_bits(builder)
    role = compute_role_bits(builder, substance)

    masks: dict[str, int] = {}
    for symbol in builder.symbol_table:
        if symbol.kind not in ("function", "method"):
            continue
        qname = symbol.qualified_name
        masks[qname] = compose_mask(
            substance.get(qname, 0),
            form.get(qname, 0),
            output.get(qname, 0),
            role.get(qname, 0),
        )
    return masks


def _file_content_hash(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def compute_feature_masks_cached(builder: ConcreteGraphBuilder, repo_root: str) -> dict[str, int]:
    """Same result as `compute_feature_masks`, but the file-local portion
    of each axis (Substance's *direct* sinks, all of Form, all of Output)
    is read from `prism.cache.sqlite_cache`'s per-file `file_cache_v2`
    table when that file's content hash hasn't changed, instead of
    re-running tree-sitter/AST extraction - see that module's own
    docstring for exactly what is and isn't safe to cache this way and
    why (Substance's one-hop transitive wrapper propagation and all of
    Role are graph-shaped, not file-shaped, and are always recomputed
    fresh here, cheaply, from the - possibly cached - direct bits).

    Step 1c: an in-memory session cache sits in front of the whole
    function body below - see `_FEATURE_MASKS_CACHE`'s own docstring.
    """
    cache_key = _feature_masks_cache_key(repo_root)
    cached_masks = _FEATURE_MASKS_CACHE.get(cache_key)
    if cached_masks is not None:
        return cached_masks

    symbols_by_file: dict[str, list] = {}
    for symbol in builder.symbol_table:
        if symbol.kind not in ("function", "method"):
            continue
        symbols_by_file.setdefault(symbol.file, []).append(symbol)

    base_bits: dict[str, FeatureBit] = {}
    statement_counts: dict[str, int] = {}
    import_map_cache: dict[str, object] = {}

    for file_path, symbols in symbols_by_file.items():
        parsed = builder.parsed_file(file_path)
        if parsed is None:
            continue
        content_hash = _file_content_hash(file_path)
        relative_path = symbols[0].module  # stable, file-derived identifier for this cache's own key
        cached = load_file_cache_entry(repo_root, relative_path, content_hash) if content_hash else None

        if cached is not None:
            for symbol in symbols:
                base_bits[symbol.qualified_name] = FeatureBit(cached["feature_bitmasks"].get(symbol.qualified_name, 0))
                def_node = builder.def_node(symbol.qualified_name)
                statement_counts[symbol.qualified_name] = count_statements(def_node, parsed.language_id) if def_node is not None else 99
            continue

        file_bitmasks: dict[str, int] = {}
        for symbol in symbols:
            def_node = builder.def_node(symbol.qualified_name)
            if def_node is None:
                continue
            if symbol.module not in import_map_cache:
                import_map_cache[symbol.module] = builder._build_import_map(parsed, symbol.module)
            import_map = import_map_cache[symbol.module]
            direct = _direct_sink_bits(def_node, parsed, import_map)
            statement_counts[symbol.qualified_name] = count_statements(def_node, parsed.language_id)
            if not direct:
                direct = FeatureBit.SINK_PURE_COMPUTE if not _has_state_mutation(def_node, parsed) else FeatureBit(0)
            combined = compose_mask(
                direct,
                extract_form(def_node, parsed, symbol.qualified_name),
                extract_output(def_node, parsed),
            )
            base_bits[symbol.qualified_name] = FeatureBit(combined)
            file_bitmasks[symbol.qualified_name] = combined

        if content_hash:
            save_file_cache_entry(
                repo_root, relative_path, content_hash, 0.0,
                serialized_symbols=[s.qualified_name for s in symbols],
                feature_bitmasks=file_bitmasks,
                local_data_flow=[],
            )

    # Substance-transitive propagation and Role are graph-shaped - always
    # recomputed fresh (cheap: no AST walking, just edge/dict lookups).
    substance_only: dict[str, int] = {}
    for symbol in builder.symbol_table:
        if symbol.kind not in ("function", "method"):
            continue
        qname = symbol.qualified_name
        own = int(base_bits.get(qname, FeatureBit(0))) & (_SUBSTANCE_MASK & ~int(FeatureBit.SINK_PURE_COMPUTE))
        transitive = 0
        if qname in builder.graph:
            for _u, callee, data in builder.graph.out_edges(qname, data=True):
                if data.get("relation", "CALLS") not in ("CALLS", "INSTANTIATES"):
                    continue
                callee_bits = int(base_bits.get(callee, FeatureBit(0))) & (_SUBSTANCE_MASK & ~int(FeatureBit.SINK_PURE_COMPUTE))
                if callee_bits and statement_counts.get(callee, 99) <= _TRANSITIVE_WRAPPER_MAX_STATEMENTS:
                    transitive |= callee_bits
        combined = own | transitive
        if not combined:
            # Mirror `compute_substance_bits`'s own fallback exactly - an
            # empty combination means "no real sink, direct or
            # transitive", not "pure": a symbol with a real state
            # mutation and no sink must stay bit-empty here too, not be
            # miscategorized as SINK_PURE_COMPUTE. `_has_state_mutation`
            # is re-checked fresh rather than trusted from `base_bits`
            # because masking out SINK_PURE_COMPUTE above (to keep it
            # out of `own`/`callee_bits`, which must never themselves
            # carry it) also erases the impure/pure distinction the
            # earlier direct-bit computation already made.
            def_node = builder.def_node(qname)
            parsed = builder.parsed_file(symbol.file)
            is_pure = True
            if def_node is not None and parsed is not None:
                is_pure = not _has_state_mutation(def_node, parsed)
            if is_pure:
                combined = int(FeatureBit.SINK_PURE_COMPUTE)
        substance_only[qname] = combined

    role = compute_role_bits(builder, substance_only)

    masks: dict[str, int] = {}
    for symbol in builder.symbol_table:
        if symbol.kind not in ("function", "method"):
            continue
        qname = symbol.qualified_name
        non_substance = int(base_bits.get(qname, FeatureBit(0))) & ~_SUBSTANCE_MASK
        masks[qname] = compose_mask(substance_only.get(qname, 0), non_substance, role.get(qname, 0))
    _FEATURE_MASKS_CACHE[cache_key] = masks
    return masks
