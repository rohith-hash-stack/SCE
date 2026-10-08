# Your first Prism query from VS Code Copilot, step by step

One complete round: set up once, ask one question, save both logs, read the timeline. After that, every
other question is just steps 7–12 again.

Commands are shown for **Windows (PowerShell)** and **macOS/Linux**. Replace:
- `C:\work\SCE` or `~/work/SCE`: where you clone this repository;
- `C:\work\ui-tests` or `~/work/ui-tests`: your Playwright (or Robot) repository;
- `C:\work\prism-logs` or `~/work/prism-logs`: where logs go. Keep it outside both repositories.

---

## Part A: one-time setup (about 15 minutes)

### Step 1. Get the branch and install Prism

You need Python 3.10 or newer, and VS Code with GitHub Copilot Chat.

Windows (PowerShell):
```powershell
cd C:\work
git clone -b manual-sanity-check/test-framework-support https://github.com/rohith-hash-stack/SCE.git
cd SCE
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

macOS/Linux:
```bash
cd ~/work
git clone -b manual-sanity-check/test-framework-support https://github.com/rohith-hash-stack/SCE.git
cd SCE
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

**Check:** `prism --help` prints a list of commands, and `python tools/debug_log --help` prints `serve`,
`find` and `timeline`.

Write down the full path of this Python. You need it in step 4.
- Windows: `(Get-Command python).Source`, for example `C:\work\SCE\.venv\Scripts\python.exe`.
- macOS/Linux: `which python`, for example `/Users/you/work/SCE/.venv/bin/python`.

### Step 2. Index your test repository once from the terminal

```powershell
prism index C:\work\ui-tests
```

**Check:** you see `Symbols indexed: <number>` and `CALLS edges: <number>`, both well above zero. If there
is a `Skipped files` section, note it.

Prism writes a cache folder `.prism/` inside your repository. Add `.prism/` to that repository's
`.gitignore`.

### Step 3. Pick the symbol for your first question

Choose a page-object method you know well, one that several tests use. Find its exact Prism name:

```powershell
python C:\work\SCE\tools\debug_log find --repo C:\work\ui-tests login page
```

Every word you give must appear in the name. You get lines like:

```
pages.LoginPage.LoginPage.login
    method, implementation, pages/LoginPage.ts:16, 3 direct caller(s)
```

The first line is the name to use. Then, **before asking Copilot**, write down the tests you know use this
method (file and test title). That list is your gold. Without it you can't judge the answer.

### Step 4. Tell VS Code to start Prism through the logger

Open your test repository in VS Code (`File > Open Folder > C:\work\ui-tests`). Create the file
`.vscode/mcp.json` in it with this content, using your paths:

```json
{
  "servers": {
    "prism-debug": {
      "type": "stdio",
      "command": "C:\\work\\SCE\\.venv\\Scripts\\python.exe",
      "args": [
        "C:\\work\\SCE\\tools\\debug_log",
        "serve",
        "--repo", "${workspaceFolder}",
        "--log-dir", "C:\\work\\prism-logs"
      ]
    }
  }
}
```

- In JSON, Windows backslashes are doubled (`\\`). On macOS/Linux use normal paths such as
  `"/Users/you/work/SCE/.venv/bin/python"`.
- If you already have another `prism` server configured, stop it, so Copilot can only call this one.

### Step 5. Start the server and confirm the logger is on

In `mcp.json`, VS Code shows a small **Start** link above `"prism-debug"`. Click it. You can also open the
Command Palette (`Ctrl+Shift+P`), run **MCP: List Servers**, pick `prism-debug` and choose **Start Server**.

**Check:** open the server's output (**MCP: List Servers → prism-debug → Show Output**). You should see a
line like:

```
prism debug log: C:\work\prism-logs\session-20261008-101500-12345
```

That folder is where this run's log goes. If you see an error instead, copy it and send it to me.

### Step 6. Make the tools available to Copilot

