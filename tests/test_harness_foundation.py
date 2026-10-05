"""Foundation tests for `harness/` (config, base interface, canonical model,
task schema, tokenizer). One section per module, added step by step."""
from __future__ import annotations

import http.server
import json
import os
import pytest
import threading
from harness import config as C
from harness import tokenizer as T
from harness.arms.base import FidelityGrade, RetrievalArm
from harness.scoring import registry as R
from harness.scoring.canonical import DeliveredContext, DeliveredItem, NormalizedAnswer
from harness.tasks.schema import parse_task
from pathlib import Path
from pydantic import ValidationError


def test_config_envelope_and_arms():
    assert C.RETRIEVAL_BUDGET == 13_000 and C.CONTEXT_WINDOW == 18_432 and C.GENERATION_RESERVE == 4_096
    assert C.MODEL_NAME == "qwen2.5-coder:14b-instruct-q8_0"
    assert sum([C.SYSTEM_PROMPT_TOKENS, C.TASK_PROMPT_TOKENS, C.RETRIEVAL_BUDGET,
                C.GENERATION_RESERVE, C.SAFETY_MARGIN]) == C.CONTEXT_WINDOW
    assert C.TEMPERATURE == 0.4 and C.SEEDS == [42, 43, 44]
    assert C.NOISE_SWEEP_ENABLED is False
    assert set(C.FIDELITY) == set(C.ARM_IDS)
    assert C.FIDELITY["arm1"] is C.FidelityGrade.HIGH and C.FIDELITY["arm4"] is C.FidelityGrade.MEDIUM_HIGH
    assert set(C.ACTIVE_ARMS) == {"arm0", "arm1", "arm2", "arm3", "arm4", "arm5", "oracle"}


# ---- Step 2: arms/base.py ----


def test_retrieval_arm_is_abstract_and_declares_fidelity():
    with pytest.raises(TypeError):
        RetrievalArm()  # type: ignore[abstract]

    class Dummy(RetrievalArm):
        arm_id = "arm1"
        def index(self, repo_path, config): ...
        def retrieve(self, query, seed): ...
        def build_prompt(self, ctx, tokenizer): return ""

    d = Dummy()
    assert d.fidelity is FidelityGrade.HIGH
    assert d.fidelity_meta()["fidelity"] == "HIGH"

    class NoId(Dummy):
        arm_id = ""
    with pytest.raises(TypeError):
        NoId()


# ---- Step 3: scoring/canonical.py ----


def _item(rank, syms, kind="code_chunk"):
    return DeliveredItem(source_id=f"a.py:{rank}-{rank}", content="x", token_count=3, rank=rank, kind=kind, symbols=syms)


def test_canonical_roundtrip_and_validation():
    ctx = DeliveredContext("arm1", "t1", [_item(1, ["a.f"]), _item(2, ["a.g", "a.f"])], 6, 13_000, {"k": 1})
    assert ctx.delivered_symbols == {"a.f", "a.g"}
    back = DeliveredContext.from_json(ctx.to_json())
    assert back == ctx
    with pytest.raises(ValueError):
        _item(1, [], kind="bogus")
    ans = NormalizedAnswer("arm1", "t1", "raw", "ans", ["a.f"], "plain_text", True, 10, 1.5)
    assert NormalizedAnswer.from_dict(ans.to_dict()) == ans
    with pytest.raises(ValueError):
        NormalizedAnswer("arm1", "t1", "", "", [], "telepathy", False, 0, 0.0)


# ---- Step 4: tasks/schema.py ----


def _base(tt, **extra):
    return {"task_id": f"syn_{tt}", "task_type": tt, "repo_id": "r", "repo_root": "/r", "query": "q",
            "seed_symbol": "m.seed", "ground_truth": {"pipeline_symbols": ["m.a", "m.b"], "context_symbols": ["m.c"]}, **extra}


def test_schema_accepts_all_five_types_and_discriminates():
    tasks = [
        parse_task(_base("T1_conceptual", reference_answer="It does X.")),
        parse_task(_base("T2_localization", gold_spans=[{"path": "m.py", "start": 1, "end": 3}])),
        parse_task(_base("T3_codegen", gold_code="def f(): return 1")),
        parse_task(_base("T4_edit", gold_patch="-return 1\n+return 2", fail_to_pass=["assert f() == 2"])),
        parse_task(_base("T5_blast_radius")),
    ]
    assert [type(t).__name__ for t in tasks] == ["ConceptualTask", "LocalizationTask", "CodeGenTask", "EditTask", "BlastRadiusTask"]
    assert tasks[4].gold_affected == ["m.a", "m.b"]
    assert tasks[1].ground_truth.universe_symbols() == {"m.a", "m.b", "m.c", "m.seed"}
    assert tasks[2].seed_dict()["task_type"] == "T3_codegen"
    with pytest.raises(ValidationError):
        parse_task(_base("T6_bogus"))
    with pytest.raises(ValidationError):
        parse_task(_base("T3_codegen"))  # gold_code required


