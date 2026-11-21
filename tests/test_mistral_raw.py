"""Phase 1, re-baseline fix (A): devstral's template rendered by the harness, the raw reply parsed by the harness."""
import http.server
import json
import threading

from harness.experiments.r3 import loop as L
from harness.experiments.r3.mistral_raw import OllamaRawChat, go_json, parse_raw, render

TOOL = {"type": "function", "function": {"name": "read_file", "description": "Read <a> & b.",
                                         "parameters": {"type": "object", "properties": {
                                             "path": {"type": "string"},
                                             "end": {"type": ["integer", "null"]},
                                             "files": {"type": ["array", "null"], "items": {"type": "string"}}},
                                             "required": ["path"]}}}


def test_render_follows_the_template_exactly():
    msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "TASK"},
            {"role": "assistant", "content": json.dumps({"tool": "read_file", "args": {"path": "a<b>.ts", "end": 3}})},
            {"role": "user", "content": "OUT1"},
            {"role": "assistant", "content": "not a call"}, {"role": "user", "content": "REMIND"}]
    # property names sorted (Ollama v0.34.0, measured); the call's arguments keep their order
    tools_json = ('[{"type":"function","function":{"name":"read_file","description":"Read \\u003ca\\u003e \\u0026 b.",'
                  '"parameters":{"type":"object","required":["path"],"properties":{"end":{"type":["integer","null"]},'
                  '"files":{"type":["array","null"],"items":{"type":"string"}},"path":{"type":"string"}}}}}]')
    expected = ("[SYSTEM_PROMPT]SYS[/SYSTEM_PROMPT][INST]TASK[/INST]"
                '[TOOL_CALLS]read_file[ARGS]{"path":"a\\u003cb\\u003e.ts","end":3}</s>'
                "[TOOL_RESULTS]OUT1[/TOOL_RESULTS]"
                "not a call</s>"
                f"[AVAILABLE_TOOLS]{tools_json}[/AVAILABLE_TOOLS][INST]REMIND[/INST]")
    assert render(msgs, [TOOL]) == expected


def test_tools_go_before_the_last_user_message_only():
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "T"}]
    assert render(msgs, [TOOL]).startswith("[SYSTEM_PROMPT]S[/SYSTEM_PROMPT][AVAILABLE_TOOLS]")
    out = render(msgs + [{"role": "assistant", "content": '{"tool": "done", "args": {}}'},
                         {"role": "user", "content": "R"}], [TOOL])
    assert out.count("[AVAILABLE_TOOLS]") == 1 and out.index("[AVAILABLE_TOOLS]") < out.index("[INST]T[/INST]")


def test_go_json_escapes_like_go():
    assert go_json({"a": "x<y>&\"\\\n\t\x08 é", "n": [1, 2.0, None, True]}) == \
        '{"a":"x\\u003cy\\u003e\\u0026\\"\\\\\\n\\t\\u0008\\u2028é","n":[1,2,null,true]}'


NAMES = ["read_file", "grep", "edit_file", "done", "run_tests"]


def test_parse_raw_reads_calls_without_their_special_tokens():
    """/api/generate drops [TOOL_CALLS]/[ARGS] (first Kaggle pre-flight: `read_file{"path": "src/a.ts"}`)."""
    assert L.parse_call(parse_raw('read_file{"path": "src/a.ts"}', NAMES)[0]) == ("read_file", {"path": "src/a.ts"})
    two = parse_raw('read_file{"path":"a"}grep{"pattern":"x"}', NAMES)
    assert L.parse_call(two[0]) == ("read_file", {"path": "a"}) and two[1]["tool_calls"] == 2
    assert parse_raw("Use read_file to read", NAMES)[0] == "Use read_file to read"
    bad = parse_raw('edit_file{"path": "a", "new": "unterminated', NAMES)
    assert L.parse_call(bad[0]) is None and bad[1]["malformed"].startswith("arguments")
    assert parse_raw('my_read_file{"a": 1}', NAMES)[1]["tool_calls"] == 0          # only whole tool names
    # wave 1 of the 3-arm re-baseline: prose ending in a full stop, then the call
    prose = parse_raw('I need to read more. Let me continue reading the file.read_file{"path":"a.py","start":1}', NAMES)
    assert L.parse_call(prose[0]) == ("read_file", {"path": "a.py", "start": 1}) and prose[1]["tool_calls"] == 1


def test_parse_raw_cases():
    ok = parse_raw('[TOOL_CALLS]edit_file[ARGS]{"path": "a.py", "old": "x", "new": "y"}')
    assert L.parse_call(ok[0]) == ("edit_file", {"path": "a.py", "old": "x", "new": "y"}) and ok[1]["tool_calls"] == 1
    trailing = parse_raw('[TOOL_CALLS]done[ARGS]{"summary": "ok"}  \n trailing words')
    assert L.parse_call(trailing[0]) == ("done", {"summary": "ok"})
    bad = parse_raw('[TOOL_CALLS]edit_file[ARGS]{"path": "a.py", "new": "unterminated\\n\\n\\n')
    assert L.parse_call(bad[0]) is None and bad[1]["malformed"].startswith("arguments") and "unterminated" in bad[0]
    prose = parse_raw("I will read the file first.")
    assert prose == ("I will read the file first.", {"tool_calls": 0, "raw_tag": False})
    assert L.parse_call(parse_raw('{"tool": "run_tests", "args": {}}')[0]) == ("run_tests", {})


def test_raw_client_request_and_reply():
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"response": '[TOOL_CALLS]read_file[ARGS]{"path": "a.py"}', "done": True,
                                         "done_reason": "stop", "prompt_eval_count": 50, "eval_count": 9}).encode())

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = OllamaRawChat(f"http://127.0.0.1:{srv.server_port}", "m", [TOOL])
        r = c([{"role": "system", "content": "S"}, {"role": "user", "content": "T"}], 5)
        path, body = seen[0]
        assert path == "/api/generate" and body["raw"] is True and "format" not in body and "tools" not in body
        assert body["prompt"].startswith("[SYSTEM_PROMPT]S[/SYSTEM_PROMPT][AVAILABLE_TOOLS]")
        assert body["options"]["seed"] == 5 and body["options"]["num_predict"] == L.ANSWER_TOKENS
        assert L.parse_call(r["text"]) == ("read_file", {"path": "a.py"}) and r["prompt_tokens"] == 50
    finally:
        srv.shutdown()


def test_preflight_reads_a_json_format_reply_as_the_loop_does():
    """The smoke-4 pre-flight: after a history of JSON-format calls, devstral replied in that format; `/api/chat` read
    it as a call, and so does a run (`parse_raw` keeps the text, the loop's `parse_call` reads it)."""
    from harness.experiments.r3.loop import native_tools, parse_call
    from harness.experiments.r3.mistral_raw import parse_raw
    names = [t["function"]["name"] for t in native_tools("PRISM")]
    text, meta = parse_raw('{"tool":"read_file","args":{"path":"src/a.ts"}}', names)
    assert meta["tool_calls"] == 0 and parse_call(text) == ("read_file", {"path": "src/a.ts"})
    text, _ = parse_raw('read_file{"path": "src/a.ts"}', names)
    assert parse_call(text) == ("read_file", {"path": "src/a.ts"})
