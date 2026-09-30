"""Deterministic context operations the 8 arms are built from.

Everything here is pure with respect to the LLM: no API call, no
randomness that isn't seeded from the task identity. That is what lets a
reviewer re-derive any arm's context (and its hash) offline.

- 4-axis manifest annotation and redaction (`annotate_manifest_axes`,
  `redact_package_axes`) - the `prism_full` vs `ablation_signature_only`
  vs `ablation_no_purity` contrast.
- Lexical (BM25) anchor selection (`LexicalAnchorIndex`) - the
  `ablation_lexical_anchors` contrast.
- The deterministic AST scaffolding rule (`ast_scaffold`) - the
  `scaffolded_oracle` arm and every arm's `sufficiency_ratio`.
- Fixed distractor sets (`select_distractors`, `inject_distractors`) -
  the `prism_plus_distractors` dose-response arm.
- The hard token ceiling (`enforce_token_ceiling`) - `is_truncated`.
"""
from __future__ import annotations

import hashlib
import random
import re
from collections import deque
from dataclasses import dataclass

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.semantics.bitmask import FORM_BITS, OUTPUT_BITS, ROLE_BITS, SUBSTANCE_BITS, FeatureBit
from prism.slicer.tokenizer import count_tokens
from prism.surface.build import _axis_labels, _node_body, _node_signature, _relative_path
from prism.surface.models import (
    ContextPackage,
    CoverageSummary,
    NodeEntry,
    NodeFeatures,
    NodeSignatureReturn,
)
from prism.surface.renderer import RenderOptions, render

from benchmarks.final_sweep.config import AxisPolicy

RENDER_OPTIONS = RenderOptions(include_timestamp=False, include_run_id=False)

#: Symbol kinds that declare a type rather than executable behavior.
TYPE_KINDS = frozenset({"class", "interface", "struct", "enum", "type_alias", "trait", "protocol"})
#: Kinds eligible as distractors - executable code only, the same shape
#: of thing a real retrieval false positive is.
CALLABLE_KINDS = frozenset({"function", "method"})

#: A type declaration pulled in as scaffolding is rendered from its first
#: lines only (header, fields, member signatures). A whole Django model
#: class at L0 would otherwise dwarf the budget and make "scaffolding
#: included" and "scaffolding truncated" the same event.
SCAFFOLD_TYPE_MAX_LINES = 40
#: Hard cap on AST-rule scaffold symbols per task, so a hub pipeline
#: symbol can't turn the scaffolded oracle into a second BFS.
SCAFFOLD_MAX_SYMBOLS = 12

#: Hops around every ground-truth/candidate symbol inside which a symbol
#: is considered related, and so never eligible as a distractor.
DISTRACTOR_EXCLUSION_HOPS = 2

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_context(pkg: ContextPackage) -> str:
    return render(pkg, RENDER_OPTIONS)


# --------------------------------------------------------------------- #
# 4-axis annotation / redaction
# --------------------------------------------------------------------- #

_AXIS_TABLE = (("S", SUBSTANCE_BITS), ("F", FORM_BITS), ("O", OUTPUT_BITS), ("R", ROLE_BITS))


def axes_field(mask: int, policy: AxisPolicy) -> str | None:
    """The `axes=` manifest field for one symbol under `policy`, or `None`
    when the policy shows no axes at all. Substance (`S`) is the
    purity/side-effect axis: its bits are the effect sinks
    (`SINK_NETWORK_IO`, `SINK_DATABASE_IO`, ...) and `SINK_PURE_COMPUTE`."""
    if policy == "none":
        return None
    parts = []
    for label, bits in _AXIS_TABLE:
        if policy == "no_substance" and label == "S":
            continue
        parts.append(f"{label}:{_axis_labels(mask, bits)}")
    return "axes=" + ";".join(parts)


def annotate_manifest_axes(manifest_text: str, feature_masks: dict[str, int], policy: AxisPolicy) -> str:
    """Appends each row's `|axes=...` field. Rows are the production
    `qualified_name|role|kind|signature|calls=[...]` lines
    (`prism.packer.candidate_index.build_candidate_manifest`); the
    `<candidate_index>` wrapper lines pass through untouched. A no-op
    for `policy == "none"`, so that arm's manifest is byte-identical to
    the production two-pass manifest."""
    if policy == "none":
        return manifest_text
    out = []
    for line in manifest_text.splitlines():
        if not line or line.startswith("<"):
            out.append(line)
            continue
        qname = line.split("|", 1)[0]
        field = axes_field(feature_masks.get(qname, 0), policy)
        out.append(f"{line}|{field}" if field else line)
    return "\n".join(out)


