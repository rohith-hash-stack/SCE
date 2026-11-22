"""R3's agent loop, identical in both versions (pre-registration, "The loop and its parity"; R3-1 and R3-5).

    runner = AgentRun(task, version, seed, model=OllamaChat(url, MODEL), tools=..., tokenizer_path=...)
    result = runner.run(out_dir)          # writes OUT/<run id>.jsonl (every turn) and OUT/<run id>.json (the summary)

**One turn** is one model call, which returns one JSON tool call: `{"tool": NAME, "args": {...}}`. `done` ends the
run.

**The window rule** (20,480 tokens per call, the same in both versions):
- the system prompt and the task are always kept;
- then the most recent turns that fit, with 4,096 tokens left for the answer;
- older tool outputs are replaced, oldest first, by one-line stubs ("output of `read_file` omitted: N tokens");
- only if that is not enough are the oldest turns dropped.

**The budget:** 300,000 tokens per run, input plus output, as Ollama counts them (`prompt_eval_count` +
`eval_count`). The run ends as if `done` when the next call would pass it.

**The turn cap:** `turn_cap` (30 until the pilot sets it, amendment R3-1, item 2).

**A reply that is not one valid tool call** costs its turn. The model is told the format again; nothing is retried
for it.

**Logged per turn:** the call's input and output tokens, the answer's stop reason (`length` = cut at the answer cap),
the tool and its arguments, the tool output's size, whether it was cut at the 4,000-token cap, any R3-5 notice and
its token count, the refresh (rebuild, gated files), and the elapsed time.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.request

MODEL = "qwen2.5-coder:14b-instruct-q8_0"
NUM_CTX = 20480
ANSWER_TOKENS = 4096
BUDGET_TOKENS = 300_000
DEFAULT_TURN_CAP = 30
TEMPERATURE = 0.2
STUCK_DONE_REJECTIONS = 3     # amendment R4-SANITY-2

SHARED_DOC = {
    "read_file": ('{"path": str, "start": int = 1, "end": int | null}', "Read a file's lines (numbered)."),
    "list_dir": ('{"path": str = "."}', "List a directory."),
    "grep": ('{"pattern": str, "path": str = "."}', "Search the repository's files for a regular expression."),
    "edit_file": ('{"path": str, "old": str, "new": str}',
                  "Replace one exact, unique span of a file with new text. With old = \"\", create a new file."),
    "run_tests": ('{"files": [str] | null}', "Run test files (default: the task's tests). Reports failures."),
    "done": ('{"summary": str}', "Finish: your changes are complete."),
}
PRISM_DOC = {
    "prism_change_context": ('{"symbol": str}', "Everything to read before changing a function: its callers and what "
                             "each does with it, its dependencies, the style to follow, the callers' code, the wider "
                             "impact."),
    "prism_blast_radius": ('{"symbol": str}', "Every function that can reach this one, by distance, with their code."),
    "prism_new_function_context": ('{"file": str, "name": str, "signature": str, "purpose": str, "cls": str | null}',
                                   "Before writing a new function: the existing helpers to reuse, conventions, and "
                                   "the closest existing functions."),
    "prism_ask": ('{"question": str}', "Ask where something is in the code: ranked functions."),
    "prism_read_code": ('{"symbol": str}', "The exact code of a symbol, a location (path:line) or a file that "
                        "prism_ask or prism_change_context named, with line numbers."),
    "prism_digest": ('{"symbols": [str]}', "Line-cited summaries of up to 10 functions (parameters, calls, raises, "
                     "returns)."),
    "prism_search": ('{"pattern": str, "path": str = ".", "kind": str | null}',
                     "Search the repository's files for a regular expression; each hit says what it is (import, "
                     "definition, call, type annotation, decorator, isinstance, except, string, comment), the "
                     "function or class it is in, and whether its file is a test, docs, config or template. `kind` "
                     "keeps one kind."),
    "prism_read": ('{"path": str, "start": int = 1, "end": int | null}',
                   "Read any file's lines (numbered; its current text, edits included), with the definitions in "
                   "them."),
}
BASELINE_DOC = {
    "related_code": ('{"query": str}', "The functions a keyword search over the repository ranks highest, as code."),
}
# BASELINE_LSP (Phase 3, the user's decision 2026-11-22): the baseline with a language server's reference search
# (`r3/lsp_tools.py`, jedi), as Copilot- or Claude-style agents have.
LSP_DOC = {
    "find_references": ('{"symbol": str}', "A language server's Find All References: every place in the project that "
                        "refers to this definition (resolved through imports, aliases and types; same-named things "
                        "that are other objects are not included), as path:line:column and the line. `symbol`: a "
                        "dotted name (pkg.module.Class.method, or Class.method) or path:line[:column] of a use."),
    "semantic_search": ('{"query": str}', "Search the code by meaning (words, not exact text): the code chunks "
                        "(functions, classes, module code) that best match, with their lines."),
    "ast_search": ('{"pattern": str, "path": str = "."}', "Structural search of the Python code with an ast-grep "
                   "pattern: `$X.close()` (any receiver), `$F($A, $B)` (two arguments), `def $NAME($$$ARGS): "
                   "$$$BODY`, `class $C($BASE)`. Each match's path:line:column and line."),
    "run_shell": ('{"command": str}', "Run a read-only shell command in the repository (ls, cat, head, find, wc, rg, "
                  "grep, git log/show/diff/status/blame, ...; joined by |, && or ;). No redirection; use edit_file to "
                  "change files."),
}
# Phase 3 PRISM_LSP and PRISM_ONLY (the user's decision 2026-11-22): PRISM's own Find All References, from its graph.
PRISM_REFS_DOC = {
    "prism_find_references": ('{"symbol": str}', "Find All References from PRISM's graph: every place that refers to "
                              "this definition, at its line and column, labelled (call, import, type annotation, "
                              "subclass, isinstance, decorator, value...), each with the function it is in; calls "
                              "PRISM could not resolve by type are listed apart as possible. `symbol`: a qualified "
                              "name or path:line of a use or of the definition."),
}

SYSTEM = """You are a software engineer working in a code repository. You work in turns: each turn, reply with
exactly one JSON object calling one tool:
{{"tool": "<name>", "args": {{...}}}}
No other text. When your changes are complete, call "done".

