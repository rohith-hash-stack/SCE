# ============================================================
# Prism Pilot-4 — Patched Two-Pass: Full Rerun + Artifact Preservation
# Branch: feature/two-pass-phase-b-patches @ b75d1c5
# (commits 7512ab0 fix-turn1-resilience, b75d1c5 feat-callee-
# autoinclusion-and-upstream-admission)
#
# Why this exists: an earlier patched-two-pass evaluation (seeds
# 42-45, 240 cells) completed successfully on Kaggle, but its own
# cell never pushed the result files anywhere, and the log-parsed
# numbers (mean score 0.957, 196/240 perfect, 0/240 parse failures,
# ~5733 tokens/cell) could not be independently re-verified once the
# session ended. This cell fixes that in two ways:
#
#   1. Adds the missing 5th seed (46) to BOTH single-pass and
#      two-pass, completing the pilot-4 plan for real.
#   2. Two-pass has to be run for ALL FIVE seeds here, not just the
#      new one - the earlier 42-45 two-pass result was never saved
#      anywhere durable, so there is no real checkpoint to --resume
#      from. Single-pass is different: its 42-45 result IS safely on
#      pilot-4-progress, so --resume there only computes seed 46 fresh.
#   3. Pushes BOTH checkpoint files to a results branch immediately
#      (PROGRESS_BRANCH below) - the step the earlier cell was
#      missing - so this can never happen again.
#
# Paste this whole file into one Kaggle notebook cell. GPU T4 x2
# required; GITHUB_TOKEN attached as a Kaggle secret (or env var).
# ============================================================

import hashlib
import json
import os
import subprocess
import sys
import time

# --------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------- #
MODEL = "qwen2.5-coder:14b-instruct-q8_0"          # Ollama model tag (LLM_MODEL)
PATCH_COMMIT = "b75d1c58fe9cc48a8525e4ff876b229cfb212aa9"
PATCH_BRANCH = "feature/two-pass-phase-b-patches"
SINGLE_PASS_SOURCE_BRANCH = "pilot-4-progress"      # holds the real, durable seeds 42-45 single-pass data
PROGRESS_BRANCH = "pilot-4-patched-progress"        # NEW - where THIS run's results get pushed, this time
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
EXPECTED_GITHUB_USER = "rohith-hash-stack"
OLLAMA_VERSION = "0.34.0"

SCE_DIR = "/kaggle/working/SCE"
SINGLE_PASS_REPORT_DIR = f"{SCE_DIR}/reports/pilot-4"
TWO_PASS_REPORT_DIR = f"{SCE_DIR}/reports/pilot-4-patched"
SINGLE_PASS_CKPT = f"{SINGLE_PASS_REPORT_DIR}/checkpoint_single_pass.json"
TWO_PASS_CKPT = f"{TWO_PASS_REPORT_DIR}/checkpoint_two_pass.json"
MERGED_CKPT = f"{TWO_PASS_REPORT_DIR}/checkpoint_merged.json"

ALL_FIVE_SEEDS = "42,43,44,45,46"
BUDGETS = ["2000", "4000", "8000"]

print("=== Pilot-4 patched two-pass: full rerun + preservation (5 seeds) ===", flush=True)


def run(cmd, cwd=None, check=True, capture=False, env=None):
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


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------- #
# Step 1: GITHUB_TOKEN, verify user
# --------------------------------------------------------------------- #
github_token = os.environ.get("GITHUB_TOKEN")
if not github_token:
    try:
        from kaggle_secrets import UserSecretsClient
        github_token = UserSecretsClient().get_secret("GITHUB_TOKEN")
    except Exception as exc:
        abort(f"GITHUB_TOKEN not in env and UserSecretsClient fallback failed: {exc}")
if not github_token:
    abort("GITHUB_TOKEN is not set - attach it before running this cell")

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
# Step 2: GPU T4 x2
# --------------------------------------------------------------------- #
gpu_check = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], check=False, capture=True)
gpu_lines = [line for line in (gpu_check.stdout or "").strip().splitlines() if line.strip()]
if gpu_check.returncode != 0 or sum(1 for l in gpu_lines if "T4" in l) < 2:
    abort(f"expected GPU T4 x2, found {gpu_lines!r}")
print(f"[ok] GPU check passed: {gpu_lines}", flush=True)

# --------------------------------------------------------------------- #
# Step 3: Ollama
# --------------------------------------------------------------------- #
run(["apt-get", "-y", "-qq", "install", "zstd"], check=True)
if subprocess.run(["which", "ollama"], capture_output=True, text=True).returncode != 0:
    run(f"curl -fsSL https://ollama.com/install.sh | OLLAMA_VERSION={OLLAMA_VERSION} sh", check=True)
