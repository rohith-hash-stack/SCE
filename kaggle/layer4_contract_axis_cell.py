# ============================================================
# PRISM Turn-1 contract-axis (Option 3) evaluation - Kaggle, self-contained cell
# Paste this whole file into ONE Kaggle notebook cell and Run.
# Settings: Accelerator GPU T4 x2, Internet ON, secret GITHUB_TOKEN.
#
# The 8 Layer 4 forward (blast-radius) tasks x 4 arms x SEEDS, same model,
# server, context window and sampling as the tournament:
#   prism_full             Arm A, control: the manifest with the default axes=
#   prism_atomic_caps      Arm B: + |caps=[name@hop,...] and the caps instruction
#   prism_contract         Arm C: Substance dropped from axes=, + |contract=[state
#                          throws ret entry] (prism.semantics.contract_axis) and
#                          "Use both the AST call hierarchy and the contracts
#                          (contract) to identify which upstream/downstream
#                          functions are causally relevant to the query."
#   prism_r0_rule_callers  baseline: Turn 1 = every caller row, no model call
#
# Checks out CODE_COMMIT of BRANCH (no patch) and verifies its tree hashes.
# Results only are pushed to RESULTS_PATH on BRANCH after each corpus, from a
# separate clean worktree. Re-running resumes: pushed cells are restored and
# skipped. Output: RESULTS_PATH/SUMMARY.md - Turn-1 gold recall, gold picked
# per seed (out of 49) with stdev, requested rows per task, the MUTATES_* vs
# READ_ONLY selection delta per arm and corpus, Jaccard vs control, and the
# control's own across-seed Jaccard (noise floor).
# ============================================================

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

# ---------- Config ----------
BRANCH = "feature/option3-contract-axis"
CODE_COMMIT = "535aad8c8ad84df6cc012febbb6ec34f346da1f1"
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
EXPECTED_GITHUB_USER = "rohith-hash-stack"
MODEL = "qwen2.5-coder:14b-instruct-q8_0"   # the tournament's model: no fallback
OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_VERSION = "0.34.0"
NUM_CTX = 24576
SEEDS = "42,43,44"
ARMS = "prism_full,prism_atomic_caps,prism_contract,prism_r0_rule_callers"
CORPORA = ("django", "express", "fastapi", "trpc")
SCE_DIR = "/kaggle/working/SCE"
PUSH_WT = "/kaggle/working/SCE_results"
RESULTS_PATH = "reports/layer4/diagnostics/contract_axis"
PUSH_RESULTS = True
CODE_TREES = {"src": "36c9fb5fb938051c6ddea3e8ddad272459f8bf27",
              "harness": "b612c623e65cac2f4cf6da3b24b10155f5fec1ff",
              "benchmarks": "293edfba1be06149afde2250d3d91a26386598cf"}
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")

print("=" * 64)
print("PRISM Turn-1 contract-axis evaluation (arms A/B/C + R0)")
print("=" * 64, flush=True)


def mask(text):
    tok = os.environ.get("GITHUB_TOKEN")
    return text.replace(tok, "***") if tok else text


def run(cmd, cwd=None, check=True, env=None, capture=False, timeout=None):
    print(f"$ {mask(cmd if isinstance(cmd, str) else ' '.join(cmd))}", flush=True)
    return subprocess.run(cmd, cwd=cwd, check=check, env=env, shell=isinstance(cmd, str),
                          capture_output=capture, text=True, timeout=timeout)


def abort(msg):
    print(f"[ABORT] {msg}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def push_results(message, max_attempts=4):
    """Copy OUT into a clean worktree of BRANCH and push it there."""
    if not PUSH_RESULTS:
        return False
    for attempt in range(max_attempts):
        run(["git", "fetch", "origin", BRANCH], cwd=SCE_DIR, check=False, env=GIT_ENV)
        run(["git", "checkout", "-q", "--detach", f"origin/{BRANCH}"], cwd=PUSH_WT, check=True)
        dst = os.path.join(PUSH_WT, RESULTS_PATH)
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(OUT, dst, ignore=shutil.ignore_patterns("cell.schema.ablation.json"))
        run(["git", "add", "-f", RESULTS_PATH], cwd=PUSH_WT)
        if run(["git", "diff", "--cached", "--quiet"], cwd=PUSH_WT, check=False).returncode == 0:
            print("[push] nothing new", flush=True)
            return True
        run(["git", "-c", "user.email=kaggle@local", "-c", "user.name=Kaggle", "commit", "-q", "-m", message],
            cwd=PUSH_WT, check=True)
        r = run(["git", "push", "origin", f"HEAD:{BRANCH}"], cwd=PUSH_WT, check=False, capture=True, env=GIT_ENV)
        if r.returncode == 0:
            print("[push] ok", flush=True)
            return True
        print(f"[push] exit {r.returncode}: {mask((r.stderr or '') + (r.stdout or ''))[-800:]}", flush=True)
        time.sleep(2 ** (attempt + 1))
    print(f"[push] FAILED - results are still in {OUT}", flush=True)
    return False


print("\n[setup 1/7] GitHub token", flush=True)
github_token = os.environ.get("GITHUB_TOKEN")
if not github_token:
    try:
        from kaggle_secrets import UserSecretsClient
        github_token = UserSecretsClient().get_secret("GITHUB_TOKEN")
    except Exception as exc:
        abort(f"GITHUB_TOKEN unavailable: {exc}")
if not github_token:
    abort("GITHUB_TOKEN not set")
os.environ["GITHUB_TOKEN"] = github_token
who = json.loads(run(["curl", "-s", "-H", f"Authorization: token {github_token}", "https://api.github.com/user"],
                     capture=True).stdout or "{}")
if who.get("login") != EXPECTED_GITHUB_USER:
    abort(f"token is {who.get('login')!r}")
print(f"[ok] token: {who['login']}", flush=True)

print("\n[setup 2/7] GPU check + apt", flush=True)
gpu = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], check=False, capture=True)
gpu_lines = [ln for ln in (gpu.stdout or "").strip().splitlines() if ln.strip()]
if sum("T4" in ln for ln in gpu_lines) < 2:
    abort(f"expected GPU T4 x2: {gpu_lines}")
