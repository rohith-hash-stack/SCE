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

from prism.cache.sqlite_cache import load_file_cache_entry, save_file_cache_entry
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.semantics._ast_utils import count_statements
from prism.semantics.bitmask import SUBSTANCE_BITS, FeatureBit, compose_mask
from prism.semantics.form import compute_form_bits, extract_form
from prism.semantics.output import compute_output_bits, extract_output
from prism.semantics.role import compute_role_bits
from prism.semantics.substance import _direct_sink_bits, _has_state_mutation, compute_substance_bits

_SUBSTANCE_MASK = compose_mask(*SUBSTANCE_BITS)


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
    """
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
                if callee_bits and statement_counts.get(callee, 99) <= 2:
                    transitive |= callee_bits
        combined = own | transitive
        if not combined:
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
    return masks
