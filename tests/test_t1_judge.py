"""T1 judge (M4): the protocol and its combination in the scorer (TSR = the
minimum of faithfulness and answer relevancy), a mechanism-blind judge
input, the OpenAI-compatible client against a fake HTTP layer (payload,
retries, refusals), and one live call guarded by HARNESS_T1_JUDGE_LIVE=1.
No test here makes a network call unless that flag is set."""
import json
import math
import os

import pytest

from harness import config as C
from harness.scoring import judge as J
from harness.scoring.canonical import DeliveredContext, DeliveredItem, NormalizedAnswer
from harness.scoring.scorer import score
from harness.tasks.synthetic import synthetic_tasks


@pytest.fixture
def t1():
    return next(t for t in synthetic_tasks("/tmp/repo") if t.task_type == "T1_conceptual")


def _ctx(task, arm="arm3"):
    items = [
        DeliveredItem("fastapi/dependencies/utils.py:247", "# fastapi/dependencies/utils.py:247  (outline)\n"
                      "def get_typed_annotation(annotation, globalns):\n    ...", 20, 1, "lsp_symbol",
                      ["fastapi.dependencies.utils.get_typed_annotation"], {"origin": "seed_outline"}),
        DeliveredItem("x.py:1", "plain text with no header", 5, 2, "code_chunk", [], {}),
    ]
    return DeliveredContext(arm, task.task_id, items, 25, 13_000,
                            {"arm": arm, "fidelity": "MEDIUM", "ranking_method": "lsp_hop_order"})


def _ans(task, arm="arm3", text="It evaluates a string annotation as a ForwardRef."):
    return NormalizedAnswer(arm, task.task_id, text, text, [], "plain_text", True, 10, 1.0)


# ----------------------------------------------------- protocol + scorer
@pytest.mark.parametrize("faith,relev", [(0.9, 0.6), (0.3, 0.8), (1.0, 1.0), (0.0, 0.0)])
def test_stub_judge_scores_combine_and_tsr_is_the_min(t1, faith, relev):
    seen = {}

    def stub(task, answer, context):
        seen.update(task=task, answer=answer, context=context)
        return {"faithfulness": faith, "answer_relevancy": relev}

    res = score(t1, _ctx(t1), _ans(t1), judge=stub)
    assert res.tsr == min(faith, relev)
    assert res.task_specific["faithfulness"] == faith and res.task_specific["answer_relevancy"] == relev
    assert res.task_specific["judge_status"] == "ok"
    assert res.task_success is (min(faith, relev) >= C.TASK_SUCCESS_THRESHOLD)
    assert seen["task"] is t1 and seen["answer"].answer_text.startswith("It evaluates")


def test_stub_judge_returns_non_nan_tsr_on_synthetic_t1(t1):
    res = score(t1, _ctx(t1), _ans(t1), judge=lambda t, a, c: {"faithfulness": 0.75, "answer_relevancy": 0.5})
    assert not math.isnan(res.tsr) and res.tsr == 0.5


def test_unconfigured_judge_is_nan_and_judge_error_is_nan(t1, monkeypatch):
    monkeypatch.delenv(C.T1_JUDGE_API_KEY_ENV, raising=False)
    monkeypatch.setattr(J, "_CONFIGURED", None)
    res = score(t1, _ctx(t1), _ans(t1))
    assert math.isnan(res.tsr) and res.task_specific["judge_status"].startswith("unavailable")

    def broken(task, answer, context):
        raise J.JudgeError("HTTP 500 after retries")

    res = score(t1, _ctx(t1), _ans(t1), judge=broken)
    assert math.isnan(res.tsr) and res.task_specific["judge_status"] == "error: HTTP 500 after retries"


def test_judge_input_is_mechanism_blind(t1):
    messages, cut = J.judge_messages(t1, _ans(t1), _ctx(t1, arm="arm3"))
    text = json.dumps(messages)
    assert not cut
    for leak in ("arm3", "outline", "lsp_symbol", "lsp_hover", "seed_outline", "MEDIUM", "lsp_hop_order",
                 "fastapi/dependencies/utils.py:247"):
        assert leak not in text, leak
    user = messages[1]["content"]
    assert user.startswith(f"QUESTION:\n{t1.query}")
    assert "[1]\ndef get_typed_annotation(annotation, globalns):" in user        # header line dropped
    assert "[2]\nplain text with no header" in user                            # non-header first line kept
    assert t1.reference_answer not in text                                     # never the reference answer
    # the same context from two different arms gives the judge the same input
    assert J.judge_messages(t1, _ans(t1, "arm1"), _ctx(t1, arm="arm1"))[0] == messages


def test_empty_context_and_cap(t1):
    empty = DeliveredContext("arm0", t1.task_id, [], 0, 0, {})
    user = J.judge_messages(t1, _ans(t1, "arm0"), empty)[0][1]["content"]
    assert "CONTEXT:\n(no context)" in user
    text, cut = J.context_passages(_ctx(t1), max_chars=65)   # fits the first passage only
    assert cut and text.count("[") == 1