print(f"[ok] GPU: {gpu_lines}", flush=True)
run("apt-get update -qq && apt-get install -y -qq zstd curl git", check=False)

print("\n[setup 3/7] Ollama + model", flush=True)
if subprocess.run(["which", "ollama"], capture_output=True).returncode != 0:
    run(f"curl -L https://github.com/ollama/ollama/releases/download/v{OLLAMA_VERSION}/ollama-linux-amd64.tar.zst "
        "-o /tmp/ollama.tar.zst", check=True)
    run("tar -I zstd -xf /tmp/ollama.tar.zst -C /usr/local", check=True)
    run("chmod 755 /usr/local/bin/ollama", check=True)
log_path = "/kaggle/working/ollama.log"
ollama_env = dict(os.environ, OLLAMA_HOST="127.0.0.1:11434", OLLAMA_KEEP_ALIVE="1h",
                  OLLAMA_CONTEXT_LENGTH=str(NUM_CTX), OLLAMA_NUM_PARALLEL="1",
                  OLLAMA_FLASH_ATTENTION="1", OLLAMA_KV_CACHE_TYPE="q8_0", OLLAMA_MAX_LOADED_MODELS="1")
if subprocess.run(["pgrep", "-f", "ollama serve"], capture_output=True).returncode != 0:
    subprocess.Popen(["ollama", "serve"], env=ollama_env, stdout=open(log_path, "a"), stderr=subprocess.STDOUT)
    time.sleep(10)
for _ in range(30):
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/version", timeout=2) as r:
            if r.status == 200:
                break
    except Exception:
        time.sleep(2)
else:
    abort(f"ollama not responding (see {log_path})")
if MODEL not in (run(["ollama", "list"], capture=True, env=ollama_env).stdout or ""):
    if run(["ollama", "pull", MODEL], check=False, env=ollama_env).returncode != 0:
        abort(f"could not pull {MODEL} - no fallback model")
print(f"[ok] model: {MODEL}", flush=True)

print("\n[setup 4/7] Clone and check out the code commit", flush=True)
clone_url = REPO_URL.replace("https://", f"https://x-access-token:{github_token}@")
if not os.path.isdir(f"{SCE_DIR}/.git"):
    run(["git", "clone", clone_url, SCE_DIR], check=True, env=GIT_ENV)
run(["git", "remote", "set-url", "origin", clone_url], cwd=SCE_DIR)
run(["git", "fetch", "origin", BRANCH], cwd=SCE_DIR, check=True, env=GIT_ENV)
run(["git", "checkout", "-q", "-f", "--detach", CODE_COMMIT], cwd=SCE_DIR, check=True)
run(["git", "clean", "-fdx", "-e", ".benchmarks", "-e", RESULTS_PATH], cwd=SCE_DIR, check=True)
got = {p: run(["git", "rev-parse", f"HEAD:{p}"], cwd=SCE_DIR, capture=True).stdout.strip() for p in CODE_TREES}
if got != CODE_TREES:
    abort(f"checked-out code differs from the reviewed commit: {got}")
print(f"[ok] code {CODE_COMMIT[:7]} {got}", flush=True)
if PUSH_RESULTS and not os.path.isdir(PUSH_WT):
    run(["git", "worktree", "add", "-f", "--detach", PUSH_WT, f"origin/{BRANCH}"], cwd=SCE_DIR, check=True)
OUT = os.path.join(SCE_DIR, RESULTS_PATH)
pushed = os.path.join(SCE_DIR, "_pushed_cells.jsonl")
if run(["git", "cat-file", "-e", f"origin/{BRANCH}:{RESULTS_PATH}/cells.jsonl"], cwd=SCE_DIR, check=False).returncode == 0:
    os.makedirs(OUT, exist_ok=True)
    data = run(["git", "show", f"origin/{BRANCH}:{RESULTS_PATH}/cells.jsonl"], cwd=SCE_DIR, capture=True).stdout
    local = os.path.join(OUT, "cells.jsonl")
    have = open(local).read() if os.path.exists(local) else ""
    with open(local, "w") as fh:
        fh.write(data + ("" if data.endswith("\n") or not data else "\n") + have)
    print(f"[resume] restored {sum(1 for ln in data.splitlines() if ln.strip())} pushed cells", flush=True)

