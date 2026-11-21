"""Task specifications: one Pydantic model per task type, combined into a
discriminated union on `task_type`.

Every task carries a `GroundTruth` with the same two parts, so universal
metrics read every type the same way:
- `pipeline_symbols`: the gold set the type is scored against. For T2 this
  is the adjudicated pipeline; for T5 it is the gold affected set.
- `context_symbols`: other symbols that legitimately belong in context
  (required context, boundary symbols, ...).
`universe_symbols()` (G*_universe) = pipeline ∪ context ∪ {seed}. It is the
single candidate set for cleanliness, precision/recall and hallucination,
for every arm.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

TaskType = Literal["T1_conceptual", "T2_localization", "T3_codegen", "T4_edit", "T5_blast_radius"]


class GoldSpan(BaseModel):
    model_config = ConfigDict(frozen=True)
    path: str
    start: int
    end: int


class GroundTruth(BaseModel):
    pipeline_symbols: list[str] = Field(default_factory=list)
    context_symbols: list[str] = Field(default_factory=list)
    seed_symbol: str | None = None

    def universe_symbols(self) -> set[str]:
        out = set(self.pipeline_symbols) | set(self.context_symbols)
        if self.seed_symbol:
            out.add(self.seed_symbol)
        return out


class _TaskBase(BaseModel):
    task_id: str
    repo_id: str
    repo_root: str
    query: str
    seed_symbol: str | None = None
    ground_truth: GroundTruth
    #: Where the task came from (legacy file, synthetic, ...), for audit.
    source: str = ""
    #: Extra hints an arm may use (root imports, seed file, ...).
    hints: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _seed_into_ground_truth(self):
        if self.seed_symbol and not self.ground_truth.seed_symbol:
            self.ground_truth.seed_symbol = self.seed_symbol
        return self

    def seed_dict(self) -> dict:
        """What an arm's `retrieve(query, seed)` receives."""
        return {"task_id": self.task_id, "task_type": self.task_type, "repo_id": self.repo_id,
                "repo_root": self.repo_root, "seed_symbol": self.seed_symbol, **self.hints}


class ConceptualTask(_TaskBase):
    task_type: Literal["T1_conceptual"] = "T1_conceptual"
    reference_answer: str


class LocalizationTask(_TaskBase):
    task_type: Literal["T2_localization"] = "T2_localization"
    gold_spans: list[GoldSpan] = Field(default_factory=list)
    expected_solution: str = ""


class CodeGenTask(_TaskBase):
    task_type: Literal["T3_codegen"] = "T3_codegen"
    gold_code: str
    tests: list[str] = Field(default_factory=list)


class EditTask(_TaskBase):
    task_type: Literal["T4_edit"] = "T4_edit"
    gold_patch: str
    fail_to_pass: list[str] = Field(default_factory=list)
    pass_to_pass: list[str] = Field(default_factory=list)


class BlastRadiusTask(_TaskBase):
    task_type: Literal["T5_blast_radius"] = "T5_blast_radius"

    @property
    def gold_affected(self) -> list[str]:
        return list(self.ground_truth.pipeline_symbols)


TaskSpec = Annotated[
    Union[ConceptualTask, LocalizationTask, CodeGenTask, EditTask, BlastRadiusTask],
    Field(discriminator="task_type"),
]
TASK_ADAPTER: TypeAdapter = TypeAdapter(TaskSpec)


def parse_task(data: dict) -> "ConceptualTask | LocalizationTask | CodeGenTask | EditTask | BlastRadiusTask":
    return TASK_ADAPTER.validate_python(data)
