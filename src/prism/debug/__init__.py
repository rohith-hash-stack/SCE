"""Debug logging for manual testing - off unless asked for.

`prism mcp --debug-log <dir>` (or `PRISM_DEBUG_LOG=<dir>`) records every MCP
tool call: arguments, per-stage timings, index cache behaviour, what was
retrieved and delivered, token counts, errors. `prism debug timeline` joins
those records with a VS Code Copilot chat export; `prism debug find` looks up
exact symbol names. See docs/debug_logger.md.

Isolated from the retrieval code on purpose: nothing here is imported unless
one of those commands is used, and the recording wraps functions at server
start instead of adding logging calls inside Prism's modules.
"""
