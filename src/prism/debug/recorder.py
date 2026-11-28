"""Recording for `prism mcp --debug-log <dir>`: wraps the MCP server's tools
and a few internal functions so each tool call produces one JSON line plus
its full payloads.

Layout of a session directory (one per server process):

    session.json                     server start: repo, argv, versions
    calls.jsonl                      one line per tool call (summary)
    payloads/<call_id>/args.json     the call's arguments (api_key removed)
    payloads/<call_id>/manifest.txt  the candidate manifest, when one was built
    payloads/<call_id>/result.*      what the tool returned to the client

Nothing is wrapped or recorded unless `install` is called, which only
`prism mcp --debug-log` does.
"""
from __future__ import annotations

import contextvars
import functools
import json
import os
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_current: contextvars.ContextVar["_CallRecord | None"] = contextvars.ContextVar("prism_debug_call", default=None)
_REDACT = frozenset({"api_key", "ctx"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class _CallRecord:
    def __init__(self, tool: str, args: dict[str, Any], payload_dir: Path) -> None:
        self.call_id = f"{datetime.now(timezone.utc).strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self.tool = tool
        self.args = args
        self.payload_dir = payload_dir / self.call_id
        self.stages: list[dict[str, Any]] = []
        self.index: dict[str, Any] = {"source": "memory"}   # memory | disk_cache | full_build
        self.extra: dict[str, Any] = {}

    def stage(self, name: str, ms: float, **detail: Any) -> None:
        self.stages.append({"stage": name, "ms": round(ms, 1), **{k: v for k, v in detail.items() if v is not None}})

    def save(self, name: str, content: str) -> str:
        self.payload_dir.mkdir(parents=True, exist_ok=True)
        path = self.payload_dir / name
        path.write_text(content, encoding="utf-8")
        return str(path.relative_to(self.payload_dir.parents[1]))


class Recorder:
    def __init__(self, log_dir: str, repo: str | None = None) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self.dir = Path(log_dir).expanduser().resolve() / f"session-{stamp}-{os.getpid()}"
        (self.dir / "payloads").mkdir(parents=True, exist_ok=True)
        self.calls_path = self.dir / "calls.jsonl"
        self._write_json(self.dir / "session.json", {
            "started": _now(), "pid": os.getpid(), "repo": repo, "argv": sys.argv,
            "python": sys.version.split()[0], "prism_commit": _prism_commit(),
        })

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def append(self, record: dict[str, Any]) -> None:
        with open(self.calls_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")

    # -- tool wrapping ------------------------------------------------- #
    def wrap_tool(self, name: str, fn: Callable) -> Callable:
        recorder = self

        @functools.wraps(fn)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            shown = {k: v for k, v in kwargs.items() if k not in _REDACT}
            call = _CallRecord(name, shown, recorder.dir / "payloads")
            call.save("args.json", json.dumps(shown, indent=2, default=str))
            token = _current.set(call)
            started_at, t0 = _now(), time.perf_counter()
            status, error, result = "ok", None, None
            try:
                result = fn(*args, **kwargs)
                return result
            except BaseException as exc:            # recorded, then re-raised unchanged
                status = "error"
                error = {"type": type(exc).__name__, "message": str(exc)[:2000],
                         "code": getattr(getattr(exc, "error", None), "code", None),
                         "traceback": traceback.format_exc(limit=8)}
                raise
            finally:
                total_ms = (time.perf_counter() - t0) * 1000
                _current.reset(token)
                record = {
                    "call_id": call.call_id, "started": started_at, "ended": _now(),
                    "latency_ms": round(total_ms, 1), "tool": name, "args": shown, "status": status,
                    "index": call.index, "stages": call.stages, **call.extra,
                }
                if error is not None:
                    record["error"] = error
                if result is not None:
                    record["result"] = _summarize_result(name, result, call)
                recorder.append(record)

        return wrapped


def _summarize_result(tool: str, result: Any, call: _CallRecord) -> dict[str, Any]:
    from prism.slicer.tokenizer import count_tokens

    summary: dict[str, Any] = {}
    if isinstance(result, str):                       # get_symbol_context (Markdown)
        summary["tokens"] = count_tokens(result)
        summary["payload"] = call.save("result.md", result)
        return summary
    if not isinstance(result, dict):
        summary["payload"] = call.save("result.txt", str(result))
        return summary
    envelope = result.get("envelope")
    if isinstance(envelope, str):
        ext = "json" if envelope.lstrip().startswith("{") else "xml"
        summary["envelope_payload"] = call.save(f"envelope.{ext}", envelope)
        summary["tokens"] = result.get("token_count")
        summary["truncated"] = result.get("truncated")
        summary["delivered"] = _envelope_nodes(envelope, ext)
    if "callers" in result:
        summary["callers_total"] = result.get("callers_total")
        summary["callers"] = [f"{c['hop']}|{'test' if c['is_test'] else 'code'}|{c['symbol']}" for c in result["callers"]]
        summary["callers_in_context"] = result.get("callers_in_context")
    rest = {k: v for k, v in result.items() if k != "envelope"}
    summary["payload"] = call.save("result.json", json.dumps(rest, indent=2, default=str))
    return summary


def _envelope_nodes(envelope: str, ext: str) -> list[str]:
    """`role|compression|cost|id` per delivered node."""
    rows: list[str] = []
    if ext == "json":
        try:
            for n in json.loads(envelope).get("nodes", []):
                rows.append(f"{n.get('role')}|{n.get('compression')}|{n.get('cost')}|{n.get('id')}")
        except (ValueError, AttributeError):
            pass
        return rows
    import re

    for tag in re.findall(r"<node\s[^>]*>", envelope):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', tag))
        rows.append(f"{attrs.get('role')}|{attrs.get('compression')}|{attrs.get('cost')}|{attrs.get('id')}")
    return rows


def _prism_commit() -> str | None:
    try:
        from prism.traversal._cache_keys import engine_commit_hash
        return engine_commit_hash()
    except Exception:  # noqa: BLE001 - informational only
        return None


# ---------------------------------------------------------------------- #
# Stage instrumentation (internal functions, wrapped where they are looked up)
# ---------------------------------------------------------------------- #
def _timed(name: str, fn: Callable, describe: Callable[..., dict] | None = None) -> Callable:
    @functools.wraps(fn)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        call = _current.get()
        if call is None:
            return fn(*args, **kwargs)
        t0 = time.perf_counter()
        result = fn(*args, **kwargs)
        detail = {}
        if describe is not None:
            try:
                detail = describe(call, result, *args, **kwargs) or {}
            except Exception as exc:  # noqa: BLE001 - never break the tool over logging
                detail = {"describe_error": repr(exc)}
        call.stage(name, (time.perf_counter() - t0) * 1000, **detail)
        return result

    wrapped.__prism_debug_wrapped__ = True
    return wrapped


def _patch(owner: Any, attr: str, name: str, describe: Callable[..., dict] | None = None) -> None:
    original = getattr(owner, attr)
    if getattr(original, "__prism_debug_wrapped__", False):
        return
    setattr(owner, attr, _timed(name, original, describe))


def _describe_index(call, ctx, *_a, **_k):
    call.index["source"] = "disk_cache" if call.index.get("disk_cache_hit") else "full_build"
    builder = getattr(ctx, "builder", None)
    if builder is not None:
        call.index["symbols"] = sum(1 for _ in builder.symbol_table)
        call.index["edges"] = builder.graph.number_of_edges()
        call.index["index_errors"] = len(builder.index_errors)
    return {"repo": getattr(ctx, "repo_root", None)}


def _describe_cache_load(call, result, *_a, **_k):
    call.index["disk_cache_hit"] = result is not None
    return {"hit": result is not None}


def _describe_manifest(call, result, _engine, seed, *a, **k):
    text, universe = result
    rows = [line.split("|") for line in text.splitlines() if "|" in line]
    roles: dict[str, int] = {}
    for r in rows:
        roles[r[1]] = roles.get(r[1], 0) + 1
    path = call.save("manifest.txt", text)
    return {"seed": seed, "direction": k.get("direction", "downstream"), "rows": len(rows), "by_role": roles,
            "payload": path}


def _describe_hydrate(call, result, _engine, seed, budget, requested, universe, *a, **k):
    pkg, skipped = result
    return {"seed": seed, "budget": budget, "requested": len(requested), "skipped": skipped,
            "nodes": len(pkg.nodes)}


def _describe_walk(call, result, *_a, **k):
    return {"callers": len(result), "include_tests": k.get("include_tests")}


def _describe_pack(call, result, *_a, **_k):
    nodes = getattr(result, "nodes", None)
    return {"nodes": len(nodes) if nodes is not None else None}


def _describe_render(call, result, *_a, **_k):
    from prism.slicer.tokenizer import count_tokens
    return {"tokens": count_tokens(result) if isinstance(result, str) else None}


def install(log_dir: str, repo: str | None = None) -> Recorder:
    """Wraps every registered tool and the internal stages. Idempotent per
    process for the stage wrappers; returns the session's `Recorder`."""
    import prism.cli
    import prism.mcp.cache as mcp_cache
    import prism.mcp.server as mcp_server
    from prism.engine import PrismEngine
    from prism.slicer.knapsack import ContextKnapsackPacker

    recorder = Recorder(log_dir, repo)
    _patch(prism.cli, "load_pipeline_from_cache", "index.disk_cache_lookup", _describe_cache_load)
    _patch(mcp_cache, "build_pipeline", "index.build_pipeline")
    _patch(mcp_cache, "compute_or_load_contracts", "index.contracts")
    _patch(mcp_cache.GraphCache, "_index", "index.total", lambda call, ctx, *a, **k: _describe_index(call, ctx))
    _patch(PrismEngine, "build_candidate_manifest", "manifest", _describe_manifest)
    _patch(PrismEngine, "retrieve_requested", "hydrate", _describe_hydrate)
    _patch(mcp_server, "upstream_callers_by_hop", "caller_walk", _describe_walk)
    _patch(mcp_server, "build_context_package", "pack", _describe_pack)
    _patch(ContextKnapsackPacker, "pack", "pack_classic")
    _patch(mcp_server, "render", "render", _describe_render)
    _patch(mcp_server, "render_markdown", "render", _describe_render)

    for name, tool in mcp_server.server._tool_manager._tools.items():
        original = getattr(tool.fn, "__prism_debug_original__", tool.fn)
        wrapped = recorder.wrap_tool(name, original)
        wrapped.__prism_debug_original__ = original
        tool.fn = wrapped
    return recorder