def turn1_row_format(policy: AxisPolicy) -> str:
    """The row-format sentence for the Turn-1 system prompt - it has to
    describe exactly the fields the arm's manifest carries."""
    base = (
        "qualified_name|role|kind|signature|calls=[...] (role is one of seed/callee/caller/transitive; "
        "signature is the symbol's own raw declaration line; calls lists the names it directly invokes "
        "in its own body, deterministically extracted, never a docstring or comment)"
    )
    if policy == "none":
        return base
    axes = [
        ("S", "Substance: side effects the body performs - NETWORK_IO/DATABASE_IO/FILESYSTEM_IO/PROCESS_IO/"
              "TIME_IO/RANDOMNESS, or PURE_COMPUTE for none"),
        ("F", "Form: control-flow motifs such as VALIDATOR, GUARD_EARLY_EXIT, BRANCH_DISPATCH, PIPELINE"),
        ("O", "Output: return contract such as PREDICATE, COMMAND, QUERY, FACTORY, TRANSFORMER, FLUENT"),
        ("R", "Role: topological role such as ENTRYPOINT, ORCHESTRATOR, ADAPTER, LEAF_UTILITY, BRIDGE"),
    ]
    if policy == "no_substance":
        axes = axes[1:]
    listing = "; ".join(f"{k} = {d}" for k, d in axes)
    return f"{base}, followed by |axes=... giving each symbol's statically extracted semantic axes ({listing})"


def redact_package_axes(pkg: ContextPackage, policy: AxisPolicy) -> ContextPackage:
    """Applies the same axis policy to the Turn-2 context the manifest got,
    so an ablation can't leak the suppressed axis back in through the
    rendered `<features>` element, the coverage summary, or the Output-
    derived `returns.kind`."""
    if policy == "all":
        return pkg
    nodes = []
    for node in pkg.nodes:
        if policy == "none":
            features = NodeFeatures(substance="NONE", form="NONE", output="NONE", role="NONE")
            signature = node.signature
            if signature.returns is not None:
                signature = signature.model_copy(
                    update={"returns": NodeSignatureReturn(type=signature.returns.type, kind="Unknown")}
                )
            nodes.append(node.model_copy(update={"features": features, "signature": signature}))
        else:
            nodes.append(node.model_copy(update={"features": node.features.model_copy(update={"substance": "NONE"})}))
    if policy == "none":
        coverage = CoverageSummary(total_features=0, covered_features=0, omitted_features=0, features=[])
    else:
        kept = [f for f in pkg.coverage.features if not f.id.startswith("SINK_")]
        gaps = [g for g in pkg.coverage.gaps if not g.feature.startswith("SINK_")]
        covered = sum(1 for f in kept if f.present)
        coverage = CoverageSummary(
            total_features=len(kept), covered_features=covered, omitted_features=len(kept) - covered,
            features=kept, gaps=gaps,
        )
    return pkg.model_copy(update={"nodes": nodes, "coverage": coverage})


# --------------------------------------------------------------------- #
# Lexical (BM25) anchors
# --------------------------------------------------------------------- #

def lexical_tokens(text: str) -> list[str]:
    """Identifier-aware word tokens: `createRouterFactory`,
    `create_router_factory` and `core.router` all split into their
    lower-cased parts, and the whole identifier is kept too, so an exact
    name mention still outscores a partial one."""
    tokens: list[str] = []
    for ident in _IDENT_RE.findall(text):
        tokens.append(ident.lower())
        parts = [p.lower() for chunk in ident.split("_") for p in _CAMEL_RE.findall(chunk)]
        if len(parts) > 1:
            tokens.extend(parts)
    return tokens


