# Pilot run notebooks

Per-pilot Kaggle notebook instructions. For the general Kaggle workflow
(branch/results-branch conventions, env vars both harnesses read), see
`kaggle/README.md`.

## Pilot-4

6 engines (5 single-pass: BM25, BFS-forward, BFS-bidirectional,
PragmaticOracle, Prism single-pass; 1 two-pass: Prism two-pass) x 20
Django T02 debug tasks x 3 budgets (2000/4000/8000) x 5 seeds. Split
into 5 batches by seed, one Kaggle session per batch.

Prerequisites:
- GPU T4 x2 (Kaggle accelerator setting).
- A `GITHUB_TOKEN` secret attached to the notebook, for the account
  authorized to push to the `pilot-4-progress` branch.
- The 14B model pre-pulled speeds up the first batch; if it isn't, the
  cell pulls it itself (a few extra minutes on batch 1 only).

Run:
1. Paste `kaggle/pilot_4_cell.py` into a Kaggle notebook cell.
2. Set `BATCH_NUM = 1`. Save & Run All. ~1.5 hours.
3. Repeat with `BATCH_NUM = 2, 3, 4, 5`, each in a new session.
4. After batch 5, run the merge + gate commands the cell prints at the
   end (locally, not on Kaggle - they don't need a GPU).

Expected total: ~7.5 hours across 5 sessions.

Progress persists across batches via the `pilot-4-progress` branch:
each batch pulls `reports/pilot-4/` from it at the start and
force-pushes the accumulated checkpoints back at the end, regardless
of whether either harness call succeeded - a partial batch never loses
the other harness's progress.

## Pilot-4, patched two-pass (Phase B) — status: strongly promising, not yet final

A patched two-pass evaluation (`feature/two-pass-phase-b-patches`,
seeds 42-45, Django, `qwen2.5-coder:14b-instruct-q8_0`) completed
successfully on Kaggle, but the cell that ran it never pushed its
result files anywhere - `checkpoint_two_pass.json` and
`checkpoint_merged.json` from that run do not exist on any branch in
this repository. The only record of it is a pasted log transcript, so
its numbers are unverified against real data. Full status, the exact
results table with each number labeled JSON-verified vs. log-parsed,
and the remaining gaps: **`docs/pilot/two_pass_phase_b_status.md`**.

To fix this - rerun and this time actually preserve the result:
1. Paste `kaggle/pilot_4_patched_rerun_and_preserve_cell.py` into a
   Kaggle notebook cell. Same prerequisites as above (GPU T4 x2,
   `GITHUB_TOKEN` secret).
2. Save & Run All. Runs all 5 seeds for two-pass (the lost 42-45 data
   has to be redone, not just the missing seed 46) and tops up
   single-pass with the one real missing seed (46) against the
   already-durable `pilot-4-progress` data. ~2-2.5 hours.
3. Unlike the earlier cell, this one pushes both checkpoint files to
   `pilot-4-patched-progress` immediately after each harness run
   completes, not just at the end - a crash partway through still
   leaves real, pushed data behind.
4. It prints `sha256sum` for all three output files and runs the
   sanity check + both gate comparisons itself. Bring back the two
   gate reports, the three hashes, and the parse-failure count.

Single-pass is not deprecated by any of this - it stays the fallback
comparison baseline until the project's own 15pp gate threshold is
cleared.
