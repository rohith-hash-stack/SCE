"""Phase 1 (`harness/experiments/r4/PHASE1_PLAN.md`), items 2 and 3, without a model:

- item 3: `OllamaChat(stream_cut=True)` against a fake streaming Ollama server; the loop's `hygiene` (an invalid reply
  goes back into the history cut short; a call without token counts is counted with the tokenizer);
- item 2: `r3/compaction.compact` rule by rule, and `AgentRun(compact=True)` end to end.
"""
import http.server
import json
import threading
import time

import pytest

from harness.experiments.r3 import loop as L
from harness.experiments.r3.compaction import KEEP_RECENT, compact


# ------------------------------------------------------------------ a fake streaming Ollama
class _Ollama:
    """Answers /api/chat with `chunks` (strings) as NDJSON stream chunks, then a final chunk with counts, unless
    `final` is False (the stream just stops, as if cut). Records each request body and whether the client hung up."""

    def __init__(self, chunks, final=True, delay=0.0):
        self.chunks, self.final, self.delay, self.requests = chunks, final, delay, []
        self.sent = 0
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                if not body.get("stream"):
                    text = "".join(outer.chunks)
                    self.wfile.write(json.dumps({"message": {"content": text}, "done": True, "done_reason": "stop",
                                                 "prompt_eval_count": 111, "eval_count": 22}).encode())
                    return
                try:
                    for c in outer.chunks:
                        time.sleep(outer.delay)
                        self.wfile.write((json.dumps({"message": {"content": c}, "done": False}) + "\n").encode())
                        self.wfile.flush()
                        outer.sent += 1
                    if outer.final:
                        self.wfile.write((json.dumps({"message": {"content": ""}, "done": True, "done_reason": "stop",
                                                      "prompt_eval_count": 111, "eval_count": 22}) + "\n").encode())
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"

    def close(self):
        self.httpd.shutdown()


CALL = '{"tool": "edit_file", "args": {"path": "a.py", "old": "x", "new": "y"}}'


def _chat(server, **kw):
    return L.OllamaChat(server.url, "m", stream_cut=True, **kw)([{"role": "user", "content": "u"}], 7)


def test_a_normal_streamed_reply_is_unchanged_with_its_counts():
    s = _Ollama([CALL[:20], CALL[20:]])
    try:
        r = _chat(s)
        assert r["text"] == CALL and L.parse_call(r["text"]) == ("edit_file", {"path": "a.py", "old": "x", "new": "y"})
        assert (r["prompt_tokens"], r["answer_tokens"], r["done_reason"]) == (111, 22, "stop")
        assert "meta" not in r and s.requests[0]["stream"] is True and s.requests[0]["format"] == "json"
    finally:
        s.close()


def test_whitespace_after_the_args_is_cut_and_the_missing_brace_repaired():
    """feature-08 PRISM's shape: the args object closes, the outer brace never comes, whitespace runs on."""
    s = _Ollama([CALL[:-1]] + [" \n"] * 400, final=False)
    try:
        r = _chat(s)
        assert r["done_reason"] == "degenerate_cut" and r["meta"]["degenerate_cut"] is True
        assert r["meta"]["tail_chars"] > L.DEGENERATE_TAIL_CHARS and r["meta"]["repaired_closing_braces"] == 1
        assert L.parse_call(r["text"]) == ("edit_file", {"path": "a.py", "old": "x", "new": "y"})
        assert (r["prompt_tokens"], r["answer_tokens"]) == (0, 0)          # no counts: the loop estimates them
        assert s.sent < 400                                                # the client hung up early
    finally:
        s.close()


def test_escaped_newlines_inside_a_string_are_cut_but_not_repaired():
    """delete-05 PRISM's shape: `\\n` escapes repeat inside the `new` string; no brace can make it valid."""
    s = _Ollama(['{"tool": "edit_file", "args": {"path": "m.py", "old": "a", "new": "else:'] + ["\\n"] * 400,
                final=False)
    try:
        r = _chat(s)
        assert r["done_reason"] == "degenerate_cut" and "repaired_closing_braces" not in r["meta"]
        assert r["text"].endswith("else:") and L.parse_call(r["text"]) is None
    finally:
        s.close()


def test_legitimate_blank_lines_are_not_cut():
    body = "\\n".join(["line"] * 50) + "\\n" * 20                      # 20 blank lines: far below the limit
    reply = '{"tool": "edit_file", "args": {"path": "a.py", "old": "x", "new": "' + body + '"}}'
    s = _Ollama([reply[i:i + 7] for i in range(0, len(reply), 7)])
    try:
        r = _chat(s)
        assert r["text"] == reply and r["done_reason"] == "stop" and "meta" not in r
    finally:
        s.close()


