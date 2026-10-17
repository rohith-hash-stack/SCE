# ============================================================
# PRISM breadcrumb verification - Kaggle, self-contained cell
# Paste this whole file into ONE Kaggle notebook cell and Run.
# Settings: Accelerator GPU T4 x2, Internet ON, secret GITHUB_TOKEN.
# Run it as an interactive session (not "Save & Run All").
#
# Verifies the breadcrumb patch (f55de74) on the Stage 2 debug tasks whose
# breadcrumb payload it changed, against the frozen Stage 2 cells:
#   prism_oracle - 7 tasks x 10 seeds = 70 cells: the 6 tasks whose gold
#                  slice now gets a breadcrumb + django_t02_016 (now none),
#                  baseline: frozen normal_oracle (= pragmatic_oracle) cells
#   prism_ask    - 26 tasks x 10 seeds = 260 cells: every task whose
#                  prism_ask breadcrumbs were added, removed or rewritten,
#                  baseline: frozen prism_ask cells
# The task lists come from the committed static audit
# (reports/two_stage/breadcrumb_audit/breadcrumb_audit.json); the cell
# prints them and checks the counts. ~330 cells, about 2.5-3 h.
#
# Same model, quantization, context window, temperature (runner default,
# 0.4), seeds and Ollama setup as the 6,300-cell sweep. Results go to
# reports/two_stage/breadcrumb_verification/ only - the frozen
# reports/two_stage/ run files are never written.
#
# Pre-registered criteria (computed at the end, VERIFICATION.md):
#   1. no task drops by >= 3/10 against its baseline
#   2. mean per-task delta TSR >= -0.01
#   3. django_t02_016 recovers to 10/10 under prism_oracle
#
# Resumable: every finished cell is appended to cells.jsonl at once; the
# directory is pushed to BRANCH every PUSH_EVERY_S and after each
# arm/corpus. Re-running the cell restores the pushed cells and skips them.
# Cells that fail (e.g. a request timeout) are retried up to RETRY_PASSES
# times. No hybrid-RAG arm runs here, so no embedding cache is needed.
# ============================================================

import json
import os
import subprocess
import sys
import time
import urllib.request

# ---------- Config ----------
BRANCH = "eval/held-out-benchmark"
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
EXPECTED_GITHUB_USER = "rohith-hash-stack"
MODEL = "qwen2.5-coder:14b-instruct-q8_0"   # must match the sweep: no fallback
OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_VERSION = "0.34.0"
NUM_CTX = 24576
PARALLEL = 2
SEEDS = "42,43,44,45,46,47,48,49,50,51"
ARMS = ("prism_oracle", "prism_ask")
EXPECTED_TASKS = {"prism_oracle": 7, "prism_ask": 26}
RETRY_PASSES = 3

SCE_DIR = "/kaggle/working/SCE"
PUSH_PATH = "reports/two_stage/breadcrumb_verification"
AUDIT = "reports/two_stage/breadcrumb_audit/breadcrumb_audit.json"
FROZEN = "reports/two_stage/stage2"
PUSH_EVERY_S = 900
MAX_RUNTIME_HOURS = 11.3
TRANSFORMERS_PINS = [
    ("transformers==4.45.2", "sentence-transformers>=3.0,<4.0"),
    ("transformers==4.46.3", "sentence-transformers>=3.0,<4.0"),
    ("transformers==4.47.1", "sentence-transformers>=3.0,<4.0"),
    ("transformers==4.44.2", "sentence-transformers<3.0"),
]
CODE_PATHS = ("src", "harness", "benchmarks")
MAX_PUSH_FILE_MB = 90
START = time.time()
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")

print("=" * 64)
print("PRISM breadcrumb verification")
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


def code_identity(rev="HEAD"):
    return {p: run(["git", "rev-parse", f"{rev}:{p}"], cwd=SCE_DIR, capture=True).stdout.strip() for p in CODE_PATHS}


def out_of_time():
    return time.time() - START > MAX_RUNTIME_HOURS * 3600


