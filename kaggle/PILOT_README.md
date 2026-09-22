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