def test_without_stream_cut_the_request_is_not_streamed():
    s = _Ollama([CALL])
    try:
        r = L.OllamaChat(s.url, "m")([{"role": "user", "content": "u"}], 7)
        assert s.requests[0]["stream"] is False and r["text"] == CALL and r["prompt_tokens"] == 111
    finally:
        s.close()


def test_a_stream_that_runs_past_the_timeout_raises():
    s = _Ollama(["{"] + ['"a'] * 50, final=False, delay=0.1)
    try:
        with pytest.raises(Exception):
            L.OllamaChat(s.url, "m", stream_cut=True, timeout=1)([{"role": "user", "content": "u"}], 7)
    finally:
        s.close()


# ------------------------------------------------------------------ the loop's hygiene
class _Shared:
    done_summary = None

    def __init__(self, outputs=None):
        self.outputs = outputs or {}

    def call(self, tool, **args):
        if tool == "done":
            self.done_summary = args.get("summary", "")
            return "done"
        f = self.outputs.get(tool)
        return f(args) if callable(f) else (f if f is not None else f"{tool} ok")


class _Scripted:
    """Replies from a list of (text, prompt_tokens, answer_tokens); records the messages it was sent."""

    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def __call__(self, msgs, seed):
        self.seen.append(msgs)
        text, pt, at = self.replies.pop(0)
        return {"text": text, "thinking": None, "prompt_tokens": pt, "answer_tokens": at, "done_reason": None,
                "seconds": 0.0}


def _run(tmp_path, replies, **kw):
    model = _Scripted(replies)
    out = L.AgentRun({"id": "t", "prompt": "p"}, "BASELINE", 42, model, _Shared(), **kw).run(str(tmp_path))
    log = [json.loads(ln) for ln in open(tmp_path / "t__BASELINE__42.jsonl")]
    return out, log, model


BAD = '{"tool": "edit_file", "args": {"path": "a.py", "new": "' + "z" * 3000 + '\\n' * 200
DONE = '{"tool": "done", "args": {"summary": "ok"}}'


def test_hygiene_cuts_an_invalid_reply_in_the_history_and_estimates_missing_counts(tmp_path):
    out, log, model = _run(tmp_path, [(BAD, 0, 0), (DONE, 50, 5)], hygiene=True)
    assert log[0]["parse_error"] is True and log[0]["tokens_estimated"] is True
    assert log[0]["prompt_tokens"] > 0 and log[0]["answer_tokens"] > 0 and log[0]["reply"] == BAD   # logged in full
    sent_back = model.seen[1][-2]["content"]                    # the assistant message on the next call
    assert len(sent_back) < L.PARSE_ERROR_KEEP_CHARS + 120 and "reply cut here" in sent_back
    assert out["used_tokens"] == log[0]["prompt_tokens"] + log[0]["answer_tokens"] + 55


def test_without_hygiene_the_invalid_reply_goes_back_in_full_and_costs_nothing(tmp_path):
    out, log, model = _run(tmp_path, [(BAD, 0, 0), (DONE, 50, 5)])
    assert model.seen[1][-2]["content"] == BAD and "tokens_estimated" not in log[0]
    assert out["used_tokens"] == 55


# ------------------------------------------------------------------ compaction, rule by rule
def _n(text):
    return len(text) // 4


def _read(path, a, b, total=500):
    return f"{path} ({'lines'} {a}-{b} of {total})\n" + "\n".join(f"{i}\tcode line {i} " + "x" * 40
                                                                for i in range(a, b + 1))


def _hist(*calls):
    """calls: (tool, args, output) -> the loop's history entries, turn by turn."""
    h = []
    for turn, (tool, args, output) in enumerate(calls, 1):
        h += [{"role": "assistant", "content": json.dumps({"tool": tool, "args": args})},
              {"role": "user", "content": output, "tool_output": True, "tool": tool, "args": args, "turn": turn}]
    return h


def test_a_read_covered_by_a_later_read_is_superseded():
    h = _hist(("read_file", {"path": "a.py"}, _read("a.py", 10, 40)),
              ("read_file", {"path": "a.py"}, _read("a.py", 1, 80)))
    out, c = compact(h, _n)
    assert out[1]["content"].startswith("(superseded: `a.py` lines 10-40 were read again at turn 2")
    assert out[3]["content"] == h[3]["content"] and c["superseded"] == 1        # the newest read is untouched


