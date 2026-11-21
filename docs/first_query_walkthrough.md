# Your first Prism query from VS Code Copilot, step by step

One complete round: configure once, ask one question, save the chat, read the timeline. After that, each
new question is steps 5–10 again.

Replace:
- `C:\work\ui-tests`: your Playwright (or Robot) repository;
- `C:\work\prism-logs`: where logs go. Keep it outside your repositories.

On macOS/Linux, use normal paths (`/Users/you/work/...`).

The commands use `uvx`, the same launcher as Prism's usual VS Code setup (`docs/mcp_setup.md`). Nothing to
clone or install apart from `uv` itself.

To keep the commands short, set this once per terminal.

PowerShell:
```powershell
$PRISM = "git+https://github.com/rohith-hash-stack/SCE.git@manual-sanity-check/test-framework-support"
```

macOS/Linux:
```bash
PRISM="git+https://github.com/rohith-hash-stack/SCE.git@manual-sanity-check/test-framework-support"
```

---

## Part A: once (about 10 minutes)

### Step 1. Point VS Code's Prism server at this branch, with logging on

In your test repository, open `.vscode/mcp.json` (create it if it doesn't exist) and make the `prism` entry
this. Compared with your usual config, only two things differ: the `@manual-sanity-check/...` branch and the
last two arguments.

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

In JSON, Windows backslashes are doubled (`\\`).

### Step 2. Start it and confirm logging is on

Click the **Start** (or **Restart**) link VS Code shows above `"prism"` in `mcp.json`. Then open the server
output: Command Palette → **MCP: List Servers** → `prism` → **Show Output**.

**Check:** a line like

```
prism debug log: C:\work\prism-logs\session-20261008-101500-12345
```

The first start can take a minute while `uvx` builds the branch. If you see an error instead, send it to me.

### Step 3. Make the tools available to Copilot

Open Copilot Chat (`Ctrl+Alt+I`) and switch the mode dropdown to **Agent**. Click the **tools** icon (🔧)
and make sure the `prism` tools are ticked, including `prism.blast_radius`.

### Step 4. Prism creates a `.prism/` cache folder in your repository

Add `.prism/` to your repository's `.gitignore`.

---

## Part B: one query (about 5 minutes)

### Step 5. Pick the symbol and write down what you expect

Choose a page-object method (or a Robot keyword) you know well, one that several tests use. Get its exact
Prism name:

```powershell
uvx --from $PRISM prism debug find --repo C:\work\ui-tests login page
```

Every word must appear in the name. The output looks like:

```
pages.LoginPage.LoginPage.login
    method, implementation, pages/LoginPage.ts:16, 3 direct caller(s)
```

The first line is the name to use. **Before asking Copilot**, write down the tests you know use it (file and
test title). That is your gold.

### Step 6. Ask the question

Start a new chat (**+**) so this session holds only this question. Paste, filling in your symbol and path:

```
Use the prism.blast_radius tool with repo_path "C:\work\ui-tests" and
seed_symbol "pages.LoginPage.LoginPage.login".
Then list every test that would be affected if I change this method,
with its file and test title, and say how you know.
```

Naming the tool and symbol keeps this first run deterministic. Later you can ask naturally ("which tests
break if I change the login page object?"); the log shows whether Copilot picks the right tool and symbol
by itself.

### Step 7. Approve the tool call

Copilot shows **Run prism.blast_radius?** with the arguments. Check `seed_symbol`, then click **Continue**.
The first call takes a few seconds while Prism indexes your repository.

### Step 8. Compare with your gold

Against the list from step 5, note:
- which expected tests Copilot named;
- which are missing;
- which named tests surprise you.

### Step 9. Export the chat (the Copilot half)

Command Palette → **Chat: Export Chat...** → save as `C:\work\prism-logs\chat-001.json`.

### Step 10. Build and read the timeline

```powershell
uvx --from $PRISM prism debug timeline --log-dir C:\work\prism-logs --copilot C:\work\prism-logs\chat-001.json --out C:\work\prism-logs\timeline-001.md
```

Open `timeline-001.md` in VS Code and press `Ctrl+Shift+V` for the preview. You get one section per
question:

```
## Turn 1 - 2026-10-08T10:16:02

You: Use the prism.blast_radius tool ... seed_symbol "pages.LoginPage.LoginPage.login" ...
model: copilot/...; Copilot total 9800 ms; ~45 tokens in your message, ~210 in the answer (estimates)

- Prism `prism.blast_radius` {"seed_symbol": "pages.LoginPage.LoginPage.login"} - ok, 2100 ms, index: full_build
  - stages: index.total 1500 ms; manifest 320 ms {"rows": 14, "by_role": {"caller": 9, ...}}; hydrate 150 ms; ...
  - callers: 9 (8 tests), 9 with code in context
  - delivered: 6800 tokens, 12 symbols
  - payloads: payloads/101602-ab12cd/envelope.xml, payloads/101602-ab12cd/result.json, ...manifest.txt

Copilot: The following tests call login(): ...
```

| Line | Meaning |
|---|---|
| `index: full_build` | Prism parsed the whole repository on this call. Later calls: `memory`; `disk_cache` means it reused `.prism/`. |
| `stages` | where the time went. The index build dominates the first call. |
| `callers: 9 (8 tests)` | callers Prism found, and how many are tests. The full list with hops is in `result.json` (in the session folder). |
| `with code in context` | how many callers had their code sent to Copilot. The token budget can cut some. |
| `delivered: N tokens` | exactly what Prism sent to Copilot. `envelope.xml` is the full text. |
| `Copilot:` | what the model made of it. |

**Where did a miss happen?** For an expected test missing from the answer:

1. Not in `result.json` → `callers`: Prism's graph doesn't have it. Tell me how the test reaches the method
   (a helper, a fixture, a base class…).
2. In `callers` but not in `callers_in_context`: found, but its code didn't fit the budget.
3. Delivered, but not in Copilot's answer: the model ignored it.

### Step 11. Send me

- `chat-001.json`;
- `timeline-001.md`;
- your gold list.

If you may share code, also send the session folder (zipped). It contains your code in `envelope.xml`; if
you can't share code, send only `calls.jsonl` from it.

When you're done testing, remove the `--debug-log` argument from `mcp.json`.

---

## If something goes wrong

| Symptom | Likely cause |
|---|---|
| No `prism debug log:` line in the output | `--debug-log` is missing from `args`, or VS Code is still running the old server (click **Restart**) |
| `uvx` not found | install `uv` (see `docs/mcp_setup.md`) and restart VS Code |
| Copilot never calls the tool | not in **Agent** mode, or the tools are unticked (step 3) |
| Tool error `-32002` | wrong symbol name. The error lists close candidates; or use `prism debug find` (step 5) |
| Tool error `-32001` | `repo_path` isn't the folder VS Code opened; use the same absolute path |
| Prism calls under "not matched" in the timeline | the export had no timestamps and Copilot's tool names didn't match; send me the export |
| Blank questions or answers in the timeline | your VS Code's export format differs from what the reader expects; send me the export |
