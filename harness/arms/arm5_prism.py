"""Arm 5 — PRISM (fidelity HIGH). A wrapper: the graph, the two-pass
linker, the candidate manifest, Turn-1 parsing, Turn-2 hydration and the
Turn-2b external-dependency step all come from the existing code
(`src/prism` and `benchmarks/run_two_pass_benchmark.py`), called exactly as
the final sweep's `prism_full` arm calls them. Nothing is reimplemented.

Per task:
1. Turn 1 manifest: `PrismEngine.build_candidate_manifest(seed)` (zero
   bodies), anchored on the task's seed symbol.
2. Turn 1 call: the model selects `requested_symbols` from the manifest
   (PRISM's own system/user prompts and parser, including its degeneration
   guard).
3. Turn 2: `PrismEngine.retrieve_requested` hydrates that selection.
4. Turn 2b (only when the task declares root imports): external candidate
   manifest, a second model call, external hydration. The internal/external
   budget split is PRISM's own (`split_budget_for_external`).
5. Each hydrated node becomes one DeliveredItem (L0 bodies `code_chunk`,
   compressed nodes `signature_stub`).

At the harness boundary every item is re-counted with the harness tokenizer
and trimmed from the lowest rank to 13,000 tokens (`finalize_context`);
PRISM's internal cl100k counts are ignored there (kept in provenance).

A task without a seed symbol cannot be anchored: PRISM delivers nothing
and says so in build_meta (`no_seed`).
"""
from __future__ import annotations

import os

from harness import config as C
from harness.arms.base import RetrievalArm, item_header
from harness.scoring.canonical import DeliveredContext, DeliveredItem, ItemKind
from harness.scoring.latency import timed

TURN1_MAX_TOKENS = 1024


def strict_manifest(manifest: str, universe: set[str], seed: str, distances: dict[str, float],
                    max_hops: float) -> tuple[str, set[str]]:
    """PRISM's Turn-1 manifest without the downstream symbols farther than
    `max_hops` from `seed` (`distances`: PRISM's downstream distance map).
    The seed is always kept; a symbol absent from `distances` is one of
    PRISM's upstream callers (already bounded by PRISM) and is kept too.
    Manifest lines are `qualified_name|role|...`; the wrapper lines stay."""
    keep = {s for s in universe if s == seed or s not in distances or distances[s] <= max_hops}
    lines = manifest.split("\n")
    body = [ln for ln in lines[1:-1] if ln.split("|", 1)[0] in keep]
    return "\n".join([lines[0], *body, lines[-1]]), keep


def _downstream_distances(engine, seed: str) -> dict[str, float]:
    """PRISM's own weighted downstream distances, to the manifest's own horizon."""
    from prism.packer.candidate_index import CANDIDATE_INDEX_MAX_HOPS
    from prism.traversal.continuous_dijkstra import compute_topological_distances
    return compute_topological_distances(engine.builder, seed, d_max=CANDIDATE_INDEX_MAX_HOPS)


