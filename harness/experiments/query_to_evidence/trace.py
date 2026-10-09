"""Stage tracing for one question through the actual Arm 5 pipeline.

`Tracer` records what the two engine stages Arm 5 calls return
(`build_candidate_manifest`, `retrieve_requested`) by shadowing them on the
engine *instance* with wrappers that call the original and keep a copy of its
result. It never changes arguments or results; `detach()` restores the
instance. Everything after hydration is taken from the harness's own
objects: Arm 5's DeliveredContext, `finalize_context` (the harness budget
trim) and `build_prompt` (the model input) - the same calls
`harness.pipeline.Pipeline.run_cell` makes, in the same order.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from harness.arms.base import RESPONSE_CONTRACTS, SYSTEM_PROMPT
from harness.llm import Completion
from harness.scoring.adapters import finalize_context


class Cl100kCounter:
    """Harness tokenizer stand-in. The benchmark re-counts with Qwen's
    tokenizer, which is not available offline; this counts with Prism's
    exact cl100k encoding instead. It only affects `finalize_context`'s trim,
    and the report records how close each case came to the limit."""

    name = "cl100k (offline stand-in for the Qwen harness tokenizer)"

    def count(self, text: str) -> int:
        from prism.slicer.tokenizer import count_tokens
        return count_tokens(text) if text else 0


@dataclass
class Trace:
    manifests: list[dict] = field(default_factory=list)     # {seed, direction, text, universe, rows}
    hydrations: list[dict] = field(default_factory=list)    # {seed, requested, nodes, edges, causal_path, skipped}

    @property
    def manifest(self) -> dict | None:
        return self.manifests[0] if self.manifests else None

    @property
    def hydration(self) -> dict | None:
        return self.hydrations[0] if self.hydrations else None


class Tracer:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.trace = Trace()
        build, retrieve = engine.build_candidate_manifest, engine.retrieve_requested
        trace = self.trace

        def traced_build(seed_id, *args, **kwargs):
            text, universe = build(seed_id, *args, **kwargs)
            rows = [line.split("|")[:2] for line in text.splitlines() if "|" in line]
            trace.manifests.append({"seed": seed_id, "direction": kwargs.get("direction", "downstream"),
                                    "text": text, "universe": sorted(universe), "rows": rows})
            return text, universe

        def traced_retrieve(seed_id, budget_tokens, requested_symbols, candidate_universe, *args, **kwargs):
            pkg, skipped = retrieve(seed_id, budget_tokens, requested_symbols, candidate_universe, *args, **kwargs)
            trace.hydrations.append({
                "seed": seed_id, "budget": budget_tokens, "requested": list(requested_symbols), "skipped": list(skipped),
                "nodes": [{"id": n.id, "role": n.role, "compression": n.compression, "cost": n.cost,
                           "distance": n.distance} for n in pkg.nodes],
                "edges": [{"from": e.from_node, "to": e.to_node, "type": e.type} for e in pkg.edges],
                "causal_path": [s.symbol for s in pkg.causal_path.stages] if pkg.causal_path else None,
                "warnings": [w.code for w in pkg.warnings],
            })
            return pkg, skipped

        engine.build_candidate_manifest = traced_build
        engine.retrieve_requested = traced_retrieve

    def detach(self) -> None:
        for name in ("build_candidate_manifest", "retrieve_requested"):
            self.engine.__dict__.pop(name, None)


class ReplayLLM:
    """Returns stored model turns by purpose (seeded controls with a stored
    Turn 1); no network."""

    model = "replay"

    def __init__(self, turns: list[dict]) -> None:
        self.turns = {t["purpose"]: t for t in turns}

    def __call__(self, system, user, max_tokens=0, seed=None, purpose=""):
        t = self.turns.get(purpose)
        if t is None:
            raise RuntimeError(f"no stored turn for purpose {purpose!r}")
        return Completion(t["text"], t.get("prompt_tokens", 0), t.get("completion_tokens", 0),
                          t.get("latency_seconds", 0.0), model=t.get("model"), finish_reason=t.get("finish_reason"),
                          purpose=purpose)


class AllRowsLLM:
    """Turn-1 stand-in when no model is available: requests every manifest
    row. An upper bound on Turn-1 selection, labelled as such in results -
    not a production policy."""

    model = "all_rows_upper_bound"

    def __call__(self, system, user, max_tokens=0, seed=None, purpose=""):
        import json
        start, end = user.find("<candidate_index>"), user.find("</candidate_index>")
        rows = [ln.split("|", 1)[0] for ln in user[start:end].splitlines()[1:] if "|" in ln] if start >= 0 else []
        return Completion(json.dumps({"requested_symbols": rows}), 0, 0, 0.0, model=self.model,
                          finish_reason="stop", purpose=purpose)


def run_case(arm, query: str, seed_dict: dict, tokenizer, trace: bool = True) -> dict:
    """Arm 5 retrieve -> harness budget trim -> model input, exactly as
    `Pipeline.run_cell` orders them. Returns everything needed for
    accounting (and the raw objects, for equivalence checks)."""
    tracer = Tracer(arm.engine) if trace and getattr(arm, "engine", None) is not None else None
    t0 = time.perf_counter()
    try:
        raw = arm.retrieve(query, seed_dict)
    finally:
        if tracer is not None:
            tracer.detach()
    retrieve_ms = (time.perf_counter() - t0) * 1000
    ctx = finalize_context(raw, tokenizer)
    task_type = ctx.build_meta.get("task_type")
    prompt = arm.build_prompt(ctx, tokenizer) if task_type in RESPONSE_CONTRACTS else None
    return {
        "trace": tracer.trace if tracer is not None else None,
        "raw_items": [{"symbols": list(i.symbols), "kind": i.kind, "source_id": i.source_id} for i in raw.items],
        "items": [{"symbols": list(i.symbols), "kind": i.kind, "source_id": i.source_id, "content": i.content,
                   "tokens": i.token_count} for i in ctx.items],
        "build_meta": ctx.build_meta,
        "prompt": prompt,
        "model_input": (SYSTEM_PROMPT + "\n\n" + prompt) if prompt is not None else None,
        "prompt_tokens": (tokenizer.count(SYSTEM_PROMPT) + tokenizer.count(prompt)) if prompt is not None else None,
        "retrieve_ms": round(retrieve_ms, 1),
        "budget": ctx.budget_tokens,
    }
