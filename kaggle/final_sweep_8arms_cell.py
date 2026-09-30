# Kaggle cell - PRISM final empirical sweep, 8-arm protocol
#
# 4 corpora (FastAPI 25, Django 20, Express 20, tRPC 25 debug tasks =
# 90 tasks) x 8 arms x 10 seeds (42..51) = 7,200 cells, against the
# pinned OpenAI snapshot gpt-4o-mini-2024-07-18 at temperature 0.2.
# (The "8,000 cells" figure assumed 25 tasks per corpus; Django and
# Express have 20 debug tasks each that pass the agreement gate.)
#
# Paste this whole file into one Kaggle notebook cell (internet ON).
# Secrets (Kaggle "Add-ons > Secrets", or env vars):
#   OPENAI_API_KEY - the key the sweep bills against
#   GITHUB_TOKEN   - only if RESULTS_BRANCH is set, to push progress
#
# Resumable: each repo's cells.jsonl is the checkpoint. With
# RESULTS_BRANCH set, reports/final_sweep/full/ is pulled from that
# branch before running and pushed back after every repo, so a session
# that hits Kaggle's time limit continues where it stopped when the
# cell is re-run.

import os
import subprocess
import sys

# --------------------------------------------------------------------- #
# User-editable
# --------------------------------------------------------------------- #
CODE_REF = "claude/prism-final-empirical-8arms-kp3tpt"  # branch or commit with benchmarks/final_sweep
RESULTS_BRANCH = ""  # e.g. "final-sweep-8arms-results"; "" = don't push
REPOS = ["trpc", "express", "fastapi", "django"]  # cheapest first
SEEDS = ",".join(str(s) for s in range(42, 52))
WORKERS = 8
MAX_COST_USD = 25.0  # hard stop per corpus (counts cells already in that corpus's cells.jsonl)
EXTRA_ARMS = ""  # e.g. ",prism_plus_distractors_k5,prism_plus_distractors_k10" for a dose-response curve

REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
SCE_DIR = "/kaggle/working/SCE"
OUT_ROOT = "reports/final_sweep/full"


def run(cmd, cwd=None, check=True, env=None):
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=cwd, check=check, env=env)


def secret(name):
    value = os.environ.get(name)
    if value:
        return value
    try:
        from kaggle_secrets import UserSecretsClient

        return UserSecretsClient().get_secret(name)
    except Exception:
        return None


openai_key = secret("OPENAI_API_KEY")
if not openai_key:
    raise SystemExit("OPENAI_API_KEY is not set (env var or Kaggle secret)")
github_token = secret("GITHUB_TOKEN") if RESULTS_BRANCH else None
if RESULTS_BRANCH and not github_token:
    raise SystemExit("RESULTS_BRANCH is set but GITHUB_TOKEN is not")

clone_url = REPO_URL.replace("https://", f"https://{github_token}@") if github_token else REPO_URL
if not os.path.isdir(SCE_DIR):
    run(["git", "clone", clone_url, SCE_DIR])
run(["git", "fetch", "origin", CODE_REF], cwd=SCE_DIR)
run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=SCE_DIR)
run([sys.executable, "-m", "pip", "install", "-q", "-e", ".[dev,bench]", "pyarrow"], cwd=SCE_DIR)

if RESULTS_BRANCH:
    # Restore earlier progress, if any, without touching the code checkout.
    fetched = run(["git", "fetch", "origin", RESULTS_BRANCH], cwd=SCE_DIR, check=False)
    if fetched.returncode == 0:
        run(["git", "checkout", "FETCH_HEAD", "--", OUT_ROOT], cwd=SCE_DIR, check=False)


def push_progress(label):
    if not RESULTS_BRANCH:
        return
    run(["git", "add", "-f", OUT_ROOT], cwd=SCE_DIR, check=False)
    run(["git", "-c", "user.name=kaggle", "-c", "user.email=kaggle@localhost", "commit", "-q", "-m",
         f"final sweep progress: {label}"], cwd=SCE_DIR, check=False)
    run(["git", "push", "-q", "origin", f"HEAD:refs/heads/{RESULTS_BRANCH}", "--force"], cwd=SCE_DIR, check=False)


env = dict(os.environ, OPENAI_API_KEY=openai_key)
arms = "all" if not EXTRA_ARMS else (
    "baseline_bfs_bidirectional,pragmatic_oracle,scaffolded_oracle,prism_full,ablation_lexical_anchors,"
    "ablation_signature_only,ablation_no_purity,prism_plus_distractors" + EXTRA_ARMS
)
for repo in REPOS:
    result = run([
        sys.executable, "-m", "benchmarks.final_sweep.runner",
        "--repo", repo, "--arms", arms, "--seeds", SEEDS, "--workers", str(WORKERS),
        "--out", f"{OUT_ROOT}/{repo}", "--max-cost-usd", str(MAX_COST_USD),
    ], cwd=SCE_DIR, check=False, env=env)
    push_progress(f"{repo} (exit {result.returncode})")
    if result.returncode not in (0, 1):  # 1 = finished with schema-invalid rows, still worth pushing
        raise SystemExit(f"{repo} sweep failed with exit code {result.returncode}")

print("done - per-repo summaries in", f"{SCE_DIR}/{OUT_ROOT}/<repo>/summary.md", flush=True)
