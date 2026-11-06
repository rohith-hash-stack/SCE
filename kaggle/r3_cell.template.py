# ============================================================
# PRISM round R3 - the qwen notice check (amendment R3-5): 5 PRISM-version runs, before the pilot.
#
# Order (BUILD_PLAN section 6):
#   1. token, then the code at the pinned commit, then the Qwen2.5 tokenizer (sha256-checked);
#   2. the JS/TS builder smoke test on one repository: install only, no test run. It stops the cell on a
#      structural failure, before any GPU or model work;
#   3. GPU, Ollama and the locked model;
#   4. the notice check: werkzeug at the pinned revision, seeds 42, 1001, 2002, 3003, 4004, PRISM version.
# Results go to RESULTS_PATH, pushed to BRANCH every CHECKPOINT_MIN minutes. Run the cell again to resume (a
# finished run is never repeated). "=== ALL DONE ===" at the end means finished.
#
# Paste this whole file into ONE Kaggle notebook cell and Run.
# Settings: Accelerator GPU T4 x2, Internet ON, secret GITHUB_TOKEN.
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
CODE_COMMIT = "@@CODE_COMMIT@@"
CODE_TREES = {"src": "@@SRC_TREE@@",
              "harness": "@@HARNESS_TREE@@"}
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
EXPECTED_GITHUB_USER = "rohith-hash-stack"
SUMMARIZER = "qwen2.5-coder:14b-instruct-q8_0"   # the locked model
OLLAMA_VERSION = "0.34.0"
PORTS = (11434,)          # 14B q8_0 (~15.7 GB) needs both T4s: one server across them
GPU_GROUPS = ("0,1",)
PARALLEL_PER_SERVER = 1
TOKENIZER_URL = "https://registry.npmjs.org/@lenml/tokenizer-qwen2_5/-/tokenizer-qwen2_5-3.7.2.tgz"
TOKENIZER_SHA = "c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539"
SMOKE_REPO = ("https://github.com/unjs/h3.git", "@@SMOKE_REV@@")
NOTICE_REPO = ("https://github.com/pallets/werkzeug.git", "@@NOTICE_REV@@")
TURN_CAP = 30
SESSION_HOURS = 11.0
CHECKPOINT_MIN = 10
WORK = "/kaggle/working"
SCE_DIR = f"{WORK}/SCE"
PUSH_WT = f"{WORK}/SCE_results"
RESULTS_PATH = "reports/r3/notice_check"
CACHE = f"{WORK}/r3cache"
PUSH_RESULTS = True
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")
print("=" * 64)
print("PRISM round R3 - qwen notice check (5 runs)")
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

print("\n[setup] Install prism, the tokenizer library and uv", flush=True)
run([sys.executable, "-m", "pip", "install", "-q", "-e", ".", "tokenizers", "uv", "packaging"], cwd=SCE_DIR, check=True)
child_env = dict(os.environ, PYTHONPATH=os.pathsep.join([SCE_DIR, os.path.join(SCE_DIR, "src")]),
                 PYTHONDONTWRITEBYTECODE="1")
child_env.pop("GITHUB_TOKEN", None)
os.makedirs(CACHE, exist_ok=True)

print("\n[setup] The Qwen2.5 tokenizer (npm registry tarball, sha256-checked)", flush=True)
TOK_DIR = f"{WORK}/qwen-tokenizer"
TOKENIZER = f"{TOK_DIR}/package/models/tokenizer.json"
if not os.path.exists(TOKENIZER):   # the npm package's tarball, straight from the registry (no npm needed)
    import tarfile
    os.makedirs(TOK_DIR, exist_ok=True)
    tgz = f"{TOK_DIR}/pkg.tgz"
    urllib.request.urlretrieve(TOKENIZER_URL, tgz)
    with tarfile.open(tgz) as tar:
        tar.extract("package/models/tokenizer.json", TOK_DIR)
if hashlib.sha256(open(TOKENIZER, "rb").read()).hexdigest() != TOKENIZER_SHA:
    abort("the tokenizer is not the reviewed one")
child_env["R3_TOKENIZER"] = TOKENIZER
print(f"[ok] tokenizer {TOKENIZER_SHA[:12]}", flush=True)


def fetch(url, rev, name):
    path = f"{WORK}/repos/{name}"
    if not os.path.isdir(f"{path}/.git"):
        run(["git", "clone", "-q", url, path], check=True, env=GIT_ENV)
    if run(["git", "-C", path, "cat-file", "-e", rev + "^{commit}"], check=False).returncode:
        run(["git", "-C", path, "fetch", "-q", "origin"], check=True, env=GIT_ENV)
    return path


print("\n[smoke] JS/TS builder on one repository: install only, no test run", flush=True)
h3 = fetch(SMOKE_REPO[0], SMOKE_REPO[1], "h3")
smoke = run([sys.executable, "-m", "harness.experiments.r3.jsbuild", "smoke", h3, SMOKE_REPO[1], "--cache",
             f"{CACHE}/js", "--scratch", f"{WORK}/scratch"], cwd=SCE_DIR, env=child_env, check=False, capture=True)
print(smoke.stdout[-3000:], smoke.stderr[-2000:], flush=True)
os.makedirs(OUT, exist_ok=True)
open(os.path.join(OUT, "smoke.json"), "w").write(smoke.stdout.strip().splitlines()[-1] if smoke.stdout.strip() else "{}")
if smoke.returncode == 2:
    push_results(f"r3: JS/TS smoke test FAILED (structural) @ {CODE_COMMIT[:7]}")
    abort("the JS/TS builder smoke test failed structurally on Kaggle - see smoke.json; nothing else was run")
if smoke.returncode != 0:
    push_results(f"r3: JS/TS smoke test failed (network/package) @ {CODE_COMMIT[:7]}")
    abort("the JS/TS builder smoke test failed on a network or package error - run the cell again")
print("[ok] JS/TS builder installs on Kaggle", flush=True)

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
                push_results(f"r3: {label} checkpoint @ {CODE_COMMIT[:7]}")
                last_push = time.time()
    print(open(os.path.join(OUT, f"{label}.log")).read()[-3000:], flush=True)
    print(f"[{label}] exit {proc.returncode} after {(time.time() - start) / 60:.0f} min", flush=True)
    return proc.returncode



print("\n[notice check] 5 PRISM-version runs on werkzeug", flush=True)
werk = fetch(NOTICE_REPO[0], NOTICE_REPO[1], "werkzeug")
START = time.time()
rc = module(["run", "--clone", werk, "--rev", NOTICE_REPO[1], "--out", OUT, "--cache", f"{CACHE}/py",
             "--base-url", f"http://127.0.0.1:{PORTS[0]}", "--turn-cap", str(TURN_CAP)], "notice_check",
            push_while=True, name="harness.experiments.r3.notice_check")
module(["summarize", OUT], "notice_summary", name="harness.experiments.r3.notice_check")
push_results(f"r3: notice check @ {CODE_COMMIT[:7]} ({SUMMARIZER})")
if rc != 0:
    abort("the notice check stopped early - run the cell again to resume")
print(open(os.path.join(OUT, "summary.json")).read(), flush=True)
print("\n=== ALL DONE ===", flush=True)
