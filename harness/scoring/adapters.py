"""Per-arm adapters: raw arm output -> (DeliveredContext, NormalizedAnswer).

A cell's raw output is the dict written by the pipeline:
    {"bundle": <DeliveredContext.to_dict()>,   # what the arm delivered
     "completion": {"text": str, "generation_tokens": int,
                    "latency_seconds": float}}
plus anything arm-specific (e.g. PRISM's turn log, in the bundle's
build_meta).

Budget enforcement happens BEFORE the prompt is built (`finalize_context`):
every item is re-counted with the harness tokenizer (an engine's own counts,
e.g. PRISM's cl100k, are ignored), and if the total exceeds the budget,
items are dropped from the lowest rank up and `over_budget=True` is logged.
Only what survives is delivered. Adapters re-check this at scoring time.

Answer extraction is identical for every arm (`extract_answer`); only the
`extraction_method` label differs (`prism_final` for PRISM's final turn,
`answer_tool` for Arm 4).
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Callable

from harness import config as C
from harness.scoring.canonical import DeliveredContext, DeliveredItem, ExtractionMethod, NormalizedAnswer
from harness.scoring.fairness import rerank_consecutive, verify_ranking
from harness.scoring.hallucination import extract_identifiers

_FENCE = re.compile(r"```([\w+-]*)\s*\n?([\s\S]*?)```")
#: Task types whose answer contract is the JSON symbols object.
JSON_TYPES = {"T2_localization", "T5_blast_radius"}
CODE_TYPES = {"T3_codegen", "T4_edit"}


# --------------------------------------------------------------------------
# budget enforcement (Phase 2, before prompting)
# --------------------------------------------------------------------------
def finalize_context(ctx: DeliveredContext, tokenizer, budget: int | None = None) -> DeliveredContext:
    """Re-count every item with `tokenizer`, drop from the lowest rank until
    the total fits `budget` (default: ctx.budget_tokens), renumber ranks.
    Engine-reported counts are kept in provenance for audit."""
    budget = ctx.budget_tokens if budget is None else budget
    recounted = []
    for it in ctx.items:
        prov = dict(it.provenance)
        prov.setdefault("engine_token_count", it.token_count)
        recounted.append(replace(it, token_count=tokenizer.count(it.content), provenance=prov))
    ordered = sorted(recounted, key=lambda it: it.rank)
    kept, dropped = list(ordered), []
    while kept and sum(it.token_count for it in kept) > budget:
        dropped.append(kept.pop())
    meta = dict(ctx.build_meta)
    meta["tokenizer"] = getattr(tokenizer, "name", type(tokenizer).__name__)
    meta["over_budget"] = bool(dropped) or bool(meta.get("over_budget", False))
    meta["budget_dropped"] = [it.source_id for it in reversed(dropped)] + list(meta.get("budget_dropped", []))
    meta["pre_trim_tokens"] = sum(it.token_count for it in ordered)
    kept = rerank_consecutive(kept)
    return DeliveredContext(ctx.arm, ctx.task_id, kept, sum(it.token_count for it in kept), budget, meta)


# --------------------------------------------------------------------------
# answer extraction (identical for every arm)
# --------------------------------------------------------------------------
def _json_symbols(text: str) -> tuple[list[str], str] | None:
    candidates = [body for _lang, body in _FENCE.findall(text)] + [text]
    for cand in candidates:
        cand = cand.strip()
        start, end = cand.find("{"), cand.rfind("}")
        if start < 0 or end <= start:
            continue
        try:
            obj = json.loads(cand[start:end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("symbols"), list) and all(isinstance(s, str) for s in obj["symbols"]):
            return [s.strip().strip("`").removesuffix("()") for s in obj["symbols"] if s.strip()], str(obj.get("reasoning", ""))
    return None


def _dedup(xs: list[str]) -> list[str]:
    seen, out = set(), []
    for x in xs:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def extract_answer(text: str, task_type: str) -> tuple[str, list[str], ExtractionMethod, bool]:
    """(answer_text, answer_symbols, method, success).

    - T2/T5: the JSON object `{"reasoning": ..., "symbols": [...]}`
      (fenced or bare). On failure, identifiers are taken from the prose,
      method `plain_text`, success False.
    - T3/T4: the first fenced code block; identifiers from it.
    - T1: the whole text as prose; identifiers from it.
    """
    text = text or ""
    if task_type in JSON_TYPES:
        parsed = _json_symbols(text)
        if parsed is not None:
            symbols, reasoning = parsed
            return reasoning or text, _dedup(symbols), "code_block", True
        return text, extract_identifiers(text), "plain_text", False
    if task_type in CODE_TYPES:
        blocks = _FENCE.findall(text)
        if blocks:
            code = blocks[0][1]
            return code, extract_identifiers(code), "code_block", True
        return text, extract_identifiers(text), "plain_text", False
    return text, extract_identifiers(text), "plain_text", bool(text.strip())


_QUOTED_DOTTED = re.compile(r'"([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+)"')


def repetition_count(text: str) -> int:
    """Symbol mentions minus distinct symbols. Counted over the JSON
    `symbols` list when the answer parses; otherwise over every quoted
    dotted name in the raw text, which still sees the partial `symbols`
    array of an answer cut off by the generation cap. 0 for prose."""
    text = text or ""
    for _lang, body in _FENCE.findall(text) + [("", text)]:
        start, end = body.find("{"), body.rfind("}")
        if start < 0 or end <= start:
            continue
        try:
            obj = json.loads(body[start:end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("symbols"), list):
            syms = [s.strip() for s in obj["symbols"] if isinstance(s, str) and s.strip()]
            return len(syms) - len(set(syms))
    quoted = _QUOTED_DOTTED.findall(text)
    return len(quoted) - len(set(quoted))


def _answer(arm: str, task, completion: dict, method_override: ExtractionMethod | None = None) -> NormalizedAnswer:
    answer_text, symbols, method, ok = extract_answer(completion.get("text", ""), task.task_type)
    if method_override and ok:
        method = method_override
    return NormalizedAnswer(
        arm=arm, task_id=task.task_id, raw_text=completion.get("text", ""), answer_text=answer_text,
        answer_symbols=symbols, extraction_method=method, extraction_success=ok,
        generation_tokens=int(completion.get("generation_tokens", 0)),
        latency_seconds=float(completion.get("latency_seconds", 0.0)),
        finish_reason=str(completion.get("finish_reason") or ""),
        repetition_count=repetition_count(completion.get("text", "")),
    )


def _context(raw: dict, arm: str, task, allowed_kinds: set[str]) -> DeliveredContext:
    ctx = DeliveredContext.from_dict(raw["bundle"])
    if ctx.arm != arm or ctx.task_id != task.task_id:
        raise ValueError(f"bundle is {ctx.arm}/{ctx.task_id}, expected {arm}/{task.task_id}")
    verify_ranking(ctx.items)
    bad = {str(it.kind) for it in ctx.items} - allowed_kinds
    if bad:
        raise ValueError(f"{arm} delivered item kinds {sorted(bad)}; allowed {sorted(allowed_kinds)}")
    if sum(it.token_count for it in ctx.items) != ctx.total_tokens:
        raise ValueError(f"{arm}/{task.task_id}: total_tokens does not equal the sum of item counts")
    if ctx.total_tokens > ctx.budget_tokens:
        # finalize_context should have prevented this; flag, never hide.
        ctx.build_meta["over_budget"] = True
    return ctx


# --------------------------------------------------------------------------
# symbol provenance (accounting only; set here, by each arm's adapter)
# --------------------------------------------------------------------------
#: item kind -> symbol_provenance, for the arms whose kind decides it
KIND_PROVENANCE = {"code_chunk": "body", "signature_stub": "signature_stub", "oracle_truth": "oracle",
                   "tool_result_grep": "tool_result", "tool_result_read": "tool_result",
                   "tool_result_digest": "tool_result"}
#: Arm 3: the LSP method that produced the item (provenance["lsp_method"])
LSP_METHOD_PROVENANCE = {"textDocument/hover": "hover", "textDocument/definition": "definition",
                         "textDocument/documentSymbol": "documentSymbol"}


def _with_symbol_provenance(ctx: DeliveredContext, of: Callable[[DeliveredItem], str]) -> DeliveredContext:
    items = []
    for it in ctx.items:
        value = of(it)
        if it.symbol_provenance and it.symbol_provenance != value:
            raise ValueError(f"{ctx.arm}/{it.source_id}: symbol_provenance {it.symbol_provenance!r}, "
                             f"expected {value!r}")
        items.append(replace(it, symbol_provenance=value))
    return replace(ctx, items=items)


def _by_kind(it: DeliveredItem) -> str:
    return KIND_PROVENANCE[str(it.kind)]


def _by_lsp_method(it: DeliveredItem) -> str:
    method = it.provenance.get("lsp_method")
    if method not in LSP_METHOD_PROVENANCE:
        raise ValueError(f"arm3/{it.source_id}: unknown lsp_method {method!r}")
    return LSP_METHOD_PROVENANCE[method]


# --------------------------------------------------------------------------
# the seven adapters
# --------------------------------------------------------------------------
def adapt_arm0(raw: dict, task) -> tuple[DeliveredContext, NormalizedAnswer]:
    ctx = _context(raw, "arm0", task, set())
    if ctx.items or ctx.budget_tokens != C.ARM0_BUDGET:
        raise ValueError("arm0 must deliver nothing with a zero budget")
    return ctx, _answer("arm0", task, raw["completion"])


def adapt_arm1_rag(raw: dict, task) -> tuple[DeliveredContext, NormalizedAnswer]:
    ctx = _with_symbol_provenance(_context(raw, "arm1", task, {"code_chunk"}), _by_kind)     # "body"
    return ctx, _answer("arm1", task, raw["completion"])


def adapt_arm2_priompt(raw: dict, task) -> tuple[DeliveredContext, NormalizedAnswer]:
    """Components above the Priompt cutoff: full chunks (`code_chunk`) and
    stubs that replaced their bodies (`signature_stub`). Chunks below every
    cutoff were never delivered and are not here."""
    ctx = _with_symbol_provenance(_context(raw, "arm2", task, {"code_chunk", "signature_stub"}), _by_kind)
    for key in ("cutoff", "n_full", "n_stub", "n_dropped"):
        if key not in ctx.build_meta:
            raise ValueError(f"arm2 bundle missing build_meta[{key!r}]")
    return ctx, _answer("arm2", task, raw["completion"])


def adapt_arm3_lsp(raw: dict, task) -> tuple[DeliveredContext, NormalizedAnswer]:
    """Hovers (`lsp_hover`) and file outlines (`lsp_symbol`) from at most
    two LSP hops around the seed symbol; nothing else is ever delivered."""
    ctx = _with_symbol_provenance(_context(raw, "arm3", task, {"lsp_hover", "lsp_symbol"}), _by_lsp_method)
    for key in ("hop1_definitions", "hop2_files", "ready"):
        if key not in ctx.build_meta:
            raise ValueError(f"arm3 bundle missing build_meta[{key!r}]")
    if any(int(it.provenance.get("hop", 0)) > 2 for it in ctx.items):
        raise ValueError("arm3 delivered an item from beyond hop 2")
    return ctx, _answer("arm3", task, raw["completion"])


def adapt_arm4_agent(raw: dict, task):
    # M3: tool_result_* items get symbol_provenance "tool_result" (KIND_PROVENANCE)
    raise NotImplementedError("Arm 4 (agent loop) adapter arrives in M3")


def adapt_arm5_prism(raw: dict, task) -> tuple[DeliveredContext, NormalizedAnswer]:
    ctx = _with_symbol_provenance(_context(raw, "arm5", task, {"code_chunk", "signature_stub"}), _by_kind)
    for key in ("turn_count", "turn2b_triggered"):
        if key not in ctx.build_meta:
            raise ValueError(f"arm5 bundle missing build_meta[{key!r}]")
    return ctx, _answer("arm5", task, raw["completion"], method_override="prism_final")


def adapt_oracle(raw: dict, task) -> tuple[DeliveredContext, NormalizedAnswer]:
    ctx = _with_symbol_provenance(_context(raw, "oracle", task, {"oracle_truth"}), _by_kind)   # "oracle"
    return ctx, _answer("oracle", task, raw["completion"])


ADAPTERS: dict[str, Callable] = {
    "arm0": adapt_arm0,
    "arm1": adapt_arm1_rag,
    "arm2": adapt_arm2_priompt,
    "arm3": adapt_arm3_lsp,
    "arm4": adapt_arm4_agent,
    "arm5": adapt_arm5_prism,
    "oracle": adapt_oracle,
}


def adapt(arm: str, raw: dict, task) -> tuple[DeliveredContext, NormalizedAnswer]:
    return ADAPTERS[arm](raw, task)


__all__ = ["ADAPTERS", "adapt", "extract_answer", "finalize_context", "DeliveredItem"]
