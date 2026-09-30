"""The 8-arm sweep runner: one cell = (repo, task, arm, seed).

    # 2-seed pilot on tRPC, all 8 arms (400 cells):
    python -m benchmarks.final_sweep.runner --repo trpc --seeds 42,43 \\
        --out reports/final_sweep/pilot_trpc --workers 8 --max-cost-usd 5

    # zero-LLM wiring check (real retrieval, every arm, no API call):
    python -m benchmarks.final_sweep.runner --repo trpc --dry-run --out /tmp/dry

    # re-print the summary of an existing run:
    python -m benchmarks.final_sweep.runner --out reports/final_sweep/pilot_trpc --summarize-only

A real (non---dry-run) run makes paid API calls with the key named by
`--api-key-env`. `--max-cost-usd` stops scheduling new cells once the
running total passes it.

Output (`--out`):
    cells.jsonl     one JSON record per executed cell, appended as each
                    finishes (the checkpoint; `--resume` skips keys that
                    already have an `ok` record)
    cells.parquet   the same records, list/dict fields JSON-encoded
    summary.json / summary.md   per-arm statistics and cost accounting
    run_config.json the frozen protocol parameters of this run

Parallelism: cells are grouped into (task, seed) units that run their
arms sequentially in `ARM_ORDER` - so `prism_plus_distractors` can reuse
the same unit's `prism_full` Turn-1 selection - and units run on a
thread pool. Retrieval is serialized by `CorpusState.lock`; only the LLM
calls overlap.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from prism.packer.candidate_index import _outgoing_call_names
from prism.packer.submodular_knapsack import split_budget_for_external

from benchmarks.corpora.resolver import resolve
from benchmarks.engines.base import selected_symbols
from benchmarks.final_sweep import config as C
from benchmarks.final_sweep.arms import BuiltContext, CorpusState, build_bfs, build_oracle, build_pagerank, distractor_nodes
from benchmarks.final_sweep.context_ops import (
    annotate_manifest_axes,
    enforce_token_ceiling,
    inject_distractors,
    redact_package_axes,
    render_context,
    select_distractors,
    sha256_text,
    turn1_row_format,
)
from benchmarks.ground_truth.loader import load_tasks_from_dir
from benchmarks.ground_truth.schema import EvaluationTask
from benchmarks.metrics.cpi import cpi_end_to_end, cpi_fractional, cpi_turn1_selection
from benchmarks.metrics.fpr import fpr
from benchmarks.run_two_pass_benchmark import (
    TURN2B_SYSTEM_PROMPT,
    _check_turn1_degeneration,
    _external_manifest_for_task,
    _hydrate_external,
    _parse_requested_symbols,
    _request_all_external_candidates,
    _turn1_user_prompt,
    _turn2b_user_prompt,
)
from benchmarks.runner import DEBUG_TASK_RESPONSE_CONTRACT, DEFAULT_TASKS_DIR_TEMPLATE, SYSTEM_PROMPT
from benchmarks.tsr.client import OpenAICompatibleClient, build_prompt
from benchmarks.tsr.scorer_debug import ParseError, extract_flat_symbols, score_debug_causal


def turn1_system_prompt(axes: C.AxisPolicy) -> str:
    """`benchmarks.run_two_pass_benchmark.TURN1_SYSTEM_PROMPT` with its
    row-format sentence describing exactly the fields this arm's
    manifest carries. For `axes="none"` it is that prompt verbatim."""
    return (
        "You are a senior software engineer investigating a codebase. You will be given a compact "
        f"<candidate_index> - every symbol reachable from a seed function, one per line as {turn1_row_format(axes)}"
        " - followed by a real task. Examine the symbol signatures and their direct call targets to trace the "
        "complete causal execution path from the seed to termination. Request all necessary intermediate and "
        "helper symbols required to form an unbroken execution chain. Only name symbols that appear in the "
        "index - never invent one."
    )


@dataclasses.dataclass
class SweepConfig:
    model: str = C.DEFAULT_MODEL
    temperature: float = C.DEFAULT_TEMPERATURE
    budget: int = C.DEFAULT_BUDGET
    token_ceiling: int = C.DEFAULT_TOKEN_CEILING
    max_tokens: int = C.DEFAULT_MAX_TOKENS
    base_url: str = C.DEFAULT_BASE_URL
    api_key_env: str = C.DEFAULT_API_KEY_ENV
    dry_run: bool = False
    max_cost_usd: float | None = None
    #: Client-side tokens-per-minute budget shared by all workers. Keep it
    #: under the account's TPM limit (gpt-4o-mini tier: 200K) - the pilot
    #: at 8 unthrottled workers lost 8% of cells to 429s.
    tpm_limit: int | None = 150_000
    #: Local (Ollama) endpoints only - sent as `options.num_ctx` on every
    #: call. Ollama's default context window (2-4K tokens) silently drops
    #: the head of longer prompts; the BFS floor alone renders ~6.4K.
    #: The Kaggle driver also starts `ollama serve` with the same
    #: OLLAMA_CONTEXT_LENGTH, which is what older servers honor.
    num_ctx: int = 24_576
    #: Local endpoints only, Turn-1 calls only - the same Ollama sampler
    #: knob `benchmarks.run_two_pass_benchmark` applies to Turn 1 for
    #: Qwen (see `DEFAULT_TURN1_REPEAT_PENALTY` there).
    turn1_repeat_penalty: float = 1.15

    @property
    def base_urls(self) -> list[str]:
        return [u.strip() for u in self.base_url.split(",") if u.strip()]

    @property
    def local(self) -> bool:
        """Every endpoint is a local server (Ollama) - calls are unmetered
        and take Ollama-native sampler options."""
        return all(is_local_url(u) for u in self.base_urls)


def is_local_url(url: str) -> bool:
    return any(h in url for h in ("localhost", "127.0.0.1", "0.0.0.0"))


def sampler_kwargs(cfg: SweepConfig, turn: str) -> dict:
    """`{"extra_body": {"options": {...}}}` for a local endpoint, `{}`
    otherwise (OpenAI rejects the unknown `options` field with a 400)."""
    if not cfg.local:
        return {}
    options: dict = {"num_ctx": cfg.num_ctx}
    if turn == "turn1":
        options.update(repeat_penalty=cfg.turn1_repeat_penalty, num_predict=cfg.max_tokens)
    return {"extra_body": {"options": options}}


# --------------------------------------------------------------------- #
# Per-cell record + schema
# --------------------------------------------------------------------- #

@dataclasses.dataclass
class CellRecord:
    # identifiers
    repo: str
    task_id: str
    engine_id: str
    seed: int | None
    budget: int
    token_ceiling: int
    status: str = "ok"  # "ok" | "error" | "dry_run"
    error: str | None = None
    # downstream outcome
    tsr: int | None = None
    tsr_partial: float | None = None
    answer_parsed_ok: bool | None = None
    # retrieval metrics
    cleanliness: float | None = None
    fpr_gt: float | None = None
    cleanliness_scaffold_adjusted: float | None = None
    cpi_context: float | None = None
    cpi_answer: float | None = None
    cpi_e2e: float | None = None
    cpi_turn1: float | None = None
    # confound / sufficiency controls
    sufficiency_ratio: float | None = None
    is_sufficient: bool | None = None
    n_required_scaffold: int = 0
    n_required_scaffold_present: int = 0
    is_truncated: bool | None = None
    n_budget_dropped: int = 0
    n_downgraded: int = 0
    n_ceiling_dropped: int = 0
    context_tokens_pre_ceiling: int | None = None
    context_tokens: int | None = None
    # stability / determinism
    manifest_hash: str | None = None
    manifest_candidate_count: int | None = None
    requested_symbols: list[str] = dataclasses.field(default_factory=list)
    selected_symbols: list[str] = dataclasses.field(default_factory=list)
    context_hash: str | None = None
    anchor_symbol: str | None = None
    anchor_matches_task_seed: bool | None = None
    turn1_parsed_ok: bool | None = None
    turn1_degenerate: bool | None = None
    turn1_reused_from: str | None = None
    skipped_hallucinated: list[str] = dataclasses.field(default_factory=list)
    # distractor dose
    distractor_k: int = 0
    distractors: list[str] = dataclasses.field(default_factory=list)
    distractors_in_context: int = 0
    distractors_named: int = 0
    # LLM metadata
    model_requested: str = ""
    response_models: list[str] = dataclasses.field(default_factory=list)
    system_fingerprint: str | None = None
    system_fingerprints: list[str] = dataclasses.field(default_factory=list)
    n_llm_calls: int = 0
    #: Calls whose reported prompt size reached the local context window
    #: (`SweepConfig.num_ctx`) - a sign the server truncated the prompt.
    n_calls_at_ctx_limit: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    cost_usd: float = 0.0
    calls: list[dict] = dataclasses.field(default_factory=list)
    temperature: float = C.DEFAULT_TEMPERATURE
    turn1_response: str | None = None
    answer_response: str | None = None
    harness_version: str = C.HARNESS_VERSION
    wall_time_s: float = 0.0
    finished_at: str = ""


_NUM = (int, float)
#: `field -> (allowed types, nullable)` - checked by `validate_record`
#: over every cell the pilot writes.
CELL_SCHEMA: dict[str, tuple[tuple[type, ...], bool]] = {
    "repo": ((str,), False), "task_id": ((str,), False), "engine_id": ((str,), False),
    "seed": ((int,), True), "budget": ((int,), False), "token_ceiling": ((int,), False),
    "status": ((str,), False), "error": ((str,), True),
    "tsr": ((int,), True), "tsr_partial": (_NUM, True), "answer_parsed_ok": ((bool,), True),
    "cleanliness": (_NUM, True), "fpr_gt": (_NUM, True), "cleanliness_scaffold_adjusted": (_NUM, True),
    "cpi_context": (_NUM, True), "cpi_answer": (_NUM, True), "cpi_e2e": (_NUM, True), "cpi_turn1": (_NUM, True),
    "sufficiency_ratio": (_NUM, True), "is_sufficient": ((bool,), True),
    "n_required_scaffold": ((int,), False), "n_required_scaffold_present": ((int,), False),
    "is_truncated": ((bool,), True), "n_budget_dropped": ((int,), False), "n_downgraded": ((int,), False),
    "n_ceiling_dropped": ((int,), False), "context_tokens_pre_ceiling": ((int,), True), "context_tokens": ((int,), True),
    "manifest_hash": ((str,), True), "manifest_candidate_count": ((int,), True),
    "requested_symbols": ((list,), False), "selected_symbols": ((list,), False), "context_hash": ((str,), True),
    "anchor_symbol": ((str,), True), "anchor_matches_task_seed": ((bool,), True),
    "turn1_parsed_ok": ((bool,), True), "turn1_degenerate": ((bool,), True), "turn1_reused_from": ((str,), True),
    "skipped_hallucinated": ((list,), False),
    "distractor_k": ((int,), False), "distractors": ((list,), False),
    "distractors_in_context": ((int,), False), "distractors_named": ((int,), False),
    "model_requested": ((str,), False), "response_models": ((list,), False),
    "system_fingerprint": ((str,), True), "system_fingerprints": ((list,), False),
    "n_llm_calls": ((int,), False), "n_calls_at_ctx_limit": ((int,), False), "prompt_tokens": ((int,), False), "completion_tokens": ((int,), False),
    "latency_s": (_NUM, False), "cost_usd": (_NUM, False), "calls": ((list,), False),
    "temperature": (_NUM, False), "turn1_response": ((str,), True), "answer_response": ((str,), True),
    "harness_version": ((str,), False), "wall_time_s": (_NUM, False), "finished_at": ((str,), False),
}

_RATE_FIELDS = (
    "tsr_partial", "cleanliness", "fpr_gt", "cleanliness_scaffold_adjusted", "cpi_context", "cpi_answer",
    "cpi_e2e", "cpi_turn1", "sufficiency_ratio",
)


def validate_record(row: dict) -> list[str]:
    """Every schema violation in one record (empty list = valid): a
    missing or unexpected field, a wrong type, an out-of-[0,1] rate, a
    non-binary `tsr`, or an `ok` cell missing its outcome/LLM metadata."""
    problems = []
    for name, (types, nullable) in CELL_SCHEMA.items():
        if name not in row:
            problems.append(f"missing field {name}")
            continue
        value = row[name]
        if value is None:
            if not nullable:
                problems.append(f"{name} is null")
            continue
        if isinstance(value, bool) and bool not in types:
            problems.append(f"{name} is bool, expected {types}")
        elif not isinstance(value, types):
            problems.append(f"{name} has type {type(value).__name__}, expected {types}")
    for extra in set(row) - set(CELL_SCHEMA):
        problems.append(f"unexpected field {extra}")
    for name in _RATE_FIELDS:
        value = row.get(name)
        if isinstance(value, _NUM) and not 0.0 <= value <= 1.0:
            problems.append(f"{name}={value} outside [0, 1]")
    if row.get("tsr") not in (None, 0, 1):
        problems.append(f"tsr={row.get('tsr')} not binary")
    if row.get("status") == "ok":
        for name in ("tsr", "cleanliness", "cpi_answer", "cpi_e2e", "is_sufficient", "is_truncated", "context_hash"):
            if row.get(name) is None:
                problems.append(f"ok cell has null {name}")
        if row.get("n_llm_calls", 0) < 1:
            problems.append("ok cell made no LLM call")
        if not row.get("response_models"):
            problems.append("ok cell has no response model")
    return problems


def cell_key(repo: str, task_id: str, engine_id: str, seed: int | None, budget: int) -> str:
    return f"{repo}|{task_id}|{engine_id}|{seed}|{budget}"


# --------------------------------------------------------------------- #
# One cell
# --------------------------------------------------------------------- #

class _CallLog:
    """Collects every LLM call a cell makes, including ones made before
    the cell later failed - so an error cell's spend is still counted."""

    def __init__(self, local: bool = False, num_ctx: int | None = None) -> None:
        self.calls: list[dict] = []
        self.local = local
        self.num_ctx = num_ctx

    def add(self, turn: str, call) -> None:
        # A local model has no metered price, whatever pricing table its
        # tag happens to match (`qwen2.5-coder*` resolves to Qwen's hosted
        # API rates in `benchmarks.tsr.client`).
        cost = 0.0 if self.local else (call.cost_usd or 0.0)
        at_limit = bool(self.local and self.num_ctx and call.prompt_tokens >= 0.98 * self.num_ctx)
        self.calls.append({
            "turn": turn,
            "model": call.model,
            "response_model": call.response_model,
            "system_fingerprint": call.system_fingerprint,
            "prompt_tokens": call.prompt_tokens,
            "completion_tokens": call.completion_tokens,
            "latency_s": call.latency_seconds,
            "cost_usd": cost,
            "at_ctx_limit": at_limit,
        })

    def apply(self, record: CellRecord) -> None:
        record.calls = self.calls
        record.n_llm_calls = len(self.calls)
        record.n_calls_at_ctx_limit = sum(1 for c in self.calls if c.get("at_ctx_limit"))
        record.prompt_tokens = sum(c["prompt_tokens"] for c in self.calls)
        record.completion_tokens = sum(c["completion_tokens"] for c in self.calls)
        record.latency_s = round(sum(c["latency_s"] for c in self.calls), 4)
        record.cost_usd = round(sum(c["cost_usd"] for c in self.calls), 8)
        record.response_models = sorted({c["response_model"] for c in self.calls if c["response_model"]})
        record.system_fingerprints = sorted({c["system_fingerprint"] for c in self.calls if c["system_fingerprint"]})
        # The answer call's fingerprint is the headline one - it served
        # the output `tsr` scores.
        answer_calls = [c for c in self.calls if c["turn"] == "answer"]
        record.system_fingerprint = (answer_calls[-1] if answer_calls else self.calls[-1])["system_fingerprint"] if self.calls else None


