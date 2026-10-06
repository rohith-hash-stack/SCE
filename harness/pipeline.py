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
from harness.llm import Completion
from harness.scoring.adapters import adapt, finalize_context
from harness.scoring.canonical import DeliveredContext, NormalizedAnswer
from harness.scoring.latency import latency_profile, timed
from harness.scoring.scorer import ScoreResult, score


#: a server prompt count below this fraction of the harness's own count is
#: treated as server-side truncation
SERVER_SHORTFALL_RATIO = 0.95


def class_ancestors(builder) -> dict[str, frozenset[str]]:
    """class FQN -> every ancestor class FQN (transitive), from the code
    graph's EXTENDS edges (subclass -> base)."""
    parents: dict[str, set[str]] = {}
    for u, v, data in builder.graph.edges(data=True):
        if data.get("relation") == "EXTENDS":
            parents.setdefault(u, set()).add(v)
    out: dict[str, frozenset[str]] = {}
    for cls in parents:
        seen: set[str] = set()
        stack = list(parents[cls])
        while stack:
            p = stack.pop()
            if p not in seen:
                seen.add(p)
                stack.extend(parents.get(p, ()))
        out[cls] = frozenset(seen)
    return out


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
                 symbol_cache: frozenset[str] | None = None, ancestors: dict | None = None) -> None:
        self.arms = arms
        self.llm = llm
        self.tok = tokenizer
        self.out_dir = Path(out_dir) if out_dir else None
        self.symbol_cache = symbol_cache
        #: class FQN -> ancestor FQNs, for the T2 inherited-gold diagnostic
        self.ancestors = ancestors
        #: the stage the current cell is in, read by callers when a cell fails
        self.step = "idle"

    def _seed_dict(self, arm_id: str, task, seed: int | None) -> dict:
        d = {**task.seed_dict(), "llm_seed": seed}
        if arm_id == "oracle":
            # the ground truth reaches the oracle only
            d["oracle_pipeline"] = list(task.ground_truth.pipeline_symbols)
            d["oracle_universe"] = sorted(task.ground_truth.universe_symbols())
            if task.task_type == "T5_blast_radius":
                # where each gold name is defined, for names PRISM's symbol table lacks
                from harness.tasks.loaders import gold_locations
                locs = gold_locations(task.repo_id)
                d["oracle_locations"] = {g: locs[g] for g in task.ground_truth.pipeline_symbols if g in locs}
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
            self.step = "retrieve"
            with timed(lat, "L_retrieve"):
                raw_ctx = arm.retrieve(task.query, self._seed_dict(arm_id, task, seed))
            self.step = "budget"
            ctx = finalize_context(raw_ctx, self.tok)
            self.step = "prompt"
            own = ctx.build_meta.get("agent_answer") if getattr(arm, "self_answering", False) else None
            if own is not None:
                # Arm 4 answered through its own answer tool: no separate answer
                # call. Its last turn's prompt is what the model answered from.
                prompt = ""
                prompt_tokens = int(ctx.build_meta.get("agent_prompt_tokens", 0))
            else:
                prompt = arm.build_prompt(ctx, self.tok)
                prompt_tokens = self.tok.count(SYSTEM_PROMPT) + self.tok.count(prompt)
            ctx.build_meta["prompt_tokens"] = prompt_tokens
            ctx.build_meta["prompt_over_window"] = prompt_tokens + C.GENERATION_RESERVE > C.CONTEXT_WINDOW
            self.step = "generate"
            with timed(lat, "L_generate"):
                if own is not None:
                    comp = Completion(own["text"], int(own.get("prompt_tokens_server") or 0),
                                      int(own.get("generation_tokens") or 0), float(own.get("latency_seconds") or 0.0),
                                      model=own.get("model", ""), finish_reason=own.get("finish_reason", ""),
                                      purpose="answer_tool")
                else:
                    comp = self.llm(SYSTEM_PROMPT, prompt, max_tokens=C.GENERATION_RESERVE, seed=seed,
                                    purpose="answer")
        # The server counts the chat template too, so it should report at least
        # what we sent. Clearly fewer means the server cut the prompt (its
        # window was smaller than ours): flagged, never silently scored.
        ctx.build_meta["server_prompt_tokens"] = comp.prompt_tokens
        ctx.build_meta["server_prompt_shortfall"] = bool(
            comp.prompt_tokens and comp.prompt_tokens < SERVER_SHORTFALL_RATIO * prompt_tokens)
        for k, v in (raw_ctx.build_meta.get("latency_ms") or {}).items():
            if k not in lat:
                lat[k] = list(v)
        raw = {"bundle": ctx.to_dict(),
               "completion": {"text": comp.text, "generation_tokens": comp.completion_tokens,
                              "latency_seconds": comp.latency_seconds, "prompt_tokens_server": comp.prompt_tokens,
                              "finish_reason": comp.finish_reason, "model": comp.model}}
        self._write("bundles", arm_id, task.task_id, seed, {"bundle": raw["bundle"], "prompt": prompt})
        self._write("completions", arm_id, task.task_id, seed, raw["completion"])
        self.step = "adapt"
        ctx2, ans = adapt(arm_id, raw, task)
        self.step = "score"
        result = score(task, ctx2, ans, symbol_cache=self.symbol_cache, latency_profile=latency_profile(lat), seed=seed,
                       ancestors=self.ancestors)
        self.step = "done"
        return CellOutput(arm_id, task.task_id, seed, ctx2, ans, result, prompt_tokens)
