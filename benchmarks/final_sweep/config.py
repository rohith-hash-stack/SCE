"""Frozen protocol parameters and the arm registry for the 8-arm sweep.

Every value a reviewer might ask "what was it set to" lives here, once,
and is copied into every logged cell (`runner.CellRecord`) so a result
file is self-describing without this source tree.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

#: Bumped whenever a change here or in `arms.py`/`context_ops.py` could
#: change what a cell measures - logged per cell, so rows produced by
#: different harness revisions are never silently pooled.
HARNESS_VERSION = "final-sweep-8arms/1.1"

#: Pinned snapshot, never the floating `gpt-4o-mini` alias.
DEFAULT_MODEL = "gpt-4o-mini-2024-07-18"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_API_KEY_ENV = "OPENAI_API_KEY"

#: Non-zero on purpose: at 0.0 repeated seeds collapse to near-identical
#: samples and the seed axis stops estimating variance. The tRPC pilot ran
#: at 0.2 and only 0-3/25 tasks per arm changed outcome across seeds, so
#: the full sweep uses 0.4.
DEFAULT_TEMPERATURE = 0.4

#: Retrieval budget every arm packs against.
DEFAULT_BUDGET = 8000
#: Hard ceiling on the rendered context's real token count, enforced by
#: the harness after every arm has built its package. Sits above
#: `DEFAULT_BUDGET` by the same slack `prism.surface.build` already allows
#: its own render (5%) plus XML envelope overhead, so an arm that honestly
#: packs to budget is not clipped by the ceiling for markup alone -
#: anything the ceiling does drop is logged (`n_ceiling_dropped`).
DEFAULT_TOKEN_CEILING = 8800

#: 10 sampling seeds per (task, arm).
DEFAULT_SEEDS: tuple[int, ...] = tuple(range(42, 52))
PILOT_SEEDS: tuple[int, ...] = (42, 43)

DEFAULT_MAX_TOKENS = 2048

#: Default distractor dose for `prism_plus_distractors`. Further dose
#: levels are separate arms, spelled `prism_plus_distractors_k<N>`.
DEFAULT_DISTRACTOR_K = 8

#: The four corpora of the final sweep.
FINAL_SWEEP_REPOS: tuple[str, ...] = ("fastapi", "django", "express", "trpc")

AnchorMode = Literal["taxonomy", "lexical"]
AxisPolicy = Literal["all", "none", "no_substance"]


@dataclass(frozen=True)
class ArmSpec:
    engine_id: str
    kind: Literal["bfs", "oracle", "two_pass", "pagerank"]
    #: oracle arms only: include the deterministic AST scaffold.
    scaffolded: bool = False
    #: two-pass arms only.
    anchor: AnchorMode = "taxonomy"
    axes: AxisPolicy = "all"
    distractor_k: int = 0
    description: str = ""


ARMS: dict[str, ArmSpec] = {
    spec.engine_id: spec
    for spec in (
        ArmSpec(
            "baseline_bfs_bidirectional", "bfs",
            description="Unpruned bidirectional AST call-graph BFS, greedy-filled to budget (floor).",
        ),
        ArmSpec(
            "pragmatic_oracle", "oracle",
            description="Exactly the adjudicated pipeline_symbols - the strict minimal ground truth.",
        ),
        ArmSpec(
            "scaffolded_oracle", "oracle", scaffolded=True,
            description="pipeline_symbols + annotated required_context + deterministic AST scaffold "
            "(referenced type declarations, validators, factory/builder initializers).",
        ),
        ArmSpec(
            "prism_full", "two_pass", anchor="taxonomy", axes="all",
            description="Two-pass: taxonomy anchor (task seed), 4-axis annotated manifest, k-hop expansion.",
        ),
        ArmSpec(
            "ablation_lexical_anchors", "two_pass", anchor="lexical", axes="all",
            description="Two-pass with the anchor picked by BM25 over the task prompt; 4-axis metadata kept.",
        ),
        ArmSpec(
            "ablation_signature_only", "two_pass", anchor="taxonomy", axes="none",
            description="Two-pass with taxonomy anchor; manifest and context carry raw signatures, no 4-axis annotations.",
        ),
        ArmSpec(
            "ablation_no_purity", "two_pass", anchor="taxonomy", axes="no_substance",
            description="prism_full with the purity/side-effect axis (Substance) suppressed everywhere.",
        ),
        ArmSpec(
            "prism_plus_distractors", "two_pass", anchor="taxonomy", axes="all", distractor_k=DEFAULT_DISTRACTOR_K,
            description=f"prism_full's context plus {DEFAULT_DISTRACTOR_K} fixed, unrelated repository functions.",
        ),
    )
}

ARM_ORDER: tuple[str, ...] = tuple(ARMS)

#: Arms outside the frozen 8-arm protocol: resolvable by name via
#: `--arms`, never included in `--arms all` or in `ARM_ORDER`.
EXPERIMENTAL_ARMS: dict[str, ArmSpec] = {
    spec.engine_id: spec
    for spec in (
        ArmSpec(
            "baseline_pagerank_repomap", "pagerank",
            description="Aider-style repo map: PageRank over the AST symbol graph, personalized toward "
            "identifiers named in the task prompt; top signatures packed to a 4,000-token budget.",
        ),
    )
}

_DOSE_ARM_RE = re.compile(r"^prism_plus_distractors_k(\d+)$")


def resolve_arm(engine_id: str) -> ArmSpec:
    """`ARMS[engine_id]`, or a dose-variant `prism_plus_distractors_k<N>`
    built on the fly - so a dose-response sweep adds arms by name alone."""
    if engine_id in ARMS:
        return ARMS[engine_id]
    if engine_id in EXPERIMENTAL_ARMS:
        return EXPERIMENTAL_ARMS[engine_id]
    match = _DOSE_ARM_RE.match(engine_id)
    if match:
        k = int(match.group(1))
        return ArmSpec(
            engine_id, "two_pass", anchor="taxonomy", axes="all", distractor_k=k,
            description=f"prism_full's context plus {k} fixed, unrelated repository functions.",
        )
    raise ValueError(f"unknown arm {engine_id!r} - known: {list(ARMS)} (or prism_plus_distractors_k<N>)")