def _bare(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def _two_pass_context(
    corpus: CorpusState, client, spec: C.ArmSpec, task: EvaluationTask, seed: int | None,
    cfg: SweepConfig, record: CellRecord, log: _CallLog, turn1_cache: dict,
) -> tuple[BuiltContext, list[str]]:
    static = corpus.task_static(task)
    if spec.anchor == "lexical":
        if static.lexical_anchor is None:
            raise RuntimeError("lexical anchor index is empty")
        anchor = static.lexical_anchor[0]
    else:
        anchor = task.seed_symbol
    record.anchor_symbol = anchor
    record.anchor_matches_task_seed = anchor == task.seed_symbol

    with corpus.lock:
        manifest_text, universe = corpus.prism.build_candidate_manifest(anchor)
    manifest = annotate_manifest_axes(manifest_text, corpus.feature_masks, spec.axes)
    record.manifest_hash = sha256_text(manifest)
    record.manifest_candidate_count = len(universe)

    # Distractor arms answer from prism_full's own retrieved context, so
    # they reuse that unit's prism_full Turn-1 selection when it exists:
    # the only difference left between the two arms is the injected noise.
    reuse_key = (task.task_id, seed)
    reused = turn1_cache.get(reuse_key) if spec.distractor_k else None
    if client is None:
        content, completion_tokens = '{"requested_symbols": []}', 0
    elif reused is not None:
        content, completion_tokens = reused
        record.turn1_reused_from = "prism_full"
    else:
        call = client.complete(
            cfg.model, turn1_system_prompt(spec.axes), _turn1_user_prompt(manifest, task.prompt),
            temperature=cfg.temperature, max_tokens=cfg.max_tokens, seed=seed,
            task_id=task.task_id, engine=f"{spec.engine_id}/turn1", **sampler_kwargs(cfg, "turn1"),
        )
        log.add("turn1", call)
        content, completion_tokens = call.content, call.completion_tokens
        if spec.engine_id == "prism_full":
            turn1_cache[reuse_key] = (content, completion_tokens)
    record.turn1_response = content
    requested, parsed_ok = _parse_requested_symbols(content, universe)
    requested, degenerate = _check_turn1_degeneration(content, completion_tokens, cfg.max_tokens, requested, universe)
    record.turn1_parsed_ok = parsed_ok
    record.turn1_degenerate = degenerate
    record.requested_symbols = list(requested)
    record.cpi_turn1 = cpi_turn1_selection(requested, task.adjudicated.pipeline_symbols)

    internal_budget, external_budget = split_budget_for_external(cfg.budget) if task.root_imports else (cfg.budget, 0)
    with corpus.lock:
        pkg, skipped = corpus.prism.retrieve_requested(anchor, internal_budget, requested, universe, task_type=task.task_type)
        direct = set(_outgoing_call_names(corpus.builder, anchor)) & universe
    hydration = ({anchor} | (set(requested) & universe) | direct)
    record.skipped_hallucinated = list(skipped)

    if task.root_imports:
        with corpus.lock:
            ext_manifest, ext_candidates = _external_manifest_for_task(corpus.prism, task, requested)
        if ext_candidates:
            if client is None:
                requested_ext = _request_all_external_candidates(ext_manifest)
            else:
                call = client.complete(
                    cfg.model, TURN2B_SYSTEM_PROMPT, _turn2b_user_prompt(ext_manifest, task.prompt),
                    temperature=cfg.temperature, max_tokens=cfg.max_tokens, seed=seed,
                    task_id=task.task_id, engine=f"{spec.engine_id}/turn2b", **sampler_kwargs(cfg, "turn2b"),
                )
                log.add("turn2b", call)
                requested_ext, _ok = _parse_requested_symbols(call.content, ext_candidates)
            with corpus.lock:
                pkg, ext_skipped = _hydrate_external(corpus.prism, pkg, requested_ext, external_budget)
            record.skipped_hallucinated += list(ext_skipped)
            hydration |= set(requested_ext) & set(ext_candidates)

    packed = {n.id for n in pkg.nodes}
    dropped = sorted(hydration - packed)
    distractors: list[str] = []
    if spec.distractor_k:
        related = static.gt_universe | set(static.required_scaffold) | static.taxonomy_universe
        with corpus.lock:
            distractors = select_distractors(corpus.builder, corpus.repo, task.task_id, related, spec.distractor_k)
        pkg = inject_distractors(pkg, distractor_nodes(corpus, distractors), corpus.repo, task.task_id)
    return BuiltContext(pkg=pkg, n_budget_dropped=len(dropped), budget_dropped=dropped), distractors


def run_cell(
    corpus: CorpusState, client, spec: C.ArmSpec, task: EvaluationTask, seed: int | None,
    cfg: SweepConfig, turn1_cache: dict,
) -> CellRecord:
    started = time.monotonic()
    record = CellRecord(
        repo=corpus.repo, task_id=task.task_id, engine_id=spec.engine_id, seed=seed, budget=cfg.budget,
        token_ceiling=cfg.token_ceiling, model_requested=cfg.model, temperature=cfg.temperature,
        distractor_k=spec.distractor_k,
    )
    log = _CallLog(local=cfg.local, num_ctx=cfg.num_ctx)
    try:
        _run_cell_inner(corpus, client, spec, task, seed, cfg, turn1_cache, record, log)
        record.status = "dry_run" if client is None else "ok"
    except Exception as exc:  # one broken cell must never take down the sweep
        record.status = "error"
        record.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}"[:4000]
    log.apply(record)
    record.wall_time_s = round(time.monotonic() - started, 3)
    record.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return record


