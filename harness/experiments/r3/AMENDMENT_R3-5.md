# Amendment R3-5: edit handling (2026-11-06; a dated addition to the pre-registration hash-committed at `e711bcfb`)

Written before the R3 harness is built or frozen, and before any R3a model call. It changes no other rule.

**The choice (decided, from the prior-art investigation):**
- **A + parse gate, with a lazy trigger:** refresh before the query, as trail-code does; cold-build equivalent.
- Base+delta and divergence detection are deferred to R4 (`harness/experiments/r4/README.md`).

## Rule

**Core:**
- "PRISM's and the baseline's index-backed tools answer from the working tree as of the call. Before such a call,
  files changed since the last index are re-indexed. A changed file that does not parse is gated (below). Answers
  equal a cold build of that tree. There are no divergence warnings (R4)."

**The index-backed tools:**
- PRISM's version: `prism_digest`, `prism_change_context`, `prism_blast_radius`, `prism_ask`,
  `prism_new_function_context`.
- The baseline's version: `related_code`.
  - It is index-backed too (BM25 over the parsed functions), so the same refresh and the same gate apply to it.
  - This keeps the two versions symmetric. Without it, `related_code` would serve a gated file's old functions with
    no notice.
- **Not gated:** `read_file`, `grep`, `list_dir`, `edit_file` and `run_tests`. They read the disk, as before.

**The trigger:**
- Before each index-backed tool call, a drift probe runs against the files last indexed:
  - `(size, mtime)` first;
  - then a content hash of the files that disagree.
- The index is rebuilt only if a file's bytes moved. Edits with no index-backed call in between cost nothing.

**The gate (as revised by Revision 1, below; the original refuse rule is struck through here):**

> ~~**The gate, and the fairness rule:**~~
> ~~- A changed file whose tree-sitter tree reports an error (`root_node.has_error`) is **gated**.~~
> ~~  - Its index content stays at its **last parsable version**: the agent's last edit that parsed, or the task's start~~
> ~~    state if it never parsed.~~
> ~~  - It is never indexed as broken.~~
> ~~- When a tool call requests content from a gated file, the tool does not return the last parsable version as if it~~
> ~~  were current. It returns:~~
> ~~  - (a) a notice naming the gated file: "file X was edited and does not currently parse; read it directly for the~~
> ~~    current content";~~
> ~~  - (b) whatever information the call would otherwise provide that is not that file's content: for example, callers~~
> ~~    from the graph, flagged "based on last parsable version".~~
> ~~- The agent must call `read_file` to see the broken content. Both versions are then on equal footing: both must read~~
> ~~  broken files directly, and neither can silently serve stale content in place of current.~~
> ~~- **A harness requirement, not only a PRISM behaviour:**~~
> ~~  - the harness never retries a gated call in a way that lets the agent skip `read_file`;~~
> ~~  - an agent that ignores the notice and asks again gets the same notice.~~

→ replaced by Revision 1.

**Equivalence:**
- Every index-backed answer equals, byte for byte, the answer of a cold build of the working tree, with gated files
  at their last parsable version, for all content not from a gated file.

## Equivalence tests (as first written; superseded by Revision 1)

>
> - **Where:** in `tests/`, against the harness's real tool layer, on real repositories from R3's pool: at least one
>   Python and one TypeScript.
> - **What "answer" means:** the full rendered output of each index-backed tool, for a fixed set of queries per
>   repository:
>   - the edited function;
>   - one caller of it;
>   - one function in an unedited file;
>   - one `prism_ask` and one `related_code` query.
>
> **E1, a clean edit:**
> 1. Cold build.
> 2. Edit a file so that it still parses.
> 3. Make the tool calls (which re-index).
> 4. Compare with a fresh cold build of the edited tree. **Byte-identical.**
>
> **E2, a broken edit:**
> 1. Cold build.
> 2. Edit a file so that it has a syntax error.
> 3. Make the tool calls. Assert that the file is gated, that the notice appears, and that the gated content is not
>    served as current.
> 4. Compare with a fresh cold build of the tree with that file at its last parsable version. **Byte-identical for all
>    non-gated content.**
>
> **E3, consecutive edits:**
> - Three consecutive edits to the same file, each followed by the tool calls.
> - Each answer must match the cold build of that snapshot.
> - Include one broken edit between two clean ones, so the last parsable version advances correctly.
>
> **If any test fails:** this amendment is revised before the harness is frozen, never frozen with a failing test.
>

## The qwen notice check (a known model behaviour; not a pass/fail condition)

- **When:** before the pilot.
- **What:** 5 runs of `qwen2.5-coder:14b-instruct-q8_0`, on the same Kaggle setup, on a task where the agent will hit
  a gated file.
- **Recorded:** whether the agent reads the notice and acts on it, by calling `read_file` on the named file.
- **If it does not:**
  - first, the notice's wording is changed;
  - then, the refusal is strengthened: the tool returns an error that names `read_file` as the next step.

  Either change is recorded here before the pilot.

## Results (filled in before the harness freeze)

