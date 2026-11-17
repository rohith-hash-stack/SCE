"""Phase 1 (re-baseline fix, "Option A"): devstral's native tool calling with the harness reading the raw output.

**Why:** with `tools` in an `/api/chat` request, Ollama (v0.34.0) feeds the reply to its tool parser
(`server/routes.go`). When the model starts a `[TOOL_CALLS]` call the parser cannot complete, `Parser.Content()`
(`tools/tools.go`) returns "" for that tag, so the whole reply is discarded and the harness gets an empty message.
Wave 1 of the re-baseline lost 76 replies this way (`REBASELINE_WAVE1_STOP.md`).

**What this does instead:**
1. `render(messages, tools)` builds the prompt exactly as devstral-small-2's Ollama template does: the registry
   template, applied to the same message list `/api/chat` would get (`native_messages`). The JSON that the template
   prints (`{{ $.Tools }}`, `{{ .Function.Arguments }}`) is reproduced as Ollama v0.34.0 writes it: compact; `<`, `>`,
   `&`, U+2028 and U+2029 escaped; tool fields in `api.Tool`'s order; **parameter property names sorted
   alphabetically**; call arguments in their original order. (Measured against Ollama itself: the first pre-flight
   on Kaggle found 1-2 tokens' difference with properties in insertion order; a local Ollama v0.34.0 with devstral's
   template on a small model then matched `/api/chat`'s prompt token count on every tool and every pre-flight case
   only with sorted property names and unsorted arguments.)
2. `OllamaRawChat` sends it to `/api/generate` with `raw: true` (no template applied by the server), with the same
   options as `OllamaChat` (temperature, seed, num_ctx, num_predict; no JSON mode).
3. `parse_raw(text, names)` reads the call itself. `/api/generate` drops special tokens from its text, so the reply
   arrives as `name{...}` (seen in the first pre-flight: `read_file{"path": "src/a.ts"}`), with `[TOOL_CALLS]` and
   `[ARGS]` present only if the server keeps them; both forms are read. A well-formed first call becomes the loop's
   `{"tool": ..., "args": ...}` text; anything else is returned as written, so the loop records it as a parse error
   with its text visible (and the step 1c history cut applies). Nothing is discarded unseen.

No prompt wording is added: the prompt is the model's own template around the harness's unchanged messages.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request

from harness.experiments.r3.loop import ANSWER_TOKENS, NUM_CTX, TEMPERATURE, native_messages

CALL_TAG, ARGS_TAG = "[TOOL_CALLS]", "[ARGS]"


_GO_ESC = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t",
           "<": "\\u003c", ">": "\\u003e", "&": "\\u0026", "\u2028": "\\u2028", "\u2029": "\\u2029"}


def _go_str(s: str) -> str:
    """A string as Go's encoder writes it: `\\n \\r \\t \\" \\\\` escaped, other control characters as `\\u00XX`, HTML
    characters and U+2028/2029 escaped, everything else (non-ASCII included) as is."""
    out = []
    for ch in s:
        e = _GO_ESC.get(ch)
        out.append(e if e else (f"\\u{ord(ch):04x}" if ord(ch) < 0x20 else ch))
    return '"' + "".join(out) + '"'


def go_json(value) -> str:
    """`encoding/json.Marshal`'s output for JSON-decoded data kept in insertion order (Ollama's ordered maps)."""
    if isinstance(value, dict):
        return "{" + ",".join(f"{_go_str(str(k))}:{go_json(v)}" for k, v in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(go_json(v) for v in value) + "]"
    if isinstance(value, str):
        return _go_str(value)
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e21:
        return str(int(value))            # Go writes a whole float64 without ".0"
    return json.dumps(value)


def _go_tool(tool: dict) -> dict:
    """A tool in `api.Tool`'s field order, with its `omitempty` fields dropped (as Ollama re-marshals it)."""
    fn = tool.get("function") or {}
    params = fn.get("parameters") or {}
    p = {"type": params.get("type", "")}
    for k in ("$defs", "items"):
        if params.get(k):
            p[k] = params[k]
    if params.get("required"):
        p["required"] = params["required"]
    props = params.get("properties")
    p["properties"] = {name: _go_property(props[name]) for name in sorted(props)} if props is not None else None
    f = {"name": fn.get("name", "")}
    if fn.get("description"):
        f["description"] = fn["description"]
    f["parameters"] = p
    out = {"type": tool.get("type", "")}
    if tool.get("items"):
        out["items"] = tool["items"]
    out["function"] = f
    return out