def _run_cell_inner(corpus, client, spec, task, seed, cfg, turn1_cache, record: CellRecord, log: _CallLog) -> None:
    static = corpus.task_static(task)
    pipeline = list(task.adjudicated.pipeline_symbols)
    distractors: list[str] = []
    if spec.kind == "bfs":
        built = build_bfs(corpus, task, cfg.budget)
        record.anchor_symbol, record.anchor_matches_task_seed = task.seed_symbol, True
    elif spec.kind == "pagerank":
        built = build_pagerank(corpus, task)
        record.anchor_symbol = task.seed_symbol
    elif spec.kind == "oracle":
        built = build_oracle(corpus, task, cfg.budget, spec.scaffolded, spec.engine_id)
        record.anchor_symbol, record.anchor_matches_task_seed = task.seed_symbol, True
    else:
        built, distractors = _two_pass_context(corpus, client, spec, task, seed, cfg, record, log, turn1_cache)

    pkg = redact_package_axes(built.pkg, spec.axes if spec.kind == "two_pass" else "all")
    with corpus.lock:
        pkg, ceiling_dropped, tokens_before, tokens_after = enforce_token_ceiling(pkg, cfg.token_ceiling, set(distractors))
    rendered = render_context(pkg)
    packed = selected_symbols(pkg)

    if spec.kind == "pagerank":
        # A repo map has no anchor; record whether the task seed survived
        # into the final (post-ceiling) context.
        record.anchor_matches_task_seed = task.seed_symbol in packed
    record.distractors = distractors
    record.distractors_in_context = len(set(distractors) & packed)
    record.selected_symbols = sorted(packed)
    record.context_hash = sha256_text(rendered)
    record.context_tokens_pre_ceiling = tokens_before
    record.context_tokens = tokens_after
    record.n_budget_dropped = built.n_budget_dropped
    record.n_downgraded = sum(1 for n in pkg.nodes if n.compression != "L0_full" and n.id not in built.designed_partial)
    record.n_ceiling_dropped = len(ceiling_dropped)
    record.is_truncated = bool(record.n_budget_dropped or record.n_downgraded or record.n_ceiling_dropped)

    record.fpr_gt = fpr(packed, static.gt_universe)
    record.cleanliness = 1.0 - record.fpr_gt
    record.cleanliness_scaffold_adjusted = 1.0 - fpr(packed, static.gt_universe | set(static.required_scaffold))
    record.cpi_context = cpi_fractional(packed, pipeline)
    required = set(static.required_scaffold)
    present = required & packed
    record.n_required_scaffold = len(required)
    record.n_required_scaffold_present = len(present)
    record.sufficiency_ratio = len(present) / len(required) if required else 1.0
    record.is_sufficient = record.sufficiency_ratio == 1.0

    if client is None:
        return
    system, user = build_prompt(SYSTEM_PROMPT, rendered, task.prompt + DEBUG_TASK_RESPONSE_CONTRACT)
    call = client.complete(
        cfg.model, system, user, temperature=cfg.temperature, max_tokens=cfg.max_tokens, seed=seed,
        task_id=task.task_id, engine=f"{spec.engine_id}/answer", **sampler_kwargs(cfg, "answer"),
    )
    log.add("answer", call)
    record.answer_response = call.content
    record.tsr_partial = score_debug_causal(call.content, pipeline, packed)
    record.tsr = 1 if record.tsr_partial == 1.0 else 0
    try:
        answer = extract_flat_symbols(call.content)
        record.answer_parsed_ok = True
    except ParseError:
        answer = []
        record.answer_parsed_ok = False
    record.cpi_answer = cpi_end_to_end(answer, pipeline)
    pipeline_set = set(pipeline)
    record.cpi_e2e = len(set(answer) & packed & pipeline_set) / len(pipeline_set) if pipeline_set else 1.0
    answer_bare = {_bare(s) for s in answer}
    record.distractors_named = sum(1 for d in distractors if _bare(d) in answer_bare)