def push(paths, message, max_attempts=4):
    if not os.environ.get("GITHUB_TOKEN"):
        print("[push] skipped (no GITHUB_TOKEN)", flush=True)
        return False
    run(["git", "config", "user.email", "kaggle@local"], cwd=SCE_DIR)
    run(["git", "config", "user.name", "Kaggle"], cwd=SCE_DIR)
    run(["git", "add", "-f", *paths], cwd=SCE_DIR)
    staged = run(["git", "diff", "--cached", "--name-only"], cwd=SCE_DIR, capture=True).stdout.split()
    for f in staged:
        p = os.path.join(SCE_DIR, f)
        if os.path.isfile(p) and os.path.getsize(p) > MAX_PUSH_FILE_MB * 2**20:
            print(f"[push] [warn] not pushing {f} ({os.path.getsize(p) / 2**20:.0f} MB)", flush=True)
            run(["git", "reset", "-q", "--", f], cwd=SCE_DIR, check=False)
    if run(["git", "diff", "--cached", "--quiet"], cwd=SCE_DIR, check=False).returncode == 0:
        return True
    run(["git", "commit", "-q", "-m", message], cwd=SCE_DIR, check=False)
    for attempt in range(max_attempts):
        r = run(["git", "push", "origin", f"HEAD:{BRANCH}"], cwd=SCE_DIR, check=False, capture=True, env=GIT_ENV)
        if r.returncode == 0:
            print("[push] ok", flush=True)
            return True
        said = mask((r.stderr or "") + (r.stdout or "")).strip()
        print(f"[push] git push exit {r.returncode}:\n{said[-1500:]}", flush=True)
        if "non-fast-forward" in said or "fetch first" in said:
            run(["git", "pull", "--rebase", "origin", BRANCH], cwd=SCE_DIR, check=False, env=GIT_ENV)
        time.sleep(2 ** (attempt + 1))
    print("[push] FAILED after retries - results are still on local disk", flush=True)
    return False


# ============================================================
# Setup
# ============================================================
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

print("\n[setup 2/7] GPU check", flush=True)
gpu = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], check=False, capture=True)
gpu_lines = [ln for ln in (gpu.stdout or "").strip().splitlines() if ln.strip()]
if sum("T4" in ln for ln in gpu_lines) < 2:
    abort(f"expected GPU T4 x2: {gpu_lines}")
print(f"[ok] GPU: {gpu_lines}", flush=True)

print("\n[setup 3/7] apt: zstd, curl, git", flush=True)
run("apt-get update -qq && apt-get install -y -qq zstd curl git ripgrep", check=False)

print("\n[setup 4/7] Ollama (one server across both T4s) + model", flush=True)
if subprocess.run(["which", "ollama"], capture_output=True).returncode != 0:
    run(f"curl -L https://github.com/ollama/ollama/releases/download/v{OLLAMA_VERSION}/ollama-linux-amd64.tar.zst "
        "-o /tmp/ollama.tar.zst", check=True)
    run("tar -I zstd -xf /tmp/ollama.tar.zst -C /usr/local", check=True)
    run("chmod 755 /usr/local/bin/ollama", check=True)
