"""Phase 0a: the OpenAI-compatible client the R3 loop uses for gpt-oss-120b on Groq (`r3/loop.OpenAIChat`)."""
import http.server
import json
import threading

import pytest

from harness.experiments.r3.loop import OpenAIChat


class _Server:
    """A local fake endpoint: `script` is a list of (status, headers, body) answers, one per request."""

    def __init__(self, script):
        self.script, self.requests = list(script), []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                outer.requests.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                       "ua": self.headers.get("User-Agent"),
                                       "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
                status, headers, body = outer.script.pop(0)
                if status == "hang":
                    import time
                    time.sleep(3)
                    status = 200
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def log_message(self, *a):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/openai/v1"

    def close(self):
        self.httpd.shutdown()


OK = {"model": "openai/gpt-oss-120b", "system_fingerprint": "fp_x",
      "choices": [{"message": {"content": '{"tool": "done", "args": {}}', "reasoning": "think first"},
                   "finish_reason": "stop"}],
      "usage": {"prompt_tokens": 120, "completion_tokens": 30}}


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("FAKE_KEY", "sk-test")


def test_request_shape_and_the_reply_mapping(key):
    s = _Server([(200, {"x-ratelimit-remaining-tokens": "5000", "x-ratelimit-limit-tokens": "8000"}, OK)])
    try:
        c = OpenAIChat(s.url, "openai/gpt-oss-120b", "FAKE_KEY", answer_tokens=8192, reasoning_effort="medium")
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        r = c(msgs, 42)
        req = s.requests[0]
        assert req["path"] == "/openai/v1/chat/completions" and req["auth"] == "Bearer sk-test"
        assert req["ua"] == "prism-harness/1.0"
        assert req["body"]["messages"] == msgs and req["body"]["seed"] == 42 and req["body"]["max_tokens"] == 8192
        assert req["body"]["reasoning_effort"] == "medium" and req["body"]["response_format"] == {"type": "json_object"}
        assert r["text"] == '{"tool": "done", "args": {}}' and r["thinking"] == "think first"
        assert (r["prompt_tokens"], r["answer_tokens"], r["done_reason"]) == (120, 30, "stop")
        assert r["meta"]["model"] == "openai/gpt-oss-120b" and r["meta"]["system_fingerprint"] == "fp_x"
        assert r["meta"]["rate"] == {"x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": "5000"}
        assert r["meta"]["retries"] == 0
    finally:
        s.close()


def test_429_and_5xx_are_retried_honoring_retry_after_and_not_counted_as_time(key):
    s = _Server([(429, {"retry-after": "7"}, {"error": "rate"}), (503, {}, {"error": "busy"}), (200, {}, OK)])
    try:
        c = OpenAIChat(s.url, "m", "FAKE_KEY")
        waits = []
        c.sleep = waits.append
        r = c([{"role": "user", "content": "u"}], 1)
        assert waits == [7.0, 4.0] and r["meta"]["retries"] == 2 and r["meta"]["retry_wait_s"] == 11.0
        assert r["text"]
    finally:
        s.close()


def test_errors_after_the_retries_and_other_errors_propagate(key):
    s = _Server([(429, {}, {})] * 4 + [(400, {}, {"error": "bad"})])
    try:
        c = OpenAIChat(s.url, "m", "FAKE_KEY")
        c.sleep = lambda w: None
        with pytest.raises(RuntimeError, match="HTTP 429 after 3 retries"):
            c([{"role": "user", "content": "u"}], 1)
        with pytest.raises(RuntimeError, match="HTTP 400 after 0 retries"):
            c([{"role": "user", "content": "u"}], 1)
    finally:
        s.close()


def test_a_timeout_propagates_and_a_missing_key_is_an_error(key, monkeypatch):
    s = _Server([("hang", {}, OK)])
    try:
        c = OpenAIChat(s.url, "m", "FAKE_KEY", timeout=1)
        with pytest.raises(Exception):
            c([{"role": "user", "content": "u"}], 1)
    finally:
        s.close()
    monkeypatch.delenv("FAKE_KEY")
    with pytest.raises(RuntimeError, match="FAKE_KEY is not set"):
        OpenAIChat("http://127.0.0.1:9", "m", "FAKE_KEY")([], 1)


def test_json_format_and_effort_are_optional():
    c = OpenAIChat("http://x", "m", "K", json_format=False)
    body = c.payload([{"role": "user", "content": "u"}], 3)
    assert "response_format" not in body and "reasoning_effort" not in body


# ------------------------------------------------------------------ native tool calling (Phase 0a fix)
from harness.experiments.r3.loop import BASELINE_DOC, PRISM_DOC, SHARED_DOC, native_tools, parse_call


