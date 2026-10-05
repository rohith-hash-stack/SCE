"""Arm 4 — Claude-Code-style agent loop (fidelity MEDIUM_HIGH).

The model explores the repository itself with read-only tools and answers
through an `answer` tool. Tool calls use the native Qwen2.5-Coder text
format, one JSON object (or a list of them) per block:

    <tools>{"name": "grep", "arguments": {"pattern": "def solve"}}</tools>

not Hermes' <tool_call>. The system prompt carries few-shot examples of
that format to activate it. Every block in a turn is parsed, not just the
first.

Tools (read-only; every path is resolved and must stay inside the repo):
  grep(pattern, path=".", glob=None)  ripgrep, `-m` per file, then the whole
                                      result truncated to 30 lines
  glob(pattern)                       matching file paths (at most 30)
  read(path, start_line=1, end_line)  at most 150 lines
  answer(response)                    the final answer, in the task's
                                      response format; ends the loop

Hard caps: 10 turns, 5 tool calls per turn (later ones are rejected),
grep 30 lines, read 150 lines, 10 s per tool subprocess (a timeout is a
soft failure: an error result and a counter, never a crash).

Duplicate calls, keyed by (tool, sorted arguments), are not re-executed:
the model is pointed to the earlier result.

Compaction: before each model call, if the conversation exceeds 60% of the
context window, grep/glob results are replaced by digests and every read
except the last 3 (by stable msg_id, never position) is replaced by a
one-line stub. The 3 most recent reads stay verbatim.

answer-in-batch: when a turn calls `answer` alongside other tools, the
other calls run first (recorded in the trajectory), then the answer ends
the loop; their results never reached the model, so they are not
delivered.

If no block in a turn parses, the turn is an error (`is_error=True`) and
the model is told so. If the model never calls `answer` within 10 turns,
one final call with the uniform answer prompt over what it gathered
produces the answer (`forced_answer`).

Delivered context (what the model's prompt held at its final call): each
tool result once, in the form the model last saw it: `tool_result_read`,
`tool_result_grep`, or `tool_result_digest` (the original kept in
provenance). Symbols are the definitions (Python `ast`) the result covers.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from harness import config as C
from harness.arms.base import (RESPONSE_CONTRACTS, SYSTEM_PROMPT, RetrievalArm, compose_user_prompt, item_header,
                               render_items)
from harness.ast_splitter import module_name
from harness.llm import Completion
from harness.scoring.canonical import DeliveredContext, DeliveredItem, ItemKind
from harness.scoring.latency import timed

TOOLS_RE = re.compile(r"<tools>\s*(.*?)\s*</tools>", re.DOTALL)
HERMES_RE = re.compile(r"<tool_call>")
TOOL_NAMES = ("grep", "glob", "read", "answer")

AGENT_SYSTEM_PROMPT = SYSTEM_PROMPT + """

You are working in a code repository through tools. Call a tool by writing one JSON object inside <tools></tools> tags:
<tools>{"name": "<tool>", "arguments": {...}}</tools>
You may make up to 5 calls in one turn (one <tools> block each). Tool results come back in the next message.

Tools:
- grep: search file contents with a regular expression. Arguments: {"pattern": str, "path": str (optional, default "."), "glob": str (optional, e.g. "*.py")}. At most 30 matching lines are returned.
- glob: list files matching a pattern. Arguments: {"pattern": str, e.g. "**/routing.py"}.
- read: read lines of a file. Arguments: {"path": str, "start_line": int (optional), "end_line": int (optional)}. At most 150 lines per call.
- answer: give your final answer and stop. Arguments: {"response": str}, where response is exactly what the task's Response format asks for.

Example (a different repository):
<tools>{"name": "grep", "arguments": {"pattern": "def parse_config", "glob": "*.py"}}</tools>
<tools>{"name": "glob", "arguments": {"pattern": "**/settings.py"}}</tools>
Then, after the results:
<tools>{"name": "read", "arguments": {"path": "app/config.py", "start_line": 10, "end_line": 60}}</tools>
And finally:
<tools>{"name": "answer", "arguments": {"response": "```json\\n{\\"reasoning\\": \\"parse_config reads the file and calls load_env.\\", \\"symbols\\": [\\"app.config.parse_config\\", \\"app.config.load_env\\"]}\\n```"}}</tools>

