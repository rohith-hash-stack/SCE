"""Oracle — the ceiling. Delivers G*_universe (the task's ground-truth
universe) directly, one `oracle_truth` item per symbol: its full source
from the repository's symbol table. Pipeline (gold) symbols come first in
their annotated order, then the rest of the universe alphabetically.

Single variant. There is no "repaired" variant: the original
construction gap was on the scoring side (candidates omitted external
dependencies), and uniform candidates (G*_universe for every arm) fix it.

A universe symbol the symbol table cannot locate (e.g. an external
dependency) is listed in `build_meta["unresolved_universe"]`, not invented.

T5 (M4): the item set is the gold affected set, exactly: never filtered by
what PRISM indexes, and without the rest of the universe. A gold name's body
comes from PRISM's symbol table when it has the name, otherwise from the
gold's recorded definition location (`seed["oracle_locations"]`, from
benchmarks/ground_truth/tasks/<corpus>/gold_locations.json: tsserver's
outline for TypeScript). `adapt_oracle` asserts that the delivered symbols
equal the gold set, so a gold name the Oracle cannot deliver fails the cell
loudly. T2 is unchanged.
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

    def _located(self, symbol: str, loc: dict | None) -> tuple[str, str] | None:
        """The body at a recorded definition location (repository-relative
        file, 1-based inclusive lines)."""
        if not loc:
            return None
        path = os.path.join(self.repo_root, loc["file"])
        if not os.path.isfile(path):
            return None
        if path not in self._lines:
            with open(path, encoding="utf-8", errors="replace") as fh:
                self._lines[path] = fh.read().split("\n")
        start, end = int(loc["start"]), int(loc["end"])
        return f"{loc['file']}:{start}-{end}", "\n".join(self._lines[path][start - 1:end])

    def retrieve(self, query: str, seed: dict) -> DeliveredContext:
        pipeline = list(seed.get("oracle_pipeline") or [])
        t5 = seed.get("task_type") == "T5_blast_radius"
        # T5: exactly the gold affected set; T2: the whole universe, gold first
        universe = set(pipeline) if t5 else set(seed.get("oracle_universe") or []) | set(pipeline)
        locations = seed.get("oracle_locations") or {}
        order = pipeline + sorted(universe - set(pipeline))
        items: list[DeliveredItem] = []
        unresolved: list[str] = []
        from_location: list[str] = []
        for sym in order:
            found = self._source(sym)
            if found is None and t5:
                found = self._located(sym, locations.get(sym))
                if found is not None:
                    from_location.append(sym)
            if found is None:
                unresolved.append(sym)
                continue
            sid, body = found
            content = f"{item_header(sid, sym)}\n{body}"
            items.append(DeliveredItem(sid, content, self.tok.count(content), len(items) + 1, "oracle_truth", [sym],
                                       {"in_pipeline": sym in pipeline}))
        meta = {**self.fidelity_meta(), "query": query, "task_type": seed.get("task_type"), "turn_count": 1,
                "over_budget": False, "ranking_method": "pipeline_then_alphabetical",
                "universe_size": len(universe), "unresolved_universe": unresolved,
                "delivered_from_gold_location": from_location}
        return DeliveredContext("oracle", seed["task_id"], items, sum(i.token_count for i in items), self.budget, meta)

    def build_prompt(self, ctx: DeliveredContext, tokenizer=None) -> str:
        return self.build_prompt_default(ctx)