def test_native_schemas_cover_every_tool_and_shared_tools_are_identical_in_both_arms():
    p, b = native_tools("PRISM"), native_tools("BASELINE")
    names = lambda ts: [t["function"]["name"] for t in ts]
    assert names(p) == list({**SHARED_DOC, **PRISM_DOC}) and names(b) == list({**SHARED_DOC, **BASELINE_DOC})
    shared_p = [t for t in p if t["function"]["name"] in SHARED_DOC]
    shared_b = [t for t in b if t["function"]["name"] in SHARED_DOC]
    assert shared_p == shared_b                                   # no asymmetry in the shared tools
    for t in p + b:
        f = t["function"]
        assert t["type"] == "function" and f["parameters"]["type"] == "object"
        assert set(f["parameters"]["required"]) <= set(f["parameters"]["properties"])
        assert f["description"] == {**SHARED_DOC, **PRISM_DOC, **BASELINE_DOC}[f["name"]][1]
    ef = next(t for t in b if t["function"]["name"] == "edit_file")["function"]["parameters"]
    assert ef["required"] == ["path", "old", "new"]


def _native(msg):
    return {"model": "m", "choices": [{"message": msg, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def test_a_native_tool_call_comes_back_as_the_loops_text_and_the_request_forces_one_call(key):
    call = {"tool_calls": [{"id": "c1", "type": "function", "function": {
        "name": "edit_file", "arguments": json.dumps({"path": "a.py", "old": "x", "new": "y"})}},
        {"id": "c2", "type": "function", "function": {"name": "done", "arguments": "{}"}}]}
    s = _Server([(200, {}, _native(call))])
    try:
        c = OpenAIChat(s.url, "m", "FAKE_KEY", tools=native_tools("BASELINE"))
        r = c([{"role": "user", "content": "u"}], 1)
        body = s.requests[0]["body"]
        assert body["tools"] == native_tools("BASELINE") and body["tool_choice"] == "required"
        assert body["parallel_tool_calls"] is False and "response_format" not in body
        assert parse_call(r["text"]) == ("edit_file", {"path": "a.py", "old": "x", "new": "y"})
        assert r["meta"]["tool_calls"] == 2                     # the loop takes the first; the count is logged
    finally:
        s.close()


def test_malformed_native_arguments_stay_malformed_text_for_the_loops_reminder(key):
    bad = {"tool_calls": [{"function": {"name": "grep", "arguments": "{not json"}}]}
    s = _Server([(200, {}, _native(bad)), (200, {}, _native({"content": "plain words"}))])
    try:
        c = OpenAIChat(s.url, "m", "FAKE_KEY", tools=native_tools("PRISM"))
        assert parse_call(c([{"role": "user", "content": "u"}], 1)["text"]) is None
        assert c([{"role": "user", "content": "u"}], 1)["text"] == "plain words"   # no call: the content as before
    finally:
        s.close()


def test_a_refused_tool_call_is_a_bad_reply_not_an_infrastructure_death(key):
    """The smoke test's case: gpt-oss called a tool it was not given (`repo_browser.print_tree`); Groq answered 400."""
    err = {"error": {"message": "Tool choice is none, but model called a tool", "type": "invalid_request_error",
                     "code": "tool_use_failed",
                     "failed_generation": '{"name": "repo_browser.print_tree", "arguments": {"path": ""}}'}}
    s = _Server([(400, {}, err), (400, {}, {"error": {"code": "json_validate_failed"}})])
    try:
        c = OpenAIChat(s.url, "m", "FAKE_KEY", tools=native_tools("BASELINE"))
        r = c([{"role": "user", "content": "u"}], 1)
        assert r["done_reason"] == "tool_use_failed" and "repo_browser.print_tree" in r["text"]
        assert parse_call(r["text"]) is None and "tool_use_failed" in r["meta"]["provider_error"]
        with pytest.raises(RuntimeError, match="HTTP 400"):                     # any other 400 still propagates
            c([{"role": "user", "content": "u"}], 1)
    finally:
        s.close()


def test_native_tool_replies_drive_the_real_loop(tmp_path, key):
    """End to end through AgentRun: a native call is executed by the loop like a text call."""
    from harness.experiments.r3 import loop as L
    calls = [_native({"tool_calls": [{"function": {"name": "read_file", "arguments": '{"path": "a"}'}}]}),
             _native({"tool_calls": [{"function": {"name": "done", "arguments": '{"summary": "ok"}'}}]})]
    s = _Server([(200, {}, x) for x in calls])

    class Shared:
        done_summary = None

        def call(self, tool, **args):
            if tool == "done":
                self.done_summary = args.get("summary")
            return f"{tool} ran"
    try:
        c = OpenAIChat(s.url, "m", "FAKE_KEY", tools=native_tools("BASELINE"))
        out = L.AgentRun({"id": "t1", "prompt": "p"}, "BASELINE", 42, c, Shared()).run(str(tmp_path))
        assert out["end"] == "done" and out["turns"] == 2 and out["done_summary"] == "ok"
        log = [json.loads(ln) for ln in open(tmp_path / "t1__BASELINE__42.jsonl")]
        assert [t["tool"] for t in log] == ["read_file", "done"] and log[0]["model_meta"]["tool_calls"] == 1
    finally:
        s.close()