class LexicalAnchorIndex:
    """BM25 over one document per callable symbol (qualified name +
    declaration line + docstring), queried with the task prompt. The top
    hit is the lexical anchor. Ties break by qualified name, so the
    choice is deterministic."""

    def __init__(self, builder: ConcreteGraphBuilder, contracts: dict) -> None:
        from rank_bm25 import BM25Okapi

        self._names: list[str] = []
        corpus: list[list[str]] = []
        for qname in sorted(builder.symbol_table._symbols):
            info = builder.symbol_table.get(qname)
            if info is None or info.kind not in CALLABLE_KINDS:
                continue
            body = _node_body(builder, qname)
            header = body.split("\n", 1)[0] if body else ""
            contract = contracts.get(qname)
            doc = getattr(contract, "docstring", None) or ""
            corpus.append(lexical_tokens(f"{qname} {header} {doc}"))
            self._names.append(qname)
        self._bm25 = BM25Okapi(corpus) if corpus else None

    def top(self, query: str, k: int = 1) -> list[tuple[str, float]]:
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(lexical_tokens(query))
        ranked = sorted(zip(self._names, scores), key=lambda p: (-p[1], p[0]))
        return [(name, float(score)) for name, score in ranked[:k]]


# --------------------------------------------------------------------- #
# Deterministic AST scaffolding rule
# --------------------------------------------------------------------- #

def _identifiers_in_symbol(builder: ConcreteGraphBuilder, qname: str) -> set[str]:
    """Every identifier-like leaf in `qname`'s own AST subtree
    (tree-sitter `identifier`/`type_identifier`/...), read from the parsed
    tree rather than regex over text - so comments and string literals
    don't count as references."""
    info = builder.symbol_table.get(qname)
    if info is None:
        return set()
    parsed = builder.parsed_file(info.file)
    if parsed is None:
        return set()
    start, end = info.line_range
    found: set[str] = set()
    stack = [parsed.root_node]
    while stack:
        node = stack.pop()
        node_start, node_end = node.start_point[0] + 1, node.end_point[0] + 1
        if node_end < start or node_start > end:
            continue
        if node.child_count == 0:
            if node.type.endswith("identifier") and start <= node_start <= end:
                found.add(parsed.source[node.start_byte:node.end_byte].decode("utf-8", errors="replace"))
            continue
        if node.type in ("comment", "string", "template_string", "string_literal", "interpreted_string_literal"):
            continue
        stack.extend(node.children)
    return found


@dataclass(frozen=True)
class ScaffoldEntry:
    symbol: str
    #: "type" | "validator" | "initializer"
    category: str


class ScaffoldIndex:
    """The deterministic AST scaffolding rule, per corpus. For each
    ground-truth pipeline symbol `p` (excluding pipeline members
    themselves), in this category order:

    1. **type** - every class/interface/struct/enum declaration whose
       bare name appears as an identifier in `p`'s own AST subtree
       (parameter/return annotations, instantiations, `extends` clauses).
       A bare name declared by several types resolves to the one sharing
       `p`'s module if exactly one does, else is skipped as ambiguous.
    2. **validator** - a direct 1-hop CALLS/INSTANTIATES callee of `p`
       whose Form axis carries `FORM_VALIDATOR`.
    3. **initializer** - a direct 1-hop CALLS/INSTANTIATES callee of `p`
       whose Output axis is `OUTPUT_FACTORY` or `OUTPUT_FLUENT` - the
       router/builder/procedure construction code a pipeline stage
       invokes. Callers are deliberately excluded: an upstream consumer
       that happens to build something is not scaffolding for `p`.

    Deduplicated in first-seen order, capped at `SCAFFOLD_MAX_SYMBOLS`.
    Nothing here reads ground truth beyond `pipeline_symbols` itself, so
    it's the same rule for every task and every corpus."""

    def __init__(self, builder: ConcreteGraphBuilder, feature_masks: dict[str, int]) -> None:
        self._builder = builder
        self._masks = feature_masks
        self._types_by_name: dict[str, list[str]] = {}
        for qname in sorted(builder.symbol_table._symbols):
            info = builder.symbol_table.get(qname)
            if info is not None and info.kind in TYPE_KINDS:
                self._types_by_name.setdefault(qname.rsplit(".", 1)[-1], []).append(qname)

    def _resolve_type(self, name: str, module: str) -> str | None:
        options = self._types_by_name.get(name, [])
        if len(options) == 1:
            return options[0]
        same_module = [q for q in options if (info := self._builder.symbol_table.get(q)) is not None and info.module == module]
        return same_module[0] if len(same_module) == 1 else None

    def scaffold(self, pipeline: list[str]) -> list[ScaffoldEntry]:
        builder = self._builder
        pipeline_set = set(pipeline)
        entries: list[ScaffoldEntry] = []
        seen: set[str] = set()

        def add(symbol: str, category: str) -> None:
            if symbol in seen or symbol in pipeline_set or builder.symbol_table.get(symbol) is None:
                return
            seen.add(symbol)
            entries.append(ScaffoldEntry(symbol, category))

        for p in pipeline:
            info = builder.symbol_table.get(p)
            if info is None:
                continue
            for name in sorted(_identifiers_in_symbol(builder, p)):
                resolved = self._resolve_type(name, info.module)
                if resolved is not None and resolved != info.enclosing_class and not p.startswith(resolved + "."):
                    add(resolved, "type")
        graph = builder.graph
        for p in pipeline:
            if p not in graph:
                continue
            for succ in sorted(graph.successors(p)):
                relation = (graph.get_edge_data(p, succ) or {}).get("relation")
                if relation in ("CALLS", "INSTANTIATES") and self._masks.get(succ, 0) & int(FeatureBit.FORM_VALIDATOR):
                    add(succ, "validator")
        init_bits = int(FeatureBit.OUTPUT_FACTORY) | int(FeatureBit.OUTPUT_FLUENT)
        for p in pipeline:
            if p not in graph:
                continue
            for succ in sorted(graph.successors(p)):
                relation = (graph.get_edge_data(p, succ) or {}).get("relation")
                if relation in ("CALLS", "INSTANTIATES") and self._masks.get(succ, 0) & init_bits:
                    add(succ, "initializer")
        return entries[:SCAFFOLD_MAX_SYMBOLS]