Only use names you have seen in tool results. Paths are relative to the repository root."""


@dataclass
class ToolResult:
    msg_id: str            # stable id, e.g. "r2.1" (turn 2, call 1): never a list position
    tool: str
    args: dict
    text: str              # what the model was shown (verbatim)
    is_error: bool = False
    files: list[str] = field(default_factory=list)
    lines: tuple[int, int] | None = None          # read: the line range returned
    hits: list[tuple[str, int]] = field(default_factory=list)   # grep: (file, line)
    digest: str | None = None                     # set by compaction
    #: ok | grep_empty | read_empty | read_nonexistent | read_out_of_bounds | timeout | redundant |
    #: path_rejected | unknown_tool | error (for tool_fpr)
    outcome: str = "ok"


def parse_tool_calls(text: str) -> tuple[list[dict], int, int]:
    """Every <tools> block in `text` -> (calls, blocks, unparseable_blocks).
    A block holds one JSON object or a list of them."""
    calls, bad = [], 0
    blocks = TOOLS_RE.findall(text or "")
    for raw in blocks:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            bad += 1
            continue
        objs = obj if isinstance(obj, list) else [obj]
        ok = [o for o in objs if isinstance(o, dict) and isinstance(o.get("name"), str)]
        if not ok:
            bad += 1
        for o in ok:
            args = o.get("arguments")
            calls.append({"name": o["name"], "arguments": args if isinstance(args, dict) else {}})
    return calls, len(blocks), bad


def call_key(name: str, args: dict) -> tuple:
    """Duplicate-suppression key: the tool and its arguments, sorted."""
    return name, json.dumps(args, sort_keys=True, default=str)


class Arm4AgentLoop(RetrievalArm):
    arm_id = "arm4"
    #: the pipeline takes the agent's own answer instead of making its answer call
    self_answering = True

    def __init__(self, llm=None, tokenizer=None, budget: int = C.RETRIEVAL_BUDGET, rg: str = "rg",
                 tool_timeout: float = C.AGENT_TOOL_TIMEOUT_S) -> None:
        super().__init__()
        self.llm = llm
        self._tok = tokenizer
        self.budget = budget
        self.rg = rg
        self.tool_timeout = tool_timeout
        self.index_latency: dict[str, list[float]] = {}
        self.simplifications = [
            "tools are grep (ripgrep), glob, read and answer; read-only, no shell or edit tools",
            "tool calls are parsed from the model's text (<tools> blocks), not through a server-side tool API",
            "tool results return as a user message; older reads are digested to a one-line stub at compaction",
            "if the model never calls answer within the turn cap, one call with the uniform answer prompt "
            "over the gathered context answers (forced_answer)",
        ]

    @property
    def tok(self):
        if self._tok is None:
            from harness.tokenizer import get_tokenizer
            self._tok = get_tokenizer()
        return self._tok

    # ---------------------------------------------------------------- index
    def index(self, repo_path: str, config: dict | None = None) -> None:
        with timed(self.index_latency, "L_index"):
            self.repo_root = os.path.realpath(repo_path)
            if shutil.which(self.rg) is None and not os.path.exists(self.rg):
                raise RuntimeError(f"{self.rg} not found: ripgrep is required by Arm 4's grep tool")
            self._defs: dict[str, list[tuple[int, int, str]]] = {}

    # ------------------------------------------------------------ helpers
    def _resolve(self, path: str) -> str | None:
        """The real path of `path` under the repository, or None when it
        escapes it (.., absolute paths elsewhere, symlinks out)."""
        full = os.path.realpath(os.path.join(self.repo_root, path or "."))
        return full if full == self.repo_root or full.startswith(self.repo_root + os.sep) else None

    def _rel(self, full: str) -> str:
        return os.path.relpath(full, self.repo_root)

    def _definitions(self, rel: str) -> list[tuple[int, int, str]]:
        """(start, end, FQN) of every def/class in a Python file."""
        if rel not in self._defs:
            out: list[tuple[int, int, str]] = []
            if rel.endswith(".py"):
                try:
                    tree = ast.parse(Path(self.repo_root, rel).read_text(encoding="utf-8", errors="replace"))
                except (SyntaxError, ValueError, OSError):
                    tree = None
                mod = module_name(rel)

                def walk(nodes, prefix):
                    for n in nodes:
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            fq = f"{prefix}.{n.name}"
                            start = min([d.lineno for d in n.decorator_list] + [n.lineno])
                            out.append((start, n.end_lineno or n.lineno, fq))
                            walk(n.body, fq)
                if tree is not None:
                    walk(tree.body, mod)
            self._defs[rel] = out
        return self._defs[rel]

    def _symbols_in(self, rel: str, lo: int, hi: int) -> list[str]:
        return [fq for s, e, fq in self._definitions(rel) if s <= hi and e >= lo]

    def _innermost(self, rel: str, line: int) -> str | None:
        hits = [(e - s, fq) for s, e, fq in self._definitions(rel) if s <= line <= e]
        return min(hits)[1] if hits else None

    # -------------------------------------------------------------- tools
    def tool_grep(self, args: dict, stats: dict) -> tuple[str, dict]:
        pattern = str(args.get("pattern") or "")
        if not pattern:
            return "error: grep needs a non-empty pattern", {"is_error": True}
        full = self._resolve(str(args.get("path") or "."))
        if full is None:
            stats["path_rejected"] += 1
            return "error: path is outside the repository", {"is_error": True, "outcome": "path_rejected"}
        cmd = [self.rg, "--line-number", "--no-heading", "--color", "never", "--max-columns", "300",
               "-m", str(C.AGENT_GREP_MAX_LINES)]
        if args.get("glob"):
            cmd += ["--glob", str(args["glob"])]
        cmd += ["-e", pattern, full]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.tool_timeout, cwd=self.repo_root)
        except subprocess.TimeoutExpired:
            stats["timeouts"] += 1
            return f"error: grep timed out after {self.tool_timeout:.0f}s", {"is_error": True, "outcome": "timeout"}
        if proc.returncode not in (0, 1):
            return f"error: grep failed: {proc.stderr.strip()[:200]}", {"is_error": True}
        lines = [ln for ln in proc.stdout.splitlines() if ln]
        hits, shown = [], []
        for ln in lines[:C.AGENT_GREP_MAX_LINES]:       # -m is per file: truncate the total here
            path, _, rest = ln.partition(":")
            num, _, text = rest.partition(":")
            rel = self._rel(path) if os.path.isabs(path) else path
            if num.isdigit():
                hits.append((rel, int(num)))
            shown.append(f"{rel}:{num}:{text}")
        more = len(lines) - len(shown)
        if not lines:
            stats["grep_empty"] += 1
        body = "\n".join(shown) + (f"\n[{more} more matching lines not shown]" if more > 0 else "")
        return body or "no matches", {"hits": hits, "files": sorted({h[0] for h in hits}),
                                      "outcome": "ok" if lines else "grep_empty"}

    def tool_glob(self, args: dict, stats: dict) -> tuple[str, dict]:
        pattern = str(args.get("pattern") or "")
        if not pattern or os.path.isabs(pattern) or ".." in Path(pattern).parts:
            stats["path_rejected"] += 1
            return "error: glob needs a relative pattern inside the repository", {"is_error": True,
                                                                                   "outcome": "path_rejected"}
        found = sorted(self._rel(str(p)) for p in Path(self.repo_root).glob(pattern)
                       if self._resolve(self._rel(str(p))) and p.is_file())
        shown = found[:C.AGENT_GREP_MAX_LINES]
        more = len(found) - len(shown)
        return ("\n".join(shown) + (f"\n[{more} more files not shown]" if more > 0 else "")) or "no files", \
            {"files": shown}

    def tool_read(self, args: dict, stats: dict) -> tuple[str, dict]:
        full = self._resolve(str(args.get("path") or ""))
        if full is None:
            stats["path_rejected"] += 1
            return "error: path is outside the repository", {"is_error": True, "outcome": "path_rejected"}
        if not os.path.isfile(full):
            stats["read_nonexistent"] += 1
            return f"error: no such file: {args.get('path')}", {"is_error": True, "outcome": "read_nonexistent"}
        source = Path(full).read_text(encoding="utf-8", errors="replace")
        lines = source.split("\n")
        try:
            start = max(1, int(args.get("start_line") or 1))
            end = int(args.get("end_line") or start + C.AGENT_READ_MAX_LINES - 1)
        except (TypeError, ValueError):
            return "error: start_line/end_line must be integers", {"is_error": True}
        if start > len(lines):
            stats["read_out_of_bounds"] += 1
            return f"error: {args.get('path')} has {len(lines)} lines", {"is_error": True,
                                                                         "outcome": "read_out_of_bounds"}
        end = min(end, start + C.AGENT_READ_MAX_LINES - 1, len(lines))
        if end < start or not source.strip():
            stats["read_empty"] += 1
            return f"error: no lines in {args.get('path')} {start}-{end}", {"is_error": True, "outcome": "read_empty"}
        rel = self._rel(full)
        body = "\n".join(f"{n}: {lines[n - 1]}" for n in range(start, end + 1))
        note = f"\n[lines {start}-{end} of {len(lines)}]"
        return body + note, {"files": [rel], "lines": (start, end), "outcome": "ok"}

    # ----------------------------------------------------------- the loop
    def _chat(self, messages: list[dict], seed, purpose: str) -> Completion:
        if hasattr(self.llm, "chat"):
            return self.llm.chat(messages, max_tokens=C.GENERATION_RESERVE, seed=seed, purpose=purpose)
        # plain callables (tests, dry runs): the conversation as one transcript
        transcript = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages[1:])
        return self.llm(messages[0]["content"], transcript, max_tokens=C.GENERATION_RESERVE, seed=seed,
                        purpose=purpose)

    def _render_results(self, results: list[ToolResult]) -> str:
        parts = []
        for r in results:
            shown = r.digest if r.digest is not None else r.text
            parts.append(f'<tool_result id="{r.msg_id}" tool="{r.tool}" args={json.dumps(r.args, sort_keys=True)}>\n'
                         f"{shown}\n</tool_result>")
        return "<tool_results>\n" + "\n".join(parts) + "\n</tool_results>"

    def _conversation(self, base: list[dict], turns: list[tuple[str, list[ToolResult]]]) -> list[dict]:
        msgs = list(base)
        for assistant_text, results in turns:
            msgs.append({"role": "assistant", "content": assistant_text})
            if results:
                msgs.append({"role": "user", "content": self._render_results(results)})
        return msgs

    def _tokens(self, msgs: list[dict]) -> int:
        return sum(self.tok.count(m["content"]) for m in msgs)

    def compact(self, results: list[ToolResult]) -> int:
        """Digest grep/glob results and every read but the last
        AGENT_PRESERVE_LAST_READS (chosen by msg_id order of creation,
        not list position). Returns how many results were digested."""
        reads = [r for r in results if r.tool == "read" and not r.is_error]
        keep = {r.msg_id for r in sorted(reads, key=lambda r: _id_key(r.msg_id))[-C.AGENT_PRESERVE_LAST_READS:]}
        n = 0
        for r in results:
            if r.digest is not None or r.is_error or r.msg_id in keep:
                continue
            if r.tool == "grep":
                counts: dict[str, int] = {}
                for f, _ in r.hits:
                    counts[f] = counts.get(f, 0) + 1
                r.digest = (f"[digested grep {r.args.get('pattern')!r}: {len(r.hits)} matching lines in "
                            + ", ".join(f"{f} ({c})" for f, c in sorted(counts.items())) + "]") if counts \
                    else f"[digested grep {r.args.get('pattern')!r}: no matches]"
            elif r.tool == "glob":
                r.digest = f"[digested glob {r.args.get('pattern')!r}: {len(r.files)} files, e.g. " \
                           + ", ".join(r.files[:5]) + "]"
            elif r.tool == "read" and r.lines:
                r.digest = f"[digested read {r.files[0]} lines {r.lines[0]}-{r.lines[1]}; read again if needed]"
            else:
                continue
            n += 1
        return n

    def retrieve(self, query: str, seed: dict) -> DeliveredContext:
        if self.llm is None:
            raise RuntimeError("arm4 needs an llm")
        task_type = seed.get("task_type") or "T2_localization"
        llm_seed = seed.get("llm_seed")
        lat: dict[str, list[float]] = {}
        stats = {"tool_calls": 0, "duplicates": 0, "timeouts": 0, "parse_error_turns": 0, "calls_over_cap": 0,
                 "path_rejected": 0, "grep_empty": 0, "read_empty": 0, "read_nonexistent": 0, "read_out_of_bounds": 0,
                 "unknown_tool": 0, "hermes_blocks": 0, "compactions": 0, "digested": 0, "no_tool_turns": 0,
                 "executed_after_answer": 0}
        base = [{"role": "system", "content": AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": compose_user_prompt("", query, task_type)
                 + "\n\nExplore the repository with the tools, then call answer."}]
        turns: list[tuple[str, list[ToolResult]]] = []
        results: list[ToolResult] = []
        seen: dict[tuple, str] = {}
        trajectory: list[dict] = []
        answer: Completion | None = None
        answer_text = None
        last_prompt_tokens = 0
        window_limit = C.CONTEXT_WINDOW - C.GENERATION_RESERVE
        with timed(lat, "L_retrieve"):
            for turn in range(1, C.AGENT_MAX_TURNS + 1):
                msgs = self._conversation(base, turns)
                if self._tokens(msgs) > C.AGENT_COMPACTION_FRACTION * C.CONTEXT_WINDOW:
                    with timed(lat, "L_compact"):
                        stats["digested"] += self.compact(results)
                        stats["compactions"] += 1
                    msgs = self._conversation(base, turns)
                if self._tokens(msgs) > window_limit:
                    trajectory.append({"turn": turn, "event": "window_full", "tokens": self._tokens(msgs)})
                    break
                last_prompt_tokens = self._tokens(msgs)
                with timed(lat, "L_generate_turn"):
                    comp = self._chat(msgs, llm_seed, "agent_turn")
                calls, n_blocks, n_bad = parse_tool_calls(comp.text)
                stats["hermes_blocks"] += len(HERMES_RE.findall(comp.text or ""))
                step: dict = {"turn": turn, "blocks": n_blocks, "unparseable_blocks": n_bad, "calls": [],
                        "completion_tokens": comp.completion_tokens, "finish_reason": comp.finish_reason,
                        "is_error": False}
                turn_results: list[ToolResult] = []
                if n_blocks and not calls:            # every block failed to parse
                    stats["parse_error_turns"] += 1
                    step["is_error"] = True
                    err = ToolResult(f"r{turn}.0", "parse_error", {}, "error: no <tools> block could be parsed as "
                                     'JSON; use <tools>{"name": ..., "arguments": {...}}</tools>', is_error=True)
                    turn_results.append(err)
                elif not calls:
                    stats["no_tool_turns"] += 1
                    turn_results.append(ToolResult(f"r{turn}.0", "notice", {}, "No tool call found. Call a tool, "
                                                   "or call answer when you are done.", is_error=True))
                answer_call = next((c for c in calls if c["name"] == "answer"), None)
                others = [c for c in calls if c["name"] != "answer"]
                # answer-in-batch: sibling calls run first, then the answer ends the loop
                for k, call in enumerate(others, start=1):
                    if k > C.AGENT_MAX_CALLS_PER_TURN:
                        stats["calls_over_cap"] += 1
                        turn_results.append(ToolResult(f"r{turn}.{k}", call["name"], call["arguments"],
                                                       f"error: at most {C.AGENT_MAX_CALLS_PER_TURN} calls per turn",
                                                       is_error=True))
                        continue
                    turn_results.append(self._execute(f"r{turn}.{k}", call, seen, stats, lat))
                    step["calls"].append({"tool": call["name"], "args": call["arguments"],
                                          "msg_id": turn_results[-1].msg_id, "is_error": turn_results[-1].is_error,
                                          "outcome": turn_results[-1].outcome})
                trajectory.append(step)
                if answer_call is not None:
                    answer = comp
                    answer_text = self._answer_text(answer_call["arguments"], task_type)
                    stats["executed_after_answer"] += len(others)
                    trajectory[-1]["answered"] = True
                    break
                turns.append((comp.text, turn_results))
                results.extend(r for r in turn_results if r.tool in ("grep", "glob", "read"))

        items = self._items(results)
        forced = answer is None
        meta = {**self.fidelity_meta(), "query": query, "task_type": task_type, "over_budget": False,
                "ranking_method": "agent_trajectory", "turn_count": len(trajectory) + (1 if forced else 0),
                **stats, "forced_answer": forced, "trajectory": trajectory, "latency_ms": lat,
                "agent_prompt_tokens": last_prompt_tokens}
        if not forced:
            assert answer is not None
            meta["agent_answer"] = {"text": answer_text, "generation_tokens": answer.completion_tokens,
                                    "latency_seconds": answer.latency_seconds, "finish_reason": answer.finish_reason,
                                    "prompt_tokens_server": answer.prompt_tokens, "model": answer.model}
        return DeliveredContext("arm4", seed["task_id"], items, sum(i.token_count for i in items), self.budget, meta)

    def _execute(self, msg_id: str, call: dict, seen: dict, stats: dict, lat: dict) -> ToolResult:
        name, args = call["name"], call["arguments"]
        if name not in TOOL_NAMES:
            stats["unknown_tool"] += 1
            return ToolResult(msg_id, name, args, f"error: unknown tool {name!r}; tools: grep, glob, read, answer",
                              is_error=True, outcome="unknown_tool")
        key = call_key(name, args)
        if key in seen:
            stats["duplicates"] += 1
            return ToolResult(msg_id, name, args, f"duplicate call: same as result {seen[key]}", is_error=True,
                              outcome="redundant")
        stats["tool_calls"] += 1
        with timed(lat, "L_tool_exec"):
            text, extra = getattr(self, f"tool_{name}")(args, stats)
        seen[key] = msg_id
        return ToolResult(msg_id, name, args, text, is_error=bool(extra.get("is_error")),
                          files=extra.get("files", []), lines=extra.get("lines"), hits=extra.get("hits", []),
                          outcome=extra.get("outcome", "error" if extra.get("is_error") else "ok"))

    @staticmethod
    def _answer_text(args: dict, task_type: str) -> str:
        """The answer in the task's response format: `response` as given, or
        a JSON contract rebuilt from `reasoning`/`symbols` arguments."""
        if isinstance(args.get("response"), str):
            return args["response"]
        if isinstance(args.get("symbols"), list) and task_type in ("T2_localization", "T5_blast_radius"):
            obj = {"reasoning": str(args.get("reasoning", "")), "symbols": [str(s) for s in args["symbols"]]}
            return f"```json\n{json.dumps(obj)}\n```"
        return json.dumps(args)

    def _items(self, results: list[ToolResult]) -> list[DeliveredItem]:
        items: list[DeliveredItem] = []
        for r in results:
            if r.is_error:
                continue
            shown = r.digest if r.digest is not None else r.text
            kind: ItemKind
            symbols: list[str]
            if r.digest is not None:
                kind, symbols = "tool_result_digest", []
            elif r.tool == "read":
                kind = "tool_result_read"
                symbols = self._symbols_in(r.files[0], *r.lines) if r.lines else []
            else:
                kind = "tool_result_grep"
                symbols = list(dict.fromkeys(s for f, ln in r.hits if (s := self._innermost(f, ln))))
            label = f"{r.tool} {json.dumps(r.args, sort_keys=True)}"
            content = f"{item_header(r.msg_id, label)}\n{shown}"
            prov = {"msg_id": r.msg_id, "tool": r.tool, "args": r.args}
            if r.digest is not None:
                prov["original"] = r.text
            items.append(DeliveredItem(r.msg_id, content, self.tok.count(content), len(items) + 1, kind, symbols, prov))
        return items

    def build_prompt(self, ctx: DeliveredContext, tokenizer=None) -> str:
        """The forced-answer prompt (only when the agent never called answer):
        the uniform answer prompt over what it gathered."""
        meta = ctx.build_meta
        return compose_user_prompt(render_items(ctx), meta["query"], meta["task_type"])


def _id_key(msg_id: str) -> tuple[int, int]:
    turn, _, k = msg_id[1:].partition(".")
    return int(turn), int(k or 0)


__all__ = ["AGENT_SYSTEM_PROMPT", "Arm4AgentLoop", "RESPONSE_CONTRACTS", "ToolResult", "call_key",
           "parse_tool_calls"]
