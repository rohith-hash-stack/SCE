"""The unified scorer: one `score()` for every arm, dispatched by task type.

No arm-specific branches. Every number is computed from the canonical
`DeliveredContext` and `NormalizedAnswer`, against the task's ground truth
and G*_universe, with the same tokenizer for every arm.

Definitions (also in README_M1.md):

Gold set `G` for ranking metrics: `task.ground_truth.pipeline_symbols`
(the T2 pipeline; the T5 gold affected set).

An item is *relevant at rank r* if it covers at least one gold symbol not
already covered by a higher-ranked item. Counting only new gold coverage
keeps P/nDCG/MAP in [0, 1] when an engine delivers the same symbol twice
(e.g. two chunks of one function).

An empty delivered set (the no-retrieval arm) has no precision: cleanliness,
context_precision and relevant_token_density are NaN there, never a
trivially perfect 1.0. Recall-type metrics are 0 (nothing was retrieved).
This is decided by the delivered set being empty, not by the arm's name.

Answer matching: an answer identifier names gold symbol g if it equals g or
is a dotted suffix of g at a component boundary (`get_dependant`,
`utils.get_dependant`). This is the legacy harness's bare-name matching.

`tsr` is the continuous per-type success score; `task_success = tsr >= 0.5`
is a secondary, binarised view of it (False when tsr is NaN).
- T2: ANSWER-based: 1.0 if the answer names the gold (config.T2_ANSWER_RULE:
  "all" gold symbols by default, or "any" one), else 0.0. The same rule runs
  for every arm, Arm 0 included, so Arm 0's T2 rate is the parametric floor.
  Retrieval quality is NOT part of it: `acc_at_5_retrieval` (every gold
  symbol in the top-5 delivered items) is a diagnostic column.
- T5: PRIMARY metric = `tsr` = FRACTIONAL recall of the gold affected set in
  the model's answer, |gold named in answer| / |gold| (8 of 10 named gives
  0.8). `recall_at_5` (gold affected present in the top-5 delivered items)
  is a RETRIEVAL diagnostic, not part of task_success.
  false_negative_rate = 1 - tsr.
- T1: judge-scored (faithfulness, answer relevancy); NaN without a judge.
- T3/T4: stubs (NaN) until a sandboxed test runner exists.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Callable, Optional

from harness import config as C
from harness.scoring import registry as R
from harness.scoring.canonical import DeliveredContext, DeliveredItem, NormalizedAnswer
from harness.scoring.fairness import verify_ranking
from harness.scoring.hallucination import hallucination_rate
from harness.tasks.schema import TaskType

NAN = float("nan")


@dataclass
class ScoreResult:
    """One scored cell.

    `tsr` is the primary per-type score. `task_success = tsr >= 0.5` is
    SECONDARY: a binarised convenience view, reported alongside, never instead.

    T5 (official interpretation): the PRIMARY metric is `tsr`, the
    FRACTIONAL recall of the gold affected set in the model's answer.
    `task_specific["recall_at_5"]` (gold affected present in the top-5
    delivered items) is a RETRIEVAL diagnostic and is not part of
    `task_success`.

    T2: `tsr` is answer-based (did the answer name the gold), identical for
    every arm. `task_specific["acc_at_5_retrieval"]` (all gold in the top-5
    delivered items) is a RETRIEVAL diagnostic and is not part of
    `task_success`.
    """
    arm: str
    task_id: str
    task_type: TaskType
    repo_id: str
    # Universal
    task_success: bool
    tsr: float
    uniform_cpi: float
    cleanliness: float
    context_precision: float
    context_recall: float
    mrr_at_5: float
    mrr_at_10: float
    ndcg_at_5: float
    ndcg_at_10: float
    map: float
    p_at_5: float
    r_at_5: float
    f1_at_5: float
    relevant_token_density: float
    hallucination_rate: float
    hallucination_breakdown: dict[str, list[str]]
    # Type-specific
    task_specific: dict
    # Efficiency
    budget_tokens: int
    total_tokens: int
    budget_utilization: float
    latency_profile: dict[str, dict[str, float]]
    turn_count: int
    # Diagnostics
    extraction_success: bool
    over_budget: bool
    seed: Optional[int] = None
    digest_safety_loss: Optional[float] = None
    tool_fpr: Optional[float] = None
    verification_lift: Optional[float] = None
    recovery_rate: Optional[float] = None
    total_tool_output_tokens: Optional[int] = None
    #: server finish reason ("stop" / "length"); "" when unknown
    finish_reason: str = ""
    #: True when the answer hit the generation cap (finish_reason "length")
    generation_capped: bool = False
    #: symbol mentions minus distinct symbols in the answer (repetition loops)
    repetition_count: int = 0
    #: accounting only (see the registry): unique delivered symbols resolved
    #: (body / stub / hover / oracle) vs. named only (outline / definition)
    delivered_symbols_resolved: int = 0
    delivered_symbols_named_only: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# matching and ranking helpers
# --------------------------------------------------------------------------
def names_symbol(gold: str, answer_symbols: list[str]) -> bool:
    for s in answer_symbols:
        if s == gold or gold.endswith("." + s):
            return True
    return False


def answer_recall(gold: list[str], answer_symbols: list[str]) -> float:
    if not gold:
        return NAN
    return sum(1 for g in gold if names_symbol(g, answer_symbols)) / len(gold)


def _coverage(item: DeliveredItem, gold: set[str]) -> set[str]:
    return set(item.symbols) & gold


def relevance_vector(items: list[DeliveredItem], gold: set[str]) -> list[int]:
    """1 at rank r if the item adds gold coverage not seen above it."""
    seen: set[str] = set()
    rel = []
    for it in sorted(items, key=lambda x: x.rank):
        new = _coverage(it, gold) - seen
        rel.append(1 if new else 0)
        seen |= new
    return rel


def gold_covered_in_top(items: list[DeliveredItem], gold: set[str], k: int) -> set[str]:
    out: set[str] = set()
    for it in sorted(items, key=lambda x: x.rank)[:k]:
        out |= _coverage(it, gold)
    return out


def mrr_at(rel: list[int], k: int) -> float:
    for i, r in enumerate(rel[:k], start=1):
        if r:
            return 1.0 / i
    return 0.0


def ndcg_at(rel: list[int], k: int, n_gold: int) -> float:
    if n_gold == 0:
        return NAN
    dcg = sum(r / math.log2(1 + i) for i, r in enumerate(rel[:k], start=1))
    ideal = sum(1 / math.log2(1 + i) for i in range(1, min(k, n_gold) + 1))
    return dcg / ideal


def average_precision(rel: list[int], n_gold: int) -> float:
    if n_gold == 0:
        return NAN
    hits, total = 0, 0.0
    for i, r in enumerate(rel, start=1):
        if r:
            hits += 1
            total += hits / i
    return total / n_gold


# --------------------------------------------------------------------------
# per-type scorers
# --------------------------------------------------------------------------
#: (task, answer, context) -> {"faithfulness": float, "answer_relevancy": float}
Judge = Callable[[object, NormalizedAnswer, DeliveredContext], dict]


def default_judge(task, ans: NormalizedAnswer, ctx: DeliveredContext) -> dict:
    # Held-out judge required — must NOT be a Qwen model. Every arm's
    # answer comes from a Qwen model; a Qwen judge would grade its own
    # family's output. Wire an independent judge here (M4).
    raise NotImplementedError("T1 judge not configured: held-out judge required — must NOT be a Qwen model")


def _score_t1(task, ans, ctx, judge: Judge | None) -> tuple[float, dict]:
    try:
        verdict = (judge or default_judge)(task, ans, ctx)
    except NotImplementedError as exc:
        return NAN, {"faithfulness": NAN, "answer_relevancy": NAN, "judge_status": f"unavailable: {exc}"}
    faith, relev = float(verdict["faithfulness"]), float(verdict["answer_relevancy"])
    # Both must hold: a faithful but irrelevant answer, or a relevant but
    # unfaithful one, is not a success.
    return min(faith, relev), {"faithfulness": faith, "answer_relevancy": relev, "judge_status": "ok"}


#: class FQN -> every ancestor class FQN (transitive), from the code graph
Ancestry = dict[str, frozenset[str]]


def names_symbol_inherited(gold: str, answer_symbols: list[str], ancestors: Ancestry) -> bool:
    """`gold` named strictly, or as the same member on a subclass of the
    gold's class: gold `exceptions.ValidationException.errors` is matched by
    `RequestValidationError.errors` when RequestValidationError extends
    ValidationException. Diagnostic only; strict matching stays primary."""
    if names_symbol(gold, answer_symbols):
        return True
    if "." not in gold:
        return False
    gold_cls, member = gold.rsplit(".", 1)
    for s in answer_symbols:
        if "." not in s:
            continue
        s_cls, s_member = s.rsplit(".", 1)
        if s_member != member:
            continue
        for cls, anc in ancestors.items():
            if (cls == s_cls or cls.endswith("." + s_cls)) and gold_cls in anc:
                return True
    return False


def _score_t2(task, ans, ctx, ancestors: Ancestry | None = None) -> tuple[float, dict]:
    """Primary: did the answer name the gold? Same rule for every arm.

    "all" (default) needs every gold symbol named; "any" needs one. In every
    real T2 task the seed symbol is gold AND named in the prompt, so "any"
    is met by echoing the question; it is reported as a diagnostic only.
    Retrieval (all gold in the top-5 delivered items) is a diagnostic too.
    """
    gold = list(task.ground_truth.pipeline_symbols)
    if not gold:
        return NAN, {"acc_at_5_retrieval": NAN, "answer_names_gold": NAN, "answer_names_any_gold": NAN,
                     "answer_names_inherited_gold": NAN, "answer_gold_recall": NAN}
    recall = answer_recall(gold, ans.answer_symbols)
    names_all = 1.0 if recall == 1.0 else 0.0
    names_any = 1.0 if recall > 0.0 else 0.0
    covered = gold_covered_in_top(ctx.items, set(gold), 5)
    acc5 = 1.0 if set(gold) <= covered else 0.0
    # every gold named, accepting a subclass's inherited member (NaN when no
    # class hierarchy was supplied)
    inherited = (1.0 if all(names_symbol_inherited(g, ans.answer_symbols, ancestors) for g in gold) else 0.0) \
        if ancestors is not None else NAN
    tsr = names_any if C.T2_ANSWER_RULE == "any" else names_all
    return tsr, {"acc_at_5_retrieval": acc5, "answer_names_gold": names_all,
                 "answer_names_any_gold": names_any, "answer_names_inherited_gold": inherited,
                 "answer_gold_recall": recall}


def _score_t3_stub(task, ans, ctx) -> tuple[float, dict]:
    return NAN, {"stub": True, "pass_at_1": NAN, "codebleu": NAN}


def _score_t4_stub(task, ans, ctx) -> tuple[float, dict]:
    return NAN, {"stub": True, "patch_exact_match": NAN, "regression_rate": NAN}


def _score_t5(task, ans, ctx) -> tuple[float, dict]:
    gold = list(task.ground_truth.pipeline_symbols)
    recall = answer_recall(gold, ans.answer_symbols)          # fractional, primary
    r5 = len(gold_covered_in_top(ctx.items, set(gold), 5)) / len(gold) if gold else NAN
    fnr = 1.0 - recall if recall == recall else NAN
    return recall, {"recall_at_5": r5, "false_negative_rate": fnr}


# --------------------------------------------------------------------------
# score()
# --------------------------------------------------------------------------
def score(task, ctx: DeliveredContext, ans: NormalizedAnswer, *, judge: Judge | None = None,
          symbol_cache: frozenset[str] | None = None, latency_profile: dict | None = None,
          seed: int | None = None, ancestors: Ancestry | None = None) -> ScoreResult:
    """Single scoring function for every arm. Dispatches by task type."""
    if ctx.task_id != task.task_id or ans.task_id != task.task_id or ans.arm != ctx.arm:
        raise ValueError(f"mismatched cell: task={task.task_id} ctx={ctx.arm}/{ctx.task_id} ans={ans.arm}/{ans.task_id}")
    verify_ranking(ctx.items)                                   # fairness rule 2

    # ---- universal ----
    delivered = ctx.delivered_symbols
    gold_list = list(task.ground_truth.pipeline_symbols)
    gold = set(gold_list)
    universe = task.ground_truth.universe_symbols()
    empty = not delivered

    uniform_cpi = len(delivered & gold) / max(len(gold), 1)
    cleanliness = NAN if empty else 1 - len(delivered - universe) / len(delivered)
    ctx_precision = NAN if empty else len(delivered & universe) / len(delivered)
    ctx_recall = len(delivered & universe) / max(len(universe), 1)

    rel = relevance_vector(ctx.items, gold)
    mrr5, mrr10 = mrr_at(rel, 5), mrr_at(rel, 10)
    ndcg5, ndcg10 = ndcg_at(rel, 5, len(gold)), ndcg_at(rel, 10, len(gold))
    ap = average_precision(rel, len(gold))
    p5 = sum(rel[:5]) / 5
    r5 = len(gold_covered_in_top(ctx.items, gold, 5)) / len(gold) if gold else NAN
    f1 = (2 * p5 * r5 / (p5 + r5)) if (r5 == r5 and p5 + r5 > 0) else (0.0 if r5 == r5 else NAN)

    rel_tokens = sum(it.token_count for it in ctx.items if set(it.symbols) & universe)
    rel_density = NAN if ctx.total_tokens == 0 else rel_tokens / ctx.total_tokens
    halluc, halluc_bd = hallucination_rate(ans.answer_symbols, task, ctx, symbol_cache, ancestors)

    # ---- type-specific ----
    tt = task.task_type
    if tt == "T1_conceptual":
        tsr, specific = _score_t1(task, ans, ctx, judge)
    elif tt == "T2_localization":
        tsr, specific = _score_t2(task, ans, ctx, ancestors)
    elif tt == "T3_codegen":
        tsr, specific = _score_t3_stub(task, ans, ctx)
    elif tt == "T4_edit":
        tsr, specific = _score_t4_stub(task, ans, ctx)
    elif tt == "T5_blast_radius":
        tsr, specific = _score_t5(task, ans, ctx)
    else:
        raise ValueError(f"Unknown task_type: {tt}")

    meta = ctx.build_meta
    result = ScoreResult(
        arm=ctx.arm, task_id=task.task_id, task_type=tt, repo_id=task.repo_id,
        task_success=bool(tsr == tsr and tsr >= C.TASK_SUCCESS_THRESHOLD), tsr=tsr,
        uniform_cpi=uniform_cpi, cleanliness=cleanliness,
        context_precision=ctx_precision, context_recall=ctx_recall,
        mrr_at_5=mrr5, mrr_at_10=mrr10, ndcg_at_5=ndcg5, ndcg_at_10=ndcg10, map=ap,
        p_at_5=p5, r_at_5=r5, f1_at_5=f1,
        relevant_token_density=rel_density, hallucination_rate=halluc, hallucination_breakdown=halluc_bd,
        task_specific=specific,
        budget_tokens=ctx.budget_tokens, total_tokens=ctx.total_tokens,
        budget_utilization=(ctx.total_tokens / ctx.budget_tokens) if ctx.budget_tokens else NAN,
        latency_profile=latency_profile or {},
        turn_count=int(meta.get("turn_count", 1)),
        extraction_success=ans.extraction_success,
        over_budget=bool(ctx.total_tokens > ctx.budget_tokens or meta.get("over_budget", False))
        if ctx.budget_tokens else bool(ctx.total_tokens > 0),
        seed=seed,
        total_tool_output_tokens=meta.get("total_tool_output_tokens"),
        finish_reason=ans.finish_reason,
        generation_capped=ans.finish_reason == "length",
        repetition_count=ans.repetition_count,
        delivered_symbols_resolved=len(ctx.delivered_symbols_resolved),
        delivered_symbols_named_only=len(ctx.delivered_symbols_named_only),
    )
    _validate(result)
    return result


def _validate(res: ScoreResult) -> None:
    for name in R.universal_metrics():
        if name == "task_success":
            continue
        R.check_value(name, getattr(res, name))
    for name, value in res.task_specific.items():
        if name in R.METRICS and isinstance(value, (int, float)):
            R.check_value(name, value)
    R.check_value("tsr", res.tsr)