def required_scaffold(task, scaffold: list[ScaffoldEntry], builder: ConcreteGraphBuilder) -> list[str]:
    """What `sufficiency_ratio` checks for, identically for every arm:
    the annotators' own `required_context` (only names that resolve in
    the indexed corpus - an unresolvable annotation can't be present in
    any arm's context and would only add a constant offset) plus the AST
    scaffold. Sorted for determinism."""
    annotated = {s for s in task.adjudicated.required_context if builder.symbol_table.get(s) is not None}
    return sorted(annotated | {e.symbol for e in scaffold})


def scaffold_body(builder: ConcreteGraphBuilder, qname: str) -> tuple[str, bool]:
    """`(body, pruned)` for a scaffold symbol: callables in full, type
    declarations cut to `SCAFFOLD_TYPE_MAX_LINES`."""
    body = _node_body(builder, qname)
    info = builder.symbol_table.get(qname)
    if info is not None and info.kind in TYPE_KINDS:
        lines = body.split("\n")
        if len(lines) > SCAFFOLD_TYPE_MAX_LINES:
            return "\n".join(lines[:SCAFFOLD_TYPE_MAX_LINES]) + "\n  // ... (scaffold truncated)", True
    return body, False


def node_for_symbol(
    builder: ConcreteGraphBuilder,
    repo_root: str,
    contracts: dict,
    feature_masks: dict[str, int],
    qname: str,
    role: str,
    distance: float,
    body: str | None = None,
    compression: str = "L0_full",
) -> NodeEntry | None:
    """A fully populated `NodeEntry` for an in-repo symbol, built exactly
    the way `benchmarks.engines.oracle_engine.PragmaticOracle` builds its
    own - `None` if `qname` isn't in the symbol table."""
    info = builder.symbol_table.get(qname)
    if info is None:
        return None
    mask = feature_masks.get(qname, 0)
    text = _node_body(builder, qname) if body is None else body
    return NodeEntry(
        id=qname,
        role=role,
        distance=distance,
        compression=compression,
        cost=count_tokens(text),
        symbol_name=qname.rsplit(".", 1)[-1],
        symbol_kind=info.kind,
        language=info.language_id,
        file=_relative_path(repo_root, info.file),
        line=info.line_range[0],
        end_line=info.line_range[1],
        signature=_node_signature(qname, contracts, mask),
        features=NodeFeatures(
            substance=_axis_labels(mask, SUBSTANCE_BITS),
            form=_axis_labels(mask, FORM_BITS),
            output=_axis_labels(mask, OUTPUT_BITS),
            role=_axis_labels(mask, ROLE_BITS),
        ),
        body=text,
    )


