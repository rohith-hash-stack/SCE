"""The interface every arm implements, and the fidelity disclosure every arm
carries into its `build_meta`."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from harness import config as C
from harness.config import FidelityGrade

if TYPE_CHECKING:
    from harness.scoring.canonical import DeliveredContext

__all__ = ["FidelityGrade", "RetrievalArm", "SYSTEM_PROMPT", "compose_user_prompt", "item_header", "render_items"]

#: The one system prompt every arm's answer call uses.
SYSTEM_PROMPT = (
    "You are a senior software engineer answering questions about a code repository. "
    "Use the provided context when it is relevant. Name every code symbol you refer to by its "
    "exact fully-qualified name (for example `package.module.Class.method`). "
    "If the context does not contain what you need, say so rather than inventing names."
)


def item_header(source_id: str, label: str = "") -> str:
    """The header line an arm puts at the top of an item's content. It is
    part of `content`, so it is counted against the arm's budget."""
    return f"# {source_id}" + (f"  ({label})" if label else "")


def render_items(ctx: "DeliveredContext") -> str:
    """Delivered items in rank order, contents only (each item's content
    already carries its own header). Shared by every arm, so prompt framing
    is not a difference between arms."""
    return "\n\n".join(item.content.rstrip() for item in sorted(ctx.items, key=lambda it: it.rank))


RESPONSE_CONTRACTS = {
    "T1_conceptual": "Answer in a few sentences of prose.",
    "T2_localization": (
        "Respond with a single fenced JSON object and nothing else:\n"
        '```json\n{"reasoning": "<short explanation>", "symbols": ["<fully.qualified.name>", "..."]}\n```\n'
        "`symbols` lists every code symbol that is part of your answer, in order, by exact fully-qualified name."
    ),
    "T5_blast_radius": (
        "Respond with a single fenced JSON object and nothing else:\n"
        '```json\n{"reasoning": "<short explanation>", "symbols": ["<fully.qualified.name>", "..."]}\n```\n'
        "`symbols` lists every affected symbol by exact fully-qualified name."
    ),
    "T3_codegen": "Respond with the complete code in a single fenced ```python block.",
    "T4_edit": "Respond with the complete modified code in a single fenced ```python block.",
}


def compose_user_prompt(context_text: str, query: str, task_type: str) -> str:
    """The user message every arm sends: optional context, the task, the
    response contract for its type. Identical wording for every arm."""
    contract = RESPONSE_CONTRACTS[task_type]
    if context_text:
        return f"## Repository context\n\n{context_text}\n\n## Task\n\n{query}\n\n## Response format\n\n{contract}"
    return f"## Task\n\n{query}\n\n## Response format\n\n{contract}"


class RetrievalArm(ABC):
    #: Short id used in file names and result rows ("arm1", "oracle", ...).
    arm_id: str = ""
    fidelity: FidelityGrade

    def __init__(self) -> None:
        if not self.arm_id:
            raise TypeError(f"{type(self).__name__} must set arm_id")
        self.fidelity = C.FIDELITY[self.arm_id]
        #: Every simplification relative to the system this arm replicates.
        #: Declared up front and copied into every build_meta.
        self.simplifications: list[str] = []

    @abstractmethod
    def index(self, repo_path: str, config: dict) -> None:
        """One-time, per-repository preparation (Phase 1)."""

    @abstractmethod
    def retrieve(self, query: str, seed: dict) -> "DeliveredContext":
        """Context for one task. `seed` carries the task's metadata
        (task_id, seed_symbol, file hints, ...)."""

    @abstractmethod
    def build_prompt(self, ctx: "DeliveredContext", tokenizer: Any) -> str:
        """The user message sent to the model for this context."""

    def build_prompt_default(self, ctx: "DeliveredContext") -> str:
        meta = ctx.build_meta
        return compose_user_prompt(render_items(ctx), meta["query"], meta["task_type"])

    def fidelity_meta(self) -> dict:
        return {
            "arm_name": C.ARM_NAMES[self.arm_id],
            "fidelity": self.fidelity.value,
            "simplifications": list(self.simplifications),
        }
