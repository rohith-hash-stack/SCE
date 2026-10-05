"""Fixes after the d087d1b Kaggle run: inherited-gold diagnostic, generation
diagnostics, secondary T2 lift, failure records, server truncation guard,
native Ollama client."""
import http.server
import json
import math
import threading

import pandas as pd
import pytest

from harness import config as C
from harness.llm import Completion, OllamaChatLLM
from harness.scoring.adapters import adapt
from harness.scoring.canonical import DeliveredContext, DeliveredItem, NormalizedAnswer
from harness.scoring.scorer import names_symbol_inherited, score
from harness.tasks.schema import GroundTruth, LocalizationTask

GOLD = ["fastapi.exception_handlers.request_validation_exception_handler",
        "fastapi.exceptions.ValidationException.errors", "fastapi.encoders.jsonable_encoder"]
ANCESTORS = {"fastapi.exceptions.RequestValidationError": frozenset({"fastapi.exceptions.ValidationException"})}


def _task(tmp_path, gold=GOLD, tid="t2"):
    return LocalizationTask(task_id=tid, repo_id="fastapi", repo_root=str(tmp_path), query="q",
                            ground_truth=GroundTruth(pipeline_symbols=list(gold)))


def _ans(task, syms, arm="oracle", finish="stop", reps=0):
    return NormalizedAnswer(arm, task.task_id, "", "", syms, "code_block", True, 5, 0.1,
                            finish_reason=finish, repetition_count=reps)


def test_inherited_gold_is_a_diagnostic_and_strict_stays_primary(tmp_path):
    """Kaggle d087d1b, oracle on t02_005: the answer named the subclass
    member RequestValidationError.errors for gold ValidationException.errors."""
    task = _task(tmp_path)
    named = ["fastapi.exception_handlers.request_validation_exception_handler",
             "fastapi.exceptions.RequestValidationError.errors", "fastapi.encoders.jsonable_encoder"]
    ctx = DeliveredContext("oracle", task.task_id, [], 0, 13_000, {})
    res = score(task, ctx, _ans(task, named), ancestors=ANCESTORS)
    assert res.tsr == 0.0                                                    # strict: primary
    assert res.task_specific["answer_names_inherited_gold"] == 1.0           # inherited: diagnostic
    assert math.isnan(score(task, ctx, _ans(task, named)).task_specific["answer_names_inherited_gold"])
    assert not names_symbol_inherited("fastapi.exceptions.ValidationException.errors",
                                      ["fastapi.exceptions.RequestValidationError.json"], ANCESTORS)  # other member
    assert not names_symbol_inherited("fastapi.exceptions.ValidationException.errors",
                                      ["fastapi.other.Unrelated.errors"], ANCESTORS)                 # not a subclass


def test_generation_diagnostics_flow_to_parquet(tmp_path):
    from harness.reporting.output_schema import read_parquet, to_frame, write_parquet
    task = _task(tmp_path, gold=["a.b"])
    loop = "```json\n{\"reasoning\": \"x\", \"symbols\": [" + ", ".join(['"a.b"'] * 50)   # cut off by the cap
    raw = {"bundle": DeliveredContext("arm1", task.task_id, [DeliveredItem("s", "c", 1, 1, "code_chunk", ["a.b"])],
                                      1, 13_000, {}).to_dict(),
           "completion": {"text": loop, "generation_tokens": 4096, "latency_seconds": 1.0, "finish_reason": "length"}}
    ctx, ans = adapt("arm1", raw, task)
    assert ans.finish_reason == "length" and ans.repetition_count == 49 and not ans.extraction_success
    res = score(task, ctx, ans)
    assert res.generation_capped is True and res.repetition_count == 49 and res.finish_reason == "length"
    back = read_parquet(write_parquet(to_frame([res]), tmp_path / "c.parquet")).iloc[0]
    assert back.finish_reason == "length" and bool(back.generation_capped) and back.repetition_count == 49
    ok = score(task, ctx, _ans(task, ["a.b"], arm="arm1"))
    assert ok.generation_capped is False and ok.repetition_count == 0


def test_generation_cap_and_window():
    assert C.GENERATION_RESERVE == 4096 and C.RETRIEVAL_BUDGET == 13_000
    assert C.CONTEXT_WINDOW >= C.SYSTEM_PROMPT_TOKENS + C.TASK_PROMPT_TOKENS + C.RETRIEVAL_BUDGET + C.GENERATION_RESERVE