# --------------------------------------------------------------------- #
# The sweep
# --------------------------------------------------------------------- #

def load_debug_tasks(repo: str, task_ids: list[str] | None = None, tasks_dir: str | None = None) -> list[EvaluationTask]:
    loaded = load_tasks_from_dir(Path(tasks_dir or DEFAULT_TASKS_DIR_TEMPLATE.format(repo=repo)))
    tasks = sorted((t for t in loaded.accepted if t.task_type == "debug"), key=lambda t: t.task_id)
    if task_ids:
        wanted = set(task_ids)
        missing = wanted - {t.task_id for t in tasks}
        if missing:
            raise ValueError(f"unknown task id(s) for {repo}: {sorted(missing)}")
        tasks = [t for t in tasks if t.task_id in wanted]
    return tasks


def load_records(path: Path) -> dict[str, dict]:
    """Latest record per cell key from an append-only `cells.jsonl`."""
    records: dict[str, dict] = {}
    if not path.exists():
        return records
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # A session killed mid-append (Kaggle's hard time limit)
                # leaves at most one partial trailing line; that cell is
                # simply re-run on resume.
                print(f"[final_sweep] skipping unreadable line in {path}", file=sys.stderr)
                continue
            records[cell_key(row["repo"], row["task_id"], row["engine_id"], row["seed"], row["budget"])] = row
    return records


