# Prism debug logging

For investigating Prism during manual testing. It is off unless you turn it on, and it doesn't change what
Prism retrieves.

## Turn it on

Add one argument, `--debug-log <folder>`, to the `prism mcp` command you already have in VS Code's
`mcp.json`:

```json
{
  "servers": {
    "prism": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--refresh", "--from",
        "git+https://github.com/rohith-hash-stack/SCE.git@manual-sanity-check/test-framework-support",
        "prism", "mcp", "--transport", "stdio", "--repo", "${workspaceFolder}",
        "--debug-log", "C:\\work\\prism-logs"
      ]
    }
  }
}
```

Instead of the argument you can set the environment variable `PRISM_DEBUG_LOG=<folder>`, for example in
the server's `"env"` block. Remove the argument (or the variable) and logging is off again.

Each server start creates `<folder>/session-<date>-<time>-<pid>/`. The server's output in VS Code shows the
exact path: `prism debug log: ...`.

## Why there are two halves

When you use Prism from Copilot, three parties are involved:

```
 You ──question──▶ Copilot (in VS Code) ──tool call: prism.blast_radius(seed=…)──▶ Prism MCP server
                       │      ▲                                                        │
                       │      └────────────── tool result: callers + code ─────────────┘
                       ▼
                 the language model ──answer──▶ You
```

Prism only sees the middle arrow: the tool call Copilot makes and what Prism sends back. Your question, the
prompt Copilot builds and the model's answer stay inside VS Code. So there are two sources, joined
afterwards:

| Half | Where it comes from | What it contains |
|---|---|---|
| **Prism side** | `--debug-log` | every tool call: arguments, whether the index was in memory / loaded from disk cache / fully rebuilt (and how long), the candidate manifest, callers by hop, each delivered symbol with its compression and token cost, the exact envelope sent back, time per stage, errors with codes |
| **Copilot side** | VS Code: **Chat: Export Chat...** | your question, which tools Copilot called with what input, Copilot's answer, the model id, Copilot's total time and time to first output |

`prism debug timeline` joins them into one report per question.

**Not available from either half:** Copilot does not record token usage or its hidden system prompt. The
report shows *estimated* tokens for your message and the answer, and *exact* tokens for what Prism
delivered.

## Commands

You need `prism` in a terminal. Either install it (`pip install -e <SCE clone>`), or run it without
installing through the same `uvx` source as `mcp.json`:

```bash
uvx --from git+https://github.com/rohith-hash-stack/SCE.git@manual-sanity-check/test-framework-support prism debug --help
```

| Command | Does |
|---|---|
| `prism debug find --repo <repo> <words...>` | exact symbol names containing all the words, with file:line and direct caller count |
| `prism debug timeline --log-dir <folder> --copilot chat.json --out timeline.md` | the joined report. Without `--copilot` you get Prism's calls alone; `--session <name>` limits it to one server run; `--json` gives machine-readable output |

How calls are matched to questions:
- **By time** when the export has timestamps: a call belongs to the last question asked before it.
- **By order** otherwise: each Prism tool call in the chat is paired with the next recorded call of that
  tool.
- Anything unmatched is listed at the end.

The VS Code export format changes between versions. The reader is tolerant, and missing fields show as
unknown.

## What is in a session folder

```
session.json                      server start: repo, Prism commit, Python version
calls.jsonl                       one line per tool call
payloads/<call_id>/args.json      arguments (api_key removed)
payloads/<call_id>/manifest.txt   the candidate manifest (prism.blast_radius)
payloads/<call_id>/envelope.xml   exactly what Prism returned
payloads/<call_id>/result.json    the rest of the result (callers list, ...)
payloads/<call_id>/result.md      get_symbol_context's Markdown
```

A `calls.jsonl` line, shortened:

```json
{"call_id": "162653-5af94f", "tool": "prism.blast_radius", "status": "ok", "latency_ms": 776.0,
 "args": {"seed_symbol": "...create_user", "budget_tokens": 4000},
 "index": {"source": "memory", "symbols": 34, "edges": 42},
 "stages": [{"stage": "manifest", "ms": 421.5, "rows": 7, "by_role": {"caller": 5, "callee": 1, "seed": 1}},
            {"stage": "hydrate", "ms": 78.9, "requested": 5, "nodes": 8},
            {"stage": "caller_walk", "ms": 0.1, "callers": 5},
            {"stage": "render", "ms": 17.0, "tokens": 2808}],
 "result": {"tokens": 2808, "callers_total": 5, "callers": ["1|code|...Create_Test_User", "2|test|...Delete_User"],
            "delivered": ["seed|L0_full|61|...create_user", "caller|L0_full|37|...Create_Test_User"]}}
```

## Explaining a miss

For a test you expected but did not get:

| Look at | If it's missing there | Cause |
|---|---|---|
| `args.seed_symbol` | Copilot asked about a different symbol | Copilot's choice of seed |
| `result.callers` | not among the callers | not in Prism's graph (a resolution gap) |
| `callers_in_context` / `delivered` | a caller, but its code wasn't sent | cut by the token budget |
| Copilot's answer | delivered but not mentioned | the model ignored it |

## How it stays out of the way

- **Code location:** the logger lives in `prism.debug`.
- **Off by default:** plain `prism mcp` never imports it; a test checks this.
- **No logging in retrieval code:** with `--debug-log`, it wraps the tool functions and a few internal
  functions at server start. The retrieval modules contain no logging code, and results are identical with
  and without it.

## Privacy

Logs contain your source code and your questions. Keep the folder outside your repositories (or git-ignore
it; `.prism-debug/` is already ignored here), don't share it outside your team, and delete old sessions.

Step-by-step first query: `docs/first_query_walkthrough.md`.
