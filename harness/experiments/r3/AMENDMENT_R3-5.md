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

**The gate, and the fairness rule:**
- A changed file whose tree-sitter tree reports an error (`root_node.has_error`) is **gated**.
  - Its index content stays at its **last parsable version**: the agent's last edit that parsed, or the task's start
    state if it never parsed.
  - It is never indexed as broken.
- When a tool call requests content from a gated file, the tool does not return the last parsable version as if it
  were current. It returns:
  - (a) a notice naming the gated file: "file X was edited and does not currently parse; read it directly for the
    current content";
  - (b) whatever information the call would otherwise provide that is not that file's content: for example, callers
    from the graph, flagged "based on last parsable version".
- The agent must call `read_file` to see the broken content. Both versions are then on equal footing: both must read
  broken files directly, and neither can silently serve stale content in place of current.
- **A harness requirement, not only a PRISM behaviour:**
  - the harness never retries a gated call in a way that lets the agent skip `read_file`;
  - an agent that ignores the notice and asks again gets the same notice.

**Equivalence:**
- Every index-backed answer equals, byte for byte, the answer of a cold build of the working tree, with gated files
  at their last parsable version, for all content not from a gated file.

## Equivalence tests (mandatory; the amendment is complete only when all three pass)

- **Where:** in `tests/`, against the harness's real tool layer, on real repositories from R3's pool: at least one
  Python and one TypeScript.
- **What "answer" means:** the full rendered output of each index-backed tool, for a fixed set of queries per
  repository:
  - the edited function;
  - one caller of it;
  - one function in an unedited file;
  - one `prism_ask` and one `related_code` query.

**E1, a clean edit:**
1. Cold build.
2. Edit a file so that it still parses.
3. Make the tool calls (which re-index).
4. Compare with a fresh cold build of the edited tree. **Byte-identical.**

**E2, a broken edit:**
1. Cold build.
2. Edit a file so that it has a syntax error.
3. Make the tool calls. Assert that the file is gated, that the notice appears, and that the gated content is not
   served as current.
4. Compare with a fresh cold build of the tree with that file at its last parsable version. **Byte-identical for all
   non-gated content.**

**E3, consecutive edits:**
- Three consecutive edits to the same file, each followed by the tool calls.
- Each answer must match the cold build of that snapshot.
- Include one broken edit between two clean ones, so the last parsable version advances correctly.

**If any test fails:** this amendment is revised before the harness is frozen, never frozen with a failing test.

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
| E1 clean edit (Python repo; TypeScript repo) | not yet run: the mechanism is not built | | |
| E2 broken edit | not yet run | | |
| E3 consecutive edits | not yet run | | |
| qwen notice check (5 runs) | not yet run: before the pilot | | |

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