def write_parquet(records: list[dict], path: Path) -> bool:
    """Writes `records` to Parquet with list/dict columns JSON-encoded
    (so the file has a flat, stable schema). Returns False, without
    raising, when `pyarrow` isn't installed - `cells.jsonl` is the
    source of truth either way."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        print("[final_sweep] pyarrow not installed - skipping cells.parquet (cells.jsonl is complete)", file=sys.stderr)
        return False
    flat = [{k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in r.items()} for r in records]
    pq.write_table(pa.Table.from_pylist(flat), path)
    return True


class TokenRateLimiter:
    """Sliding 60-second window over *estimated* request tokens (prompt
    tokens counted locally + completion allowance), shared by every
    worker thread. `acquire` blocks until the request fits."""

    COMPLETION_ALLOWANCE = 400

    def __init__(self, tokens_per_minute: int) -> None:
        self.tpm = tokens_per_minute
        self._events: list[tuple[float, int]] = []
        self._lock = threading.Lock()

    def acquire(self, tokens: int) -> None:
        tokens = min(tokens, self.tpm)
        while True:
            with self._lock:
                now = time.monotonic()
                self._events = [(t, n) for t, n in self._events if now - t < 60.0]
                used = sum(n for _, n in self._events)
                if used + tokens <= self.tpm:
                    self._events.append((now, tokens))
                    return
                wait = 60.0 - (now - self._events[0][0]) + 0.05
            time.sleep(max(wait, 0.05))


class ThrottledClient:
    """Wraps a client's `complete()` with a `TokenRateLimiter`; everything
    else passes through."""

    def __init__(self, inner, limiter: TokenRateLimiter) -> None:
        self._inner = inner
        self._limiter = limiter

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def complete(self, model=None, system="", user="", *args, **kwargs):
        from prism.slicer.tokenizer import count_tokens

        self._limiter.acquire(count_tokens(system) + count_tokens(user) + TokenRateLimiter.COMPLETION_ALLOWANCE)
        return self._inner.complete(model, system, user, *args, **kwargs)


class RoundRobinClient:
    """Spreads calls over several endpoints (one Ollama server per GPU),
    one call at a time in rotation."""

    def __init__(self, clients: list) -> None:
        self._clients = clients
        self._next = 0
        self._lock = threading.Lock()

    def __getattr__(self, name):
        return getattr(self._clients[0], name)

    def complete(self, *args, **kwargs):
        with self._lock:
            client = self._clients[self._next % len(self._clients)]
            self._next += 1
        return client.complete(*args, **kwargs)


def build_client(cfg: SweepConfig):
    if cfg.dry_run:
        return None
    key = os.environ.get(cfg.api_key_env, "")
    if not key:
        if not cfg.local:
            raise SystemExit(f"error: ${cfg.api_key_env} is not set (use --api-key-env to name the variable holding the key)")
        key = "ollama"  # local servers accept any bearer token
    clients = [OpenAICompatibleClient(api_key=key, base_url=url) for url in cfg.base_urls]
    client = clients[0] if len(clients) == 1 else RoundRobinClient(clients)
    return ThrottledClient(client, TokenRateLimiter(cfg.tpm_limit)) if cfg.tpm_limit else client


def check_model_consistency(existing: dict[str, dict], model: str, out_dir: Path) -> None:
    """Refuses to resume into a directory holding another model's cells:
    the cell key has no model in it, and one summary must never pool two
    models' results."""
    other = sorted({r["model_requested"] for r in existing.values() if r["status"] != "dry_run"} - {model})
    if other:
        raise SystemExit(
            f"error: {out_dir} already holds cells from model(s) {other}; this run uses {model!r}. "
            "Use a separate --out directory per model."
        )


