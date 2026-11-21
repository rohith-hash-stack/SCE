"""Join the two halves into one readable timeline per question.

Prism calls (from `calls.jsonl`) are attached to Copilot turns by time when
the chat export has timestamps (a call belongs to the last turn that started
before it), otherwise by order: each Prism tool invocation in the chat is
paired with the next unused Prism call of the same tool. Calls that match no
turn are listed at the end.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


def load_calls(log_dir: str) -> list[dict[str, Any]]:
    """Every recorded call under `log_dir` (all sessions), oldest first."""
    calls = []
    for path in sorted(Path(log_dir).expanduser().glob("**/calls.jsonl")):
        session = path.parent.name
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                record["session"] = session
                calls.append(record)
    calls.sort(key=lambda r: r.get("started", ""))
    return calls


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _is_prism_tool(tool_id: str, prism_tools: set[str]) -> str | None:
    n = _norm(tool_id)
    for tool in sorted(prism_tools, key=len, reverse=True):
        if n.endswith(_norm(tool)):
            return tool
    return None


def _epoch_ms(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp() * 1000


def join(turns: list[dict[str, Any]], calls: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    for turn in turns:
        turn["prism_calls"] = []
    unmatched: list[dict[str, Any]] = []
    if turns and all(t.get("timestamp_ms") for t in turns):
        starts = [t["timestamp_ms"] for t in turns]
        for call in calls:
            at = _epoch_ms(call["started"])
            idx = max((i for i, s in enumerate(starts) if s <= at), default=None)
            (turns[idx]["prism_calls"] if idx is not None else unmatched).append(call)
        return turns, unmatched
    prism_tools = {c["tool"] for c in calls}
    queue = list(calls)
    for turn in turns:
        for tc in turn.get("tool_calls", []):
            tool = _is_prism_tool(tc.get("tool_id") or "", prism_tools)
            if tool is None:
                continue
            match = next((c for c in queue if c["tool"] == tool), None)
            if match is not None:
                queue.remove(match)
                turn["prism_calls"].append(match)
    unmatched.extend(queue)
    return turns, unmatched


def _fmt_call(call: dict[str, Any], lines: list[str]) -> None:
    args = {k: v for k, v in call.get("args", {}).items() if k not in ("repo_path",)}
    lines.append(f"- **Prism `{call['tool']}`** `{json.dumps(args)}` - {call['status']}, "
                 f"{call['latency_ms']} ms, index: {call.get('index', {}).get('source')} "
                 f"(call `{call['call_id']}`, session `{call.get('session')}`)")
    if call.get("error"):
        e = call["error"]
        lines.append(f"  - error {e.get('code') or ''} {e['type']}: {e['message'][:300]}")
    stages = call.get("stages", [])
    if stages:
        parts = []
        for s in stages:
            extra = {k: v for k, v in s.items() if k not in ("stage", "ms", "payload", "skipped")}
            parts.append(f"{s['stage']} {s['ms']} ms" + (f" {json.dumps(extra)}" if extra else ""))
        lines.append("  - stages: " + "; ".join(parts))
    res = call.get("result") or {}
    if "callers_total" in res:
        tests = sum(1 for c in res.get("callers", []) if "|test|" in c)
        lines.append(f"  - callers: {res['callers_total']} ({tests} tests), "
                     f"{len(res.get('callers_in_context') or [])} with code in context")
    if res.get("tokens") is not None:
        lines.append(f"  - delivered: {res['tokens']} tokens, {len(res.get('delivered') or [])} symbols"
                     f"{' (TRUNCATED)' if res.get('truncated') else ''}")
    payloads = [p for p in (res.get("envelope_payload"), res.get("payload")) if p]
    payloads += [s["payload"] for s in stages if s.get("payload")]
    if payloads:
        lines.append("  - payloads: " + ", ".join(f"`{p}`" for p in payloads))


def render_markdown(turns: list[dict[str, Any]], unmatched: list[dict[str, Any]], log_dir: str) -> str:
    lines = [f"# Prism debug timeline", "", f"Log directory: `{log_dir}` (payload paths are relative to it).", ""]
    for turn in turns:
        lines.append(f"## Turn {turn['turn']}" + (f" - {turn['timestamp']}" if turn.get("timestamp") else ""))
        lines.append("")
        lines.append(f"**You:** {turn['user_text']}")
        lines.append("")
        meta = [f"model: {turn.get('model') or 'unknown'}"]
        if turn.get("total_elapsed_ms") is not None:
            meta.append(f"Copilot total {turn['total_elapsed_ms']} ms")
        if turn.get("first_progress_ms") is not None:
            meta.append(f"first output {turn['first_progress_ms']} ms")
        meta.append(f"~{turn['user_tokens_est']} tokens in your message, ~{turn['response_tokens_est']} in the answer (estimates)")
        lines.append("_" + "; ".join(meta) + "_")
        lines.append("")
        tool_calls = turn.get("tool_calls", [])
        if tool_calls:
            lines.append("Copilot tool calls: " + ", ".join(f"`{tc['tool_id']}`" for tc in tool_calls))
            lines.append("")
        for call in turn["prism_calls"]:
            _fmt_call(call, lines)
        if not turn["prism_calls"]:
            lines.append("- _no Prism call in this turn_")
        lines.append("")
        answer = turn.get("response_text") or ""
        lines.append("**Copilot:** " + (answer[:1500] + (" …" if len(answer) > 1500 else "")))
        lines.append("")
    if unmatched:
        lines.append("## Prism calls not matched to a chat turn" if turns else "## Prism calls")
        lines.append("")
        for call in unmatched:
            lines.append(f"### {call['started']}")
            _fmt_call(call, lines)
            lines.append("")
    return "\n".join(lines)
