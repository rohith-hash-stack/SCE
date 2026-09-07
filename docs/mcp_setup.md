# SCE as a Model Context Protocol server

`sce mcp` runs the Semantic Context Engine as an [MCP](https://modelcontextprotocol.io)
server, so any MCP-aware coding agent (Claude Desktop, Cursor, Claude Code, ...) can query a
repository's variable-resolution context, architectural invariants, and tagged symbols
directly - the same engine `sce index`/`sce query` expose on the command line, wrapped as
tools an agent calls itself instead of a human running commands.

## Zero-install setup via `uvx` (recommended for VS Code)

`mcp[cli]` is a core dependency of `semantic-context-engine` (not an extra), so a single
`uvx --from git+https://github.com/<OWNER>/<REPO>.git sce ...` invocation resolves, builds, and
runs the CLI with everything `sce mcp` needs, with no separate install step and no extras flag -
the same "run a tool straight from its source, no `pip install` first" experience `npx` gives
Node packages. [Install `uv`](https://docs.astral.sh/uv/getting-started/installation/) once
(`curl -LsSf https://astral.sh/uv/install.sh | sh` on Linux/macOS, or see the docs for Windows);
after that, no per-machine SCE install is needed at all - the client config below is
self-contained. Substitute your repository's real `<OWNER>/<REPO>` (this project itself is
`rohith-hash-stack/SCE`, so `git+https://github.com/rohith-hash-stack/SCE.git` for a fork or a
clone of this exact repo).

`scripts/verify_uvx_execution.py` exercises this exact path locally (`uvx --from .` instead of a
`git+...` URL - the same build-from-source-tree machinery either way) before you ship a config
that depends on it: CLI help, `sce mcp --help`, and a real MCP stdio `initialize` ->
`tools/list` handshake confirming all 5 tools register. Run it after any packaging-relevant
change (`pyproject.toml`, `[project.scripts]`, a new `sce.*` submodule) - `python
scripts/verify_uvx_execution.py`.

### A. Roo Code / Cline global user configuration (`mcp_settings.json`)

Roo Code and Cline read a global `mcp_settings.json` (VS Code command palette → "Roo Code: Open
MCP Config File", or Cline's equivalent settings entry):

```json
{
  "mcpServers": {
    "semantic-context-engine": {
      "command": "uvx",
      "args": [
        "--refresh",
        "--from",
        "git+https://github.com/<OWNER>/<REPO>.git",
        "sce",
        "mcp",
        "--transport",
        "stdio",
        "--repo",
        "${workspaceFolder}"
      ]
    }
  }
}
```

`--refresh` makes `uv` re-check the git ref for updates on every launch instead of reusing a
stale cached build - drop it once you've pinned a specific tag/commit you don't expect to
change. `${workspaceFolder}` is expanded by the client to the currently open project, so
`--repo` always points at whatever repository you're actually working in.

### B. Native VS Code MCP integration (`.vscode/mcp.json`)

VS Code's own built-in MCP support (no extension required, recent VS Code versions) reads a
workspace-level `.vscode/mcp.json`, so this config travels with the project instead of living in
a user's global settings:

```json
{
  "servers": {
    "semantic-context-engine": {
      "command": "uvx",
      "args": [
        "--refresh",
        "--from",
        "git+https://github.com/<OWNER>/<REPO>.git",
        "sce",
        "mcp",
        "--transport",
        "stdio",
        "--repo",
        "${workspaceFolder}"
      ]
    }
  }
}
```

Note the top-level key is `servers`, not `mcpServers` - VS Code's native format differs from
Roo Code/Cline's even though the per-server shape is identical. Commit this file so every
contributor gets SCE wired up automatically the first time they open the project, with nothing
to install by hand.

## Install (local development / non-`uvx` clients)

For local development, or a client that doesn't support launching a tool via `uvx` directly, a
normal editable or PyPI-style install works the same way - `mcp[cli]` installs automatically
since it's a core dependency, not an extra:

```bash
pip install -e ".[dev]"    # this checkout, editable
# or, once published:
pip install semantic-context-engine
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

For VS Code (Roo Code/Cline or native MCP support), prefer the `uvx`-based zero-install configs
in the section above - no local `sce` install to keep up to date. The configs below are for a
client pointed at a local `pip`/`pip -e` install instead.

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