def run_sweep(
    repo: str,
    arms: list[str],
    seeds: tuple[int, ...],
    out_dir: Path,
    cfg: SweepConfig,
    task_ids: list[str] | None = None,
    workers: int = 8,
    resume: bool = True,
    client=None,
    corpus: CorpusState | None = None,
    repo_path: str | None = None,
    tasks: list[EvaluationTask] | None = None,
) -> list[dict]:
    """Runs every missing (task, arm, seed) cell and returns all records.
    `client`/`corpus`/`tasks` are injection points for tests; normally
    they're built from `cfg`/`repo`."""
    specs = [C.resolve_arm(a) for a in arms]
    tasks = tasks if tasks is not None else load_debug_tasks(repo, task_ids)
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "cells.jsonl"
    existing = load_records(jsonl) if resume else {}
    check_model_consistency(existing, cfg.model, out_dir)
    if not resume and jsonl.exists():
        jsonl.unlink()
    if client is None and not cfg.dry_run:
        client = build_client(cfg)
    effective_seeds: tuple[int | None, ...] = seeds if not cfg.dry_run else (None,)

    (out_dir / "run_config.json").write_text(json.dumps({
        "harness_version": C.HARNESS_VERSION, "repo": repo, "arms": arms, "seeds": list(seeds),
        "tasks": [t.task_id for t in tasks], **dataclasses.asdict(cfg),
        "arm_specs": {s.engine_id: dataclasses.asdict(s) for s in specs},
    }, indent=2))

    if corpus is None:
        path = repo_path or str(resolve(repo))
        print(f"[final_sweep] indexing {repo} at {path} ...", file=sys.stderr, flush=True)
        t0 = time.monotonic()
        corpus = CorpusState(repo, path)
        print(f"[final_sweep] indexed in {time.monotonic() - t0:.1f}s", file=sys.stderr, flush=True)
    for task in tasks:  # warm per-task state serially (and surface bad seeds early)
        corpus.task_static(task)

    # Seed the Turn-1 reuse cache from already-completed prism_full cells.
    turn1_cache: dict = {}
    for row in existing.values():
        if row["engine_id"] == "prism_full" and row["status"] == "ok" and row.get("turn1_response") is not None:
            turn1_cache[(row["task_id"], row["seed"])] = (
                row["turn1_response"], next((c["completion_tokens"] for c in row["calls"] if c["turn"] == "turn1"), 0),
            )

    write_lock = threading.Lock()
    spent = [sum(r.get("cost_usd", 0.0) for r in existing.values())]
    stop = threading.Event()
    todo = 0
    units = []
    for task in tasks:
        for seed in effective_seeds:
            unit = [s for s in specs if existing.get(cell_key(repo, task.task_id, s.engine_id, seed, cfg.budget), {}).get("status") not in ("ok", "dry_run")]
            if unit:
                units.append((task, seed, unit))
                todo += len(unit)
    print(f"[final_sweep] {repo}: {len(tasks)} tasks x {len(specs)} arms x {len(effective_seeds)} seeds; "
          f"{todo} cells to run ({len(existing)} already recorded)", file=sys.stderr, flush=True)
    done = [0]

    def run_unit(task, seed, unit_specs):
        for spec in unit_specs:
            if stop.is_set():
                return
            record = run_cell(corpus, client, spec, task, seed, cfg, turn1_cache)
            row = dataclasses.asdict(record)
            with write_lock:
                with jsonl.open("a") as fh:
                    fh.write(json.dumps(row) + "\n")
                existing[cell_key(repo, task.task_id, spec.engine_id, seed, cfg.budget)] = row
                spent[0] += record.cost_usd
                done[0] += 1
                flag = "" if record.status != "error" else f"  ERROR {record.error.splitlines()[0]}"
                print(f"[final_sweep] {done[0]}/{todo} {task.task_id} {spec.engine_id} seed={seed} "
                      f"tsr={record.tsr} cost=${spent[0]:.4f}{flag}", file=sys.stderr, flush=True)
                if cfg.max_cost_usd is not None and spent[0] >= cfg.max_cost_usd and not stop.is_set():
                    print(f"[final_sweep] cost cap ${cfg.max_cost_usd} reached - no new cells will start", file=sys.stderr)
                    stop.set()

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(run_unit, t, s, u) for t, s, u in units]
        for f in as_completed(futures):
            f.result()

    rows = sorted(existing.values(), key=lambda r: (r["task_id"], C.ARM_ORDER.index(r["engine_id"]) if r["engine_id"] in C.ARM_ORDER else 99, r["engine_id"], str(r["seed"])))
    write_parquet(rows, out_dir / "cells.parquet")
    return rows


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m benchmarks.final_sweep.runner", description=__doc__.split("\n\n")[0])
    p.add_argument("--repo", choices=sorted(C.FINAL_SWEEP_REPOS))
    p.add_argument("--arms", default="all", help="Comma-separated engine ids, or 'all' (the 8 arms). "
                   "Extra distractor doses: prism_plus_distractors_k<N>.")
    p.add_argument("--tasks", nargs="+", default=None)
    p.add_argument("--seeds", default=",".join(str(s) for s in C.DEFAULT_SEEDS))
    p.add_argument("--out", required=True)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--fresh", action="store_true", help="Discard an existing cells.jsonl instead of resuming it.")
    p.add_argument("--dry-run", action="store_true", help="Real retrieval for every arm, zero LLM calls.")
    p.add_argument("--summarize-only", action="store_true")
    p.add_argument("--model", default=os.environ.get("LLM_MODEL") or C.DEFAULT_MODEL,
                   help="Default: $LLM_MODEL, else the pinned OpenAI snapshot.")
    p.add_argument("--temperature", type=float, default=C.DEFAULT_TEMPERATURE)
    p.add_argument("--budget", type=int, default=C.DEFAULT_BUDGET)
    p.add_argument("--token-ceiling", type=int, default=C.DEFAULT_TOKEN_CEILING)
    p.add_argument("--max-tokens", type=int, default=C.DEFAULT_MAX_TOKENS)
    p.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL") or os.environ.get("LLM_BASE_URL") or C.DEFAULT_BASE_URL,
                   help="Default: $OPENAI_BASE_URL, else $LLM_BASE_URL, else OpenAI. Comma-separate several "
                   "endpoints (e.g. one Ollama server per GPU) to round-robin calls across them.")
    p.add_argument("--num-ctx", type=int, default=24_576, help="Local endpoints only: Ollama options.num_ctx.")
    p.add_argument("--api-key-env", default=C.DEFAULT_API_KEY_ENV)
    p.add_argument("--max-cost-usd", type=float, default=None)
    p.add_argument("--tpm-limit", type=int, default=None,
                   help="Client-side tokens/minute budget across all workers (0 disables). "
                   "Default: 150000 for a remote API, off for local endpoints.")
    return p


