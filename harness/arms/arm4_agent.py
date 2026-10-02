"""Claude-Code-style agent loop (arm4). STUB: the implementation arrives in M3. The arm and
its fidelity grade are declared now so the dispatch surface is complete;
every entry point raises NotImplementedError until M3."""
from __future__ import annotations

from harness.arms.base import RetrievalArm

_MSG = "arm4 (Claude-Code-style agent loop) is implemented in M3"


class Arm4AgentLoop(RetrievalArm):
    arm_id = "arm4"

    def index(self, repo_path: str, config: dict) -> None:
        raise NotImplementedError(_MSG)

    def retrieve(self, query: str, seed: dict):
        raise NotImplementedError(_MSG)

    def build_prompt(self, ctx, tokenizer) -> str:
        raise NotImplementedError(_MSG)
