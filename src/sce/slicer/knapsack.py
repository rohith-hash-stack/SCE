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

TOKENS_PER_WORD = 1.3


def estimate_tokens(text: str) -> float:
    return len(text.split()) * TOKENS_PER_WORD


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
    def __init__(self, token_budget: int, compressor: ASTCompressor | None = None, distance_engine: DistanceEngine | None = None) -> None:
        self.budget = token_budget
        self.compressor = compressor or ASTCompressor()

    def pack(self, seed: str, builder: ConcreteGraphBuilder, tag_matrix: dict[str, set[str]], distance_engine: DistanceEngine) -> PackResult:
        g_c = builder.graph
        distances = distance_engine.compute_all(seed, g_c)

        seed_content = self._render(builder, tag_matrix, seed, 0)
        if seed_content is None:
            raise ValueError(f"Seed symbol '{seed}' was not found in the concrete graph (unknown or external symbol)")

        seed_lang = builder.symbol_table.get(seed).language_id
        items = [PackedItem(seed, 0, seed_content, seed_lang)]
        total_tokens = estimate_tokens(seed_content)

        # Exception/data classes are surfaced inline via a function's own
        # "Raises:"/signature contract; packing them as separate context
        # blocks would just repeat that information, so only functions and
        # methods compete for knapsack slots.
        candidates = sorted(
            (
                node
                for node in distances
                if (symbol := builder.symbol_table.get(node)) is not None and symbol.kind in ("function", "method")
            ),
            key=lambda n: distances[n],
        )

        for node in candidates:
            target_res = distance_engine.resolution_for_distance(distances[node])
            content = self._render(builder, tag_matrix, node, target_res)
            if content is None:
                continue
            cost = estimate_tokens(content)
            while total_tokens + cost > self.budget and target_res < 3:
                target_res += 1
                content = self._render(builder, tag_matrix, node, target_res)
                cost = estimate_tokens(content)
            if total_tokens + cost <= self.budget:
                node_lang = builder.symbol_table.get(node).language_id
                items.append(PackedItem(node, target_res, content, node_lang))
                total_tokens += cost
            else:
                break

        preserved = self._preserved_semantics(items, candidates)
        path = architectural_path(seed, g_c, tag_matrix, distance_engine.metamodel)
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