def main(argv: list[str] | None = None) -> int:
    from benchmarks.final_sweep.summary import render_markdown, summarize

    args = build_arg_parser().parse_args(argv)
    out_dir = Path(args.out)
    if not args.summarize_only:
        if not args.repo:
            print("error: --repo is required unless --summarize-only", file=sys.stderr)
            return 2
        arms = list(C.ARM_ORDER) if args.arms == "all" else [a.strip() for a in args.arms.split(",") if a.strip()]
        seeds = tuple(int(s) for s in args.seeds.split(",") if s.strip())
        cfg = SweepConfig(
            model=args.model, temperature=args.temperature, budget=args.budget, token_ceiling=args.token_ceiling,
            max_tokens=args.max_tokens, base_url=args.base_url, api_key_env=args.api_key_env,
            dry_run=args.dry_run, max_cost_usd=args.max_cost_usd, num_ctx=args.num_ctx,
        )
        if args.tpm_limit is None:
            cfg.tpm_limit = None if cfg.local else 150_000
        else:
            cfg.tpm_limit = args.tpm_limit or None
        run_sweep(args.repo, arms, seeds, out_dir, cfg, task_ids=args.tasks, workers=args.workers, resume=not args.fresh)
    rows = list(load_records(out_dir / "cells.jsonl").values())
    summary = summarize(rows)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    md = render_markdown(summary)
    (out_dir / "summary.md").write_text(md)
    print(md)
    return 0 if not summary["schema"]["n_invalid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
