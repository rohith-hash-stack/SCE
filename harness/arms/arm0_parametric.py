"""Arm 0 — Parametric baseline. No retrieval: the model answers from its own
weights. The floor for Retrieval Lift = (TSR_arm - TSR_0) / TSR_0.

The prompt is the task query plus the same response contract every arm
gets (so answers are extracted identically); there is no context section.
"""
from __future__ import annotations

from harness import config as C
from harness.arms.base import RetrievalArm, compose_user_prompt
from harness.scoring.canonical import DeliveredContext


class Arm0Parametric(RetrievalArm):
    arm_id = "arm0"

    def __init__(self) -> None:
        super().__init__()
        self.simplifications = []

    def index(self, repo_path: str, config: dict | None = None) -> None:
        return None

    def retrieve(self, query: str, seed: dict) -> DeliveredContext:
        meta = {**self.fidelity_meta(), "query": query, "task_type": seed.get("task_type"),
                "turn_count": 1, "over_budget": False, "ranking_method": "none", "latency_ms": {"L_retrieve": [0.0]}}
        return DeliveredContext("arm0", seed["task_id"], [], 0, C.ARM0_BUDGET, meta)

    def build_prompt(self, ctx: DeliveredContext, tokenizer=None) -> str:
        return compose_user_prompt("", ctx.build_meta["query"], ctx.build_meta["task_type"])