log_path = "/kaggle/working/ollama.log"
ollama_env = dict(os.environ, OLLAMA_HOST="127.0.0.1:11434", OLLAMA_KEEP_ALIVE="1h",
                  OLLAMA_CONTEXT_LENGTH=str(NUM_CTX), OLLAMA_NUM_PARALLEL=str(PARALLEL),
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
        abort(f"could not pull {MODEL} - the verification must use the sweep's exact model, no fallback")
print(f"[ok] model: {MODEL}", flush=True)

print("\n[setup 5/7] Clone repo", flush=True)
clone_url = REPO_URL.replace("https://", f"https://x-access-token:{github_token}@")
if not os.path.isdir(f"{SCE_DIR}/.git"):
    run(["git", "clone", clone_url, SCE_DIR], check=True, env=GIT_ENV)
run(["git", "remote", "set-url", "origin", clone_url], cwd=SCE_DIR)
run(["git", "fetch", "origin", BRANCH], cwd=SCE_DIR, check=True, env=GIT_ENV)
probe = run(["git", "push", "--dry-run", "origin", f"origin/{BRANCH}:refs/heads/{BRANCH}"],
            cwd=SCE_DIR, check=False, capture=True, env=GIT_ENV)
if probe.returncode != 0:
    abort(f"this GITHUB_TOKEN cannot push to {BRANCH}:\n{mask(probe.stderr or probe.stdout).strip()[-1500:]}")
run(["git", "checkout", "-B", BRANCH, f"origin/{BRANCH}"], cwd=SCE_DIR, check=True)
run(["git", "clean", "-fdx", "-e", ".benchmarks"], cwd=SCE_DIR, check=True)
HEAD = run(["git", "rev-parse", "HEAD"], cwd=SCE_DIR, capture=True).stdout.strip()
CODE = code_identity()
for f in ("src/prism/graph/render_breadcrumbs.py", "harness/experiments/final_validation/breadcrumb_verification.py",
          AUDIT, f"{FROZEN}/django/cells.jsonl"):
    if not os.path.exists(os.path.join(SCE_DIR, f)):
        abort(f"{BRANCH} has no {f} (wrong branch/commit)")
print(f"[ok] HEAD {HEAD}  code {CODE}", flush=True)

print("\n[setup 6/7] Python deps + transformers pin", flush=True)
run([sys.executable, "-m", "pip", "install", "-q", "-e", ".[dev,bench,baselines]", "pyarrow"], cwd=SCE_DIR, check=True)
for tf_pin, st_pin in TRANSFORMERS_PINS:
    if run([sys.executable, "-m", "pip", "install", "-q", tf_pin, st_pin], check=False).returncode == 0:
        print(f"[ok] {tf_pin}", flush=True)
        break
else:
    abort("all transformers pins failed")
run([sys.executable, "-m", "pip", "install", "-q", "onnxruntime", "onnx", "onnxscript", "einops"], check=True)

print("\n[setup 7/7] Env + warm-up + context check", flush=True)
child_env = dict(os.environ, PYTHONPATH=SCE_DIR, CUDA_VISIBLE_DEVICES="", LLM_BASE_URL=f"{OLLAMA_URL}/v1",
                 LLM_MODEL=MODEL, OPENAI_API_KEY="ollama", HARNESS_WORKDIR="/kaggle/working")
child_env.pop("GITHUB_TOKEN", None)
child_env.pop("OPENAI_BASE_URL", None)
child_env.pop("PRISM_BREADCRUMB_STRICT", None)  # production behaviour: drop, never raise
req = urllib.request.Request(f"{OLLAMA_URL}/v1/chat/completions", headers={"Content-Type": "application/json"},
                             data=json.dumps({"model": MODEL, "max_tokens": 5, "temperature": 0,
                                              "options": {"num_ctx": NUM_CTX},
                                              "messages": [{"role": "user", "content": "apple " * 12000 + "\nSay OK."}]}).encode())
seen = json.loads(urllib.request.urlopen(req, timeout=900).read())["usage"]["prompt_tokens"]
if seen < 10800:
    abort(f"context window not applied: the server saw {seen} of ~12000 prompt tokens")
if "cuda" not in open(log_path).read().lower():
    abort(f"Ollama is not using the GPU (see {log_path})")
print(f"[ok] {seen} prompt tokens accepted on GPU", flush=True)

# ---------- results directory (pushed results from a previous session are restored by the checkout) ----------
OUT = os.path.join(SCE_DIR, PUSH_PATH)
os.makedirs(OUT, exist_ok=True)
manifest_path = os.path.join(OUT, "run_manifest.json")
if os.path.exists(manifest_path):
    m = json.load(open(manifest_path))
    if m.get("code") != CODE or m.get("model") != MODEL:
        abort(f"{PUSH_PATH} holds cells from other code or another model ({m.get('code')}, {m.get('model')}); "
              "move it aside before re-running")
json.dump({"head": HEAD, "code": CODE, "model": MODEL, "num_ctx": NUM_CTX, "seeds": SEEDS, "parallel": PARALLEL},
          open(manifest_path, "w"), indent=1)

selection = json.loads(run([sys.executable, "-m", "harness.experiments.final_validation.breadcrumb_verification",
                            "select", "--audit", AUDIT], cwd=SCE_DIR, env=child_env, capture=True).stdout)
for arm in ARMS:
    n = sum(len(t) for t in selection[arm].values())
    print(f"[selection] {arm}: {n} tasks {json.dumps(selection[arm])}", flush=True)
    if n != EXPECTED_TASKS[arm]:
        abort(f"{arm}: {n} tasks selected, expected {EXPECTED_TASKS[arm]}")


def status_counts(path):
    last = {}
    if os.path.exists(path):
        for line in open(path):
            if line.strip():
                r = json.loads(line)
                last[(r["task_id"], r["seed"], r["engine_id"])] = r["status"]
    return sum(s == "ok" for s in last.values()), sum(s != "ok" for s in last.values())


def run_streaming(cmd, tag):
    print("$", " ".join(cmd), flush=True)
    proc = subprocess.Popen(cmd, cwd=SCE_DIR, env=child_env)
    last = time.time()
    while proc.poll() is None:
        time.sleep(30)
        if out_of_time():
            proc.terminate()
            proc.wait(timeout=120)
            return None
        if time.time() - last >= PUSH_EVERY_S:
            push([PUSH_PATH], f"breadcrumb verification: {tag} (periodic) @ {HEAD[:7]}")
            last = time.time()
    return proc.returncode


# ============================================================
# Run: per arm, per corpus; failed cells retried up to RETRY_PASSES times
# ============================================================
stopped = False
expected_cells = {arm: sum(len(t) for t in selection[arm].values()) * len(SEEDS.split(",")) for arm in ARMS}
for arm in ARMS:
    for repo, tasks in selection[arm].items():
        if stopped:
            break
        out_dir = os.path.join(OUT, "cells", arm, repo)
        if repo == "express":
            root = run([sys.executable, "-c", "from benchmarks.corpora.resolver import resolve; print(resolve('express'))"],
                       cwd=SCE_DIR, env=child_env, capture=True).stdout.strip().splitlines()[-1]
            if not os.path.isdir(os.path.join(root, "node_modules", "etag")):
                run("npm install --omit=dev --no-audit --no-fund --ignore-scripts", cwd=root)
        for attempt in range(1, RETRY_PASSES + 1):
            ok, failed = status_counts(os.path.join(out_dir, "cells.jsonl"))
            if ok >= len(tasks) * len(SEEDS.split(",")):
                break
            if attempt > 1:
                print(f"[retry] {arm}/{repo}: {failed} failed cell(s), pass {attempt}/{RETRY_PASSES}", flush=True)
            code = run_streaming([sys.executable, "-m", "benchmarks.final_sweep.runner", "--repo", repo,
                                  "--arms", arm, "--tasks", *tasks, "--seeds", SEEDS, "--workers", str(PARALLEL),
                                  "--out", out_dir, "--model", MODEL, "--base-url", f"{OLLAMA_URL}/v1",
                                  "--num-ctx", str(NUM_CTX)], f"{arm} {repo}")
            if code is None:
                stopped = True
                break
        ok, failed = status_counts(os.path.join(out_dir, "cells.jsonl"))
        print(f"=== {arm}/{repo}: {ok} ok, {failed} failed of {len(tasks) * len(SEEDS.split(','))} ===", flush=True)
        push([PUSH_PATH], f"breadcrumb verification: {arm} {repo} ({ok} ok, {failed} failed) @ {HEAD[:7]}")

# ============================================================
# Analysis against the frozen Stage 2 cells + pre-registered criteria
# ============================================================
res = run([sys.executable, "-m", "harness.experiments.final_validation.breadcrumb_verification", "analyse",
           "--audit", AUDIT, "--new", os.path.join(OUT, "cells"), "--old", FROZEN, "--model", MODEL,
           "--code", HEAD[:12], "--out", OUT], cwd=SCE_DIR, env=child_env, check=False)
push([PUSH_PATH], f"breadcrumb verification: {'stopped early' if stopped else 'results'} @ {HEAD[:7]}")
report = open(os.path.join(OUT, "VERIFICATION.md")).read() if os.path.exists(os.path.join(OUT, "VERIFICATION.md")) else ""

print("\n" + ("=== STOPPED EARLY - re-run this cell to resume ===" if stopped else "=== DONE ==="))
print(f"Head {HEAD}, elapsed {(time.time() - START) / 3600:.1f} h, criteria "
      f"{'PASS' if res.returncode == 0 else 'FAIL or incomplete'}")
print(f"Results on {BRANCH}: {PUSH_PATH}/VERIFICATION.md, verification.json, cells/<arm>/<corpus>/cells.jsonl")
print("If the final push failed, commit from this session with:")
print(f"  cd {SCE_DIR} && git add -f {PUSH_PATH} && git commit -m 'breadcrumb verification results' "
      f"&& git push origin HEAD:{BRANCH}")
try:
    from IPython.display import Markdown, display
    display(Markdown(report))
except Exception:
    print(report)