| item | result | date | commit |
|---|---|---|---|
| E1 clean edit (Python repo; TypeScript repo) | **pass** on both: werkzeug @ 2adaad1ab683, unstorage @ 5781aae96f7f; 9 answers each, byte-identical to an independent cold build | 2026-11-06 | see the note below |
| E2 broken edit (Revision 2's (a)-(e)) | **pass** on both: (a) answers, notice aside, byte-identical to the cold build at the last parsable version; (b) the notice names the file, edit 1 and its hash; (c) the disk holds the broken bytes; (d) a clean-file answer referencing the gated file carries the notice; (e) `related_code` serves last-good entries with the notice | 2026-11-06 | |
| E3 consecutive edits (clean, broken, clean) | **pass** on both: each step byte-identical to its snapshot's cold build; the middle step carries the edit-1 notice; the last carries none and shows edit 3 | 2026-11-06 | |
| qwen notice check (5 runs) | not yet run: before the pilot | | |

**How E1-E3 were run:**
- Command: `python -m harness.experiments.r3.equivalence CLONE SCRATCH`, or `tests/test_r3_equivalence.py` with
  `R3_EQ_REPOS` and `R3_EQ_SCRATCH` set.
- The live side is one `WorkingIndex` + `ToolLayer` over a worktree the test edits. The cold side is a fresh worktree
  per snapshot, with its own index built from nothing.
- 9 answers per snapshot:
  - change context of the edited function, a caller in another file, a function in an unedited file, and a
    clean-file callee;
  - blast radius;
  - digests of three functions;
  - a new-function context;
  - one `prism_ask` and one `related_code` query.

**Note found by the first E2 run (2026-11-06; awaiting the team's sign-off, before the freeze):**
- The first run failed E2(a) on the 3 answers that reach the 4,000-token cap. The notice had been placed inside the
  cap, so it moved the cut, and the served body was an exact prefix of the cold one.
- The tool layer now caps the tool's own answer at 4,000 tokens, and places the notice **in front of the capped
  answer, outside the cap**. That is a few dozen tokens per gated file, the same in both versions, so a notice never
  changes what is served.
- The passes above are with this placement.

## Build cost (replaces the 2-3 day estimate)

| component | time |
|---|---|
| drift probe (size, mtime, hash) | 0.5 day |
| parse gate (keep last parsable; refuse to serve it as current) | 1 day |
| notice and refusal, including the harness's no-retry rule | 0.5 day |
| equivalence tests E1-E3 | 1 day |
| qwen notice check | 0.5 day (before the pilot) |
| **total** | **3-4 days** |

**Runtime cost:** a full reindex per drifted call. Measured at 2-9 s on mid-sized repositories and 34-43 s on
aiohttp- and vitest-sized ones. The pilot's logs give the real per-run total.

## Revision 1 (2026-11-06; before the harness is built or frozen, before any R3a call)

**What changes:** the refuse rule is reverted to serving the last good version with a notice. The rest of R3-5 stands:
- the trigger;
- the index-backed tools, `related_code` included;
- the cold-build equivalence;
- the qwen check;
- the cost.

**Rationale:** "Refusing to serve gated content eliminated PRISM's specialized tools entirely on broken files, making
the PRISM and baseline arms equivalent when the agent writes broken code. The correct approach is to serve the last
parsable version with an explicit notice — preserving PRISM's value while being honest about staleness. read_file
provides the current bytes to either arm. Full versioning (tracking every edit as a version, diffing across
versions) belongs in R4 for divergence detection."

**The rule:**
- "When an index-backed tool is asked about a file that does not currently parse, it answers from the file's last
  parsable version and adds a notice at the top of the response: '⚠️ File X was edited and does not currently parse.
  Content shown is from the last parsable version (edit [n], commit [hash]). Read the file directly for the current
  content.' read_file is not gated — it returns current bytes. related_code remains gated (keyword search would serve
  stale entries silently)."
- **Terms, fixed here:**
  - **"edit [n]":** the number of the agent's edits to that file that the drift probe has seen; 0 means the task's
    start state.
  - **"commit [hash]":** inside a task, edits are not commits, so this is the first 12 hex digits of the sha256 of the
    bytes shown.
  - **"Gated" for `related_code`:** it does not return entries from a gated file, and it adds the same notice naming
    the file.
- **The fairness note from the first draft is withdrawn:** "the harness never retries a gated call in a way that lets
  the agent skip read_file". With content served, there is nothing to skip. The harness passes every tool response
  through unchanged.

**Limitation:**
- "When a file does not parse, PRISM's tools serve the last parsable version with an explicit notice; the baseline's
  related_code is gated, and its read_file/grep serve the current broken bytes. Both arms can therefore see the
  current broken content via read_file. PRISM additionally offers a structured view of the last good version. The
  comparative advantage PRISM has on valid files is reduced but not eliminated on broken files; R3 measures PRISM with
  this partial availability, not its absence."

**The equivalence tests (mandatory; they replace the versions above):**
- **Where:** in `tests/`, against the harness's real tool layer, on at least one Python and one TypeScript repository
  from R3's pool.
- **What "answer" means:** the full rendered output of each index-backed tool, for a fixed set of queries per
  repository:
  - the edited function;
  - one caller of it;
  - one function in an unedited file;
  - one `prism_ask` query;
  - one `related_code` query.

**E1, a clean edit (unchanged):**
1. Cold build.
2. Edit a file so that it still parses.
3. Make the tool calls (which re-index).
4. Every answer is byte-identical to a fresh cold build of the edited tree.

**E2, a broken edit (serve with a notice):**
1. Cold build.
2. Edit a file so that it has a syntax error.
3. Make the tool calls, and check:
   - (a) every answer, notice aside, is byte-identical to a cold build of the tree with that file at its last
     parsable version;
   - (b) the notice appears, names the file, and states that the content is from the last parsable version, with
     its edit number and hash;
   - (c) `read_file` on the same file returns the current broken bytes;
   - (d) a cross-reference from a clean file to a caller in the gated file is served with a flag naming the gated
     file;
   - (e) `related_code` returns no entry from the gated file, and carries the notice.

**E3, consecutive edits:**
1. Three edits to the same file: clean, then broken, then clean. Each is followed by the tool calls.
2. Each call matches the cold build of its snapshot.
3. The middle call serves the first edit's version (edit 1, with its hash) with the notice.
4. The last call serves the third edit with no notice.

**Results:** the results table above is filled in against these tests, before the harness freeze.

## Revision 2 (2026-11-06; before the harness is frozen, before any R3a call)

Two changes; nothing else in R3-5 or Revision 1 changes.

### 1. `related_code` serves the last parsable version (replaces Revision 1's "related_code remains gated")

- ~~"related_code remains gated (keyword search would serve stale entries silently)"~~, and ~~"`related_code` does
  not return entries from a gated file, and adds the same notice"~~ → replaced:
- "related_code behaves like PRISM's index-backed tools: on a file that does not currently parse, it serves entries
  from the file's last parsable version and carries the same notice. This preserves symmetry — both arms' index-based
  tools serve last-good when a file is broken; both arms' disk tools (read_file, grep) serve current bytes."
- **Why:** if `related_code` returned nothing on gated files while PRISM's tools served the last good version, PRISM's
  advantage on broken-file tasks would come from the asymmetric gate, not from PRISM's design. That is a confound.
- **E2(e), updated:** ~~"(e) `related_code` returns no entry from the gated file, and carries the notice."~~ →
  "(e) related_code returns entries from the last parsable version of the gated file, and carries the same notice
  naming the file, edit number, and hash. It does not return current (broken) bytes."
- **The limitation note, updated to match:** "When a file does not parse, both arms' index-backed tools (PRISM's five
  and the baseline's related_code) serve its last parsable version with the same explicit notice; both arms'
  read_file and grep serve the current broken bytes."

### 2. What counts as a file that does not parse (the gate)

- "Gating is based on whether the edit adds error nodes compared with the file's last indexed version, not on
  absolute has_error. For files that parse cleanly at task start, this is equivalent to has_error. For files that
  start with pre-existing error nodes (from grammar limitations, e.g. tree-sitter grammar coverage), a clean edit
  does not gate the file; only an edit that adds error nodes does.
- last indexed version means the last version written to the WorkingIndex shadow worktree — the version the agent
  was last served. Pre-existing error nodes are recorded per file at task start and used as the baseline for the
  delta comparison.
- Examples from the R3 pool: vitest has 9 files with pre-existing errors (e.g. packages/mocker/src/node/esmWalker.ts),
  h3 has 2 test files. Under the literal has_error rule these would be gated for the whole task even after a clean
  edit. Under Revision 2 they are gated only if the agent's edit adds new error nodes."

**Where it is implemented:**
- `harness/experiments/r3/working_index.py`: `WorkingIndex._gated`, with `FileState.shown_errors` (the ERROR plus
  MISSING count of the shadow's bytes) as the baseline.
- At task start, every file's count is recorded, and nothing is gated.

**Edit and hash terms (Revision 1):** confirmed by the team as written. n is the file's edit count (0 is the start
state); the hash is the first 12 hex digits of the sha256 of the bytes shown.

## Revision 3 (2026-11-06; before the harness is frozen, before any R3a call): the notice sits outside the cap

- The note above, "awaiting the team's sign-off", is approved as this revision.
- **Rule:** "Tool responses cap content at 4,000 tokens; the staleness notice (file name, edit number, hash) is added
  outside this cap because it is metadata about the content, not content itself. Notices count against the run's
  300,000-token total budget but not against the per-call content cap. Both arms carry the notice on gated files
  symmetrically. The pilot will record the actual notice overhead per run and report it alongside the primary
  results."
- **Rationale:** "Without the fix, E2 failed because notices displaced content, truncating the body compared to a cold
  build. The equivalence test correctly caught this. Moving notices outside the cap preserves the
  content-equivalence invariant the amendment requires."
- **Implementation:** `harness/experiments/r3/tools.py`, `ToolLayer.call`, which returns the notice followed by the
  capped answer. The token accounting (still to be built) counts the whole response, notice included, against the
  300,000 tokens.