ollama_log_path = "/kaggle/working/ollama.log"
if subprocess.run(["pgrep", "-f", "ollama serve"], capture_output=True).returncode != 0:
    run(f"nohup ollama serve > {ollama_log_path} 2>&1 &", check=False)
    time.sleep(10)
print("[ok] Ollama running", flush=True)

tags = run(["ollama", "list"], check=True, capture=True)
if MODEL not in (tags.stdout or ""):
    run(["ollama", "pull", MODEL], check=True)

# --------------------------------------------------------------------- #
# Step 4: clone, pin to the patched commit
# --------------------------------------------------------------------- #
if not os.path.isdir(SCE_DIR):
    clone_url = REPO_URL.replace("https://", f"https://{github_token}@")
    run(["git", "clone", clone_url, SCE_DIR], check=True)
run(["git", "fetch", "origin", PATCH_COMMIT], cwd=SCE_DIR, check=True)
run(["git", "checkout", PATCH_COMMIT], cwd=SCE_DIR, check=True)
run(["git", "clean", "-fdx"], cwd=SCE_DIR, check=True)
print(f"[ok] SCE pinned to {PATCH_BRANCH} @ {PATCH_COMMIT}", flush=True)

# --------------------------------------------------------------------- #
# Step 5: pull the REAL single-pass checkpoint (seeds 42-45, durable)
# --------------------------------------------------------------------- #
os.makedirs(SINGLE_PASS_REPORT_DIR, exist_ok=True)
run(["git", "fetch", "origin", SINGLE_PASS_SOURCE_BRANCH], cwd=SCE_DIR, check=True)
run(
    f"git show origin/{SINGLE_PASS_SOURCE_BRANCH}:reports/pilot-4/checkpoint_single_pass.json > {SINGLE_PASS_CKPT}",
    cwd=SCE_DIR, check=True,
)
if os.path.getsize(SINGLE_PASS_CKPT) == 0:
    abort(f"{SINGLE_PASS_CKPT} is empty after fetch - aborting before any LLM spend")
print(f"[ok] existing single-pass checkpoint fetched ({os.path.getsize(SINGLE_PASS_CKPT)} bytes)", flush=True)
os.makedirs(TWO_PASS_REPORT_DIR, exist_ok=True)

# --------------------------------------------------------------------- #
# Step 6: install + env
# --------------------------------------------------------------------- #
run([sys.executable, "-m", "pip", "install", "-e", ".[dev,bench]"], cwd=SCE_DIR, check=True)
os.environ["LLM_BASE_URL"] = "http://localhost:11434/v1"
os.environ["LLM_MODEL"] = MODEL
os.environ["LLM_API_KEY_ENV"] = "OLLAMA_API_KEY"
os.environ["OLLAMA_API_KEY"] = "ollama"
os.environ["PRISM_ENABLE_CAUSAL_PATH"] = "1"
run(["ollama", "run", MODEL, "Say OK."], check=False)


def push_progress(commit_message):
    """Commits and pushes reports/pilot-4/ and reports/pilot-4-patched/
    to PROGRESS_BRANCH right now - the step the earlier cell was
    missing. Called after EACH harness run below, not just at the very
    end, so a crash after this point still leaves real data pushed."""
    run(["git", "add", "-f", "reports/pilot-4/", "reports/pilot-4-patched/"], cwd=SCE_DIR, check=True)
    commit_result = run(["git", "commit", "-m", commit_message], cwd=SCE_DIR, check=False)
    if commit_result.returncode != 0:
        print("[warn] nothing new to commit at this checkpoint", flush=True)
    run(["git", "push", "--force", "origin", f"HEAD:{PROGRESS_BRANCH}"], cwd=SCE_DIR, check=True)
    print(f"[ok] pushed to {PROGRESS_BRANCH}", flush=True)


# --------------------------------------------------------------------- #
# Step 7: single-pass, all 5 engines, seed 46 only fresh (--resume
# skips 42-45, which are already real and on disk from Step 5)
# --------------------------------------------------------------------- #
single_pass_cmd = [
    sys.executable, "-m", "benchmarks.runner",
    "--mode=pilot", "--repo=django",
    "--task-type", "debug", "--scorer", "causal",
    "--budgets", *BUDGETS,
    "--seeds", ALL_FIVE_SEEDS,
    "--pragmatic-oracle",
    "--resume",
    "--checkpoint", SINGLE_PASS_CKPT,
    "--output", SINGLE_PASS_REPORT_DIR,
]
sp_result = run(single_pass_cmd, cwd=SCE_DIR, check=False)
if sp_result.returncode != 0:
    print(f"[warn] single-pass harness exited {sp_result.returncode}", file=sys.stderr, flush=True)
push_progress("pilot-4-patched: single-pass seed 46 complete")