def test_parse_verdict_clamps_and_rejects():
    v = J.parse_verdict('ok ```json\n{"faithfulness": 1.4, "answer_relevancy": -0.2, "rationale": "r"}\n```')
    assert v == {"faithfulness": 1.0, "answer_relevancy": 0.0, "rationale": "r"}
    for bad in ("no json", '{"faithfulness": 0.5}', '{"faithfulness": "x", "answer_relevancy": 1}'):
        with pytest.raises(J.JudgeError):
            J.parse_verdict(bad)


# ------------------------------------------------- OpenAI-compatible client
class Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text
    def json(self): return self._body


def _ok(f=0.8, r=0.7):
    return Resp(200, {"choices": [{"message": {"content": json.dumps({"faithfulness": f, "answer_relevancy": r})}}],
                      "usage": {"prompt_tokens": 100, "completion_tokens": 20}})


def test_client_payload_and_verdict(t1):
    calls = []
    judge = J.OpenAICompatibleJudge(provider="deepseek", model="deepseek-chat", api_key="k",
                                    post=lambda url, h, p, t: calls.append((url, h, p)) or _ok())
    v = judge(t1, _ans(t1), _ctx(t1))
    assert v["faithfulness"] == 0.8 and v["answer_relevancy"] == 0.7 and v["judge"] == "deepseek/deepseek-chat"
    url, headers, payload = calls[0]
    assert url == "https://api.deepseek.com/chat/completions" and headers["Authorization"] == "Bearer k"
    assert payload["model"] == "deepseek-chat" and payload["temperature"] == 0.0
    assert payload["response_format"] == {"type": "json_object"}
    assert judge.usage == {"prompt_tokens": 100, "completion_tokens": 20, "calls": 1}
    res = score(t1, _ctx(t1), _ans(t1), judge=judge)
    assert res.tsr == 0.7 and res.task_specific["judge"] == "deepseek/deepseek-chat"


def test_client_gemini_endpoint(t1):
    calls = []
    judge = J.OpenAICompatibleJudge(provider="gemini", model="gemini-2.5-flash", api_key="k",
                                    post=lambda url, h, p, t: calls.append(url) or _ok())
    judge(t1, _ans(t1), _ctx(t1))
    assert calls == ["https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"]


def test_client_retries_429_and_5xx_then_gives_up(t1):
    replies, waits = [Resp(429, text="slow down"), Resp(503, text="busy"), _ok()], []
    judge = J.OpenAICompatibleJudge(api_key="k", post=lambda *a: replies.pop(0), sleep=waits.append)
    assert judge(t1, _ans(t1), _ctx(t1))["faithfulness"] == 0.8 and waits == [2, 4]
    waits.clear()
    judge = J.OpenAICompatibleJudge(api_key="k", post=lambda *a: Resp(500, text="down"), sleep=waits.append)
    with pytest.raises(J.JudgeError, match="gave up after 5 attempts"):
        judge(t1, _ans(t1), _ctx(t1))
    assert waits == [2, 4, 8, 16]


def test_client_does_not_retry_4xx_and_refuses_qwen_and_missing_key(t1, monkeypatch):
    n = []
    judge = J.OpenAICompatibleJudge(api_key="k", post=lambda *a: n.append(1) or Resp(401, text="bad key"))
    with pytest.raises(J.JudgeError, match="HTTP 401"):
        judge(t1, _ans(t1), _ctx(t1))
    assert len(n) == 1
    with pytest.raises(ValueError, match="Qwen"):
        J.OpenAICompatibleJudge(model="qwen2.5-72b-instruct", api_key="k")
    monkeypatch.delenv("SOME_UNSET_KEY", raising=False)
    with pytest.raises(NotImplementedError, match="SOME_UNSET_KEY"):
        J.OpenAICompatibleJudge(api_key_env="SOME_UNSET_KEY")


def test_config_defaults():
    assert C.T1_JUDGE_PROVIDER in ("deepseek", "gemini")
    assert C.T1_JUDGE_API_KEY_ENV and C.T1_JUDGE_MODEL and "qwen" not in C.T1_JUDGE_MODEL.lower()


# --------------------------------------------------------------- live
@pytest.mark.skipif(os.environ.get("HARNESS_T1_JUDGE_LIVE") != "1",
                    reason="live judge call: set HARNESS_T1_JUDGE_LIVE=1 and the provider's API key")
def test_live_judge_on_one_task(t1):
    judge = J.OpenAICompatibleJudge()
    good = score(t1, _ctx(t1), _ans(t1, text=t1.reference_answer), judge=judge)
    assert not math.isnan(good.tsr) and 0.0 <= good.tsr <= 1.0
    assert good.task_specific["answer_relevancy"] >= 0.5
    off = score(t1, _ctx(t1), _ans(t1, text="The capital of France is Paris."), judge=judge)
    assert off.task_specific["answer_relevancy"] < good.task_specific["answer_relevancy"]
    print(f"live {judge.name}: on-topic {good.task_specific}, off-topic {off.task_specific}, usage {judge.usage}")
