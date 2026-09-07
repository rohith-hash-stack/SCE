"""Stage 4: the Constrained Context Knapsack (HLD section 2.3 / 4.3).

Greedily packs the seed symbol (pinned at L0) plus its nearest neighbors -
ordered by `D_hybrid` - into a token budget, downgrading resolution (L1 ->
L2 -> L3) for any candidate that doesn't fit until the budget is exhausted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sce.graph.concrete_builder import ConcreteGraphBuilder
from sce.slicer.compressor import ASTCompressor, CompressionContext
from sce.slicer.distance import DistanceEngine, architectural_path

# Illustrative resolution weights used only to report a deterministic
# "preserved semantics" figure: how much of the theoretically-available
# detail (every reachable candidate rendered at full L0) survived
# compression and the token budget. Not part of the HLD's formal knapsack
# objective - purely a reporting metric for the serialized output.
RESOLUTION_WEIGHT = {0: 1.0, 1: 0.7, 2: 0.4, 3: 0.15}

# Whitespace-word count is the only offline, dependency-free token proxy
# available to the core engine (a real BPE tokenizer needs a model-specific
# vocabulary; see benchmarks/tokenizer.py, which uses tiktoken - with a
# fallback of its own - purely for *reporting*, not for gating this budget).
# Source code tokenizes far denser than prose (BPE splits most punctuation,
# brackets, and operators into their own tokens), so a prose-appropriate
# ~1.3 systematically undercounts it. Measured against a punctuation-aware
# tokenizer across four different packed contexts - two hand-built
# fixtures, one synthetic stress repo, and a real clone of encode/starlette
# - the true-to-word-estimate ratio was consistently ~2.0-2.9x, never
# close to 1.3x. 2.6 is that sample's mean, biased slightly conservative
# (better to underpack against a hard budget than overshoot it).
TOKENS_PER_WORD = 2.6


def estimate_tokens(text: str) -> float:
    return len(text.split()) * TOKENS_PER_WORD


def _wrapping_overhead_tokens(symbol: str) -> float:
    """Estimated cost of the Markdown a serializer wraps around one packed
    item's bare `content`: a `### <symbol> (<label>)` heading plus a fenced
    code block. Without this, the packer's running total only ever counts
    the code itself and silently diverges from the size of the document it
    is actually producing - a gap that is tiny for one item but compounds
    badly once dozens or hundreds of small (L2/L3) items are packed, which
    is exactly what happens against a real, densely-connected repository.

    This lives in the slicer layer and stays deliberately approximate
    rather than byte-exact, since `sce.serializers.markdown` imports
    `PackResult` from this module - importing it back here to render the
    real wrapping would be circular. "Full Implementation - L0" is used as
    a stand-in label because it's the longest of the four, which biases
    the estimate slightly conservative (better to underpack than to blow
    the budget).
    """
    heading = f"### {symbol} (Full Implementation - L0)"
    return estimate_tokens(f"{heading}\n```python\n```\n")


@dataclass
class PackedItem:
    symbol: str
    resolution: int
    content: str
    language_id: str = "python"


@dataclass
class PackResult:
    seed: str
    budget: int
    allocated_tokens: float
    items: list[PackedItem] = field(default_factory=list)
    architectural_path: list[tuple[int, str | None, str]] = field(default_factory=list)
    preserved_semantics: float = 0.0


class ContextKnapsackPacker:
    # Word-count-based estimation still carries real error even after
    # calibrating TOKENS_PER_WORD against measured samples (see its
    # comment) - packing candidates only up to this fraction of the
    # nominal budget leaves headroom to absorb that error, so the actual
    # rendered document (measured by a real tokenizer) usually still lands
    # at or under what the caller asked for. `PackResult.budget` still
    # reports the true nominal budget; this only tightens the internal
    # admission threshold.
    SAFETY_MARGIN = 0.92

    def __init__(self, token_budget: int, compressor: ASTCompressor | None = None, distance_engine: DistanceEngine | None = None) -> None:
        self.budget = token_budget
        self._admission_budget = token_budget * self.SAFETY_MARGIN
        self.compressor = compressor or ASTCompressor()

    def pack(self, seed: str, builder: ConcreteGraphBuilder, tag_matrix: dict[str, set[str]], distance_engine: DistanceEngine) -> PackResult:
        g_c = builder.graph
        distances = distance_engine.compute_all(seed, g_c)

        seed_content = self._render(builder, tag_matrix, seed, 0)
        if seed_content is None:
            raise ValueError(f"Seed symbol '{seed}' was not found in the concrete graph (unknown or external symbol)")

        seed_lang = builder.symbol_table.get(seed).language_id
        items = [PackedItem(seed, 0, seed_content, seed_lang)]
        total_tokens = estimate_tokens(seed_content) + _wrapping_overhead_tokens(seed)
        packed: set[str] = {seed}

        # A "requires" hop in the architectural path names a confirmed
        # metamodel obligation (e.g. "this #db_write requires an
        # #auth_guard") that the seed's own call graph does *not* reach -
        # that's what makes it worth flagging in the first place. Pack a
        # real L2 contract for it before ranking anything else: without
        # this, it would surface only as a bare qualified name in the
        # Architectural Path section with no signature or import path to
        # act on, which - confirmed against a live model - gets treated as
        # if the dotted path itself were valid, callable Python
        # (`app.auth.verify_session(...)` instead of `verify_session(...)`)
        # rather than a hint to import and call the real function.
        path = architectural_path(seed, g_c, tag_matrix, distance_engine.metamodel)
        for _depth, relation, node in path:
            if relation != "requires" or node in packed:
                continue
            symbol = builder.symbol_table.get(node)
            if symbol is None or symbol.kind not in ("function", "method"):
                continue
            content = self._render(builder, tag_matrix, node, 2)
            if content is None:
                continue
            cost = estimate_tokens(content) + _wrapping_overhead_tokens(node)
            if total_tokens + cost > self._admission_budget:
                continue
            items.append(PackedItem(node, 2, content, symbol.language_id))
            total_tokens += cost
            packed.add(node)

        # Exception/data classes are surfaced inline via a function's own
        # "Raises:"/signature contract; packing them as separate context
        # blocks would just repeat that information, so only functions and
        # methods compete for knapsack slots.
        candidates = sorted(
            (
                node
                for node in distances
                if node not in packed
                and (symbol := builder.symbol_table.get(node)) is not None
                and symbol.kind in ("function", "method")
            ),
            key=lambda n: distances[n],
        )

        for node in candidates:
            target_res = distance_engine.resolution_for_distance(distances[node])
            content = self._render(builder, tag_matrix, node, target_res)
            if content is None:
                continue
            overhead = _wrapping_overhead_tokens(node)
            cost = estimate_tokens(content) + overhead
            while total_tokens + cost > self._admission_budget and target_res < 3:
                target_res += 1
                content = self._render(builder, tag_matrix, node, target_res)
                cost = estimate_tokens(content) + overhead
            if total_tokens + cost <= self._admission_budget:
                node_lang = builder.symbol_table.get(node).language_id
                items.append(PackedItem(node, target_res, content, node_lang))
                total_tokens += cost
            else:
                break

        preserved = self._preserved_semantics(items, candidates)
        return PackResult(
            seed=seed,
            budget=self.budget,
            allocated_tokens=total_tokens,
            items=items,
            architectural_path=path,
            preserved_semantics=preserved,
        )

    def _render(self, builder: ConcreteGraphBuilder, tag_matrix: dict[str, set[str]], qname: str, resolution: int) -> str | None:
        symbol = builder.symbol_table.get(qname)
        if symbol is None:
            return None
        parsed = builder.parsed_file(symbol.file)
        if parsed is None:
            return None
        source = parsed.source.decode("utf-8", errors="replace")
        name = qname.rsplit(".", 1)[-1]
        context = CompressionContext(tags=tag_matrix.get(qname, set()), callees=self._callee_labels(builder, qname))
        return self.compressor.compress(symbol.language_id, source, name, symbol.line_range, resolution, context)

    def _callee_labels(self, builder: ConcreteGraphBuilder, qname: str) -> list[str]:
        if qname not in builder.graph:
            return []
        labels = []
        for successor in sorted(builder.graph.successors(qname)):
            symbol = builder.symbol_table.get(successor)
            if symbol is not None and symbol.kind == "class":
                # Constructor calls are already visible in the caller's own
                # code; omit them from the interface-contract "Calls" line
                # so it highlights behavioral delegation, not allocation.
                continue
            labels.append(successor)
        return labels

    @staticmethod
    def _preserved_semantics(items: list[PackedItem], candidates: list[str]) -> float:
        packed_weight = sum(RESOLUTION_WEIGHT[item.resolution] for item in items)
        # +1 accounts for the pinned seed, always rendered at full L0.
        max_weight = 1.0 + len(candidates) * RESOLUTION_WEIGHT[0]
        if max_weight <= 0:
            return 100.0
        return round(min(packed_weight / max_weight, 1.0) * 100.0, 1)
