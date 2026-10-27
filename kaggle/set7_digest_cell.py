# ============================================================
# PRISM held-out set 7 - digest-grounded summaries (Kaggle, one cell)
#
# harness/experiments/heldout_retrieval/set7/README.md (pre-registered and frozen before any run).
# Writes three summary indexes for every function of the four set-7 repositories (werkzeug, aiohttp,
# kysely, nest) with the locked model:
#   V2     the shipped summarizer (prism.summaries.pipeline.build_index), unchanged
#   SRC    card + the function's numbered source (first 60 lines)
#   DIGEST card + the function's PRISM digest in place of the source (nothing else changed)
# then ranks the 120 frozen set-7 questions once through the shipped NLQIndex (BASE, V2, SRC, DIGEST)
# and applies the pre-registered rule: DIGEST qualifies iff it beats V2, SRC does not beat it, and
# its recall@5 is within 0.02 of SRC's.
#
# Output: RESULTS_PATH/<repo>/{v2,src,digest}.json, generation.json, decision/{results.json,DECISION.md}
# - pushed to BRANCH every CHECKPOINT_MIN minutes; re-running this cell in a new session resumes from
# the pushed summaries (nothing finished is written twice).
#
# Paste this whole file into ONE Kaggle notebook cell and Run.
# Settings: Accelerator GPU T4 x2, Internet ON, secret GITHUB_TOKEN.
# Expected time on T4 x2: about 7-9 hours (~6,700 functions: SRC and DIGEST one call each, V2 in
# batches of 12, plus repairs). Kaggle stops a session at 12 hours; if it ends first, run the cell
# again: it resumes.
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
CODE_COMMIT = "41c55bb952de10534e6294084e083d27e406eaf1"
CODE_TREES = {"src": "e8274a68571cf566d3c0cfbd22bfb8b59e963ae0",
              "harness": "e679939bcaeb15dc22a6d6e4770de7f10644e926"}
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
EXPECTED_GITHUB_USER = "rohith-hash-stack"
SUMMARIZER = "qwen2.5-coder:14b-instruct-q8_0"   # the locked model
OLLAMA_VERSION = "0.34.0"
PORTS = (11434,)          # 14B q8_0 (~15.7 GB) needs both T4s: one server across them
GPU_GROUPS = ("0,1",)
PARALLEL_PER_SERVER = 2
SET7 = "harness/experiments/heldout_retrieval/set7/dataset.json"
SET7_SHA = "d6f3b12fbe0bf8bf8d9e7cd96d6603f9aa45f450c9e10ad751ea95458c51ec2f"
CHECKPOINT_MIN = 15             # push RESULTS_PATH this often while generation runs
WORK = "/kaggle/working"
SCE_DIR = f"{WORK}/SCE"
PUSH_WT = f"{WORK}/SCE_results"
APPS_DIR = f"{WORK}/set7_apps"
RESULTS_PATH = "reports/set7_digest"
PUSH_RESULTS = True
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")
print("=" * 64)
print("PRISM held-out set 7 - DIGEST vs SRC vs V2 vs BASE")
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
               OLLAMA_MAX_LOADED_MODELS="1", OLLAMA_CONTEXT_LENGTH="8192")
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
run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_set7.py", "tests/test_digest.py",
     "tests/test_summary_grounding.py", "tests/test_summary_lint_v3.py"], cwd=SCE_DIR, env=child_env, check=True)



def checkout(repo, url, commit):
    dst = os.path.join(APPS_DIR, repo)
    if not os.path.isdir(f"{dst}/.git"):
        run(["git", "init", "-q", dst], check=True)
        run(["git", "fetch", "-q", "--depth", "1", url, commit], cwd=dst, check=True, env=GIT_ENV)
    run(["git", "checkout", "-q", "-f", commit], cwd=dst, check=True)
    head = run(["git", "rev-parse", "HEAD"], cwd=dst, capture=True).stdout.strip()
    if head != commit:
        abort(f"{repo} at {head}, expected {commit}")
    return dst


def module(args, label, push_while=False, name="harness.experiments.summary_grounding.run"):
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
                push_results(f"set 7 digest: {label} checkpoint @ {CODE_COMMIT[:7]}")
                last_push = time.time()
    print(open(os.path.join(OUT, f"{label}.log")).read()[-3000:], flush=True)
    print(f"[{label}] exit {proc.returncode} after {(time.time() - start) / 60:.0f} min", flush=True)
    return proc.returncode


print("\n[setup 6/6] Set 7 repositories at their pinned commits", flush=True)
set7 = json.load(open(os.path.join(SCE_DIR, SET7)))
sha = hashlib.sha256(json.dumps(set7["questions"], sort_keys=True).encode()).hexdigest()
if set7.get("status") != "frozen" or sha != SET7_SHA or set7.get("questions_sha256") != SET7_SHA:
    abort(f"set 7 is not the frozen set ({set7.get('status')}, {sha[:12]})")
for repo, spec in set7["repos"].items():
    checkout(repo, spec["url"], spec["commit"])
    print(f"[ok] {repo} @ {spec['commit'][:7]}", flush=True)

print("\n[A] generate V2, SRC and DIGEST summaries", flush=True)
rc = module(["generate", APPS_DIR, OUT, "--set", "7", "--base-urls", BASE_URLS, "--threads", THREADS], "generate",
            push_while=True)
push_results(f"set 7 digest: summaries @ {CODE_COMMIT[:7]} ({SUMMARIZER})")
if rc != 0:
    abort("generation failed - re-run this cell to resume (finished summaries are kept); see generate.log")

print("\n[B] score set 7 once (pre-registered rule)", flush=True)
DECISION = os.path.join(OUT, "decision")
if os.path.exists(os.path.join(DECISION, "results.json")):
    print("[skip] set 7 was already scored; its result is never recomputed", flush=True)
else:
    rc = module([APPS_DIR, OUT, DECISION], "score", name="harness.experiments.heldout_retrieval.set7.score")
    push_results(f"set 7 digest: decision @ {CODE_COMMIT[:7]} ({SUMMARIZER})")
    if rc != 0:
        abort("scoring failed; see score.log")
print(open(os.path.join(DECISION, "DECISION.md")).read(), flush=True)
print("\n=== DONE ===", flush=True)
