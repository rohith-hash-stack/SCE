"""Cell runner for M1 (the full Phase 1–4 scripts arrive in M3).

One cell = (arm, task, seed):
  retrieve (timed) -> finalize_context (re-count with the harness tokenizer,
  trim to 13,000) -> build_prompt -> envelope check -> generate (timed) ->
  write bundle + completion JSON -> adapter -> score().
Every arm goes through exactly this path, with the same system prompt, the
same model call and the same scorer.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from harness import config as C
from harness.arms.base import SYSTEM_PROMPT
from harness.scoring.adapters import adapt, finalize_context
from harness.scoring.canonical import DeliveredContext, NormalizedAnswer
from harness.scoring.latency import latency_profile, timed
from harness.scoring.scorer import ScoreResult, score


@dataclass
class CellOutput:
    arm: str
    task_id: str
    seed: int | None
    ctx: DeliveredContext
    ans: NormalizedAnswer
    result: ScoreResult
    prompt_tokens: int


def gpu_memory_used_mb() -> int | None:
    """Max memory.used across GPUs (MiB), or None without nvidia-smi."""
    if not shutil.which("nvidia-smi"):
        return None
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, timeout=30)
    vals = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
    return max(vals) if vals else None


def gpu_watchdog(limit_mb: int = C.GPU_MEMORY_ABORT_MB) -> int | None:
    used = gpu_memory_used_mb()
    if used is not None and used > limit_mb:
        raise RuntimeError(f"GPU watchdog: {used} MiB used > {limit_mb} MiB limit; aborting this batch")
    return used


class Pipeline:
    def __init__(self, arms: dict, llm, tokenizer, out_dir: str | Path | None = None,
                 symbol_cache: frozenset[str] | None = None) -> None:
        self.arms = arms
        self.llm = llm
        self.tok = tokenizer
        self.out_dir = Path(out_dir) if out_dir else None
        self.symbol_cache = symbol_cache

    def _seed_dict(self, arm_id: str, task, seed: int | None) -> dict:
        d = {**task.seed_dict(), "llm_seed": seed}
        if arm_id == "oracle":
            # the ground truth reaches the oracle only
            d["oracle_pipeline"] = list(task.ground_truth.pipeline_symbols)
            d["oracle_universe"] = sorted(task.ground_truth.universe_symbols())
        return d

    def _write(self, kind: str, arm_id: str, task_id: str, seed, payload: dict) -> None:
        if self.out_dir is None:
            return
        d = self.out_dir / kind
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{arm_id}_{task_id}_s{seed}.json").write_text(json.dumps(payload, indent=1, default=str))

    def run_cell(self, arm_id: str, task, seed: int | None = None) -> CellOutput:
        arm = self.arms[arm_id]
        lat: dict[str, list[float]] = {}
        with timed(lat, "L_e2e"):
            with timed(lat, "L_retrieve"):
                raw_ctx = arm.retrieve(task.query, self._seed_dict(arm_id, task, seed))
            ctx = finalize_context(raw_ctx, self.tok)
            prompt = arm.build_prompt(ctx, self.tok)
            prompt_tokens = self.tok.count(SYSTEM_PROMPT) + self.tok.count(prompt)
            ctx.build_meta["prompt_tokens"] = prompt_tokens
            ctx.build_meta["prompt_over_window"] = prompt_tokens + C.GENERATION_RESERVE > C.CONTEXT_WINDOW
            with timed(lat, "L_generate"):
                comp = self.llm(SYSTEM_PROMPT, prompt, max_tokens=C.GENERATION_RESERVE, seed=seed, purpose="answer")
        for k, v in (raw_ctx.build_meta.get("latency_ms") or {}).items():
            if k not in lat:
                lat[k] = list(v)
        raw = {"bundle": ctx.to_dict(),
               "completion": {"text": comp.text, "generation_tokens": comp.completion_tokens,
                              "latency_seconds": comp.latency_seconds, "prompt_tokens_server": comp.prompt_tokens,
                              "finish_reason": comp.finish_reason, "model": comp.model}}
        self._write("bundles", arm_id, task.task_id, seed, {"bundle": raw["bundle"], "prompt": prompt})
        self._write("completions", arm_id, task.task_id, seed, raw["completion"])
        ctx2, ans = adapt(arm_id, raw, task)
        result = score(task, ctx2, ans, symbol_cache=self.symbol_cache, latency_profile=latency_profile(lat), seed=seed)
        return CellOutput(arm_id, task.task_id, seed, ctx2, ans, result, prompt_tokens)
