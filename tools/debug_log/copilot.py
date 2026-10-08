"""Client-side half: read a VS Code Copilot Chat session.

Prism, as an MCP server, never sees your question, Copilot's prompt or its
answer - only the tool calls Copilot decides to make. Those live in VS Code.
Two ways to get them as JSON:

* Command Palette -> "Chat: Export Chat..." saves the open chat session;
* VS Code also keeps sessions on disk, typically under
  `<VS Code user dir>/workspaceStorage/<hash>/chatSessions/*.json`.

The format is VS Code's own and changes between versions, so this reader is
deliberately tolerant: every field is optional and anything it cannot find is
left `None`. Fields it uses when present: `requests[].message.text`,
`requests[].timestamp` (epoch ms), `requests[].modelId`,
`requests[].result.timings.{firstProgress,totalElapsed}`, response parts with
markdown `value`/`content.value`, and tool invocation parts (any object with
a `toolId`), whose input is read from `toolSpecificData.rawInput` or
`resultDetails.input` and output from `resultDetails.output[].value`.

Copilot does not record token usage in these files. `*_tokens_est` fields
are cl100k estimates of the visible text, not what the model was billed for;
Copilot's hidden system prompt and context are not included.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("value", "text", "content"):
            if key in value:
                return _text(value[key])
    if isinstance(value, list):
        return "".join(_text(v) for v in value)
    return ""


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _tool_calls(parts: list) -> list[dict[str, Any]]:
    calls = []
    for part in parts:
        if not isinstance(part, dict) or "toolId" not in part:
            continue
        specific = part.get("toolSpecificData") or {}
        details = part.get("resultDetails") or {}
        tool_input = specific.get("rawInput") if isinstance(specific, dict) else None
        if tool_input is None and isinstance(details, dict):
            tool_input = details.get("input")
        output = None
        if isinstance(details, dict) and isinstance(details.get("output"), list):
            output = "".join(_text(o) for o in details["output"])
        calls.append({
            "tool_id": part.get("toolId"),
            "tool_call_id": part.get("toolCallId"),
            "input": _maybe_json(tool_input),
            "message": _text(part.get("pastTenseMessage") or part.get("invocationMessage")),
            "output_chars": len(output) if output is not None else None,
        })
    return calls


def _response_text(parts: list) -> str:
    out = []
    for part in parts:
        if not isinstance(part, dict) or "toolId" in part:
            continue
        kind = part.get("kind")
        if kind in (None, "markdownContent", "markdownVuln"):
            out.append(_text(part.get("content", part.get("value", ""))))
    return "".join(out).strip()


def _iso(ms: Any) -> str | None:
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds")


def load_copilot_session(path: str) -> list[dict[str, Any]]:
    """One dict per chat turn (your message + Copilot's response)."""
    from prism.slicer.tokenizer import count_tokens

    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    requests = data.get("requests", []) if isinstance(data, dict) else []
    turns = []
    for i, req in enumerate(requests):
        if not isinstance(req, dict):
            continue
        parts = req.get("response") or []
        parts = parts if isinstance(parts, list) else [parts]
        timings = (req.get("result") or {}).get("timings") or {}
        user_text = _text(req.get("message"))
        answer = _response_text(parts)
        turns.append({
            "turn": i + 1,
            "request_id": req.get("requestId"),
            "timestamp": _iso(req.get("timestamp")),
            "timestamp_ms": req.get("timestamp") if isinstance(req.get("timestamp"), (int, float)) else None,
            "model": req.get("modelId") or (req.get("result") or {}).get("metadata", {}).get("modelId"),
            "user_text": user_text,
            "response_text": answer,
            "tool_calls": _tool_calls(parts),
            "first_progress_ms": timings.get("firstProgress"),
            "total_elapsed_ms": timings.get("totalElapsed"),
            "user_tokens_est": count_tokens(user_text) if user_text else 0,
            "response_tokens_est": count_tokens(answer) if answer else 0,
        })
    return turns
