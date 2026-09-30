"""Tests for `benchmarks.final_sweep` - the 8-arm final empirical sweep.

Pure-function tests for the context operations each ablation relies on,
plus an end-to-end sweep over the small `tests/fixtures/python_repo`
corpus with a scripted fake LLM client (no network, no cost) that
checks every arm produces schema-valid records.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.final_sweep import config as C
from benchmarks.final_sweep.arms import CorpusState
from benchmarks.final_sweep.context_ops import (
    annotate_manifest_axes,
    enforce_token_ceiling,
    lexical_tokens,
    redact_package_axes,
    select_distractors,
)
from benchmarks.final_sweep.runner import (
    SweepConfig,
    load_records,
    run_sweep,
    turn1_system_prompt,
    validate_record,
)
from benchmarks.final_sweep.summary import render_markdown, summarize, wilson_ci
from benchmarks.ground_truth.schema import EvaluationTask, GroundTruthAnnotation
from benchmarks.openai_client import CallResult, LLMCallError
from benchmarks.run_two_pass_benchmark import TURN1_SYSTEM_PROMPT

FIXTURE_REPO = str(Path(__file__).parent / "fixtures" / "python_repo")
SEED_SYMBOL = "src.controllers.checkout.CheckoutController.process_checkout"
PIPELINE = [
    SEED_SYMBOL,
    "src.auth.jwt.verify_session",
    "src.services.billing.PaymentProcessor.charge",
    "src.repositories.orders.OrderRepository.mark_paid",
]


def _task() -> EvaluationTask:
    ann = GroundTruthAnnotation(annotator_id="a", pipeline_symbols=PIPELINE, expected_solution="x")
    return EvaluationTask(
        task_id="fixture_t02_001_checkout",
        repo="django",
        pinned_commit="0" * 40,
        seed_symbol=SEED_SYMBOL,
        task_type="debug",
        prompt="Trace how CheckoutController.process_checkout verifies the session, charges and marks the order paid.",
        annotation_a=ann,
        annotation_b=ann,
        adjudicated=ann,
        cohen_kappa=1.0,
    )


class FakeClient:
    """Answers Turn 1 by requesting the ground-truth pipeline and the
    answer turn with the pipeline in order; `fail_engine` makes every
    call for that arm raise, to exercise per-cell error handling."""

    def __init__(self, fail_engine: str | None = None) -> None:
        self.calls: list[dict] = []
        self.fail_engine = fail_engine

    def complete(self, model=None, system="", user="", temperature=0.0, max_tokens=None, seed=None,
                 task_id=None, engine=None, extra_body=None):
        self.calls.append({"engine": engine, "seed": seed, "temperature": temperature, "system": system, "user": user,
                           "extra_body": extra_body})
        if self.fail_engine and engine.startswith(self.fail_engine + "/"):
            raise LLMCallError("scripted failure")
        if engine.endswith("/turn1"):
            content = json.dumps({"thought_process": "x", "requested_symbols": PIPELINE})
        else:
            content = "```json\n" + json.dumps({"reasoning": "x", "symbols": PIPELINE}) + "\n```"
        return CallResult(
            model=model, content=content, prompt_tokens=1000, completion_tokens=100, total_tokens=1100,
            cost_usd=0.00021, latency_seconds=0.01, seed=seed,
            system_fingerprint="fp_test", response_model="gpt-4o-mini-2024-07-18",
        )


@pytest.fixture(scope="module")
def corpus() -> CorpusState:
    return CorpusState("django", FIXTURE_REPO)


# --------------------------------------------------------------------- #
# Arm registry / prompts
# --------------------------------------------------------------------- #

def test_registry_has_the_eight_arms_in_protocol_order():
    assert list(C.ARM_ORDER) == [
        "baseline_bfs_bidirectional", "pragmatic_oracle", "scaffolded_oracle", "prism_full",
        "ablation_lexical_anchors", "ablation_signature_only", "ablation_no_purity", "prism_plus_distractors",
    ]


def test_dose_variant_arms_resolve_and_unknown_arms_fail():
    spec = C.resolve_arm("prism_plus_distractors_k10")
    assert (spec.kind, spec.distractor_k, spec.axes, spec.anchor) == ("two_pass", 10, "all", "taxonomy")
    with pytest.raises(ValueError):
        C.resolve_arm("prism_v99")


def test_signature_only_turn1_prompt_is_the_production_prompt_verbatim():
    assert turn1_system_prompt("none") == TURN1_SYSTEM_PROMPT
    assert "axes=" in turn1_system_prompt("all") and "Substance" in turn1_system_prompt("all")
    assert "Substance" not in turn1_system_prompt("no_substance")


# --------------------------------------------------------------------- #
# Axis annotation / redaction
# --------------------------------------------------------------------- #

def test_manifest_axis_annotation_policies(corpus):
    manifest, _universe = corpus.prism.build_candidate_manifest(SEED_SYMBOL)
    assert annotate_manifest_axes(manifest, corpus.feature_masks, "none") == manifest
    full = annotate_manifest_axes(manifest, corpus.feature_masks, "all")
    no_purity = annotate_manifest_axes(manifest, corpus.feature_masks, "no_substance")
    rows = [line for line in full.splitlines() if not line.startswith("<")]
    assert rows and all("|axes=S:" in r and ";F:" in r and ";O:" in r and ";R:" in r for r in rows)
    assert "S:" not in no_purity and "|axes=F:" in no_purity
    # Row count and wrapper lines are untouched.
    assert len(full.splitlines()) == len(manifest.splitlines())


def test_package_redaction_removes_the_suppressed_axis_everywhere(corpus):
    pkg, _ = corpus.prism.retrieve_requested(SEED_SYMBOL, 4000, PIPELINE, set(PIPELINE))
    assert any(n.features.substance != "NONE" for n in pkg.nodes)
    none = redact_package_axes(pkg, "none")
    assert all((n.features.substance, n.features.form, n.features.output, n.features.role) == ("NONE",) * 4 for n in none.nodes)
    assert all(n.signature.returns is None or n.signature.returns.kind == "Unknown" for n in none.nodes)
    assert none.coverage.features == []
    no_purity = redact_package_axes(pkg, "no_substance")
    assert all(n.features.substance == "NONE" for n in no_purity.nodes)
    assert [n.features.form for n in no_purity.nodes] == [n.features.form for n in pkg.nodes]
    assert not any(f.id.startswith("SINK_") for f in no_purity.coverage.features)
    assert redact_package_axes(pkg, "all") is pkg


# --------------------------------------------------------------------- #
# Anchors, distractors, ceiling
# --------------------------------------------------------------------- #

def test_lexical_tokens_split_identifiers():
    tokens = lexical_tokens("createRouterFactory process_checkout core.router")
    for expected in ("createrouterfactory", "create", "router", "factory", "process_checkout", "process", "checkout", "core"):
        assert expected in tokens


def test_lexical_anchor_finds_the_named_method(corpus):
    static = corpus.task_static(_task())
    assert static.lexical_anchor[0] == SEED_SYMBOL


def test_distractors_are_deterministic_nested_and_unrelated(corpus):
    related = {SEED_SYMBOL}
    five = select_distractors(corpus.builder, "django", "t1", related, 5)
    assert five == select_distractors(corpus.builder, "django", "t1", related, 5)
    assert select_distractors(corpus.builder, "django", "t1", related, 2) == five[:2]
    assert SEED_SYMBOL not in five
    # Direct neighbors of a related symbol are excluded.
    assert "src.auth.jwt.verify_session" not in five


def test_ceiling_drops_distractors_first_and_never_the_seed(corpus):
    pkg, _ = corpus.prism.retrieve_requested(SEED_SYMBOL, 4000, PIPELINE, set(PIPELINE))
    victim = next(n.id for n in pkg.nodes if n.role != "seed")
    trimmed, dropped, before, after = enforce_token_ceiling(pkg, 10**6, {victim})
    assert dropped == [] and before == after and trimmed is pkg
    trimmed, dropped, before, after = enforce_token_ceiling(pkg, before - 1, {victim})
    assert dropped[0] == victim and after < before
    trimmed, dropped, _, _ = enforce_token_ceiling(pkg, 1)
    assert [n.role for n in trimmed.nodes] == ["seed"]


# --------------------------------------------------------------------- #
# Schema + summary
# --------------------------------------------------------------------- #

def test_validate_record_flags_non_binary_tsr_and_bad_rates():
    from benchmarks.final_sweep.runner import CellRecord
    import dataclasses

    row = dataclasses.asdict(CellRecord(repo="r", task_id="t", engine_id="e", seed=1, budget=1, token_ceiling=1))
    row.update(status="dry_run")
    assert validate_record(row) == []
    row.update(tsr=2, cleanliness=1.5)
    problems = validate_record(row)
    assert any("not binary" in p for p in problems) and any("cleanliness" in p for p in problems)


def test_wilson_ci_brackets_the_estimate():
    lo, hi = wilson_ci(7, 10)
    assert lo < 0.7 < hi and 0 <= lo and hi <= 1
    assert wilson_ci(0, 0) is None


# --------------------------------------------------------------------- #
# End to end
# --------------------------------------------------------------------- #

def test_end_to_end_sweep_all_arms(tmp_path, corpus):
    client = FakeClient()
    cfg = SweepConfig(budget=4000, token_ceiling=4400)
    rows = run_sweep(
        "django", list(C.ARM_ORDER), (42, 43), tmp_path, cfg, workers=2,
        client=client, corpus=corpus, tasks=[_task()],
    )
    assert len(rows) == 16
    for row in rows:
        assert validate_record(row) == [], (row["engine_id"], validate_record(row))
        assert row["status"] == "ok", row["error"]
        assert row["system_fingerprint"] == "fp_test"
        assert row["temperature"] == pytest.approx(0.4)
    by_arm = {r["engine_id"]: r for r in rows if r["seed"] == 42}
    # The scripted model answers perfectly from any context holding the pipeline.
    assert by_arm["pragmatic_oracle"]["tsr"] == 1 and by_arm["pragmatic_oracle"]["cleanliness"] == 1.0
    assert by_arm["prism_full"]["manifest_hash"] != by_arm["ablation_signature_only"]["manifest_hash"]
    assert by_arm["prism_full"]["n_llm_calls"] == 2 and by_arm["pragmatic_oracle"]["n_llm_calls"] == 1
    # Distractor arm reuses prism_full's Turn 1 and makes only the answer call.
    assert by_arm["prism_plus_distractors"]["turn1_reused_from"] == "prism_full"
    assert by_arm["prism_plus_distractors"]["n_llm_calls"] == 1
    assert by_arm["prism_plus_distractors"]["requested_symbols"] == by_arm["prism_full"]["requested_symbols"]
    # Every call used the protocol temperature.
    assert {c["temperature"] for c in client.calls} == {C.DEFAULT_TEMPERATURE} == {0.4}
    # Manifest hashes are seed-independent.
    assert {r["manifest_hash"] for r in rows if r["engine_id"] == "prism_full"} == {by_arm["prism_full"]["manifest_hash"]}
    assert (tmp_path / "cells.parquet").exists()
    summary = summarize(rows)
    assert summary["schema"]["n_invalid"] == 0
    assert summary["cost"]["llm_calls"] == sum(r["n_llm_calls"] for r in rows)
    assert "prism_plus_distractors" in render_markdown(summary)


def test_errors_are_recorded_and_resume_retries_only_them(tmp_path, corpus):
    cfg = SweepConfig(budget=4000, token_ceiling=4400)
    arms = ["pragmatic_oracle", "prism_full"]
    run_sweep("django", arms, (42,), tmp_path, cfg, workers=1, client=FakeClient(fail_engine="prism_full"),
              corpus=corpus, tasks=[_task()])
    records = load_records(tmp_path / "cells.jsonl")
    statuses = {r["engine_id"]: r["status"] for r in records.values()}
    assert statuses == {"pragmatic_oracle": "ok", "prism_full": "error"}
    assert all(validate_record(r) == [] for r in records.values())
    retry = FakeClient()
    run_sweep("django", arms, (42,), tmp_path, cfg, workers=1, client=retry, corpus=corpus, tasks=[_task()])
    assert {c["engine"].split("/")[0] for c in retry.calls} == {"prism_full"}
    assert {r["status"] for r in load_records(tmp_path / "cells.jsonl").values()} == {"ok"}


def test_token_rate_limiter_blocks_until_the_window_frees(monkeypatch):
    from benchmarks.final_sweep import runner

    clock = [0.0]
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runner.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    limiter = runner.TokenRateLimiter(1000)
    limiter.acquire(600)
    limiter.acquire(400)
    assert clock[0] == 0.0
    limiter.acquire(100)  # window full - must wait for the first event to age out
    assert clock[0] >= 60.0


# --------------------------------------------------------------------- #
# Local SLM (Ollama) support and 2-batch seed partitioning
# --------------------------------------------------------------------- #

OLLAMA = "http://localhost:11434/v1"
QWEN = "qwen2.5-coder:7b-instruct-q8_0"


def test_sampler_options_only_go_to_local_endpoints():
    from benchmarks.final_sweep.runner import sampler_kwargs

    remote = SweepConfig()
    assert sampler_kwargs(remote, "turn1") == {} and not remote.local
    local = SweepConfig(base_url=f"{OLLAMA},http://localhost:11435/v1", model=QWEN, num_ctx=16384)
    assert local.local and local.base_urls == [OLLAMA, "http://localhost:11435/v1"]
    assert sampler_kwargs(local, "answer") == {"extra_body": {"options": {"num_ctx": 16384}}}
    turn1 = sampler_kwargs(local, "turn1")["extra_body"]["options"]
    assert turn1["num_ctx"] == 16384 and turn1["repeat_penalty"] == 1.15 and turn1["num_predict"] == local.max_tokens


def test_cli_defaults_follow_openai_base_url_and_llm_model(monkeypatch):
    from benchmarks.final_sweep.runner import build_arg_parser

    monkeypatch.setenv("OPENAI_BASE_URL", OLLAMA)
    monkeypatch.setenv("LLM_MODEL", QWEN)
    args = build_arg_parser().parse_args(["--out", "x", "--seeds", "1,2,3,4,5"])
    assert (args.base_url, args.model, args.seeds) == (OLLAMA, QWEN, "1,2,3,4,5")


def test_local_client_needs_no_real_key_and_round_robins(monkeypatch):
    from benchmarks.final_sweep.runner import RoundRobinClient, build_client

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = build_client(SweepConfig(base_url=f"{OLLAMA},http://localhost:11435/v1", model=QWEN, tpm_limit=None))
    assert isinstance(client, RoundRobinClient)
    assert [c.base_url for c in client._clients] == [OLLAMA, "http://localhost:11435/v1"]


def test_batch_two_appends_new_seeds_without_rerunning_batch_one(tmp_path, corpus):
    cfg = SweepConfig(budget=4000, token_ceiling=4400, model=QWEN, base_url=OLLAMA)
    arms = list(C.ARM_ORDER)
    batch1 = FakeClient()
    run_sweep("django", arms, (1, 2), tmp_path, cfg, workers=2, client=batch1, corpus=corpus, tasks=[_task()])
    before = {k: r["finished_at"] + r["context_hash"] for k, r in load_records(tmp_path / "cells.jsonl").items()}
    assert len(before) == 16

    batch2 = FakeClient()
    rows = run_sweep("django", arms, (3, 4), tmp_path, cfg, workers=2, client=batch2, corpus=corpus, tasks=[_task()])
    assert {c["seed"] for c in batch2.calls} == {3, 4}  # batch 1 cells were not re-run
    after = load_records(tmp_path / "cells.jsonl")
    assert len(rows) == len(after) == 32
    assert {k: after[k]["finished_at"] + after[k]["context_hash"] for k in before} == before
    lines = (tmp_path / "cells.jsonl").read_text().splitlines()
    assert len(lines) == 32  # appended, one line per cell, nothing duplicated
    # Local endpoint: unmetered, and every call carried the Ollama options.
    assert all(r["cost_usd"] == 0.0 for r in rows)
    assert all(c["extra_body"]["options"]["num_ctx"] == cfg.num_ctx for c in batch2.calls)
    # Re-running batch 2 is a no-op.
    again = FakeClient()
    run_sweep("django", arms, (3, 4), tmp_path, cfg, workers=2, client=again, corpus=corpus, tasks=[_task()])
    assert again.calls == []


def test_resume_refuses_to_mix_models(tmp_path, corpus):
    run_sweep("django", ["pragmatic_oracle"], (1,), tmp_path, SweepConfig(budget=4000, token_ceiling=4400),
              workers=1, client=FakeClient(), corpus=corpus, tasks=[_task()])
    with pytest.raises(SystemExit, match="separate --out"):
        run_sweep("django", ["pragmatic_oracle"], (2,), tmp_path,
                  SweepConfig(budget=4000, token_ceiling=4400, model=QWEN, base_url=OLLAMA),
                  workers=1, client=FakeClient(), corpus=corpus, tasks=[_task()])


def test_load_records_skips_a_truncated_trailing_line(tmp_path):
    good = {"repo": "r", "task_id": "t", "engine_id": "e", "seed": 1, "budget": 8000, "status": "ok"}
    path = tmp_path / "cells.jsonl"
    path.write_text(json.dumps(good) + "\n" + json.dumps(good)[:25])
    assert list(load_records(path).values()) == [good]
