# ============================================================
# PRISM change propagation from git history - run 2: forced changes, three versions (Kaggle, one cell)
#
# harness/experiments/change_history/README.md, "Run 2" (pre-registered; tasks frozen and planned before
# any model ran). 12 real commits (flask, click, httpx, express, jinja, requests), 4 easy / 4 medium /
# 4 hard, where the function's change FORCED its callers to change (a parameter removed or renamed, a new
# required parameter, other errors raised, returning nothing added or removed). Three versions:
#   FILE       the function's file as an editor shows it
#   PRISM      FILE + PRISM's change context (as in run 1)
#   PRISM_MAP  PRISM + the map (repository, main folders, files near the change, one sentence each)
#              + the second ring (callers of the callers, similar functions; one line each)
# Step A writes the map and second-ring sentences with the same model (before any version is answered);
# step B makes the 36 answers; step C scores them against what the developer actually changed.
#
# Output: RESULTS_PATH/{overview.json, answers.json, results.json, REPORT.md, *.log}, pushed to BRANCH.
# Re-running the cell resumes (sentences and answers already made are kept).
#
# Paste this whole file into ONE Kaggle notebook cell and Run.
# Settings: Accelerator GPU T4 x2, Internet ON, secret GITHUB_TOKEN. Expected time: about 60-90 minutes.
# ============================================================

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

# ---------- Config ----------
BRANCH = "feature/option3-contract-axis"
CODE_COMMIT = "d6cdec823f41feb718331f62d6ed9f00956810df"
CODE_TREES = {"src": "fced20e02c03ef3066882e4d32e243bc10527345",
              "harness": "688392b891c74bdd5330c1da9185f630deb3926a"}
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
EXPECTED_GITHUB_USER = "rohith-hash-stack"
SUMMARIZER = "qwen2.5-coder:14b-instruct-q8_0"   # the locked model
OLLAMA_VERSION = "0.34.0"
PORTS = (11434,)          # 14B q8_0 (~15.7 GB) needs both T4s: one server across them
GPU_GROUPS = ("0,1",)
PARALLEL_PER_SERVER = 1   # long prompts (up to ~11k tokens): one request at a time
TASKS = "harness/experiments/change_history/forced/tasks.json"
TASKS_SHA = "36aa60fd57e8e036d93c065093705ba5ef7dbb61b4b684276b839c20b1878a5c"
CHECKPOINT_MIN = 10
WORK = "/kaggle/working"
SCE_DIR = f"{WORK}/SCE"
PUSH_WT = f"{WORK}/SCE_results"
RESULTS_PATH = "reports/change_history_forced"
PUSH_RESULTS = True
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")
print("=" * 64)
print("PRISM change propagation, run 2 - FILE vs PRISM vs PRISM_MAP")
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
        shutil.copytree(OUT, dst, ignore=shutil.ignore_patterns("*.sqlite", "*.tmp"))
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


print("\n[setup 1/6] GitHub token", flush=True)
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

print("\n[setup 2/6] GPU check + apt", flush=True)
gpu = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], check=False, capture=True)
gpu_lines = [ln for ln in (gpu.stdout or "").strip().splitlines() if ln.strip()]
if sum("T4" in ln for ln in gpu_lines) < 2:
    abort(f"expected GPU T4 x2: {gpu_lines}")
print(f"[ok] GPU: {gpu_lines}", flush=True)
run("apt-get update -qq && apt-get install -y -qq zstd curl git", check=False)

print("\n[setup 3/6] Ollama + the summarizer model", flush=True)
if subprocess.run(["which", "ollama"], capture_output=True).returncode != 0:
    run(f"curl -L https://github.com/ollama/ollama/releases/download/v{OLLAMA_VERSION}/ollama-linux-amd64.tar.zst "
        "-o /tmp/ollama.tar.zst", check=True)
    run("tar -I zstd -xf /tmp/ollama.tar.zst -C /usr/local", check=True)
    run("chmod 755 /usr/local/bin/ollama", check=True)