class Arm5Prism(RetrievalArm):
    arm_id = "arm5"

    def __init__(self, llm=None, tokenizer=None, budget: int = C.RETRIEVAL_BUDGET, engine=None) -> None:
        super().__init__()
        from harness.tokenizer import get_tokenizer

        self.llm = llm
        self.tok = tokenizer or get_tokenizer()
        self.budget = budget
        self.engine = engine
        self.simplifications = [
            "hydrated nodes are rendered in the harness's uniform item format, not PRISM's <prism_context> XML envelope",
            "token budget re-counted with the harness tokenizer and trimmed from the lowest rank (PRISM packs in cl100k)",
        ]
        self.index_latency: dict[str, list[float]] = {}

    def index(self, repo_path: str, config: dict | None = None) -> None:
        from prism.engine import PrismEngine

        self.repo_root = repo_path
        with timed(self.index_latency, "L_index"):
            if self.engine is None:
                self.engine = PrismEngine.from_repo(repo_path)

    def symbol_names(self) -> list[str]:
        return list(self.engine.builder.symbol_table._symbols)

    # ------------------------------------------------------------------
    def _items(self, pkg) -> list[DeliveredItem]:
        items: list[DeliveredItem] = []
        for node in pkg.nodes:
            rel = os.path.relpath(node.file, self.repo_root) if os.path.isabs(node.file) else node.file
            sid = f"{rel}:{node.line}-{node.end_line}"
            kind: ItemKind = "code_chunk" if node.compression == "L0_full" else "signature_stub"
            content = f"{item_header(sid, node.id)}\n{node.body}"
            items.append(DeliveredItem(
                source_id=sid, content=content, token_count=self.tok.count(content), rank=len(items) + 1,
                kind=kind, symbols=[node.id],
                provenance={"role": node.role, "compression": node.compression, "engine_token_count": node.cost,
                            "distance": node.distance},
            ))
        return items

    def retrieve(self, query: str, seed: dict) -> DeliveredContext:
        from benchmarks.run_two_pass_benchmark import (TURN1_SYSTEM_PROMPT, TURN2B_SYSTEM_PROMPT,
                                                       _check_turn1_degeneration, _hydrate_external,
                                                       _parse_requested_symbols, _turn1_user_prompt,
                                                       _turn2b_user_prompt)
        from prism.packer.submodular_knapsack import split_budget_for_external

        lat: dict[str, list[float]] = {}
        anchor = seed.get("seed_symbol")
        root_imports = list(seed.get("root_imports") or [])
        meta = {**self.fidelity_meta(), "query": query, "task_type": seed.get("task_type"), "anchor": anchor,
                "turn2b_triggered": False, "turn_count": 1, "over_budget": False, "ranking_method": "prism_order",
                "prism_manifest_strict": C.PRISM_MANIFEST_STRICT}
        if not anchor:
            meta.update({"no_seed": True, "latency_ms": lat})
            return DeliveredContext("arm5", seed["task_id"], [], 0, self.budget, meta)
        if self.llm is None:
            raise RuntimeError("arm5 needs an llm for Turn 1")
        llm_seed = seed.get("llm_seed")

        with timed(lat, "L_retrieve"):
            with timed(lat, "L_turn1_manifest"):
                manifest, universe = self.engine.build_candidate_manifest(anchor)
                unfiltered = len(universe)
                if C.PRISM_MANIFEST_STRICT:     # M3 ablation scaffold, off by default
                    manifest, universe = strict_manifest(manifest, universe, anchor,
                                                         _downstream_distances(self.engine, anchor),
                                                         C.PRISM_MANIFEST_STRICT_MAX_HOPS)
            t1 = self.llm(TURN1_SYSTEM_PROMPT, _turn1_user_prompt(manifest, query), max_tokens=TURN1_MAX_TOKENS,
                          seed=llm_seed, purpose="turn1")
            requested, parsed_ok = _parse_requested_symbols(t1.text, universe)
            requested, degenerate = _check_turn1_degeneration(t1.text, t1.completion_tokens, TURN1_MAX_TOKENS,
                                                              requested, universe)
            internal_budget, external_budget = (split_budget_for_external(self.budget) if root_imports
                                                else (self.budget, 0))
            with timed(lat, "L_turn2_hydrate"):
                pkg, skipped = self.engine.retrieve_requested(anchor, internal_budget, requested, universe,
                                                              task_type="debug")
            turns = [t1.to_dict()]
            ext_requested: list[str] = []
            if root_imports:
                with timed(lat, "L_turn2b_external"):
                    ext_manifest, ext_candidates = self.engine.build_external_candidate_manifest(
                        sorted({anchor, *requested}), root_imports=root_imports)
                    if ext_candidates:
                        t2b = self.llm(TURN2B_SYSTEM_PROMPT, _turn2b_user_prompt(ext_manifest, query),
                                       max_tokens=TURN1_MAX_TOKENS, seed=llm_seed, purpose="turn2b")
                        turns.append(t2b.to_dict())
                        ext_requested, _ok = _parse_requested_symbols(t2b.text, ext_candidates)
                        pkg, ext_skipped = _hydrate_external(self.engine, pkg, ext_requested, external_budget)
                        skipped = list(skipped) + list(ext_skipped)
                        meta["turn2b_triggered"] = True
            items = self._items(pkg)

        meta.update({
            "turn_count": len(turns) + 1,   # retrieval turns + the answer turn
            "manifest_candidates": len(universe), "manifest_candidates_unfiltered": unfiltered,
            "prism_manifest_strict": C.PRISM_MANIFEST_STRICT, "requested_symbols": list(requested),
            "turn1_parsed_ok": parsed_ok, "turn1_degenerate": degenerate,
            "external_requested": ext_requested, "skipped_hallucinated": list(skipped),
            "retrieval_turns": turns, "latency_ms": lat,
        })
        return DeliveredContext("arm5", seed["task_id"], items, sum(i.token_count for i in items), self.budget, meta)

    def build_prompt(self, ctx: DeliveredContext, tokenizer=None) -> str:
        return self.build_prompt_default(ctx)
