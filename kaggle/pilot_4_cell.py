# Kaggle cell - Pilot-4 batch runner
#
# 6 engines (5 single-pass: BM25/baseline_rag, BFS-forward, BFS-
# bidirectional, PragmaticOracle, Prism single-pass/prism_v11 + 1
# two-pass: Prism two-pass/prism_two_pass) x 20 Django T02 debug tasks
# x 3 budgets (2000/4000/8000) x 5 seeds. Split into 5 batches, one per
# seed, each run in its own Kaggle session (a fresh GPU T4 x2 session
# and a fresh clone each time).
#
# Paste this whole file into a single Kaggle notebook cell and run it.
# Progress (reports/pilot-4/) is pulled from, and pushed back to, the
# `pilot-4-progress` branch, so batch N+1 continues from where batch N
# left off. Both harness invocations use --resume against the restored
# checkpoints, so a batch that's interrupted and re-run does not re-pay
# for already-completed cells.
#
# CLI flags for both harnesses were verified directly against
# benchmarks/runner.py's and benchmarks/run_two_pass_benchmark.py's own
# build_arg_parser() - see this session's report for file:line evidence.
# MODEL below (the Ollama tag) is the same qwen2.5-coder:14b-instruct-
# q8_0 tag already used in a real prior sweep on this project
# (docs/design_formalism.md Section 10.3) - change it in one place if a
# different tag/quantization is wanted for this pilot.

import json
import os
import subprocess
import sys
import time

# --------------------------------------------------------------------- #
# User-editable
# --------------------------------------------------------------------- #
BATCH_NUM = 1  # 1..5 - change this each Kaggle session
MODEL = "qwen2.5-coder:14b-instruct-q8_0"  # Ollama model tag (LLM_MODEL)

# --------------------------------------------------------------------- #
# Fixed config
# --------------------------------------------------------------------- #
BATCH_SEEDS = {1: "42", 2: "43", 3: "44", 4: "45", 5: "46"}
PINNED_COMMIT = "059409a"
PROGRESS_BRANCH = "pilot-4-progress"
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
EXPECTED_GITHUB_USER = "rohith-hash-stack"
OLLAMA_VERSION = "0.34.0"

SCE_DIR = "/kaggle/working/SCE"
REPORT_DIR = f"{SCE_DIR}/reports/pilot-4"
SINGLE_PASS_CKPT = f"{REPORT_DIR}/checkpoint_single_pass.json"
TWO_PASS_CKPT = f"{REPORT_DIR}/checkpoint_two_pass.json"
BUDGETS = ["2000", "4000", "8000"]

if BATCH_NUM not in BATCH_SEEDS:
    raise SystemExit(f"BATCH_NUM must be 1..5, got {BATCH_NUM!r}")
SEED = BATCH_SEEDS[BATCH_NUM]
print(f"=== Pilot-4 batch {BATCH_NUM}/5 (seed={SEED}, model={MODEL}) ===", flush=True)


