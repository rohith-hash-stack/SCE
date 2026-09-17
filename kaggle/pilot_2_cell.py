# Kaggle cell - Pilot-2 full run (24 tasks x 5 engines x 3 budgets x 5 seeds)
#
# Paste the contents of this file into a single Kaggle notebook cell
# (the `!`-prefixed lines are Jupyter/Kaggle shell magic - this file is
# not meant to be run as `python kaggle/pilot_2_cell.py`). Reuses the
# same structure as the causal-path A/B test cell (reports/ab_with_block,
# reports/ab_without_block) - same env-var/runner-invocation shape,
# scaled to the full 24-task corpus and the 3-budget sweep.
#
# Prerequisites (see kaggle/README.md): notebook running with GPU T4 x2,
# LLM_BASE_URL/LLM_MODEL/LLM_API_KEY_ENV already set to point at this
# session's local Ollama instance before running the cell below.
#
# Estimated wall time: ~5 hours (pilot-1 ran this same 24-task x
# 3-budget x 5-seed x 5-engine grid - 1800 cells - in ~5 hours).

# --- 1. Pin the exact commit this run measures against -------------- #
# develop HEAD at pilot-2-baseline (Phase 1A + Phase 1B, verified green
# per the Step 2 checks in this session's own report).
PINNED_COMMIT = "aa626d5480b6a792bfcecc24eac76f762e1ba017"

get_ipython().system('git fetch origin develop')
get_ipython().system('git reset --hard {PINNED_COMMIT}')

# --- 2. Per-run results branch (UTC timestamp) ----------------------- #
import datetime

RESULTS_BRANCH = f"pilot-2-{datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"
print("Results branch:", RESULTS_BRANCH)

# --- 3. The full 24-task, 3-budget, 5-seed pilot run ----------------- #
# No --tasks-dir override - omitting it falls back to runner.py's own
# DEFAULT_TASKS_DIR_TEMPLATE ("benchmarks/ground_truth/tasks/{repo}"),
# i.e. the full benchmarks/ground_truth/tasks/django/ directory (24
# tasks), not the 6-task kaggle/ab_tasks/ subset the A/B cell used.
get_ipython().system(
    'python -m benchmarks.runner --mode=pilot --repo=django '
    '--budgets 2000 4000 8000 '
    '--seeds 42,43,44,45,46 '
    '--output reports/pilot-2 '
    '--checkpoint reports/pilot-2/checkpoint.json '
    '--pragmatic-oracle'
)

# --- 4. Push results to the per-run branch --------------------------- #
# NOTE: benchmarks/runner.py's own PILOT_RESULTS_BRANCH auto-push
# mechanism (_push_checkpoint, invoked after every CHECKPOINT_INTERVAL
# fresh calls) is hardcoded to `git add -f reports/pilot/` - it does
# NOT know about reports/pilot-2/, so setting PILOT_RESULTS_BRANCH here
# would silently push nothing useful for this run. Not something this
# cell can fix (that's runner.py, out of scope) - pushed explicitly,
# once, at the end instead. This means a session that dies mid-run has
# no incremental off-machine backup the way pilot-1's run did; if you
# need that safety net, re-run just this step 4 by hand periodically
# during the ~5 hour run (reports/pilot-2/checkpoint.json is written to
# disk continuously regardless of whether this push step has run).
get_ipython().system('git add -f reports/pilot-2/')
get_ipython().system('git commit -m "pilot-2: full 24-task run ({RESULTS_BRANCH})"')
get_ipython().system('git push origin HEAD:{RESULTS_BRANCH}')

print("Done. Results pushed to branch:", RESULTS_BRANCH)
print("Compare against pilot-1 with:")
print("  python scripts/compare_ab_runs.py reports/pilot/checkpoint.json reports/pilot-2/checkpoint.json")