def test_t2_primary_lift_na_and_secondary_lift_on_any_gold(tmp_path):
    from harness.reporting.summary import T2_LIFT_NOTE, summary_table
    results = []
    for i in range(4):
        task = _task(tmp_path, gold=["pkg.seed", f"pkg.stage{i}"], tid=f"t{i}")
        a0 = DeliveredContext("arm0", task.task_id, [], 0, 0, {})
        results.append(score(task, a0, _ans(task, ["pkg.seed"] if i < 2 else ["pkg.other"], arm="arm0")))
        a1 = DeliveredContext("arm1", task.task_id, [], 0, 13_000, {})
        results.append(score(task, a1, _ans(task, ["pkg.seed", f"pkg.stage{i}"], arm="arm1")))
    df = summary_table(results, n_reps=100)
    a1 = df[df.arm == "arm1"].iloc[0]
    assert math.isnan(a1.retrieval_lift)                                  # Arm 0 strict tsr = 0 -> N/A
    assert a1.retrieval_lift_note == T2_LIFT_NOTE and "N/A" in a1.retrieval_lift_note
    assert a1.retrieval_lift_any_gold == pytest.approx((1.0 - 0.5) / 0.5)   # any-gold: arm0 0.5, arm1 1.0


def test_failure_record_shape():
    from harness.kaggle_m1 import failure_record

    def deep(n):
        if n == 0:
            raise ModuleNotFoundError("No module named 'onnxruntime'")
        deep(n - 1)
    try:
        deep(6)
    except Exception as exc:  # noqa: BLE001
        rec = failure_record(exc, "retrieve", "Pipeline.run_cell(arm='arm1', task='t', seed=42)")
    assert set(rec) == {"type", "message", "traceback_tail", "step", "cmd"}
    assert rec["type"] == "ModuleNotFoundError" and rec["step"] == "retrieve"
    assert len(rec["traceback_tail"]) == 5 and rec["traceback_tail"][-1].startswith("ModuleNotFoundError")
    v = failure_record(None, "validate", "cmd", message="x missing", type_="ValidityError")
    assert v["traceback_tail"] == [] and v["type"] == "ValidityError"


def test_server_truncation_guard(tmp_path):
    from harness.arms import build_arm
    from harness.pipeline import Pipeline

    class Words:
        name = "words"
        def count(self, t): return len(t.split())

    def server_with_small_window(system, user, max_tokens=0, seed=None, purpose=""):
        return Completion('{"symbols": []}', 10, 3, 0.0, finish_reason="stop")   # counted far fewer than sent

    task = _task(tmp_path, gold=["a.b"])
    task.query = "word " * 200
    out = Pipeline({"arm0": build_arm("arm0")}, server_with_small_window, Words()).run_cell("arm0", task, seed=1)
    assert out.ctx.build_meta["server_prompt_shortfall"] is True and out.ctx.build_meta["server_prompt_tokens"] == 10