def test_legacy_loader_maps_debug_to_t2_and_blast_to_t5():
    from benchmarks.runner import _ground_truth_universe
    from benchmarks.ground_truth.loader import load_tasks_from_dir
    from harness.tasks.loaders import load_tasks

    t2 = load_tasks("fastapi", repo_root="/nonexistent", limit=5)
    assert len(t2) == 5 and all(t.task_type == "T2_localization" for t in t2)
    t5 = load_tasks("django", task_types=["T5_blast_radius"], repo_root="/nonexistent")
    assert len(t5) == 4 and all(t.gold_affected for t in t5)
    # G*_universe equals the legacy harness's universe, task by task
    legacy = {t.task_id: t for t in load_tasks_from_dir(Path(C.TASKS_DIR_TEMPLATE.format(repo="django"))).accepted}
    for t in t5:
        assert t.ground_truth.universe_symbols() == _ground_truth_universe(legacy[t.task_id])


# ---- Step 5: tokenizer.py ----


QWEN_GGUF = os.environ.get("HARNESS_TOKENIZER_PATH", "/home/user/models/qwen2-tokenizer")
needs_qwen = pytest.mark.skipif(not os.path.exists(QWEN_GGUF), reason="Qwen2 tokenizer file not available")


class _FakeLlamaServer(http.server.BaseHTTPRequestHandler):
    """/tokenize that splits on whitespace - a deliberately different tokenizer."""
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        out = json.dumps({"tokens": list(range(len(body["content"].split())))}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _FakeLlamaServer)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@needs_qwen
def test_hf_qwen_tokenizer_counts_and_truncates():
    tok = T.HuggingFaceTokenizer(QWEN_GGUF)
    assert tok.encode("def hello_world(x):\n    return x + 1") == [750, 23811, 31792, 2075, 982, 262, 470, 856, 488, 220, 16]
    assert tok.count("") == 0
    long = "token " * 500
    assert tok.count(tok.truncate(long, 100)) <= 100


def test_llama_server_backend_and_parity(fake_server):
    srv = T.LlamaServerTokenizer(fake_server)
    assert srv.count("a b c") == 3

    class Same:
        name = "same"
        def count(self, t): return len(t.split())

    class Off:
        name = "off"
        def count(self, t): return len(t.split()) + 1

    ok = T.verify_tokenizer_parity(Same(), srv, ["a b c d e f g h i j", "x y"])
    assert ok.within_tolerance and ok.max_divergence == 0
    bad = T.verify_tokenizer_parity(Off(), srv, ["a b c d e f g h i j", "x y"])
    assert not bad.within_tolerance and bad.max_divergence == pytest.approx(0.5)


def test_set_tokenizer_overrides_global():
    class Words:
        name = "words"
        def count(self, t): return len(t.split())
    T.set_tokenizer(Words())
    try:
        assert T.count_tokens("one two three") == 3
    finally:
        T.set_tokenizer(None)


# ---- Step 6: scoring/registry.py ----


def test_registry_is_complete_and_checks_ranges():
    spec_keys = {
        "uniform_cpi", "cleanliness", "context_precision", "context_recall", "mrr_at_5", "mrr_at_10",
        "ndcg_at_5", "ndcg_at_10", "map", "p_at_5", "r_at_5", "f1_at_5", "relevant_token_density",
        "task_success", "hallucination_rate", "budget_utilization", "faithfulness", "answer_relevancy",
        "acc_at_5_retrieval", "pass_at_1", "codebleu", "patch_exact_match", "regression_rate", "recall_at_5",
        "false_negative_rate", "latency_l_index", "latency_l_retrieve_p50", "latency_l_retrieve_p95",
        "latency_l_generate_p50", "latency_l_generate_p95", "latency_l_e2e_p50", "latency_l_e2e_p95",
        "digest_safety_loss", "tool_fpr", "verification_lift", "recovery_rate", "total_tool_output_tokens",
        "retrieval_lift", "retrieval_efficiency",
    }
    assert spec_keys <= set(R.METRICS)
    assert R.task_specific_metrics("T5_blast_radius") == ["recall_at_5", "false_negative_rate"]
    assert "tsr" in R.universal_metrics()
    assert "cleanliness" in R.universal_metrics() and "faithfulness" not in R.universal_metrics()
    R.check_value("cleanliness", float("nan"))
    R.check_value("budget_utilization", 3.0)
    with pytest.raises(ValueError):
        R.check_value("cleanliness", 1.2)