Tools:
{tools}
Symbols are fully qualified names as the repository's index writes them (e.g. `pkg.module.Class.method`)."""

# The planning investigation (`r4/PLANNING_INVESTIGATION.md`; opt-in, off for R3a and the R4 sanity run): every reply
# carries a one-sentence plan inside the JSON (replies are constrained to JSON), and the plan stays in the history.
PLAN_ADDENDUM = """

Planning: every reply also states your plan, first, inside the same JSON object:
{{"plan": "I will <action> because <reason>. Then <next step>.", "tool": "<name>", "args": {{...}}}}
Base the plan on what the tools have shown you so far."""

FORMAT_REMINDER = ('Your reply was not one JSON tool call. Reply with exactly one JSON object: '
                   '{"tool": "<name>", "args": {...}}.')


# Phase 0a: the same tools as JSON schemas, for providers whose models call tools natively (gpt-oss-120b). One table
# for both arms: a shared tool has the same schema in both, and an arm's list differs only as its text prompt does.
_S, _I = {"type": "string"}, {"type": "integer"}
PARAM_SCHEMAS = {
    "read_file": ({"path": _S, "start": _I, "end": {"type": ["integer", "null"]}}, ["path"]),
    "list_dir": ({"path": _S}, []),
    "grep": ({"pattern": _S, "path": _S}, ["pattern"]),
    "edit_file": ({"path": _S, "old": _S, "new": _S}, ["path", "old", "new"]),
    "run_tests": ({"files": {"type": ["array", "null"], "items": _S}}, []),
    "done": ({"summary": _S}, []),
    "prism_change_context": ({"symbol": _S}, ["symbol"]),
    "prism_blast_radius": ({"symbol": _S}, ["symbol"]),
    "prism_new_function_context": ({"file": _S, "name": _S, "signature": _S, "purpose": _S,
                                    "cls": {"type": ["string", "null"]}}, ["file", "name"]),
    "prism_ask": ({"question": _S}, ["question"]),
    "prism_read_code": ({"symbol": _S}, ["symbol"]),
    "prism_digest": ({"symbols": {"type": "array", "items": _S}}, ["symbols"]),
    "prism_search": ({"pattern": _S, "path": _S, "kind": {"type": ["string", "null"]}}, ["pattern"]),
    "prism_read": ({"path": _S, "start": _I, "end": {"type": ["integer", "null"]}}, ["path"]),
    "related_code": ({"query": _S}, ["query"]),
    "find_references": ({"symbol": _S}, ["symbol"]),
    "semantic_search": ({"query": _S}, ["query"]),
    "ast_search": ({"pattern": _S, "path": _S}, ["pattern"]),
    "run_shell": ({"command": _S}, ["command"]),
    "prism_find_references": ({"symbol": _S}, ["symbol"]),
}


