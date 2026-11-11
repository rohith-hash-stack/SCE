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
    "prism_digest": ('{"symbols": [str]}', "Line-cited summaries of up to 10 functions (parameters, calls, raises, "
                     "returns)."),
}
BASELINE_DOC = {
    "related_code": ('{"query": str}', "The functions a keyword search over the repository ranks highest, as code."),
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


def tool_docs(version: str) -> str:
    docs = {**SHARED_DOC, **(PRISM_DOC if version == "PRISM" else BASELINE_DOC)}
    return "\n".join(f"- {name} {sig}: {what}" for name, (sig, what) in docs.items())


def system_prompt(version: str, plan: bool = False) -> str:
    """Identical in both versions except the tool list (parity table)."""
    return (SYSTEM + (PLAN_ADDENDUM if plan else "")).format(tools=tool_docs(version))


class OllamaChat:
    """One chat call to an Ollama server; the answer is constrained to JSON (`format: json`)."""

    def __init__(self, base_url: str, model: str = MODEL, timeout: int = 900, think: bool | None = None,
                 answer_tokens: int = ANSWER_TOKENS, json_format: bool = True):
        """`think` and `answer_tokens`: the planning investigation's reasoning-model runs (off by default). The prompt
        window stays NUM_CTX - ANSWER_TOKENS, as for every run; a longer answer (thinking included) widens num_ctx."""
        self.url, self.model, self.timeout = base_url.rstrip("/"), model, timeout
        self.think, self.answer_tokens, self.json_format = think, answer_tokens, json_format

    def __call__(self, messages: list[dict], seed: int) -> dict:
        req_body = {"model": self.model, "messages": messages, "stream": False,
                    "options": {"temperature": TEMPERATURE, "seed": seed,
                                "num_ctx": NUM_CTX - ANSWER_TOKENS + self.answer_tokens,
                                "num_predict": self.answer_tokens}}
        if self.json_format:
            req_body["format"] = "json"
        if self.think is not None:
            req_body["think"] = self.think
        body = json.dumps(req_body).encode()
        req = urllib.request.Request(f"{self.url}/api/chat", data=body, headers={"Content-Type": "application/json"})
        t = time.time()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read())
        text, thinking = data.get("message", {}).get("content", ""), data.get("message", {}).get("thinking")
        if "</think>" in text:          # a model that writes its reasoning into the answer: the tool call follows it
            head, text = text.rsplit("</think>", 1)
            thinking = (thinking or "") + head.replace("<think>", "")
        return {"text": text, "thinking": thinking,
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "answer_tokens": data.get("eval_count", 0), "done_reason": data.get("done_reason"),
                "seconds": round(time.time() - t, 1)}


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
                 gate=None):
        """`gate`: amendment R4-SANITY-2's verification at done (`r4/done_gate.DoneGate`), or None (R3a)."""
        """`shared`: SharedTools. `index_tools`: the ToolLayer (PRISM's five tools, or `related_code`)."""
        self.task, self.version, self.seed, self.model = task, version, seed, model
        self.shared, self.index_tools, self.turn_cap, self.plan = shared, index_tools, turn_cap, plan
        self.gate = gate
        from tokenizers import Tokenizer
        self.tok = Tokenizer.from_file(tokenizer_path) if tokenizer_path else None
        self.allowed = set(SHARED_DOC) | set(PRISM_DOC if version == "PRISM" else BASELINE_DOC)

    def n(self, text: str) -> int:
        return len(self.tok.encode(text).ids) if self.tok else len(text) // 4

    # ------------------------------------------------------------------ the window rule
    def window(self, system: str, task_msg: str, history: list[dict]) -> tuple[list[dict], int]:
        """The messages for one call, and how many tool outputs were stubbed."""
        msgs = [dict(m) for m in history]
        base = self.n(system) + self.n(task_msg)
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
                msgs, stubbed = self.window(system, task_msg, history)
                est = sum(self.n(m["content"]) for m in msgs) + ANSWER_TOKENS
                if used + est > BUDGET_TOKENS:
                    end = "budget"
                    break
                turn += 1
                try:
                    reply = self.model(msgs, self.seed)
                except Exception as exc:  # an infrastructure failure: recorded; the run ends (counts as not resolved)
                    end = f"model_error: {type(exc).__name__}: {exc}"
                    break
                used += reply["prompt_tokens"] + reply["answer_tokens"]
                rec = {"turn": turn, "prompt_tokens": reply["prompt_tokens"], "answer_tokens": reply["answer_tokens"],
                       "done_reason": reply["done_reason"], "model_seconds": reply["seconds"], "stubbed": stubbed,
                       "used_tokens": used, "reply": reply["text"]}
                if reply.get("thinking"):
                    rec["thinking"], rec["thinking_tokens"] = reply["thinking"], self.n(reply["thinking"])
                plan = _plan_of(reply["text"]) if self.plan else None
                if plan:
                    rec["plan"] = plan
                call = parse_call(reply["text"])
                if call is None:
                    rec["parse_error"] = True
                    history += [{"role": "assistant", "content": reply["text"]},
                                {"role": "user", "content": FORMAT_REMINDER}]
                    log.write(json.dumps(rec) + "\n")
                    continue
                tool, args = call
                rec.update(tool=tool, args=args)
                t = time.time()
                res = self.execute(tool, args)
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
                           output_cut="[cut at 4,000 tokens]" in res["output"], rss_mb=_rss_mb(),
                           **{k: v for k, v in res.items() if k != "output"})
                log.write(json.dumps(rec) + "\n")
                log.flush()
                said = {"plan": plan, "tool": tool, "args": args} if plan else {"tool": tool, "args": args}
                history += [{"role": "assistant", "content": json.dumps(said)},
                            {"role": "user", "content": res["output"], "tool_output": True, "tool": tool}]
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