def run(cmd, cwd=None, check=True, capture=False, env=None):
    """Logs the exact command before running it. `cmd` is a list (run
    directly) or a string (run via the shell, for pipes/backgrounding).
    Raises RuntimeError on a non-zero exit when check=True; otherwise
    returns the CompletedProcess so the caller can inspect returncode."""
    printable = cmd if isinstance(cmd, str) else " ".join(cmd)
    print(f"[cmd] {printable}", flush=True)
    result = subprocess.run(
        cmd, cwd=cwd, env=env, shell=isinstance(cmd, str), text=True,
        capture_output=capture,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed (exit {result.returncode}): {printable}")
    return result


def abort(msg):
    print(f"[ABORT] {msg}", file=sys.stderr, flush=True)
    raise SystemExit(1)


# --------------------------------------------------------------------- #
# Step 1: GITHUB_TOKEN (env, else Kaggle secret), verify user
# --------------------------------------------------------------------- #
github_token = os.environ.get("GITHUB_TOKEN")
if not github_token:
    try:
        from kaggle_secrets import UserSecretsClient
        github_token = UserSecretsClient().get_secret("GITHUB_TOKEN")
    except Exception as exc:
        abort(f"GITHUB_TOKEN not in env and UserSecretsClient fallback failed: {exc}")
if not github_token:
    abort("GITHUB_TOKEN is not set (env var or Kaggle secret) - attach it before running this cell")

whoami = run(
    ["curl", "-s", "-H", f"Authorization: token {github_token}", "https://api.github.com/user"],
    check=True, capture=True,
)
try:
    who = json.loads(whoami.stdout or "{}")
except json.JSONDecodeError:
    who = {}
if who.get("login") != EXPECTED_GITHUB_USER:
    abort(f"GITHUB_TOKEN identifies as {who.get('login')!r}, expected {EXPECTED_GITHUB_USER!r}")
print(f"[ok] GITHUB_TOKEN verified for user {who['login']}", flush=True)

# --------------------------------------------------------------------- #
# Step 2: verify GPU T4 x2
# --------------------------------------------------------------------- #
gpu_check = run(
    ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
    check=False, capture=True,
)
gpu_lines = [line for line in (gpu_check.stdout or "").strip().splitlines() if line.strip()]
t4_count = sum(1 for line in gpu_lines if "T4" in line)
if gpu_check.returncode != 0 or t4_count < 2:
    abort(f"expected GPU T4 x2, found {gpu_lines!r} (nvidia-smi exit {gpu_check.returncode})")
print(f"[ok] GPU check passed: {gpu_lines}", flush=True)

# --------------------------------------------------------------------- #
# Step 3: zstd + Ollama (pinned), start it, verify CUDA
# --------------------------------------------------------------------- #
run(["apt-get", "-y", "-qq", "install", "zstd"], check=True)

already_installed = subprocess.run(["which", "ollama"], capture_output=True, text=True).returncode == 0
if not already_installed:
    run(f"curl -fsSL https://ollama.com/install.sh | OLLAMA_VERSION={OLLAMA_VERSION} sh", check=True)
else:
    print("[ok] ollama binary already present", flush=True)

ollama_log_path = "/kaggle/working/ollama.log"
run(f"nohup ollama serve > {ollama_log_path} 2>&1 &", check=False)
time.sleep(10)

cuda_seen = False
for _ in range(12):
    if os.path.exists(ollama_log_path):
        with open(ollama_log_path) as f:
            log_text = f.read()
        if "CUDA" in log_text or "cuda" in log_text:
            cuda_seen = True
            break
    time.sleep(5)
if not cuda_seen:
    abort(f"Ollama log at {ollama_log_path} shows no CUDA line after ~70s - GPU not attached to Ollama")
print("[ok] Ollama running with CUDA", flush=True)

# --------------------------------------------------------------------- #
# Step 4: pull MODEL if absent
# --------------------------------------------------------------------- #
tags = run(["ollama", "list"], check=True, capture=True)
if MODEL not in (tags.stdout or ""):
    run(["ollama", "pull", MODEL], check=True)
else:
    print(f"[ok] {MODEL} already pulled", flush=True)

# --------------------------------------------------------------------- #
# Step 5: clone SCE, pin to PINNED_COMMIT, clean
# --------------------------------------------------------------------- #
if not os.path.isdir(SCE_DIR):
    clone_url = REPO_URL.replace("https://", f"https://{github_token}@")
    run(["git", "clone", clone_url, SCE_DIR], check=True)
run(["git", "fetch", "origin", PINNED_COMMIT], cwd=SCE_DIR, check=True)
run(["git", "checkout", PINNED_COMMIT], cwd=SCE_DIR, check=True)
run(["git", "clean", "-fdx"], cwd=SCE_DIR, check=True)
print(f"[ok] SCE pinned to {PINNED_COMMIT}", flush=True)

# --------------------------------------------------------------------- #
# Step 6: restore reports/pilot-4/ from PROGRESS_BRANCH if it exists
# remotely; else fresh start
# --------------------------------------------------------------------- #
branch_check = run(
    ["git", "ls-remote", "--exit-code", "--heads", "origin", PROGRESS_BRANCH],
    cwd=SCE_DIR, check=False,
)
if branch_check.returncode == 0:
    run(["git", "fetch", "origin", PROGRESS_BRANCH], cwd=SCE_DIR, check=True)
    restore = run(
        ["git", "checkout", f"origin/{PROGRESS_BRANCH}", "--", "reports/pilot-4"],
        cwd=SCE_DIR, check=False,
    )
    if restore.returncode != 0:
        print(f"[warn] {PROGRESS_BRANCH} exists but has no reports/pilot-4/ yet - starting fresh", flush=True)
        os.makedirs(REPORT_DIR, exist_ok=True)
    else:
        print(f"[ok] restored reports/pilot-4/ from {PROGRESS_BRANCH}", flush=True)
else:
    os.makedirs(REPORT_DIR, exist_ok=True)
    print(f"[ok] {PROGRESS_BRANCH} does not exist remotely yet - starting fresh", flush=True)

# --------------------------------------------------------------------- #
# Step 7: install the package
# --------------------------------------------------------------------- #
run([sys.executable, "-m", "pip", "install", "-e", ".[dev,bench]"], cwd=SCE_DIR, check=True)

# --------------------------------------------------------------------- #
# Step 8: point both harnesses at the local Ollama instance
# --------------------------------------------------------------------- #
os.environ["LLM_BASE_URL"] = "http://localhost:11434/v1"
os.environ["LLM_MODEL"] = MODEL
os.environ["LLM_API_KEY_ENV"] = "OLLAMA_API_KEY"
os.environ["OLLAMA_API_KEY"] = "ollama"
os.environ["PRISM_ENABLE_CAUSAL_PATH"] = "1"

# --------------------------------------------------------------------- #
# Step 9: warm-up call (loads MODEL into GPU memory before the real run)
# --------------------------------------------------------------------- #
run(["ollama", "run", MODEL, "Say OK."], check=False)

# --------------------------------------------------------------------- #
# Step 10: single-pass harness (5 engines) for this batch's seed
# --------------------------------------------------------------------- #
single_pass_cmd = [
    sys.executable, "-m", "benchmarks.runner",
    "--mode=pilot",
    "--repo=django",
    "--task-type", "debug",
    "--scorer", "causal",
    "--budgets", *BUDGETS,
    "--seeds", SEED,
    "--pragmatic-oracle",
    "--resume",
    "--checkpoint", SINGLE_PASS_CKPT,
    "--output", REPORT_DIR,
]
single_pass_result = run(single_pass_cmd, cwd=SCE_DIR, check=False)
if single_pass_result.returncode != 0:
    print(
        f"[warn] single-pass harness exited {single_pass_result.returncode} - "
        "continuing to the two-pass harness anyway (partial-batch failures must not lose the other harness's progress)",
        file=sys.stderr, flush=True,
    )

# --------------------------------------------------------------------- #
# Step 11: two-pass harness (Prism two-pass) for this batch's seed
# --------------------------------------------------------------------- #
two_pass_cmd = [
    sys.executable, "-m", "benchmarks.run_two_pass_benchmark",
    "--repo=django",
    "--budgets", *BUDGETS,
    "--seeds", SEED,
    "--resume",
    "--checkpoint", TWO_PASS_CKPT,
    "--output", f"{REPORT_DIR}/raw_two_pass_seed{SEED}.json",
]
two_pass_result = run(two_pass_cmd, cwd=SCE_DIR, check=False)
if two_pass_result.returncode != 0:
    print(f"[warn] two-pass harness exited {two_pass_result.returncode}", file=sys.stderr, flush=True)

# --------------------------------------------------------------------- #
# Step 12: push accumulated progress regardless of the exit codes above
# --------------------------------------------------------------------- #
run(["git", "add", "-f", "reports/pilot-4/"], cwd=SCE_DIR, check=True)
commit_result = run(
    ["git", "commit", "-m", f"pilot-4: batch {BATCH_NUM}/5 (seed={SEED})"],
    cwd=SCE_DIR, check=False,
)
if commit_result.returncode != 0:
    print("[warn] nothing to commit (no new progress this batch)", flush=True)
run(["git", "push", "--force", "origin", f"HEAD:{PROGRESS_BRANCH}"], cwd=SCE_DIR, check=True)
print(f"[ok] pushed reports/pilot-4/ to {PROGRESS_BRANCH}", flush=True)


# --------------------------------------------------------------------- #
# Step 13: DONE
# --------------------------------------------------------------------- #
def _count_cells(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return len(data.get("cells", {}))
    except Exception:
        return 0


sp_count = _count_cells(SINGLE_PASS_CKPT)
tp_count = _count_cells(TWO_PASS_CKPT)

print("\n=== DONE ===")
print(f"Batch {BATCH_NUM}/5 (seed={SEED}) finished.")
print(f"  single-pass checkpoint: {sp_count} cells  ({SINGLE_PASS_CKPT})")
print(f"  two-pass checkpoint:    {tp_count} cells  ({TWO_PASS_CKPT})")
print(f"  pushed to branch:       {PROGRESS_BRANCH}")

if BATCH_NUM < 5:
    print(f"\nNext: set BATCH_NUM = {BATCH_NUM + 1} above and Save & Run All in a new Kaggle session.")
else:
    print("\nAll 5 batches complete. Run these locally (not on Kaggle):")
    print("  git fetch origin pilot-4-progress")
    print("  git checkout pilot-4-progress -- reports/pilot-4")
    print("  python scripts/merge_pilot_checkpoints.py \\")
    print("    --single-pass reports/pilot-4/checkpoint_single_pass.json \\")
    print("    --two-pass    reports/pilot-4/checkpoint_two_pass.json \\")
    print("    --output      reports/pilot-4/checkpoint_merged.json")
    print("  python scripts/apply_gate.py \\")
    print("    --checkpoint reports/pilot-4/checkpoint_merged.json")