# The arms with PRISM's tools: PRISM (the tools only) and INJECTED (the 3-arm re-baseline's Arm 3: PRISM's context
# injected up front, and the same tools). BASELINE has `related_code` instead.
# PRISM_ONLY (Phase 2 D3; support only, run only under its own pre-registration): PRISM's tools with `prism_search`
# and `prism_read` in place of `grep` and `read_file`, to test whether PRISM can replace standard search.
PRISM_ARMS = ("PRISM", "INJECTED", "PRISM_ONLY", "PRISM_LSP")
ARMS = ("PRISM", "BASELINE", "INJECTED", "PRISM_ONLY", "BASELINE_LSP", "PRISM_LSP")
#: Phase 3's arms (the user's decision, 2026-11-22). BASELINE_LSP: the shared tools and an IDE agent's tools (a
#: language server's references, semantic search, ast-grep, a read-only shell). PRISM_LSP: the shared tools and all of
#: PRISM's tools, `prism_find_references` among them (not the language server's). PRISM_ONLY: PRISM's tools, with
#: `prism_find_references`, without `grep` and `read_file`.
PHASE3_ARMS = ("BASELINE_LSP", "PRISM_LSP", "PRISM_ONLY")
#: the shared tools an arm does without
REMOVED_SHARED = {"PRISM_ONLY": ("read_file", "grep")}


def index_doc(version: str) -> dict:
    if version == "BASELINE_LSP":
        return dict(LSP_DOC)
    if version in ("PRISM_LSP", "PRISM_ONLY"):
        return {**PRISM_DOC, **PRISM_REFS_DOC}
    return PRISM_DOC if version in PRISM_ARMS else BASELINE_DOC


def shared_doc(version: str) -> dict:
    return {k: v for k, v in SHARED_DOC.items() if k not in REMOVED_SHARED.get(version, ())}


def native_tools(version: str) -> list[dict]:
    """The version's tools in the OpenAI function-calling format, in the text prompt's order and with its
    descriptions."""
    docs = {**shared_doc(version), **index_doc(version)}
    out = []
    for name, (_sig, what) in docs.items():
        props, required = PARAM_SCHEMAS[name]
        out.append({"type": "function", "function": {
            "name": name, "description": what,
            "parameters": {"type": "object", "properties": props, "required": required}}})
    return out


def tool_docs(version: str) -> str:
    docs = {**shared_doc(version), **index_doc(version)}
    return "\n".join(f"- {name} {sig}: {what}" for name, (sig, what) in docs.items())


def system_prompt(version: str, plan: bool = False) -> str:
    """Identical in both versions except the tool list (parity table)."""
    return (SYSTEM + (PLAN_ADDENDUM if plan else "")).format(tools=tool_docs(version))


DEGENERATE_TAIL_CHARS = 400   # Phase 1 item 3: a reply ending in this many characters of whitespace / escaped newlines
_DEGENERATE_TAIL = re.compile(r"(?:\s|\\[nrt])+\Z")


def degenerate_tail(text: str) -> int:
    """Length of the reply's trailing run of whitespace and escaped `\\n`/`\\r`/`\\t` sequences."""
    m = _DEGENERATE_TAIL.search(text)
    return len(m.group(0)) if m else 0


def repair_unclosed(text: str, max_braces: int = 2) -> tuple[str, int] | None:
    """A reply cut after a degenerate tail, with only its final closing braces missing: the text with 1..max_braces
    `}` appended, if that makes exactly one valid tool call; else None. Never changes what the model wrote."""
    s = text.rstrip()
    for k in range(1, max_braces + 1):
        if parse_call(s + "}" * k) is not None:
            return s + "}" * k, k
    return None


