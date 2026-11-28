"""The canonical shapes every arm's output is converted into.

Retrieval -> `DeliveredContext` (a ranked list of `DeliveredItem`s).
Generation -> `NormalizedAnswer`.

Rules:
1. Only items that actually reached the model's prompt are delivered.
   Anything cut by a budget or cutoff is left out (and may be recorded in
   `build_meta`). A digest the model saw counts as delivered; the original
   it summarised lives in `provenance`.
2. Every item carries `symbols`, the fully-qualified names it references.
   Cross-engine metrics read only these.
3. Arm-specific diagnostics go in `provenance` / `build_meta`, never in new
   top-level fields.
4. Every item carries `symbol_provenance`: how the symbols it names reached
   the model (set by each arm's adapter). Accounting only: no primary
   metric reads it.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Literal, get_args

ItemKind = Literal[
    "code_chunk", "signature_stub",
    "lsp_hover", "lsp_symbol",
    "tool_result_grep", "tool_result_read", "tool_result_digest",
    "oracle_truth",
]
ExtractionMethod = Literal["answer_tool", "code_block", "plain_text", "prism_final"]
ITEM_KINDS = frozenset(get_args(ItemKind))
#: How an item's symbols reached the model: with their code or a resolved
#: description ("body", "signature_stub", "hover", "oracle"), by name and
#: location only ("documentSymbol", "definition"), or as an agent's tool
#: output ("tool_result"). "" = not yet set (the adapter sets it).
SymbolProvenance = Literal["hover", "definition", "documentSymbol", "signature_stub", "body", "tool_result", "oracle"]
SYMBOL_PROVENANCES = frozenset(get_args(SymbolProvenance))
#: delivered_symbols_resolved counts symbols with one of these ...
RESOLVED_PROVENANCES = frozenset({"hover", "body", "signature_stub", "oracle"})
#: ... delivered_symbols_named_only those delivered only with one of these.
NAMED_ONLY_PROVENANCES = frozenset({"documentSymbol", "definition"})
EXTRACTION_METHODS = frozenset(get_args(ExtractionMethod))


@dataclass
class DeliveredItem:
    source_id: str              # "path:start-end" | "FQN" | "LSP:uri#sym"
    content: str                # exact text delivered to the model
    token_count: int            # via the harness tokenizer
    rank: int                   # 1-indexed, consecutive, no gaps
    kind: ItemKind
    symbols: list[str]          # FQNs referenced (used for CPI)
    provenance: dict = field(default_factory=dict)
    symbol_provenance: str = ""  # a SymbolProvenance; "" until the adapter sets it

    def __post_init__(self) -> None:
        if self.kind not in ITEM_KINDS:
            raise ValueError(f"unknown item kind {self.kind!r}")
        if self.symbol_provenance and self.symbol_provenance not in SYMBOL_PROVENANCES:
            raise ValueError(f"unknown symbol_provenance {self.symbol_provenance!r}")
        if self.token_count < 0:
            raise ValueError("token_count must be >= 0")


@dataclass
class DeliveredContext:
    arm: str
    task_id: str
    items: list[DeliveredItem]
    total_tokens: int
    budget_tokens: int
    build_meta: dict = field(default_factory=dict)

    @property
    def delivered_symbols(self) -> set[str]:
        out: set[str] = set()
        for item in self.items:
            out.update(item.symbols)
        return out

    def symbols_by_provenance(self, provenances: frozenset[str]) -> set[str]:
        out: set[str] = set()
        for item in self.items:
            if item.symbol_provenance in provenances:
                out.update(item.symbols)
        return out

    @property
    def delivered_symbols_resolved(self) -> set[str]:
        """Symbols delivered with their code or a resolved description."""
        return self.symbols_by_provenance(RESOLVED_PROVENANCES)

    @property
    def delivered_symbols_named_only(self) -> set[str]:
        """Symbols delivered only by name and location (an outline entry, a
        definition location): never also resolved by another item."""
        return self.symbols_by_provenance(NAMED_ONLY_PROVENANCES) - self.delivered_symbols_resolved

    def ranked_items(self) -> list[DeliveredItem]:
        return sorted(self.items, key=lambda it: it.rank)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DeliveredContext":
        return cls(
            arm=d["arm"], task_id=d["task_id"],
            items=[DeliveredItem(**it) for it in d["items"]],
            total_tokens=d["total_tokens"], budget_tokens=d["budget_tokens"],
            build_meta=d.get("build_meta", {}),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=1)

    @classmethod
    def from_json(cls, text: str) -> "DeliveredContext":
        return cls.from_dict(json.loads(text))


@dataclass
class NormalizedAnswer:
    arm: str
    task_id: str
    raw_text: str               # exact model output, preserved for audit
    answer_text: str            # extracted answer payload
    answer_symbols: list[str]   # ordered, de-duplicated identifier list
    extraction_method: ExtractionMethod
    extraction_success: bool
    generation_tokens: int
    latency_seconds: float
    #: the server's finish reason ("stop", or "length" when the generation
    #: cap was hit); "" when unknown
    finish_reason: str = ""
    #: symbol mentions in the answer minus distinct symbols: how many times
    #: the model repeated a symbol it had already named (repetition loops)
    repetition_count: int = 0

    def __post_init__(self) -> None:
        if self.extraction_method not in EXTRACTION_METHODS:
            raise ValueError(f"unknown extraction method {self.extraction_method!r}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "NormalizedAnswer":
        return cls(**d)