# --------------------------------------------------------------------- #
# Distractors
# --------------------------------------------------------------------- #

def _undirected_neighborhood(builder: ConcreteGraphBuilder, roots: set[str], hops: int) -> set[str]:
    graph = builder.graph
    seen = {r for r in roots if r in graph}
    queue = deque((r, 0) for r in seen)
    while queue:
        node, depth = queue.popleft()
        if depth >= hops:
            continue
        for nb in list(graph.successors(node)) + list(graph.predecessors(node)):
            if nb not in seen:
                seen.add(nb)
                queue.append((nb, depth + 1))
    return seen | roots


def select_distractors(
    builder: ConcreteGraphBuilder,
    repo: str,
    task_id: str,
    related: set[str],
    k: int,
) -> list[str]:
    """`k` repository functions unrelated to the task: callables (never
    tests - `SymbolRole.IMPLEMENTATION` only) outside the
    `DISTRACTOR_EXCLUSION_HOPS`-hop undirected neighborhood of `related`
    (the task's ground-truth universe, scaffold, and the two-pass
    candidate universe). Sampled with an RNG seeded from `repo|task_id`
    only - never the sampling seed - so every seed of a task sees the
    *same* distractors and the seed axis measures model variance alone.
    Prefix-nested: the k=5 set is the first 5 of the k=10 set, so dose
    levels differ only in dose."""
    excluded = _undirected_neighborhood(builder, related, DISTRACTOR_EXCLUSION_HOPS)
    pool = []
    for qname in sorted(builder.symbol_table._symbols):
        info = builder.symbol_table.get(qname)
        if info is None or info.kind not in CALLABLE_KINDS or qname in excluded:
            continue
        if getattr(info.role, "value", info.role) != "implementation":
            continue
        pool.append(qname)
    rng = random.Random(int(sha256_text(f"{repo}|{task_id}|distractors")[:16], 16))
    rng.shuffle(pool)
    return pool[:k]


def inject_distractors(
    pkg: ContextPackage,
    distractor_nodes: list[NodeEntry],
    repo: str,
    task_id: str,
) -> ContextPackage:
    """Adds `distractor_nodes` with `role="transitive"` and a `distance`
    drawn (task-seeded) from the range the real non-seed nodes already
    span - the renderer orders nodes by distance, so distractors land
    interleaved with real context rather than stacked at the end where
    position alone would give them away."""
    if not distractor_nodes:
        return pkg
    distances = [n.distance for n in pkg.nodes if n.role != "seed"] or [1.0]
    lo, hi = min(distances), max(distances)
    rng = random.Random(int(sha256_text(f"{repo}|{task_id}|distractor-positions")[:16], 16))
    placed = [
        node.model_copy(update={"role": "transitive", "distance": round(rng.uniform(lo, hi), 4)})
        for node in distractor_nodes
    ]
    return pkg.model_copy(update={"nodes": [*pkg.nodes, *placed]})


# --------------------------------------------------------------------- #
# Hard token ceiling
# --------------------------------------------------------------------- #

def enforce_token_ceiling(
    pkg: ContextPackage, ceiling: int, drop_first: set[str] | None = None,
) -> tuple[ContextPackage, list[str], int, int]:
    """`(pkg, dropped_ids, tokens_before, tokens_after)`. While the
    rendered context exceeds `ceiling`, removes one node: anything in
    `drop_first` (injected distractors) before real context, then the
    non-seed node farthest from the seed (ties: last by id). The seed is
    never removed. Every removal is returned so the cell can log it."""
    drop_first = drop_first or set()
    tokens_before = count_tokens(render_context(pkg))
    tokens = tokens_before
    dropped: list[str] = []
    while tokens > ceiling:
        removable = [n for n in pkg.nodes if n.role != "seed"]
        if not removable:
            break
        victim = max(removable, key=lambda n: (n.id in drop_first, n.distance, n.id))
        pkg = pkg.model_copy(update={
            "nodes": [n for n in pkg.nodes if n.id != victim.id],
            "edges": [e for e in pkg.edges if victim.id not in (e.from_node, e.to_node)],
        })
        dropped.append(victim.id)
        tokens = count_tokens(render_context(pkg))
    return pkg, dropped, tokens_before, tokens
