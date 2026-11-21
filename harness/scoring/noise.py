"""Noise-robustness infrastructure. Execution is deferred:
`config.NOISE_SWEEP_ENABLED` is False, and `require_enabled()` (called by
any sweep entry point) refuses to run until it is switched on. The
functions themselves are pure and tested.

A variant adds irrelevant context worth `level` x the original context's
tokens (levels 0.10 / 0.25 / 0.50), drawn from one of three pools:
- adjacent:    files in the same directories as the gold files
- same_domain: files whose names share a word with a gold file's name
- random:      any repository file
Noise is placed at the START and in the MIDDLE of the context, never at the
end (small models show primacy and recency effects; appending would test
position, not noise).
"""
from __future__ import annotations

import os
import random
import re
from dataclasses import dataclass, replace
from typing import Literal

import numpy as np

from harness import config as C
from harness.scoring.canonical import DeliveredItem

NoiseType = Literal["adjacent", "same_domain", "random"]
SOURCE_EXTS = (".py", ".ts", ".js", ".tsx")
NOISE_CHUNK_LINES = 60

FAILURE_REASONS = {
    "truncation": "prompt exceeded context window",
    "distraction": "model referenced noise token",
    "hallucination": "answer contained non-existent identifier",
    "refusal": "declined or empty",
    "wrong_tool": "Arm 4: wrong tool type",
    "loop": "Arm 4: exceeded turn budget",
    "format": "answer failed extraction",
}


@dataclass
class NoiseVariant:
    level: float
    noise_type: NoiseType
    injected_items: list[DeliveredItem]   # the full context: original + noise, re-ranked
    noise_tokens: int = 0
    original_tokens: int = 0


def require_enabled() -> None:
    if not C.NOISE_SWEEP_ENABLED:
        raise RuntimeError("noise sweep is disabled (config.NOISE_SWEEP_ENABLED = False)")


def _source_files(repo_root: str) -> list[str]:
    out: list[str] = []
    for d, dirs, files in os.walk(repo_root):
        dirs[:] = [x for x in dirs if not x.startswith(".") and x not in ("node_modules", "__pycache__")]
        out.extend(os.path.join(d, f) for f in files if f.endswith(SOURCE_EXTS))
    return sorted(out)


def _words(path: str) -> set[str]:
    stem = os.path.splitext(os.path.basename(path))[0]
    return {w for w in re.split(r"[_\-.]+", stem.lower()) if len(w) >= 3}


def noise_pool(repo_root: str, gold_files: set[str], noise_type: NoiseType) -> list[str]:
    gold_abs = {os.path.realpath(os.path.join(repo_root, f)) for f in gold_files}
    files = [f for f in _source_files(repo_root) if os.path.realpath(f) not in gold_abs]
    if noise_type == "adjacent":
        dirs = {os.path.dirname(g) for g in gold_abs}
        return [f for f in files if os.path.dirname(os.path.realpath(f)) in dirs]
    if noise_type == "same_domain":
        words = set().union(*(_words(g) for g in gold_abs)) if gold_abs else set()
        return [f for f in files if _words(f) & words]
    if noise_type == "random":
        return files
    raise ValueError(f"unknown noise type {noise_type!r}")


def inject_noise(gold_items: list[DeliveredItem], repo_root: str, target_fraction: float,
                 noise_type: NoiseType, tokenizer, gold_files: set[str] | None = None,
                 seed: int = 0) -> NoiseVariant:
    original = sorted(gold_items, key=lambda it: it.rank)
    orig_tokens = sum(it.token_count for it in original)
    target = int(round(orig_tokens * target_fraction))
    noise: list[DeliveredItem] = []
    got = 0
    if target > 0:
        rng = random.Random(seed)
        pool = noise_pool(repo_root, gold_files or set(), noise_type)
        rng.shuffle(pool)
        for path in pool:
            if got >= target:
                break
            try:
                lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue
            if not lines:
                continue
            start = rng.randrange(max(len(lines) - NOISE_CHUNK_LINES, 0) + 1)
            text = "\n".join(lines[start:start + NOISE_CHUNK_LINES])
            remaining = target - got
            if tokenizer.count(text) > remaining and hasattr(tokenizer, "truncate"):
                text = tokenizer.truncate(text, remaining)
            cost = tokenizer.count(text)
            if cost == 0:
                continue
            rel = os.path.relpath(path, repo_root)
            noise.append(DeliveredItem(
                source_id=f"{rel}:{start + 1}-{start + NOISE_CHUNK_LINES}", content=text, token_count=cost,
                rank=0, kind="code_chunk", symbols=[],
                provenance={"noise": True, "noise_type": noise_type, "level": target_fraction},
            ))
            got += cost
    # half the noise first, the rest in the middle of the original context
    head, mid = noise[: (len(noise) + 1) // 2], noise[(len(noise) + 1) // 2:]
    cut = len(original) // 2
    merged = head + original[:cut] + mid + original[cut:]
    ranked = [replace(it, rank=i) for i, it in enumerate(merged, start=1)]
    return NoiseVariant(level=target_fraction, noise_type=noise_type, injected_items=ranked,
                        noise_tokens=got, original_tokens=orig_tokens)


def _xy(tsr_by_level: dict[float, float]) -> tuple[np.ndarray, np.ndarray]:
    levels = sorted(tsr_by_level)
    return np.asarray(levels, dtype=float), np.asarray([tsr_by_level[lv] for lv in levels], dtype=float)


def noise_slope(tsr_by_level: dict[float, float]) -> float:
    """OLS slope of TSR against noise level (TSR per unit of noise fraction)."""
    x, y = _xy(tsr_by_level)
    if len(x) < 2:
        return float("nan")
    return float(np.polyfit(x, y, 1)[0])


def noise_breakpoint(tsr_by_level: dict[float, float], drop: float = 0.10) -> float:
    """Lowest noise level whose TSR is more than `drop` below the level-0
    TSR; NaN if no level drops that far."""
    base = tsr_by_level.get(0.0)
    if base is None:
        raise ValueError("tsr_by_level needs the 0.0 (no noise) level")
    for lv in sorted(k for k in tsr_by_level if k > 0):
        if base - tsr_by_level[lv] > drop:
            return float(lv)
    return float("nan")


def auc_noise(tsr_by_level: dict[float, float]) -> float:
    """Area under TSR(noise level), divided by the level range: in [0, 1]
    when TSR is."""
    x, y = _xy(tsr_by_level)
    if len(x) < 2 or x[-1] == x[0]:
        return float("nan")
    area = float(np.sum((y[1:] + y[:-1]) / 2 * np.diff(x)))
    return area / float(x[-1] - x[0])