Open Copilot Chat (`Ctrl+Alt+I`) and switch the mode dropdown at the bottom to **Agent**. Click the
**tools** icon (🔧) next to it and make sure the `prism-debug` tools are ticked: `prism.blast_radius`,
`prism.slice`, `get_symbol_context`, and so on.

---

## Part B: one query (about 5 minutes)

### Step 7. Ask the question

Start a new chat (the **+** button) so this session holds only this question. Paste, filling in your symbol
and path:

```
Use the prism.blast_radius tool with repo_path "C:\work\ui-tests" and
seed_symbol "pages.LoginPage.LoginPage.login".
Then list every test that would be affected if I change this method,
with its file and test title, and say how you know.
```

Naming the tool and the exact symbol keeps this first run deterministic. Later you can ask naturally
("which tests break if I change the login page object?") and see whether Copilot finds the right tool and
symbol by itself; the log shows that too.

### Step 8. Approve the tool call

Copilot shows **Run prism.blast_radius?** with the arguments. Check that `seed_symbol` is what you
intended, then click **Continue** (or **Allow**). The first call takes a few seconds while Prism indexes
your repository; later ones are faster.

### Step 9. Compare the answer with your gold

Against the list you wrote in step 3, note:
- which tests Copilot named that you expected;
- which expected tests are missing;
- which named tests surprise you.

### Step 10. Export the chat (the Copilot half)

Command Palette → **Chat: Export Chat...** → save it as `C:\work\prism-logs\chat-001.json`.

If you can't find that command, tell me your VS Code version. As a fallback, VS Code keeps the session
under its `workspaceStorage\<id>\chatSessions\` folder.

### Step 11. Build the timeline

```powershell
python C:\work\SCE\tools\debug_log timeline --log-dir C:\work\prism-logs --copilot C:\work\prism-logs\chat-001.json --out C:\work\prism-logs\timeline-001.md
```

Open `timeline-001.md` in VS Code and press `Ctrl+Shift+V` for the preview.

### Step 12. Read it

You get one section per question. Roughly:

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

How to read it, line by line:

| Line | Meaning |
|---|---|
| `index: full_build` | Prism parsed the whole repository on this call (first call). Later: `memory`. `disk_cache` means it reused `.prism/`. |
| `stages` | where the time went. The index build dominates the first call. |
| `callers: 9 (8 tests)` | Prism found 9 callers in total; 8 are tests. The full list with hops is in `result.json`. |
| `with code in context` | how many of those callers had their code sent to Copilot (the token budget can cut some). |
| `delivered: N tokens` | exactly what Prism sent back to Copilot. `envelope.xml` is the full text. |
| `Copilot:` | what the model made of it. |

**Finding where a miss happened.** For an expected test missing from the answer, check in this order:

1. Is it in `result.json` → `callers`? If not, Prism's graph doesn't have it. Open the test and tell me how
   it reaches the method (a helper, a fixture, a base class…).
2. Is it in `callers_in_context`? If not, it was found but its code didn't fit the budget.
3. If it was delivered but Copilot didn't name it, the model ignored it.

### Step 13. Send me

- `chat-001.json`;
- `timeline-001.md`;
- your gold list from step 3.

If you're allowed to share code, also send the session folder (zip it). It contains your source code inside
`envelope.xml`; if you can't share code, send only `calls.jsonl` from it.

---

## If something goes wrong

| Symptom | Likely cause |
|---|---|
| No **Start** link in `mcp.json` | VS Code too old for MCP, or the file isn't at `<repo>/.vscode/mcp.json` |
| Output shows `No module named prism` | the `command` path isn't the venv Python from step 1 |
| Copilot never calls the tool | not in **Agent** mode, or the tools are unticked (step 6) |
| Tool error `-32002` | the symbol name is wrong. The error lists close candidates; use `find` (step 3) |
| Tool error `-32001` | `repo_path` differs from the folder given to `--repo`; use the same absolute path |
| `timeline` shows Prism calls under "not matched" | the export had no timestamps and Copilot's tool names didn't match; send me the export |
| Blank questions or answers in the timeline | your VS Code's export format differs from what the reader expects; send me the export |
