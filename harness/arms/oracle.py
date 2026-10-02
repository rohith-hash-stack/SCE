"""Oracle — the ceiling. Delivers G*_universe (the task's ground-truth
universe) directly, one `oracle_truth` item per symbol: its full source
from the repository's symbol table. Pipeline (gold) symbols come first in
their annotated order, then the rest of the universe alphabetically.

Single variant. There is no "repaired" variant: the original
construction gap was on the scoring side (candidates omitted external
dependencies), and uniform candidates (G*_universe for every arm) fix it.

A universe symbol the symbol table cannot locate (e.g. an external
dependency) is listed in `build_meta["unresolved_universe"]`, not invented.
The ground truth reaches this arm through `seed["oracle_pipeline"]` and
`seed["oracle_universe"]`, which the pipeline supplies to the oracle only.
"""
from __future__ import annotations

import os

from harness import config as C
from harness.arms.base import RetrievalArm, item_header
from harness.scoring.canonical import DeliveredContext, DeliveredItem


class Oracle(RetrievalArm):
    arm_id = "oracle"

    def __init__(self, tokenizer=None, builder=None, budget: int = C.RETRIEVAL_BUDGET) -> None:
        super().__init__()
        from harness.tokenizer import get_tokenizer

        self.tok = tokenizer or get_tokenizer()
        self.builder = builder
        self.budget = budget
        self.simplifications = []
        self._lines: dict[str, list[str]] = {}

    def index(self, repo_path: str, config: dict | None = None) -> None:
        self.repo_root = repo_path
        if self.builder is None:
            from prism.cli import build_pipeline
            self.builder, _ = build_pipeline(repo_path)

    def _source(self, symbol: str) -> tuple[str, str] | None:
        info = self.builder.symbol_table.get(symbol)
        if info is None:
            return None
        path = info.file
        if path not in self._lines:
            with open(path, encoding="utf-8", errors="replace") as fh:
                self._lines[path] = fh.read().split("\n")
        start, end = info.line_range
        rel = os.path.relpath(path, self.repo_root)
        return f"{rel}:{start}-{end}", "\n".join(self._lines[path][start - 1:end])

    def retrieve(self, query: str, seed: dict) -> DeliveredContext:
        pipeline = list(seed.get("oracle_pipeline") or [])
        universe = set(seed.get("oracle_universe") or []) | set(pipeline)
        order = pipeline + sorted(universe - set(pipeline))
        items: list[DeliveredItem] = []
        unresolved: list[str] = []
        for sym in order:
            found = self._source(sym)
            if found is None:
                unresolved.append(sym)
                continue
            sid, body = found
            content = f"{item_header(sid, sym)}\n{body}"
            items.append(DeliveredItem(sid, content, self.tok.count(content), len(items) + 1, "oracle_truth", [sym],
                                       {"in_pipeline": sym in pipeline}))
        meta = {**self.fidelity_meta(), "query": query, "task_type": seed.get("task_type"), "turn_count": 1,
                "over_budget": False, "ranking_method": "pipeline_then_alphabetical",
                "universe_size": len(universe), "unresolved_universe": unresolved}
        return DeliveredContext("oracle", seed["task_id"], items, sum(i.token_count for i in items), self.budget, meta)

    def build_prompt(self, ctx: DeliveredContext, tokenizer=None) -> str:
        return self.build_prompt_default(ctx)