def _sorted(value):
    """`any` values (decoded into Go maps) marshal with sorted keys."""
    if isinstance(value, dict):
        return {k: _sorted(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_sorted(v) for v in value]
    return value


def _go_property(prop: dict) -> dict:
    out = {}
    if prop.get("anyOf"):
        out["anyOf"] = [_go_property(x) for x in prop["anyOf"]]
    if prop.get("type"):
        out["type"] = prop["type"]
    if prop.get("items"):
        out["items"] = _sorted(prop["items"])
    for k in ("description", "enum"):
        if prop.get(k):
            out[k] = prop[k]
    if prop.get("properties"):
        out["properties"] = {n: _go_property(prop["properties"][n]) for n in sorted(prop["properties"])}
    if prop.get("required"):
        out["required"] = prop["required"]
    return out


def render(messages: list[dict], tools: list[dict]) -> str:
    """devstral-small-2's Ollama template (registry blob sha256:580f051b…), for messages that include a system
    message (the harness always sends one, so the template's default system prompt never applies)."""
    msgs = native_messages(messages)
    if not any(m["role"] == "system" for m in msgs):
        raise ValueError("render() reproduces the template only for message lists with a system message")
    last_user = max((i for i, m in enumerate(msgs) if m["role"] == "user"), default=-1)
    tools_json = go_json([_go_tool(t) for t in tools]) if tools else ""
    out = []
    for i, m in enumerate(msgs):
        role = m["role"]
        if role == "system":
            out.append(f"[SYSTEM_PROMPT]{m['content']}[/SYSTEM_PROMPT]")
        elif role == "user":
            if i == last_user and tools:
                out.append(f"[AVAILABLE_TOOLS]{tools_json}[/AVAILABLE_TOOLS]")
            out.append(f"[INST]{m['content']}[/INST]")
        elif role == "assistant":
            calls = m.get("tool_calls") or []
            if m.get("content"):
                out.append(m["content"])
                if not calls and i != len(msgs) - 1:
                    out.append("</s>")
            if calls:
                out.append(CALL_TAG)
                for c in calls:
                    fn = c.get("function") or {}
                    out.append(f"{fn.get('name', '')}{ARGS_TAG}{go_json(fn.get('arguments') or {})}")
                out.append("</s>")
        elif role == "tool":
            out.append(f"[TOOL_RESULTS]{m['content']}[/TOOL_RESULTS]")
    return "".join(out)


def parse_raw(text: str, names=()) -> tuple[str, dict]:
    """The model's raw reply as the loop's text, and what was found. A complete first call -> its JSON text; anything
    else -> the reply as written (stripped of surrounding whitespace).

    A call is `[TOOL_CALLS]name[ARGS]{...}` or, as `/api/generate` returns it with special tokens dropped,
    `name{...}` where `name` is one of the tool names given (`names`), directly followed by a JSON object. The name may
    follow any non-word character, including the full stop that ends a sentence of prose before the call (wave 1 of
    the 3-arm re-baseline: "...Let me continue reading the file.read_file{...}"; Ollama's own parser reads those)."""
    i = text.find(CALL_TAG)
    if i >= 0:
        rest = text[i + len(CALL_TAG):]
        j = rest.find(ARGS_TAG)
        name = rest[:j].strip() if j >= 0 else ""
        if j < 0 or not name:
            return text.strip(), {"tool_calls": 0, "raw_tag": True, "malformed": "no name/[ARGS]"}
        body, tagged = rest[j + len(ARGS_TAG):], True
    else:
        m = re.search(r"(?<![\w])(" + "|".join(map(re.escape, sorted(names, key=len, reverse=True))) + r")\s*(?=\{)",
                      text) if names else None
        if not m:
            return text.strip(), {"tool_calls": 0, "raw_tag": False}
        name, body, tagged = m.group(1), text[m.end():], False
    try:
        args, _end = json.JSONDecoder().raw_decode(body.lstrip())
    except ValueError as exc:
        return text.strip(), {"tool_calls": 0, "raw_tag": tagged, "malformed": f"arguments: {str(exc)[:80]}"}
    if not isinstance(args, dict):
        return text.strip(), {"tool_calls": 0, "raw_tag": tagged, "malformed": "arguments not an object"}
    calls = text.count(CALL_TAG) if tagged else len(re.findall(
        r"(?<![\w])(?:" + "|".join(map(re.escape, names)) + r")\s*\{", text))
    return json.dumps({"tool": name, "args": args}), {"tool_calls": calls, "raw_tag": tagged}


class OllamaRawChat:
    """`OllamaChat`'s interface; the prompt rendered by the harness, the reply parsed by the harness (see above)."""

    def __init__(self, base_url: str, model: str, tools: list[dict], timeout: int = 900,
                 answer_tokens: int = ANSWER_TOKENS):
        self.url, self.model, self.tools, self.timeout = base_url.rstrip("/"), model, tools, timeout
        self.answer_tokens = answer_tokens

    def __call__(self, messages: list[dict], seed: int) -> dict:
        prompt = render(messages, self.tools)
        body = {"model": self.model, "prompt": prompt, "raw": True, "stream": False,
                "options": {"temperature": TEMPERATURE, "seed": seed,
                            "num_ctx": NUM_CTX - ANSWER_TOKENS + self.answer_tokens,
                            "num_predict": self.answer_tokens}}
        req = urllib.request.Request(f"{self.url}/api/generate", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        t = time.time()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read())
        raw = data.get("response") or ""
        text, meta = parse_raw(raw, [t["function"]["name"] for t in self.tools])
        meta["raw_chars"] = len(raw)
        if text != raw.strip() or meta.get("malformed"):
            meta["raw_head"] = raw[:600]
        return {"text": text, "thinking": None, "prompt_tokens": data.get("prompt_eval_count", 0),
                "answer_tokens": data.get("eval_count", 0), "done_reason": data.get("done_reason"),
                "seconds": round(time.time() - t, 1), "meta": meta}
