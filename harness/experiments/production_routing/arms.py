"""Arm 5 variants for the production-routing ablation. Both subclass the
unmodified `Arm5Prism`, keep its arm id, prompt, Turn-1 parse, Turn-2
hydration and answer turn, and only add `build_meta` fields.

ProductionRoutingArm5 (config R2): the manifest's direction comes from the
    query text (`routing.route_manifest`); the task-type label is never read
    for routing.
RuleSelectorArm5 (config R0): on T5 seeds, Turn 1 requests every `caller` row
    of the (unchanged) manifest instead of calling the model; other task
    types call the model as usual.
"""
from __future__ import annotations

import json

from harness.arms.arm5_prism import Arm5Prism
from harness.experiments.production_routing.routing import UPSTREAM_ROLES, RoutedEngine
from harness.llm import Completion

T5 = "T5_blast_radius"


def _manifest_rows(user_prompt: str) -> list[tuple[str, str]]:
    start, end = user_prompt.find("<candidate_index>"), user_prompt.find("</candidate_index>")
    if start < 0 or end < 0:
        return []
    rows = []
    for ln in user_prompt[start:end].split("\n")[1:]:
        parts = ln.split("|")
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows


def _selection_meta(rows: list[tuple[str, str]], requested: list[str], seed: str) -> dict:
    """Caller share of the manifest and of the Turn-1 picks (picks that name a
    manifest row; the seed excluded) - logs whether a caller-heavy manifest
    moved the model's picks."""
    role = {q: r for q, r in rows if q != seed}
    picks = [q for q in dict.fromkeys(requested) if q in role]
    return {"manifest_rows": len(role),
            "manifest_caller_fraction": (sum(r in UPSTREAM_ROLES for r in role.values()) / len(role)) if role else None,
            "turn1_picks_in_manifest": len(picks),
            "turn1_picks_caller_fraction": (sum(role[q] in UPSTREAM_ROLES for q in picks) / len(picks)) if picks else None}


class _RecordingLLM:
    """Pass-through LLM wrapper that remembers the last Turn-1 user prompt."""

    def __init__(self, llm) -> None:
        self.llm = llm
        self.turn1_user = ""

    def __call__(self, system, user, max_tokens=0, seed=None, purpose=""):
        if purpose == "turn1":
            self.turn1_user = user
        return self.llm(system, user, max_tokens=max_tokens, seed=seed, purpose=purpose)


class ProductionRoutingArm5(Arm5Prism):
    def index(self, repo_path: str, config: dict | None = None) -> None:
        super().index(repo_path, config)
        if not isinstance(self.engine, RoutedEngine):
            self.engine = RoutedEngine(self.engine, self.budget)

    def retrieve(self, query: str, seed: dict):
        if not isinstance(self.engine, RoutedEngine):
            self.engine = RoutedEngine(self.engine, self.budget)
        self.engine.query = query                     # the router sees the query text only
        self.engine.last = None
        real_llm, recorder = self.llm, _RecordingLLM(self.llm)
        self.llm = recorder
        try:
            ctx = super().retrieve(query, seed)
        finally:
            self.llm = real_llm
        routed = self.engine.last
        meta = ctx.build_meta
        meta["production_routing"] = True
        meta["prism_blast_mode"] = None                # label-driven mode is not used under routing
        if routed is not None:
            meta.update({"routing_intent": routed.intent, "routing_confidence": routed.confidence,
                         "routing_policy": routed.policy,
                         "routing_evidence": {"blast": routed.evidence.get("blast"), "local": routed.evidence.get("local")}})
        meta.update(_selection_meta(_manifest_rows(recorder.turn1_user), meta.get("requested_symbols") or [],
                                    seed.get("seed_symbol") or ""))
        return ctx


class _CallerRuleLLM:
    """Turn 1 on a T5 seed: request every `caller` row shown in the manifest.
    Any other call (Turn 2b) goes to the real model."""

    def __init__(self, llm) -> None:
        self.llm = llm
        self.turn1_user = ""

    def __call__(self, system, user, max_tokens=0, seed=None, purpose=""):
        if purpose != "turn1":
            return self.llm(system, user, max_tokens=max_tokens, seed=seed, purpose=purpose)
        self.turn1_user = user
        picks = [q for q, r in _manifest_rows(user) if r in UPSTREAM_ROLES]
        text = json.dumps({"thought_process": "rule: every caller row", "requested_symbols": picks})
        return Completion(text, 0, 0, 0.0, model="rule_callers", finish_reason="stop", purpose=purpose)


class RuleSelectorArm5(Arm5Prism):
    def retrieve(self, query: str, seed: dict):
        if seed.get("task_type") != T5:
            return super().retrieve(query, seed)
        real_llm, rule = self.llm, _CallerRuleLLM(self.llm)
        self.llm = rule
        try:
            ctx = super().retrieve(query, seed)
        finally:
            self.llm = real_llm
        meta = ctx.build_meta
        meta["turn1_selector"] = "rule_callers"
        meta.update(_selection_meta(_manifest_rows(rule.turn1_user), meta.get("requested_symbols") or [],
                                    seed.get("seed_symbol") or ""))
        return ctx