class OllamaChat:
    """One chat call to an Ollama server; the answer is constrained to JSON (`format: json`).

    `stream_cut` (Phase 1 item 3, off by default): the reply is streamed, and a reply that degenerates into a run of
    more than DEGENERATE_TAIL_CHARS whitespace characters or escaped newlines is cut there (the stream is closed, so
    the server stops generating). The cut text, without its tail, is returned with `done_reason` "degenerate_cut";
    if appending one or two closing braces makes it one valid tool call, that repaired text is returned and the repair
    is recorded in `meta`. Ollama sends no token counts for a cut reply (the loop then estimates them)."""

    def __init__(self, base_url: str, model: str = MODEL, timeout: int = 900, think: bool | None = None,
                 answer_tokens: int = ANSWER_TOKENS, json_format: bool = True, stream_cut: bool = False,
                 tools: list[dict] | None = None):
        """`think` and `answer_tokens`: the planning investigation's reasoning-model runs (off by default). The prompt
        window stays NUM_CTX - ANSWER_TOKENS, as for every run; a longer answer (thinking included) widens num_ctx.

        `tools` (`native_tools(version)`, Phase 1): the model's native tool calling (Ollama's `tools`; devstral's
        template renders them as `[AVAILABLE_TOOLS]` / `[TOOL_CALLS]name[ARGS]{...}` / `[TOOL_RESULTS]`). JSON mode is
        off with it. The loop's text history is translated for the request (`native_messages`), and the model's call
        is returned as the loop's `{"tool": ..., "args": ...}` text, as `OpenAIChat` does."""
        self.url, self.model, self.timeout = base_url.rstrip("/"), model, timeout
        self.think, self.answer_tokens = think, answer_tokens
        self.tools = tools
        self.json_format = json_format and not tools
        self.stream_cut = stream_cut and not tools

    def _stream(self, req: urllib.request.Request) -> tuple[dict, dict]:
        """Read a streamed reply; returns (the final data, as the non-streamed API would give it, and meta)."""
        text, thinking, final, meta, chunks = "", [], {}, {}, 0
        deadline = time.time() + self.timeout
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            for line in r:
                if not line.strip():
                    continue
                chunk = json.loads(line)
                chunks += 1
                msg = chunk.get("message") or {}
                text += msg.get("content") or ""
                if msg.get("thinking"):
                    thinking.append(msg["thinking"])
                if chunk.get("done"):
                    final = chunk
                    break
                if time.time() > deadline:
                    raise TimeoutError(f"no complete reply within {self.timeout} s")
                if degenerate_tail(text[-(DEGENERATE_TAIL_CHARS + 8):]) > DEGENERATE_TAIL_CHARS:
                    cut = degenerate_tail(text)
                    meta = {"degenerate_cut": True, "tail_chars": cut, "chunks": chunks}
                    final = {"done_reason": "degenerate_cut", "_cut": True}
                    text = text[: len(text) - cut]
                    break                                         # leaving `with` closes the stream: Ollama stops
        if final.pop("_cut", False):
            fixed = repair_unclosed(text)
            if fixed is not None:
                text, meta["repaired_closing_braces"] = fixed[0], fixed[1]
        final.setdefault("message", {})
        final["message"] = {"content": text, "thinking": "".join(thinking) or None}
        return final, meta

    def __call__(self, messages: list[dict], seed: int) -> dict:
        if self.tools:
            messages = native_messages(messages)
        req_body = {"model": self.model, "messages": messages, "stream": self.stream_cut,
                    "options": {"temperature": TEMPERATURE, "seed": seed,
                                "num_ctx": NUM_CTX - ANSWER_TOKENS + self.answer_tokens,
                                "num_predict": self.answer_tokens}}
        if self.json_format:
            req_body["format"] = "json"
        if self.tools:
            req_body["tools"] = self.tools
        if self.think is not None:
            req_body["think"] = self.think
        body = json.dumps(req_body).encode()
        req = urllib.request.Request(f"{self.url}/api/chat", data=body, headers={"Content-Type": "application/json"})
        t = time.time()
        meta = {}
        if self.stream_cut:
            data, meta = self._stream(req)
        else:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read())
        text, thinking = data.get("message", {}).get("content", ""), data.get("message", {}).get("thinking")
        if self.tools:
            text, n_calls = OpenAIChat._as_text(data.get("message") or {})
            meta["tool_calls"] = n_calls
        if "</think>" in text:          # a model that writes its reasoning into the answer: the tool call follows it
            head, text = text.rsplit("</think>", 1)
            thinking = (thinking or "") + head.replace("<think>", "")
        out = {"text": text, "thinking": thinking,
               "prompt_tokens": data.get("prompt_eval_count", 0),
               "answer_tokens": data.get("eval_count", 0), "done_reason": data.get("done_reason"),
               "seconds": round(time.time() - t, 1)}
        if meta:
            out["meta"] = meta
        return out


def native_messages(messages: list[dict]) -> list[dict]:
    """The loop's text history in the native tool-calling shape: an assistant message that is one tool call becomes
    `tool_calls`, and the user message right after it (the tool's output) becomes a `tool` message. Anything else (the
    task, a reply that was not a call, the format reminder after it) is unchanged. The text the model sees is the same;
    only its framing follows the model's own template."""
    out, after_call = [], False
    for m in messages:
        if m["role"] == "assistant":
            call = parse_call(m["content"])
            if call is not None:
                out.append({"role": "assistant", "content": "",
                            "tool_calls": [{"function": {"name": call[0], "arguments": call[1]}}]})
                after_call = True
                continue
        elif m["role"] == "user" and after_call:
            out.append({"role": "tool", "content": m["content"]})
            after_call = False
            continue
        after_call = False
        out.append(dict(m))
    return out