print("\n[setup 5/7] Python deps", flush=True)
run([sys.executable, "-m", "pip", "install", "-q", "-e", ".[dev,bench]", "jsonschema"], cwd=SCE_DIR, check=True)
for path in (os.path.join(SCE_DIR, "src"), SCE_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)
child_env = dict(os.environ, PYTHONPATH=os.pathsep.join([SCE_DIR, os.path.join(SCE_DIR, "src")]),
                 CUDA_VISIBLE_DEVICES="", OPENAI_API_KEY="ollama", OLLAMA_API_KEY="ollama",
                 HARNESS_WORKDIR="/kaggle/working", PYTHONDONTWRITEBYTECODE="1")
child_env.pop("GITHUB_TOKEN", None)
child_env.pop("OPENAI_BASE_URL", None)
run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_atomic_caps_ablation.py", "tests/test_contract_axis.py"],
    cwd=SCE_DIR, env=child_env, check=True)

print("\n[setup 6/7] Corpora checkouts", flush=True)
for c in CORPORA:
    root = run([sys.executable, "-c", f"from benchmarks.corpora.resolver import resolve; print(resolve({c!r}))"],
               cwd=SCE_DIR, env=child_env, capture=True, check=True).stdout.strip().splitlines()[-1]
    print(f"[ok] {c}: {root}", flush=True)

print("\n[setup 7/7] Preflight: the model answers inside the context window", flush=True)
req = urllib.request.Request(f"{OLLAMA_URL}/v1/chat/completions", data=json.dumps({
    "model": MODEL, "messages": [{"role": "user", "content": "Reply with the word ok."}], "max_tokens": 5,
    "options": {"num_ctx": NUM_CTX}}).encode(), headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=900) as r:
    print(f"[ok] {json.loads(r.read())['choices'][0]['message']['content']!r}", flush=True)

tasks = json.loads(run([sys.executable, "-c",
                        "import json; from harness.experiments.layer4 import runner as R; "
                        "print(json.dumps({k: v['corpus'] for k, v in R.load_tasks(R.TASKS_DIR).items() "
                        "if v['family'] == 'forward_blast'}))"],
                       cwd=SCE_DIR, env=child_env, capture=True, check=True).stdout.strip().splitlines()[-1])
COMMON = ["--out", OUT, "--seeds", SEEDS, "--arms", ARMS, "--model", MODEL, "--base-url", f"{OLLAMA_URL}/v1",
          "--num-ctx", str(NUM_CTX)]
START = time.time()
for corpus in CORPORA:
    ids = sorted(k for k, v in tasks.items() if v == corpus)
    print(f"\n[run] {corpus}: {', '.join(ids)}", flush=True)
    r = run([sys.executable, "-m", "harness.experiments.layer4.atomic_caps_ablation", *COMMON, "--tasks", ",".join(ids)],
            cwd=SCE_DIR, env=child_env, check=False)
    print(f"[run] {corpus} exit {r.returncode} after {(time.time() - START) / 60:.0f} min", flush=True)
    push_results(f"Turn-1 contract-axis evaluation: {corpus} cells @ {CODE_COMMIT[:7]}")

print("\n[summary] all 8 tasks", flush=True)
run([sys.executable, "-m", "harness.experiments.layer4.atomic_caps_ablation", *COMMON], cwd=SCE_DIR, env=child_env,
    check=False)
push_results(f"Turn-1 contract-axis evaluation: summary @ {CODE_COMMIT[:7]}")
print("\n=== DONE ===")
print(open(os.path.join(OUT, "SUMMARY.md")).read() if os.path.exists(os.path.join(OUT, "SUMMARY.md")) else "(no summary)")

# Headline: Arm C's selection delta net of Arm A's (A never sees contracts, so
# its delta is how much MUTATES_* rows are favoured anyway).
try:
    S = json.load(open(os.path.join(OUT, "summary.json")))
    sel = S.get("selection", {})
    for rows in ("all_rows", "non_gold_rows"):
        for corpus in ("all", "django", "express", "fastapi", "trpc"):
            a = sel.get("prism_full", {}).get(rows, {}).get(corpus, {}).get("delta")
            c = sel.get("prism_contract", {}).get(rows, {}).get(corpus, {}).get("delta")
            if a is not None and c is not None:
                print(f"[selection] {rows:13} {corpus:8} A {a:+.3f}  C {c:+.3f}  C-A {c - a:+.3f}")
    for arm, p in S.get("per_seed", {}).items():
        print(f"[per-seed] {arm:24} gold {p['gold_picked_mean']:.2f} +/- {p['gold_picked_sd'] or 0:.2f} / 49 | "
              f"requested/task {p['requested_per_task_mean']:.2f} +/- {p['requested_per_task_sd'] or 0:.2f}")
except Exception as exc:
    print(f"[headline] unavailable: {exc}")