def test_a_read_is_stale_only_when_a_later_edit_overlaps_its_lines():
    h = _hist(("read_file", {"path": "a.py"}, _read("a.py", 10, 40)),
              ("read_file", {"path": "b.py"}, _read("b.py", 10, 40)),
              ("read_file", {"path": "c.py"}, _read("c.py", 10, 40)),
              ("edit_file", {"path": "a.py"}, "edited a.py at line 25: replaced 2 lines with 1"),      # inside
              ("edit_file", {"path": "b.py"}, "edited b.py at line 300: replaced 2 lines with 1"),     # below
              ("edit_file", {"path": "c.py"}, "edited c.py at line 2: replaced 3 lines with 1"),       # above
              ("edit_file", {"path": "c.py"}, "edited c.py at line 5: replaced 8 lines with 1"))       # spans in
    out, c = compact(h, _n)
    assert out[1]["content"].startswith("(stale: `a.py` was edited at turn 4 after this read of lines 10-40")
    assert out[3]["content"] == h[3]["content"]
    assert out[5]["content"].startswith("(stale: `c.py` was edited at turn 7") and c["stale"] == 2


def test_a_repeated_search_points_to_the_newer_one():
    big = "\n".join(f"src/f{i}.py:{i}: match" for i in range(40))
    h = _hist(("grep", {"pattern": "foo"}, big), ("grep", {"pattern": "bar"}, big), ("grep", {"pattern": "foo"}, big))
    out, c = compact(h, _n)
    assert out[1]["content"].startswith("(repeated: the same grep was run again at turn 3")
    assert out[3]["content"] == big and out[5]["content"] == big and c["repeated"] == 1


def test_outputs_older_than_keep_recent_are_summarised_by_kind_and_recent_ones_kept():
    tests = ("t.py: 3 passed, 2 failed, 0 skipped\nfailing:\n" + "\n".join(f"- test_{i}" for i in range(9))
             + "\n\nTraceback (most recent call last):\n" + "  frame\n" * 80)
    prism = "\n".join(f"caller {i}: " + "y" * 60 for i in range(60))
    old = [("read_file", {"path": "o.py"}, _read("o.py", 1, 60)), ("run_tests", {}, tests),
           ("prism_change_context", {"symbol": "a.b"}, prism), ("related_code", {"query": "q"}, prism)]
    filler = [("edit_file", {"path": f"f{i}.py"}, f"edited f{i}.py at line 1: replaced 1 lines with 1")
              for i in range(KEEP_RECENT)]
    h = _hist(*old, *filler)
    out, c = compact(h, _n)
    assert out[1]["content"].startswith("(older output of read_file, `o.py` lines 1-60")
    t = out[3]["content"]
    assert "t.py: 3 passed, 2 failed" in t and "- test_4" in t and "- test_5" not in t and "Traceback" not in t
    assert out[5]["content"].count("\n") == out[7]["content"].count("\n")      # PRISM and related_code: the same rule
    assert "more lines" in out[5]["content"] and c["summarised"] == 4
    assert all(out[i]["content"] == h[i]["content"] for i in range(9, len(h), 2))


def test_short_outputs_and_errors_are_never_compacted():
    h = _hist(("read_file", {"path": "x"}, "error: no file 'x'"), *[("list_dir", {"path": str(i)}, "a\nb")
                                                                   for i in range(KEEP_RECENT + 2)])
    out, c = compact(h, _n)
    assert [m["content"] for m in out] == [m["content"] for m in h] and sum(c.values()) == 0


def test_compaction_never_changes_the_raw_history():
    h = _hist(("read_file", {"path": "a.py"}, _read("a.py", 10, 40)),
              ("read_file", {"path": "a.py"}, _read("a.py", 1, 80)))
    before = json.dumps(h)
    compact(h, _n)
    assert json.dumps(h) == before


def test_compact_run_sends_smaller_prompts_and_logs_what_it_did(tmp_path):
    src = "\n".join(f"line {i} " + "q" * 60 for i in range(1, 301))

    def read(args):
        a, b = args.get("start", 1), args.get("end", 300)
        return f"a.py (lines {a}-{b} of 300)\n" + "\n".join(src.splitlines()[a - 1:b])
    replies = [('{"tool": "read_file", "args": {"path": "a.py", "start": 1, "end": 150}}', 10, 5)] * 2 + \
              [('{"tool": "read_file", "args": {"path": "a.py", "start": 1, "end": 300}}', 10, 5), (DONE, 10, 5)]
    sizes = {}
    for flag in (False, True):
        model = _Scripted(list(replies))
        d = tmp_path / str(flag)
        L.AgentRun({"id": "t", "prompt": "p"}, "BASELINE", 42, model, _Shared({"read_file": read}),
                   compact=flag).run(str(d))
        sizes[flag] = [sum(len(m["content"]) for m in msgs) for msgs in model.seen]
        log = [json.loads(ln) for ln in open(d / "t__BASELINE__42.jsonl")]
        assert ("compaction" in log[-1]) is flag
    assert sizes[True][:2] == sizes[False][:2]              # nothing to compact yet
    assert sizes[True][3] < sizes[False][3] * 0.7           # the two older reads are superseded by the full read