for port, gpus in zip(PORTS, GPU_GROUPS):
    env = dict(os.environ, OLLAMA_HOST=f"127.0.0.1:{port}", CUDA_VISIBLE_DEVICES=gpus, OLLAMA_KEEP_ALIVE="4h",
               OLLAMA_NUM_PARALLEL=str(PARALLEL_PER_SERVER), OLLAMA_FLASH_ATTENTION="1", OLLAMA_KV_CACHE_TYPE="q8_0",
               OLLAMA_MAX_LOADED_MODELS="1", OLLAMA_CONTEXT_LENGTH="20480")
    try:  # already serving (a re-run of this cell)?
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=2).close()
    except Exception:
        subprocess.Popen(["ollama", "serve"], env=env, stdout=open(f"{WORK}/ollama_{port}.log", "a"),
                         stderr=subprocess.STDOUT)
time.sleep(10)
for port in PORTS:
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=2) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(2)
    else:
        abort(f"ollama on port {port} not responding (see {WORK}/ollama_{port}.log)")
first = dict(os.environ, OLLAMA_HOST=f"127.0.0.1:{PORTS[0]}")
for attempt in range(8):  # the registry can return transient 503s / connection timeouts
    if SUMMARIZER in (run(["ollama", "list"], capture=True, env=first).stdout or ""):
        break
    if run(["ollama", "pull", SUMMARIZER], check=False, env=first).returncode == 0:
        break
    wait = min(15 * 2 ** attempt, 240)
    print(f"[retry] pull failed (attempt {attempt + 1}/8), waiting {wait}s", flush=True)
    time.sleep(wait)
else:
    abort(f"could not pull {SUMMARIZER} after 8 attempts")
BASE_URLS = ",".join(f"http://127.0.0.1:{p}" for p in PORTS)
THREADS = str(len(PORTS) * PARALLEL_PER_SERVER)
for port in PORTS:  # warm the server (loads the model onto the GPUs)
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/chat", data=json.dumps({
        "model": SUMMARIZER, "messages": [{"role": "user", "content": "Reply with {\"ok\": true}"}], "format": "json",
        "stream": False, "options": {"num_predict": 10}}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        print(f"[ok] port {port}: {json.loads(r.read())['message']['content']!r}", flush=True)

print("\n[setup 4/6] Clone and check out the code commit", flush=True)
clone_url = REPO_URL.replace("https://", f"https://x-access-token:{github_token}@")
if not os.path.isdir(f"{SCE_DIR}/.git"):
    run(["git", "clone", clone_url, SCE_DIR], check=True, env=GIT_ENV)
run(["git", "remote", "set-url", "origin", clone_url], cwd=SCE_DIR)
run(["git", "fetch", "origin", BRANCH], cwd=SCE_DIR, check=True, env=GIT_ENV)
run(["git", "checkout", "-q", "-f", "--detach", CODE_COMMIT], cwd=SCE_DIR, check=True)
run(["git", "clean", "-fdx", "-e", RESULTS_PATH], cwd=SCE_DIR, check=True)
got = {p: run(["git", "rev-parse", f"HEAD:{p}"], cwd=SCE_DIR, capture=True).stdout.strip() for p in CODE_TREES}
if got != CODE_TREES:
    abort(f"checked-out code differs from the reviewed commit: {got}")
print(f"[ok] code {CODE_COMMIT[:7]} {got}", flush=True)
if PUSH_RESULTS and not os.path.isdir(PUSH_WT):
    run(["git", "worktree", "add", "-f", "--detach", PUSH_WT, f"origin/{BRANCH}"], cwd=SCE_DIR, check=True)
OUT = os.path.join(SCE_DIR, RESULTS_PATH)
os.makedirs(OUT, exist_ok=True)
if run(["git", "cat-file", "-e", f"origin/{BRANCH}:{RESULTS_PATH}"], cwd=SCE_DIR, check=False).returncode == 0:
    run(f"git archive origin/{BRANCH} {RESULTS_PATH} | tar -x -C {SCE_DIR}", cwd=SCE_DIR, check=True)
    print(f"[resume] restored {sorted(os.listdir(OUT))}", flush=True)

