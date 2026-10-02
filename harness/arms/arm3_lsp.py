"""Copilot-style Pyright LSP (arm3). STUB: the implementation arrives in M2. The arm and
its fidelity grade are declared now so the dispatch surface is complete;
every entry point raises NotImplementedError until M2."""
from __future__ import annotations

from harness.arms.base import RetrievalArm

_MSG = "arm3 (Copilot-style Pyright LSP) is implemented in M2"


class Arm3PyrightLSP(RetrievalArm):
    arm_id = "arm3"

    def index(self, repo_path: str, config: dict) -> None:
        raise NotImplementedError(_MSG)

    def retrieve(self, query: str, seed: dict):
        raise NotImplementedError(_MSG)

    def build_prompt(self, ctx, tokenizer) -> str:
        raise NotImplementedError(_MSG)
