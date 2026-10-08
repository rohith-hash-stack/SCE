"""Debug logger for Prism's MCP server - a developer tool, not part of Prism.

Runs the normal `prism mcp` server with every tool call recorded (arguments,
per-stage timings, index cache behaviour, what was retrieved and delivered,
token counts, errors), imports VS Code Copilot chat exports, and joins the
two into one timeline per question. Prism's own code is not modified: the
instrumentation wraps functions from outside at server start.

    python tools/debug_log serve --repo <repo> --log-dir <dir>
    python tools/debug_log timeline --log-dir <dir> [--copilot chat.json]

See docs/debug_logger.md.
"""
