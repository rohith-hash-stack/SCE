# SCE as a Model Context Protocol server

`sce mcp` runs the Semantic Context Engine as an [MCP](https://modelcontextprotocol.io)
server, so any MCP-aware coding agent (Claude Desktop, Cursor, Claude Code, ...) can query a
repository's variable-resolution context, architectural invariants, and tagged symbols
directly - the same engine `sce index`/`sce query` expose on the command line, wrapped as
tools an agent calls itself instead of a human running commands.

## Install

The `mcp` package is an optional dependency - the rest of SCE (`sce index`, `sce query`,
`sce trace`, `sce status`) works without it.

```bash
pip install "semantic-context-engine[mcp]"
```

## Run it directly

```bash
# Standard stdio mode (what Claude Desktop / Cursor / Claude Code expect):
sce mcp --transport stdio --repo /absolute/path/to/project

# Streamable HTTP, for a client that connects over the network instead:
sce mcp --transport streamable-http --repo /absolute/path/to/project
```

`--repo` sets the default repository for any tool call that omits its own `repo_path` - every
tool below still accepts an explicit `repo_path` to target a different repository in the same
server process. Omit `--repo` entirely and the server falls back to its own current working
directory.

## Client configuration

Most MCP clients (Claude Desktop, Cursor, Claude Code) read a JSON config naming the command
to launch. Add an entry like this - substituting the real absolute path to the repository you
want indexed:

```json
{
  "mcpServers": {
    "semantic-context-engine": {
      "command": "sce",
      "args": ["mcp", "--transport", "stdio", "--repo", "/absolute/path/to/project"]
    }
  }
}
```

- **Claude Desktop**: `claude_desktop_config.json` (`~/Library/Application Support/Claude/`
  on macOS, `%APPDATA%\Claude\` on Windows) - open Settings → Developer → Edit Config, or edit
  the file directly.
- **Claude Code**: `claude mcp add semantic-context-engine -- sce mcp --transport stdio --repo /absolute/path/to/project`,
  or add the same JSON block under `mcpServers` in `.claude.json` / the project's
  `.mcp.json`.
- **Cursor**: Settings → MCP → Add new MCP server, or add the same JSON block to
  `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (per-project).

If `sce` isn't on the launching process's `PATH` (e.g. it lives inside a virtualenv the
client doesn't activate), use the interpreter's absolute path instead:

```json
{
  "mcpServers": {
    "semantic-context-engine": {
      "command": "/absolute/path/to/venv/bin/sce",
      "args": ["mcp", "--transport", "stdio", "--repo", "/absolute/path/to/project"]
    }
  }
}
```

## Tools

Every tool lazily indexes and caches its target repository (keyed by canonical path) on
first use - the first call against a given repository pays the indexing cost, every
subsequent call in the same server process reuses it. Call `reindex_repo` after editing
source files or after a new `sce trace` run changes the picture underfoot; nothing here
watches the filesystem automatically.

### `get_symbol_context`

The variable-resolution Markdown context package `sce query` renders: full source (L0) for
the target symbol, progressively lighter control-flow skeletons (L1), interface contracts
(L2), or signature stubs (L3) for its call-graph and tag-similarity neighbors, plus an
architectural-path diagram - all within a token budget.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `target_symbol` | string | yes | - | Fully qualified symbol name, e.g. `django.db.models.base.Model.save`. |
| `repo_path` | string | no | server's cwd / `--repo` | Absolute path to the repository root. |
| `token_budget` | int | no | `2000` | Approximate token budget for the packed context. |

A call graph edge marked `CONFIRMED_RUNTIME` (see `sce trace`) is preferentially packed over
an equally-distant, unexercised static one - no separate flag needed; `D_hybrid` (the
distance metric the packer ranks candidates by) already discounts a runtime-confirmed edge's
hop cost.

### `get_architectural_invariants`

A target's own active semantic tags, its incoming/outgoing call edges' runtime confidence
(`CONFIRMED_RUNTIME` vs. `STATIC`), and any metamodel `REQUIRES_BEFORE` relation nothing in
its ancestry satisfies - e.g. a `#db_write` with no `#auth_guard` anywhere upstream in its
callers.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `target_symbol` | string | yes | - | Fully qualified symbol name. |
| `repo_path` | string | no | server's cwd / `--repo` | Absolute path to the repository root. |

### `find_symbols_by_tag`

Every symbol the metamodel matrix maps to a given tag (`#route_handler`, `#db_write`,
`#auth_guard`, `#external_io`, `#db_read`, `#payment_charge`, `#state_mutation`,
`#event_producer`, `#event_consumer`), with its file path, line range, and runtime
invocation count.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `tag` | string | yes | - | One of the registered tags, `#`-prefixed. |
| `repo_path` | string | no | server's cwd / `--repo` | Absolute path to the repository root. |

### `get_graph_status`

Summary statistics: total symbols, total call edges, how many edges a runtime trace has
confirmed (`CONFIRMED_RUNTIME`) vs. how many the static resolver never found until a trace
revealed them (`RUNTIME_DISCOVERED`), and a per-tag symbol count.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `repo_path` | string | no | server's cwd / `--repo` | Absolute path to the repository root. |

### `reindex_repo`

Drops the cached graph for a repository (if any) and rebuilds it from source, re-merging
`.sce/runtime_state.json` if present.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `repo_path` | string | no | server's cwd / `--repo` | Absolute path to the repository root. |

## Errors

An unknown symbol, an unregistered tag, or a repository path that doesn't exist all return a
normal tool result with `isError: true` and a plain-text explanation - never a transport-level
crash - so a calling agent can read the message and retry with a corrected argument.