class _FakeOllama(http.server.BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _FakeOllama.seen.append((self.path, body))
        out = json.dumps({"model": body["model"], "message": {"role": "assistant", "content": "OK"},
                          "done_reason": "length", "prompt_eval_count": 42, "eval_count": 7}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def test_ollama_native_client_sets_window_and_cap():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        llm = OllamaChatLLM(base_url=f"http://127.0.0.1:{srv.server_address[1]}", model="m")
        c = llm("sys", "user", max_tokens=C.GENERATION_RESERVE, seed=42)
    finally:
        srv.shutdown()
    path, body = _FakeOllama.seen[-1]
    assert path == "/api/chat"
    assert body["options"] == {"num_ctx": C.CONTEXT_WINDOW, "num_predict": C.GENERATION_RESERVE,
                               "temperature": C.TEMPERATURE, "seed": 42}
    assert (c.text, c.prompt_tokens, c.completion_tokens, c.finish_reason) == ("OK", 42, 7, "length")


# ---- Arm 1 T2 diagnostic ----
def test_arm1_t2_diagnostic_separates_mapping_bug_from_retrieval_failure(tmp_path):
    from harness.scoring.arm1_t2_diagnostic import record, verdict
    task = _task(tmp_path, gold=["pkg.mod.target", "pkg.mod.other"])
    defined_but_unmapped = [{"source_id": "pkg/mod.py:1-2", "rank": 1, "symbols": [],          # the bug case
                             "content": "def target():\n    return other()"}]
    r = record(task, defined_but_unmapped)
    assert r["top5"] == {"mentioned": 2, "defined": 1, "in_symbols": 0}
    assert verdict([r]).startswith("MAPPING BUG")
    unrelated = [{"source_id": "docs/x.py:1-1", "rank": 1, "symbols": [], "content": "from pkg import thing"}]
    assert verdict([record(task, unrelated)]).startswith("RETRIEVAL FAILURE")
    assert set(r["retrieved_top5"][0]) == {"chunk_source_id", "chunk_symbols", "chunk_text"}


def test_kaggle_runner_writes_summary_wall_time_and_diagnostic(tmp_path, monkeypatch):
    import os
    import shutil
    if not os.path.isdir("/home/user/SCE/.benchmarks/corpora/fastapi"):
        pytest.skip("FastAPI checkout missing")
    if shutil.which("pyright-langserver") is None:
        pytest.skip("pyright-langserver missing (Arm 3)")
    from harness import kaggle_m1
    monkeypatch.setattr(C, "ARM1_T2_DIAGNOSTIC", True)
    # FastAPI is not pip-installed here; Pyright resolves it from the workspace root
    monkeypatch.setattr(C, "ARM3_REQUIRE_EDITABLE_INSTALL", False)
    code = kaggle_m1.main(["--dry-run", "--fake-encoders", "--real-tasks", "2", "--bootstrap-reps", "50",
                           "--out", str(tmp_path)])
    report = json.loads((tmp_path / "gate_report.json").read_text())
    assert code == 0 and report["exit_code"] == 0 and report["wall_seconds"] > 0
    summary = pd.read_parquet(tmp_path / "summary.parquet")
    t2 = summary[summary.task_type == "T2_localization"]
    assert {"retrieval_lift", "retrieval_lift_any_gold", "retrieval_lift_note"} <= set(summary.columns)
    assert set(t2.arm) == set(C.ACTIVE_ARMS)                    # every implemented arm (M2: + arm2, arm3)
    diag = json.loads((tmp_path / "arm1_t2_diagnostic.json").read_text())
    assert len(diag["records"]) == 3 and diag["verdict"]          # synthetic T2 + 2 real T2 tasks
    assert report["arm1_t2_diagnostic"] == diag["verdict"]


# ---- summary partitioned by gate (synthetic Gate A never pools with real Gate B) ----
def test_gate_summary_keeps_synthetic_and_real_t2_apart(tmp_path):
    from harness.kaggle_m1 import gate_summary
    from harness.reporting.summary import T2_LIFT_NOTE
    results, gates = [], []

    def cell(arm, task, named):
        ctx = DeliveredContext(arm, task.task_id, [], 0, 0 if arm == "arm0" else 13_000, {})
        return score(task, ctx, _ans(task, named, arm=arm))

    # Gate A: one synthetic, single-gold T2 task (corpus tag "fastapi"); Arm 0 names it
    syn = _task(tmp_path, gold=["fastapi.mod.target"], tid="syn_t2_localization")
    for arm in ("arm0", "arm5", "oracle"):
        results.append(cell(arm, syn, ["fastapi.mod.target"]))
        gates.append("A_type_coverage")
    # Gate B: real multi-gold T2 tasks; Arm 0 names only the seed (any-gold 1, strict 0)
    for i in range(3):
        real = _task(tmp_path, gold=["fastapi.mod.seed", f"fastapi.mod.stage{i}"], tid=f"fastapi_t02_00{i}")
        results.append(cell("arm0", real, ["fastapi.mod.seed"]))
        results.append(cell("arm5", real, ["fastapi.mod.seed"] + ([f"fastapi.mod.stage{i}"] if i else [])))
        results.append(cell("oracle", real, ["fastapi.mod.seed", f"fastapi.mod.stage{i}"]))
        gates += ["B_real_fastapi_T2"] * 3

    s = gate_summary(results, gates, n_reps=100)
    assert list(s.columns[:4]) == ["gate", "arm", "task_type", "corpus"]
    assert set(s.corpus) == {"fastapi"}                                   # same corpus tag ...
    assert set(s.gate) == {"A_type_coverage", "B_real_fastapi_T2"}        # ... separate groups
    a = s[s.gate == "A_type_coverage"].set_index("arm")
    b = s[s.gate == "B_real_fastapi_T2"].set_index("arm")
    assert (a.n_tasks == 1).all() and (b.n_tasks == 3).all()              # never 4 = 1 synthetic + 3 real
    assert b.loc["arm0", "mean_tsr"] == 0.0                               # not pooled with the synthetic 1.0
    assert math.isnan(b.loc["arm5", "retrieval_lift"]) and b.loc["arm5", "retrieval_lift_note"] == T2_LIFT_NOTE
    assert (b.retrieval_lift_any_gold == 0.0).all()                       # any-gold is 1.0 for every arm
    assert a.loc["arm0", "mean_tsr"] == 1.0
    with pytest.raises(ValueError):
        gate_summary(results, gates[:-1])


# ---- Gate B exclusions (t02_005: ambiguous inherited-member gold, docs/m2_closure.md) ----
def test_gate_b_excludes_listed_tasks_and_records_why(tmp_path, monkeypatch):
    import os
    if not os.path.isdir("/home/user/SCE/.benchmarks/corpora/fastapi"):
        pytest.skip("FastAPI checkout missing")
    from harness import kaggle_m1
    assert "fastapi_t02_005_request_validation_error_response" in C.GATE_B_EXCLUDED_TASKS
    monkeypatch.setattr(C, "ACTIVE_ARMS", ["arm0"])
    code = kaggle_m1.main(["--dry-run", "--fake-encoders", "--real-tasks", "5", "--bootstrap-reps", "50",
                           "--out", str(tmp_path)])
    report = json.loads((tmp_path / "gate_report.json").read_text())
    gate_b = {r["task_id"] for r in report["rows"] if r["gate"] == "B_real_fastapi_T2"}
    assert code == 0 and len(gate_b) == 4 and "fastapi_t02_005_request_validation_error_response" not in gate_b
    assert list(report["gate_b_excluded"]) == ["fastapi_t02_005_request_validation_error_response"]
