"""`python tools/debug_log <command>` - see the package docstring."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):                      # run as `python tools/debug_log`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "debug_log"

from .copilot import load_copilot_session  # noqa: E402
from .timeline import join, load_calls, render_markdown  # noqa: E402

DEFAULT_LOG_DIR = os.environ.get("PRISM_DEBUG_LOG_DIR", "~/.prism-debug")


def _serve(args: argparse.Namespace) -> int:
    from .recorder import install
    from prism.mcp.server import run_server

    recorder = install(args.log_dir, repo=args.repo)
    print(f"prism debug log: {recorder.dir}", file=sys.stderr)    # stdout is the MCP channel
    run_server(transport=args.transport, repo_path=args.repo)
    return 0


def _find(args: argparse.Namespace) -> int:
    """Symbols whose name contains every given word (case-insensitive)."""
    from prism.cli import build_pipeline

    builder, _ = build_pipeline(args.repo)
    words = [w.lower() for w in args.words]
    rows = []
    for symbol in builder.symbol_table:
        if symbol.kind not in ("function", "method", "class"):
            continue
        name = symbol.qualified_name.lower()
        if all(w in name for w in words):
            callers = builder.graph.in_degree(symbol.qualified_name) if symbol.qualified_name in builder.graph else 0
            rel = os.path.relpath(symbol.file, args.repo)
            rows.append((symbol.qualified_name, symbol.kind, symbol.role.value, f"{rel}:{symbol.line_range[0]}", callers))
    rows.sort()
    for qname, kind, role, where, callers in rows[: args.limit]:
        print(f"{qname}\n    {kind}, {role}, {where}, {callers} direct caller(s)")
    print(f"{len(rows)} match(es)" + (f", first {args.limit} shown" if len(rows) > args.limit else ""))
    return 0


def _timeline(args: argparse.Namespace) -> int:
    log_dir = str(Path(args.log_dir).expanduser())
    calls = load_calls(log_dir)
    if args.session:
        calls = [c for c in calls if c["session"] == args.session]
    turns = load_copilot_session(args.copilot) if args.copilot else []
    turns, unmatched = join(turns, calls)
    if args.json:
        text = json.dumps({"turns": turns, "unmatched_calls": unmatched}, indent=2, default=str)
    else:
        text = render_markdown(turns, unmatched, log_dir)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(turns)} turns, {len(calls)} Prism calls)")
    else:
        print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python tools/debug_log", description="Prism debug logger (developer tool).")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run `prism mcp` with every tool call recorded")
    serve.add_argument("--repo", help="repository the server is pinned to (as `prism mcp --repo`)")
    serve.add_argument("--log-dir", default=DEFAULT_LOG_DIR, help="where sessions are written (default %(default)s)")
    serve.add_argument("--transport", default="stdio")
    serve.set_defaults(func=_serve)

    find = sub.add_parser("find", help="look up exact symbol names to ask about (e.g. `find --repo . login page`)")
    find.add_argument("--repo", required=True)
    find.add_argument("words", nargs="+", help="every word must appear in the symbol name")
    find.add_argument("--limit", type=int, default=40)
    find.set_defaults(func=_find)

    timeline = sub.add_parser("timeline", help="one timeline: chat turns joined with Prism calls")
    timeline.add_argument("--log-dir", default=DEFAULT_LOG_DIR)
    timeline.add_argument("--copilot", help="a VS Code chat export / chatSessions JSON file")
    timeline.add_argument("--session", help="only this session directory name")
    timeline.add_argument("--out", help="write here instead of stdout (e.g. timeline.md)")
    timeline.add_argument("--json", action="store_true", help="machine-readable output")
    timeline.set_defaults(func=_timeline)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
