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