print("\n[setup 5/6] Install prism + tests", flush=True)
run([sys.executable, "-m", "pip", "install", "-q", "-e", ".[dev,bench]", "jsonschema"], cwd=SCE_DIR, check=True)  # tests/conftest.py imports benchmarks.runner (rank_bm25)
child_env = dict(os.environ, PYTHONPATH=os.pathsep.join([SCE_DIR, os.path.join(SCE_DIR, "src")]),
                 CUDA_VISIBLE_DEVICES="", PYTHONDONTWRITEBYTECODE="1")
child_env.pop("GITHUB_TOKEN", None)
run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_change_history.py",
     "tests/test_impact.py", "tests/test_digest.py"], cwd=SCE_DIR, env=child_env, check=True)



def module(args, label, push_while=False, name="harness.experiments.change_history.run"):
    """`python -m NAME ARGS`, logged to OUT/<label>.log."""
    cmd = [sys.executable, "-m", name, *args]
    print(f"$ {' '.join(cmd)}", flush=True)
    start = time.time()
    with open(os.path.join(OUT, f"{label}.log"), "a") as log:
        proc = subprocess.Popen(cmd, cwd=SCE_DIR, env=child_env, stdout=log, stderr=subprocess.STDOUT)
        last_push = time.time()
        while proc.poll() is None:
            time.sleep(30)
            if push_while and time.time() - last_push > CHECKPOINT_MIN * 60:
                push_results(f"change history run 2: {label} checkpoint @ {CODE_COMMIT[:7]}")
                last_push = time.time()
    print(open(os.path.join(OUT, f"{label}.log")).read()[-3000:], flush=True)
    print(f"[{label}] exit {proc.returncode} after {(time.time() - start) / 60:.0f} min", flush=True)
    return proc.returncode


print("\n[setup 6/6] Frozen tasks", flush=True)
tasks = json.load(open(os.path.join(SCE_DIR, TASKS)))
sha = hashlib.sha256(json.dumps(tasks["tasks"], sort_keys=True).encode()).hexdigest()
if tasks.get("status") != "frozen" or sha != TASKS_SHA or tasks.get("tasks_sha256") != TASKS_SHA:
    abort(f"tasks are not the frozen set ({tasks.get('status')}, {sha[:12]})")
if not all(t.get("overview_plan") for t in tasks["tasks"]):
    abort("tasks carry no overview plans (plan.py)")
print(f"[ok] {len(tasks['tasks'])} frozen tasks with overview plans", flush=True)

print("\n[A] map and second-ring sentences", flush=True)
rc = module(["overview", TASKS, OUT, "--base-url", BASE_URLS.split(",")[0], "--model", SUMMARIZER], "overview",
            push_while=True)
push_results(f"change history run 2: overview @ {CODE_COMMIT[:7]} ({SUMMARIZER})")
if rc != 0:
    abort("overview failed - re-run this cell to resume; see overview.log")

print("\n[B] model answers, FILE, PRISM and PRISM_MAP", flush=True)
rc = module(["generate", TASKS, OUT, "--base-url", BASE_URLS.split(",")[0], "--model", SUMMARIZER,
             "--threads", THREADS], "generate", push_while=True)
push_results(f"change history run 2: answers @ {CODE_COMMIT[:7]} ({SUMMARIZER})")
if rc != 0:
    abort("generation failed - re-run this cell to resume; see generate.log")

print("\n[C] score against the commits", flush=True)
rc = module(["score", TASKS, OUT], "score")
push_results(f"change history run 2: scores @ {CODE_COMMIT[:7]} ({SUMMARIZER})")
if rc != 0:
    abort("scoring failed; see score.log")
print(open(os.path.join(OUT, "REPORT.md")).read(), flush=True)
print("\n=== DONE ===", flush=True)