# ------------------------------------------------------------------ step 1e part A: the degeneration check
def test_degeneration_check_classifies_and_summarises_calls():
    from harness.experiments.r4.degeneration_check import classify, summarise
    good = classify({"text": CALL, "answer_tokens": 20, "seconds": 1.0})
    trailing = classify({"text": CALL[:-1] + " \n" * 300, "answer_tokens": 4096, "seconds": 90.0})
    fixed = classify({"text": CALL, "meta": {"degenerate_cut": True, "tail_chars": 450, "repaired_closing_braces": 1},
                      "done_reason": "degenerate_cut", "seconds": 3.0})
    assert good["valid"] and not trailing["valid"] and trailing["tail_chars"] > 400
    assert fixed["valid"] and fixed["cut"] and fixed["repaired"] == 1
    rows = [dict(trailing, condition="before", turn=8, seed=42), dict(good, condition="before", turn=8, seed=43),
            dict(fixed, condition="after", turn=8, seed=42), dict(good, condition="after", turn=8, seed=43)]
    s = summarise(rows)
    assert s["before"]["parse_errors"] == 1 and s["before"]["degenerate_tails"] == 1
    assert s["after"]["parse_errors"] == 0 and s["after"]["cut"] == 1 and s["after"]["repaired"] == 1


# ------------------------------------------------------------------ native tool calling through Ollama (devstral's own format)
class _OllamaNative:
    """Answers /api/chat non-streamed with a fixed `message` and records the request."""

    def __init__(self, message):
        self.message, self.requests = message, []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                outer.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"message": outer.message, "done": True, "done_reason": "stop",
                                             "prompt_eval_count": 90, "eval_count": 12}).encode())

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"

    def close(self):
        self.httpd.shutdown()


def test_native_messages_frame_calls_and_their_outputs_and_leave_the_rest():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "task"},
            {"role": "assistant", "content": '{"tool": "read_file", "args": {"path": "a.py"}}'},
            {"role": "user", "content": "a.py (lines 1-2 of 2)\n1\tx"},
            {"role": "assistant", "content": "not a call"}, {"role": "user", "content": L.FORMAT_REMINDER}]
    out = L.native_messages(msgs)
    assert out[2] == {"role": "assistant", "content": "",
                      "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "a.py"}}}]}
    assert out[3] == {"role": "tool", "content": "a.py (lines 1-2 of 2)\n1\tx"}
    assert out[4] == msgs[4] and out[5] == msgs[5] and out[:2] == msgs[:2]


def test_ollama_native_tools_request_and_reply_mapping():
    s = _OllamaNative({"content": "", "tool_calls": [
        {"function": {"name": "edit_file", "arguments": {"path": "a.py", "old": "x", "new": "y"}}},
        {"function": {"name": "done", "arguments": {}}}]})
    try:
        c = L.OllamaChat(s.url, "m", tools=L.native_tools("BASELINE"), stream_cut=True)
        r = c([{"role": "user", "content": "u"}, {"role": "assistant", "content": CALL},
               {"role": "user", "content": "edited a.py at line 1: replaced 1 lines with 1"}], 7)
        body = s.requests[0]
        assert "format" not in body and body["stream"] is False and body["tools"] == L.native_tools("BASELINE")
        assert body["messages"][1]["tool_calls"][0]["function"]["name"] == "edit_file"
        assert body["messages"][2]["role"] == "tool"
        assert L.parse_call(r["text"]) == ("edit_file", {"path": "a.py", "old": "x", "new": "y"})
        assert r["meta"]["tool_calls"] == 2 and r["prompt_tokens"] == 90
    finally:
        s.close()


def test_ollama_native_reply_without_a_call_falls_back_to_its_text():
    s = _OllamaNative({"content": '{"tool": "run_tests", "args": {}}'})
    try:
        r = L.OllamaChat(s.url, "m", tools=L.native_tools("PRISM"))([{"role": "user", "content": "u"}], 1)
        assert L.parse_call(r["text"]) == ("run_tests", {}) and r["meta"]["tool_calls"] == 0
    finally:
        s.close()