class OpenAIChat:
    """Phase 0a: one chat call to an OpenAI-compatible endpoint (gpt-oss-120b on Groq), returning what the loop reads
    from `OllamaChat`. The request shape is `harness/llm.ChatLLM`'s (`/chat/completions`), plus:
    - the API key from an environment variable (never logged);
    - up to RETRIES retries on HTTP 429 and 5xx, waiting for Retry-After when given (else 2, 4, 8 s); retries are not
      turns. Any other error, a timeout included, or a 429/5xx after the retries, propagates (an infrastructure death);
    - the model's `reasoning` field as `thinking`;
    - per call: the model id and fingerprint returned, retries made, and the rate-limit headers, as `meta`."""

    RETRIES = 3
    RATE_HEADERS = ("x-ratelimit-limit-requests", "x-ratelimit-limit-tokens", "x-ratelimit-remaining-requests",
                    "x-ratelimit-remaining-tokens", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens")

    def __init__(self, base_url: str, model: str, api_key_env: str, timeout: int = 300,
                 answer_tokens: int = ANSWER_TOKENS, reasoning_effort: str | None = None, json_format: bool = True,
                 tools: list[dict] | None = None):
        """`tools` (`native_tools(version)`): the model calls tools natively; its call is returned to the loop as the
        same `{"tool": ..., "args": ...}` text `parse_call` reads. JSON mode is not combined with it."""
        self.url, self.model, self.timeout = base_url.rstrip("/"), model, timeout
        self.api_key_env, self.answer_tokens = api_key_env, answer_tokens
        self.reasoning_effort, self.tools = reasoning_effort, tools
        self.json_format = json_format and not tools
        self.sleep = time.sleep                   # replaced in tests

    def payload(self, messages: list[dict], seed: int) -> dict:
        body = {"model": self.model, "messages": messages, "temperature": TEMPERATURE, "seed": seed,
                "max_tokens": self.answer_tokens}
        if self.json_format:
            body["response_format"] = {"type": "json_object"}
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        if self.tools:
            body.update(tools=self.tools, tool_choice="required", parallel_tool_calls=False)
        return body

    @staticmethod
    def _as_text(msg: dict) -> tuple[str, int]:
        """The model's native tool call as the loop's text (`{"tool": name, "args": {...}}`), and how many calls it
        made (the loop takes the first). Arguments that are not a JSON object are kept as text, so the loop's
        format reminder answers them as it answers any malformed reply."""
        calls = msg.get("tool_calls") or []
        if not calls:
            return msg.get("content") or "", 0
        fn = calls[0].get("function") or {}
        raw = fn.get("arguments")
        try:
            args = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except ValueError:
            return f'{{"tool": {json.dumps(fn.get("name"))}, "args": {raw}}}', len(calls)
        if not isinstance(args, dict):
            return f'{{"tool": {json.dumps(fn.get("name"))}, "args": {json.dumps(args)}}}', len(calls)
        return json.dumps({"tool": fn.get("name"), "args": args}), len(calls)

    def __call__(self, messages: list[dict], seed: int) -> dict:
        import urllib.error
        key = os.environ.get(self.api_key_env)
        if not key:
            raise RuntimeError(f"environment variable {self.api_key_env} is not set")
        data = json.dumps(self.payload(messages, seed)).encode()
        retries, waited = 0, 0.0
        t = time.time()
        while True:
            req = urllib.request.Request(f"{self.url}/chat/completions", data=data, headers={
                "Content-Type": "application/json", "Authorization": f"Bearer {key}",
                # Groq's edge refuses Python's default urllib User-Agent (Cloudflare error 1010)
                "User-Agent": "prism-harness/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    out = json.loads(r.read())
                    headers = {h: r.headers.get(h) for h in self.RATE_HEADERS if r.headers.get(h) is not None}
                break
            except urllib.error.HTTPError as exc:
                if (exc.code == 429 or 500 <= exc.code < 600) and retries < self.RETRIES:
                    ra = exc.headers.get("retry-after") if exc.headers else None
                    try:
                        wait = float(ra) if ra else 2.0 * 2 ** retries
                    except ValueError:
                        wait = 2.0 * 2 ** retries
                    wait = min(wait, 120.0)
                    retries += 1
                    waited += wait
                    self.sleep(wait)
                    continue
                body = exc.read().decode("utf8", "replace") if hasattr(exc, "read") else ""
                failed = self._tool_use_failed(exc.code, body)
                if failed is not None:
                    # the provider refused the model's tool call (e.g. a tool it was not given): a bad reply that
                    # costs the model its turn, as a malformed text reply does, not an infrastructure death
                    return {"text": failed, "thinking": None, "prompt_tokens": 0, "answer_tokens": 0,
                            "done_reason": "tool_use_failed", "seconds": round(time.time() - t - waited, 1),
                            "meta": {"model": self.model, "retries": retries, "retry_wait_s": round(waited, 1),
                                     "provider_error": body[:300]}}
                raise RuntimeError(f"HTTP {exc.code} after {retries} retries: {body[:300]}") from exc
        choice = (out.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        usage = out.get("usage") or {}
        text, n_calls = self._as_text(msg) if self.tools else (msg.get("content") or "", 0)
        return {"text": text, "thinking": msg.get("reasoning"),
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "answer_tokens": int(usage.get("completion_tokens") or 0),
                "done_reason": choice.get("finish_reason"), "seconds": round(time.time() - t - waited, 1),
                "meta": {"model": out.get("model"), "system_fingerprint": out.get("system_fingerprint"),
                         "retries": retries, "retry_wait_s": round(waited, 1), "rate": headers,
                         **({"tool_calls": n_calls} if self.tools else {})}}

    @staticmethod
    def _tool_use_failed(code: int, body: str) -> str | None:
        """For HTTP 400 `tool_use_failed`: the generation the provider refused, else None."""
        if code != 400:
            return None
        try:
            err = json.loads(body).get("error") or {}
        except (ValueError, AttributeError):
            return None
        if not isinstance(err, dict) or err.get("code") != "tool_use_failed":
            return None
        return err.get("failed_generation") or ""


PARSE_ERROR_KEEP_CHARS = 400  # Phase 1 item 3 (`hygiene`): how much of an invalid reply goes back into the history
OUTPUT_HEAD_CHARS = 300      # the start of each tool output, kept in the turn log for diagnosis (never shown again)


def stop_kind(end: str | None) -> str:
    """How a run stopped, as the analysis names it (BUILD_PLAN section 4, criterion 6):
    `done`: the agent called done; `stuck`: the turn cap, without done; `budget`: the token budget, without done;
    `error`: the model call failed (infrastructure)."""
    if end == "done":
        return "done"
    if end == "turn_cap":
        return "stuck"
    if end == "budget":
        return "budget"
    if end == "stuck_done":     # amendment R4-SANITY-2: 3 rejected dones in a row with no applied edit between
        return "stuck_done"
    return "error"


def _rss_mb() -> float | None:
    """This process's resident memory after a tool call (the memory test's per-call series, amendment R3-7)."""
    try:
        with open("/proc/self/status") as fh:
            for ln in fh:
                if ln.startswith("VmRSS:"):
                    return round(int(ln.split()[1]) / 1024, 1)
    except OSError:
        return None
    return None


def turn_counts(log_path: str) -> dict:
    """Per-run process counts from the turn log."""
    turns = [json.loads(ln) for ln in open(log_path)] if os.path.exists(log_path) else []
    return {"parse_errors": sum(1 for t in turns if t.get("parse_error")),
            "tool_calls": {k: sum(1 for t in turns if t.get("tool") == k) for k in sorted({t.get("tool") for t in turns
                                                                                          if t.get("tool")})},
            "outputs_cut": sum(1 for t in turns if t.get("output_cut")),
            "turns_stubbed": sum(1 for t in turns if t.get("stubbed")),
            "notice_turns": sum(1 for t in turns if t.get("notice"))}


def _plan_of(text: str) -> str | None:
    """The reply's "plan" field (the planning investigation), or None."""
    try:
        m = re.search(r"\{.*\}", text, re.S)
        obj = json.loads(m.group(0)) if m else None
    except ValueError:
        return None
    p = obj.get("plan") if isinstance(obj, dict) else None
    return p.strip() if isinstance(p, str) and p.strip() else None


def parse_call(text: str) -> tuple[str, dict] | None:
    """`(tool, args)` from the model's reply, or None when it is not exactly one tool call."""
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        obj = json.loads(s)
    except ValueError:
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            return None
    if not isinstance(obj, dict) or not isinstance(obj.get("tool"), str):
        return None
    args = obj.get("args", {})
    return (obj["tool"], args) if isinstance(args, dict) else None


class AgentRun:
    def __init__(self, task: dict, version: str, seed: int, model, shared, index_tools=None,
                 tokenizer_path: str | None = None, turn_cap: int = DEFAULT_TURN_CAP, plan: bool = False,
                 gate=None, ledger=None, record_full: bool = False, hygiene: bool = False, compact: bool = False,
                 feedback: bool = False):
        """`gate`: amendment R4-SANITY-2's verification at done (`r4/done_gate.DoneGate`), or None (R3a).
        `ledger`: amendment R4-SANITY-4's per-turn progress summary (`r4/progress_ledger.ProgressLedger`), or None.
        `hygiene` (Phase 1 item 3, both arms): a reply that is not one tool call goes back into the history cut to
        PARSE_ERROR_KEEP_CHARS; a call the server returned without token counts is counted with the tokenizer.
        `compact` (Phase 1 item 2, both arms): the history compaction rules (`r3/compaction.py`) before the window
        rule.
        The prompts are the same with native tool calling (`OllamaChat(tools=...)`): no wording is added for it."""
        """`shared`: SharedTools. `index_tools`: the ToolLayer (PRISM's six tools, or `related_code`)."""
        self.task, self.version, self.seed, self.model = task, version, seed, model
        self.shared, self.index_tools, self.turn_cap, self.plan = shared, index_tools, turn_cap, plan
        self.gate, self.ledger = gate, ledger
        self.record_full = record_full    # Phase 0b replays: the full prompt and output in the turn log
        self.hygiene, self.compact = hygiene, compact
        # Phase 2 C3 (`r3/feedback.py`; every arm, every tool): a note when a call repeats an earlier identical call
        # and gets the identical answer
        self.repeats = None
        if feedback:
            from harness.experiments.r3.feedback import Repeats
            self.repeats = Repeats()
        from tokenizers import Tokenizer
        self.tok = Tokenizer.from_file(tokenizer_path) if tokenizer_path else None
        self.allowed = set(shared_doc(version)) | set(index_doc(version))

    def n(self, text: str) -> int:
        return len(self.tok.encode(text).ids) if self.tok else len(text) // 4

    # ------------------------------------------------------------------ the window rule
    def window(self, system: str, task_msg: str, history: list[dict], tail: str = "") -> tuple[list[dict], int]:
        """The messages for one call, and how many tool outputs were stubbed. `tail` (the progress ledger) is appended
        to the newest message, rebuilt every turn, so the window rule never stubs it."""
        msgs = [dict(m) for m in history]
        base = self.n(system) + self.n(task_msg) + (self.n(tail) if tail else 0)
        size = lambda: base + sum(self.n(m["content"]) for m in msgs) + 8 * (len(msgs) + 2)
        stubbed = 0
        for m in msgs:                                   # oldest tool outputs first
            if size() + ANSWER_TOKENS <= NUM_CTX:
                break
            if m["role"] == "user" and m.get("tool_output") and not m.get("stub"):
                m["content"] = f"(output of `{m['tool']}` omitted to fit the window: {self.n(m['content'])} tokens)"
                m["stub"] = True
                stubbed += 1
        while size() + ANSWER_TOKENS > NUM_CTX and len(msgs) > 2:   # still too long: drop the oldest turn
            msgs = msgs[2:]
        out = [{"role": "system", "content": system}, {"role": "user", "content": task_msg}]
        out += [{"role": m["role"], "content": m["content"]} for m in msgs]
        if tail and len(out) > 2:
            out[-1] = {**out[-1], "content": out[-1]["content"] + "\n\n" + tail}
        return out, stubbed

    # ------------------------------------------------------------------ one tool call
    def execute(self, tool: str, args: dict) -> dict:
        if tool not in self.allowed:
            return {"output": f"error: unknown tool {tool!r}; the tools are: {', '.join(sorted(self.allowed))}"}
        if tool in SHARED_DOC:
            return {"output": self.shared.call(tool, **args)}
        out = self.index_tools.call(tool, **args)
        r = getattr(self.index_tools, "last_refresh", None)
        rec = {"output": out}
        if r is not None:
            rec["refresh"] = {"changed": r.changed, "gated": r.gated, "rebuilt": r.rebuilt,
                              "probe_ms": round(r.probe_seconds * 1000), "build_s": round(r.build_seconds, 1)}
        if out.startswith("⚠️"):
            notice = out.split("\n\n", 1)[0]
            rec["notice"], rec["notice_tokens"] = notice, self.n(notice)
        return rec

    # ------------------------------------------------------------------ the run
    def run(self, out_dir: str) -> dict:
        os.makedirs(out_dir, exist_ok=True)
        run_id = f"{self.task['id']}__{self.version}__{self.seed}"
        summary_path = os.path.join(out_dir, run_id + ".json")
        if os.path.exists(summary_path):                  # resume: a finished run is never repeated
            with open(summary_path) as fh:
                return json.load(fh)
        log_path = os.path.join(out_dir, run_id + ".jsonl")
        system, task_msg = system_prompt(self.version, self.plan), self.task["prompt"]
        history: list[dict] = []
        used, turn, end = 0, 0, None
        rejected_in_a_row = rejections = 0
        t0 = time.time()
        with open(log_path, "w") as log:
            while True:
                if turn >= self.turn_cap:
                    end = "turn_cap"
                    break
                tail = self.ledger.render(turn + 1) if self.ledger is not None and history else ""
                shown, compaction = history, None
                if self.compact:                                     # Phase 1 item 2: before the window rule
                    from harness.experiments.r3.compaction import compact
                    shown, compaction = compact(history, self.n)
                msgs, stubbed = self.window(system, task_msg, shown, tail)
                est = sum(self.n(m["content"]) for m in msgs) + ANSWER_TOKENS
                if used + est > BUDGET_TOKENS:
                    end = "budget"
                    break
                turn += 1
                # Phase 0b: the exact prompt sent, as a hash, so a replay can be compared byte for byte
                prompt_sha = hashlib.sha256(json.dumps(msgs, ensure_ascii=False).encode()).hexdigest()
                try:
                    reply = self.model(msgs, self.seed)
                except Exception as exc:  # an infrastructure failure: recorded; the run ends (counts as not resolved)
                    end = f"model_error: {type(exc).__name__}: {exc}"
                    break
                estimated = False
                if self.hygiene and not reply["prompt_tokens"]:      # Phase 1: no counts from the server
                    reply = {**reply, "prompt_tokens": est - ANSWER_TOKENS}
                    estimated = True
                if self.hygiene and not reply["answer_tokens"] and (reply["text"] or reply.get("thinking")):
                    reply = {**reply, "answer_tokens": self.n(reply["text"]) + self.n(reply.get("thinking") or "")}
                    estimated = True
                used += reply["prompt_tokens"] + reply["answer_tokens"]
                rec = {"turn": turn, "prompt_tokens": reply["prompt_tokens"], "answer_tokens": reply["answer_tokens"],
                       "done_reason": reply["done_reason"], "model_seconds": reply["seconds"], "stubbed": stubbed,
                       "used_tokens": used, "reply": reply["text"], "prompt_sha": prompt_sha}
                if compaction is not None:
                    rec["compaction"] = compaction
                if reply.get("meta"):
                    rec["model_meta"] = reply["meta"]
                if estimated:
                    rec["tokens_estimated"] = True
                if self.record_full:
                    rec["prompt"] = msgs
                if reply.get("thinking"):
                    rec["thinking"], rec["thinking_tokens"] = reply["thinking"], self.n(reply["thinking"])
                plan = _plan_of(reply["text"]) if self.plan else None
                if plan:
                    rec["plan"] = plan
                call = parse_call(reply["text"])
                if call is None:
                    rec["parse_error"] = True
                    said = reply["text"]
                    if self.hygiene and len(said) > PARSE_ERROR_KEEP_CHARS:     # Phase 1: not copied back in full
                        said = (said[:PARSE_ERROR_KEEP_CHARS].rstrip()
                                + f" … [reply cut here: {len(said)} characters, not one valid JSON tool call]")
                    history += [{"role": "assistant", "content": said},
                                {"role": "user", "content": FORMAT_REMINDER}]
                    log.write(json.dumps(rec) + "\n")
                    continue
                tool, args = call
                rec.update(tool=tool, args=args)
                t = time.time()
                res = self.execute(tool, args)
                if self.repeats is not None and tool != "done":
                    prev = self.repeats.check(turn, tool, args, res["output"])
                    if prev is not None:
                        res = {**res, "output": res["output"] + self.repeats.note(prev), "repeat_of_turn": prev}
                if self.repeats is not None:          # Phase 2 feedback in the turn log (outputs are kept only in part)
                    if tool == "edit_file" and "no longer parses" in res["output"]:
                        res = {**res, "edit_broke_syntax": True}
                    elif tool == "edit_file" and "still does not parse" in res["output"]:
                        res = {**res, "edit_left_syntax_error": True}
                    if tool == "run_tests" and "first error in the output:" in res["output"]:
                        res = {**res, "first_error_shown": True}
                if tool == "done" and self.gate is not None:
                    ok, verdict = self.gate.check()
                    res = {**res, "gate": {"accepted": ok, "message": verdict[:1500]}}
                    if not ok:
                        res["output"] = verdict
                        rejections += 1
                        rejected_in_a_row += 1
                elif tool == "edit_file" and res["output"].startswith(("edited", "created")):
                    rejected_in_a_row = 0
                rec.update(tool_seconds=round(time.time() - t, 1), output_tokens=self.n(res["output"]),
                           output_head=res["output"][:OUTPUT_HEAD_CHARS],
                           output_sha=hashlib.sha256(res["output"].encode()).hexdigest(),   # Phase 0b
                           **({"output": res["output"]} if self.record_full else {}),
                           output_cut="[cut at 4,000 tokens]" in res["output"], rss_mb=_rss_mb(),
                           **{k: v for k, v in res.items() if k != "output"})
                if self.ledger is not None:
                    self.ledger.update(turn, tool, args, res["output"], res.get("gate"))
                    rec["ledger"] = tail
                log.write(json.dumps(rec) + "\n")
                log.flush()
                said = {"plan": plan, "tool": tool, "args": args} if plan else {"tool": tool, "args": args}
                history += [{"role": "assistant", "content": json.dumps(said)},
                            {"role": "user", "content": res["output"], "tool_output": True, "tool": tool,
                             "args": args, "turn": turn}]
                if tool == "done" and not (res.get("gate") and not res["gate"]["accepted"]):
                    end = "done"
                    break
                if rejected_in_a_row >= STUCK_DONE_REJECTIONS:
                    end = "stuck_done"
                    break
        summary = {"run": run_id, "task": self.task["id"], "version": self.version, "seed": self.seed, "turns": turn,
                   "end": end, "done_called": end == "done", "stop": stop_kind(end), "used_tokens": used,
                   "seconds": round(time.time() - t0, 1), "done_summary": self.shared.done_summary,
                   "turn_cap": self.turn_cap, **turn_counts(log_path),
                   **({"gate_rejections": rejections} if self.gate is not None else {})}
        with open(summary_path, "w") as fh:
            json.dump(summary, fh, indent=1)
        return summary
