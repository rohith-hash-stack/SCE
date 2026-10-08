# Prism debug logger (developer tool)

`tools/debug_log/` is for investigating Prism during manual testing. It is not part of Prism: nothing under
`src/prism/` changes, and nothing is recorded unless you start the server through this tool.

## Why there are two halves

When you use Prism from VS Code Copilot, three parties are involved:

```
 You ──question──▶ Copilot (in VS Code) ──tool call: prism.blast_radius(seed=…)──▶ Prism MCP server
                       │      ▲                                                        │
                       │      └────────────── tool result: callers + code ─────────────┘
                       ▼
                 the language model ──answer──▶ You
```

Prism only sees the middle arrow: the tool call Copilot decides to make, and what Prism sends back. It never
sees your question, the prompt Copilot builds, the model's answer, or token usage. Those stay inside VS Code.
So "log everything" needs two sources, joined afterwards:

| Half | Where it comes from | What it contains |
|---|---|---|
| **Prism side** | this logger, running the MCP server | every tool call: arguments, whether the index was in memory, loaded from disk cache or fully rebuilt (and how long), the candidate manifest, callers by hop, each delivered symbol with its compression level and token cost, the exact envelope sent back, time per stage, errors with codes |
| **Copilot side** | VS Code's own record of the chat | your question, which tools Copilot called and with what input, Copilot's answer, the model id, Copilot's total time and time to first output |

`timeline` joins them into one report per question: what you asked → what Copilot asked Prism → what Prism
found and sent → what Copilot answered.

**What no half can give.** Copilot does not record token usage or its hidden system prompt in its chat files.
The report shows *estimated* tokens for your message and the answer (cl100k count of the visible text), and
*exact* tokens for what Prism delivered.

## 1. Run Prism through the logger in VS Code

In the repository you test, create `.vscode/mcp.json` (or edit your user MCP config). Replace the paths.

```json
{
  "servers": {
    "prism-debug": {
      "type": "stdio",
      "command": "python",
      "args": [
        "/path/to/SCE/tools/debug_log",
        "serve",
        "--repo", "${workspaceFolder}",
        "--log-dir", "/path/to/prism-debug-logs"
      ],
      "env": { "PYTHONPATH": "/path/to/SCE/src" }
    }
  }
}
```

- `python` must be the interpreter Prism is installed in (`pip install -e /path/to/SCE`). With that
  install, the `PYTHONPATH` line is optional.
- On Windows, use `C:\\path\\to\\SCE\\tools\\debug_log` style paths.
- Disable the normal `prism` server while testing, so Copilot calls the logged one.
- Each server start creates `/path/to/prism-debug-logs/session-<date>-<time>-<pid>/`. The log directory
  defaults to `~/.prism-debug`, or `PRISM_DEBUG_LOG_DIR` if set.

Then use Copilot Chat in agent mode as usual. The tools appear under `prism-debug`.

## 2. Save the Copilot side

After a chat, open the Command Palette and run **Chat: Export Chat...**, then save the JSON. VS Code also
keeps sessions on disk, typically `<VS Code user dir>/workspaceStorage/<hash>/chatSessions/*.json`. Either
file works.

The format is VS Code's own and changes between versions. The reader is tolerant: missing fields show as
unknown rather than failing.

## 3. Build the timeline

```bash
python /path/to/SCE/tools/debug_log timeline --log-dir /path/to/prism-debug-logs \
    --copilot chat.json --out timeline.md
```

- Without `--copilot`, you get the Prism calls alone.
- `--session <dir name>` limits it to one server run.
- `--json` gives machine-readable output.

How calls are matched to questions:
- **By time** when the export has timestamps: a Prism call belongs to the last question asked before it.
- **By order** otherwise: each Prism tool call Copilot made is paired with the next recorded call of that
  tool.
- Anything unmatched is listed at the end.

## What is in a session directory

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

## Using it to explain a miss

For a test you expected but did not get:

| Look at | If missing there | The cause |
|---|---|---|
| `args.seed_symbol` | Copilot asked about a different symbol | the question / Copilot's choice of seed |
| `result.callers` | not among the callers | not in Prism's graph: a resolution gap, so send the pattern |
| `result.callers_in_context` / `delivered` | listed as a caller but its code wasn't sent | cut by the token budget |
| Copilot's answer | delivered but not mentioned | the model ignored it |

## Privacy

Logs contain your source code and your questions. Keep the log directory outside the repository or
git-ignored (`.prism-debug/` is in `.gitignore`), don't share it outside your team, and delete sessions you
no longer need.