# --------------------------------------------------------------------- #
# Step 8: two-pass, ALL FIVE seeds fresh (nothing durable exists yet -
# the earlier 42-45 run was lost, so this genuinely redoes it, not
# just seed 46). --resume is still set so a crash mid-way through THIS
# run, or a re-run of this same cell, is safe and cheap.
# --------------------------------------------------------------------- #
two_pass_cmd = [
    sys.executable, "-m", "benchmarks.run_two_pass_benchmark",
    "--repo=django",
    "--budgets", *BUDGETS,
    "--seeds", ALL_FIVE_SEEDS,
    "--resume",
    "--checkpoint", TWO_PASS_CKPT,
    "--output", f"{TWO_PASS_REPORT_DIR}/eval_results.json",
]
tp_result = run(two_pass_cmd, cwd=SCE_DIR, check=False)
if tp_result.returncode != 0:
    print(f"[warn] two-pass harness exited {tp_result.returncode}", file=sys.stderr, flush=True)
push_progress("pilot-4-patched: two-pass all 5 seeds complete")

# --------------------------------------------------------------------- #
# Step 9: merge, sanity check, hashes
# --------------------------------------------------------------------- #
run(
    [sys.executable, "scripts/merge_pilot_checkpoints.py",
     "--single-pass", SINGLE_PASS_CKPT, "--two-pass", TWO_PASS_CKPT, "--output", MERGED_CKPT],
    cwd=SCE_DIR, check=True,
)

with open(MERGED_CKPT) as f:
    merged_cells = json.load(f)["cells"]
EXPECTED_ENGINES = {"baseline_bfs_bidirectional", "baseline_bfs_forward", "baseline_rag", "oracle", "prism_v11", "prism_two_pass"}
from collections import Counter
engine_counts = Counter(row["engine"] for row in merged_cells.values())
assert len(merged_cells) == 1800, f"expected 1800 cells (6 engines x 300 each, 5 seeds), got {len(merged_cells)}"
assert set(engine_counts) == EXPECTED_ENGINES, f"engine mismatch: {set(engine_counts)}"
for e, c in engine_counts.items():
    assert c == 300, f"{e}: expected 300 cells (20 tasks x 3 budgets x 5 seeds), got {c}"
missing = [k for k, r in merged_cells.items() if {"task_id","engine","budget","seed","tsr","cpi_retrieval","cpi_answer","fpr_gt","model"} - set(r.keys())]
assert not missing, f"{len(missing)} rows missing fields"
null_tsr = [k for k, r in merged_cells.items() if r.get("tsr") is None]
assert not null_tsr, f"{len(null_tsr)} rows with null tsr"
print(f"[ok] sanity check passed: 1800 cells, {dict(engine_counts)}", flush=True)

sp_hash = sha256_of(SINGLE_PASS_CKPT)
tp_hash = sha256_of(TWO_PASS_CKPT)
merged_hash = sha256_of(MERGED_CKPT)
print(f"[hash] checkpoint_single_pass.json  sha256={sp_hash}", flush=True)
print(f"[hash] checkpoint_two_pass.json     sha256={tp_hash}", flush=True)
print(f"[hash] checkpoint_merged.json       sha256={merged_hash}", flush=True)

with open(TWO_PASS_CKPT) as f:
    raw_tp = json.load(f)["cells"]
parse_failures = [k for k, c in raw_tp.items() if c.get("turn1_parsed_ok") is False]
print(f"[info] Turn-1 JSON parse failures: {len(parse_failures)}/{len(raw_tp)}", flush=True)

# --------------------------------------------------------------------- #
# Step 10: gate, both comparisons
# --------------------------------------------------------------------- #
run([sys.executable, "scripts/apply_gate.py", "--checkpoint", MERGED_CKPT,
     "--output", f"{TWO_PASS_REPORT_DIR}/gate_vs_baseline.md"], cwd=SCE_DIR, check=True)
run([sys.executable, "scripts/apply_gate.py", "--checkpoint", MERGED_CKPT,
     "--baseline-engine", "prism_v11", "--output", f"{TWO_PASS_REPORT_DIR}/gate_vs_singlepass.md"], cwd=SCE_DIR, check=True)

# --------------------------------------------------------------------- #
# Step 11: final push (idempotent if Step 7/8 already pushed everything)
# --------------------------------------------------------------------- #
push_progress("pilot-4-patched: 5-seed run complete, merged + gated")

print("\n=== DONE ===")
print(f"Pushed to branch: {PROGRESS_BRANCH}")
print(f"checkpoint_single_pass.json sha256: {sp_hash}")
print(f"checkpoint_two_pass.json    sha256: {tp_hash}")
print(f"checkpoint_merged.json      sha256: {merged_hash}")
print("Bring back: both gate report files, the three hashes above, and the [info] parse-failure line.")
