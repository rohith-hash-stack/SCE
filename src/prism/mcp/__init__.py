"""Roadmap Step 4: packaging Prism as an official Model Context Protocol
(MCP) server.

Everything in this package is a thin integration layer over the same
engine `prism.cli` already exposes to a human via the `index`/`query`/
`trace`/`status` commands - `server.py` wraps it as MCP tools instead, so
any MCP-aware coding agent (Claude Desktop, Cursor, ...) can query a
repository's variable-resolution context, architectural invariants, and
tagged symbols directly, over stdio or an HTTP transport, without shelling
out to the CLI per call.

`cache.py` holds the one piece of state genuinely new here: an in-memory
`GraphCache` keyed by canonical repository path, since indexing a real
repository is not free and an agent session calls these tools far more
than once per repository.
"""
