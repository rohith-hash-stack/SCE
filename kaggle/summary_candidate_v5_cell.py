# ============================================================
# PRISM summarizer candidate v5 - Stage 1 conversion + set 6 summaries (Kaggle, one cell)
#
# harness/experiments/purpose_search/CANDIDATE_V5.md. Everything is made with the
# production command (`prism summarize`, through stage1.py); no search or ranking runs.
#   A. Stage 1, ten repositories: the v2 and v4 indexes of the v4 Stage 1
#      (reports/summary_candidate_v4_stage1, same engine) are installed unchanged,
#      then `prism summarize --candidate v5` from v4 (v4's escalations reused;
#      functions >= 20 lines with summaries < 10 words escalated too). Gates:
#      restates_name <= 10%, every v2 flag <= 5%, nothing lost, every entry checked.
#   B. Set 6 (frozen; the cell checks its hash), four repositories: v2 summarized
#      fresh with the locked pipeline, then v4 and v5 by escalation only. These
#      indexes are scored later, once, by set6/score.py - not here.
#
# Output: RESULTS_PATH/stage1/ (STAGE1.md, stage1.json, <repo>/{v2,v4,v5}.json) and
# RESULTS_PATH/set6/<repo>/{v2,v4,v5}.json, pushed to BRANCH every CHECKPOINT_MIN
# minutes; a re-run of this cell in a new session resumes from the pushed files.
#
# Paste this whole file into ONE Kaggle notebook cell and Run.
# Settings: Accelerator GPU T4 x2, Internet ON, secret GITHUB_TOKEN.
# Expected time on T4 x2: about 3-5 hours (A: ~350 escalations; B: ~2,200 fresh
# summaries in batches, repairs, then ~700-900 escalations).
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
CODE_COMMIT = "2d167069686e587e8c63a733d7d033ebfbab658b"
CODE_TREES = {"src": "883fe500892788e5d785d9f5ac45782ffc898d1c",
              "harness": "07b5321e54ef47d5918353a8b915e3ad44a9cc8e"}
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
EXPECTED_GITHUB_USER = "rohith-hash-stack"
SUMMARIZER = "qwen2.5-coder:14b-instruct-q8_0"   # the locked model
OLLAMA_VERSION = "0.34.0"
PORTS = (11434,)          # 14B q8_0 (~15.7 GB) needs both T4s: one server across them
GPU_GROUPS = ("0,1",)
PARALLEL_PER_SERVER = 2
REPOS = ("fastapi_template", "styleguide", "express", "trpc", "flask", "httpie", "fastify", "zod", "click", "hono")
V4_FROM = "reports/summary_candidate_v4_stage1"   # <repo>/{v2,v4}.json, installed unchanged
STEPS = "v2,v4,v5"
SET6 = "harness/experiments/heldout_retrieval/set6/dataset.json"
SET6_SHA = "66905f750a3bbbced82739c03c3aceca442dfd8883309cb2457276b411786fd3"
#: Repositories that are not in a held-out dataset.json: url, commit, root.
EXTRA_PINS = {"click": ("https://github.com/pallets/click", "934813e4d421071a1b3db3973c02fe2721359a6e", "src")}
CHECKPOINT_EVERY = 25           # batches between prism summarize checkpoints
CHECKPOINT_MIN = 15             # push RESULTS_PATH this often while Stage 1 runs
WORK = "/kaggle/working"
SCE_DIR = f"{WORK}/SCE"
PUSH_WT = f"{WORK}/SCE_results"
APPS_DIR = f"{WORK}/stage1_apps"
RESULTS_PATH = "reports/summary_candidate_v5"
PUSH_RESULTS = True
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")

print("=" * 64)
print("PRISM summarizer candidate v5 - Stage 1 + set 6 summaries")
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
run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_summary_v5.py", "tests/test_summary_v4.py",
     "tests/test_set6_scoring.py", "tests/test_summary_lint_v3.py",
     "tests/test_stage1_conversion.py", "tests/test_summarize_checkpoints.py"], cwd=SCE_DIR, env=child_env, check=True)

import hashlib


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


def stage(name, manifest, out, gate):
    """stage1.py run (+ gate) for one manifest; push while it runs."""
    os.makedirs(out, exist_ok=True)
    path = os.path.join(WORK, f"{name}_manifest.json")
    json.dump(manifest, open(path, "w"), indent=1)
    start = time.time()
    cmd = [sys.executable, "-m", "harness.experiments.purpose_search.stage1", "run", path, out,
           "--base-urls", BASE_URLS, "--threads", THREADS, "--checkpoint-every", str(CHECKPOINT_EVERY), "--steps", STEPS]
    print(f"$ {' '.join(cmd)}", flush=True)
    with open(os.path.join(out, "run.log"), "a") as log:
        proc = subprocess.Popen(cmd, cwd=SCE_DIR, env=child_env, stdout=log, stderr=subprocess.STDOUT)
        last_push = time.time()
        while proc.poll() is None:
            time.sleep(30)
            if time.time() - last_push > CHECKPOINT_MIN * 60:
                push_results(f"summary candidate v5 {name}: checkpoint @ {CODE_COMMIT[:7]}")
                last_push = time.time()
    print(open(os.path.join(out, "run.log")).read()[-3000:], flush=True)
    print(f"[{name}] run exit {proc.returncode} after {(time.time() - start) / 60:.0f} min", flush=True)
    push_results(f"summary candidate v5 {name}: indexes @ {CODE_COMMIT[:7]} ({SUMMARIZER})")
    if proc.returncode != 0:
        abort(f"{name} run failed - re-run this cell to resume (finished steps are kept); see {out}/run.log")
    if gate:
        g = run([sys.executable, "-m", "harness.experiments.purpose_search.stage1", "gate", path, out, "--steps", "v2,v5"],
                cwd=SCE_DIR, env=child_env, check=False, capture=True)
        print(g.stdout, g.stderr[-2000:] if g.returncode not in (0, 1) else "", flush=True)
        push_results(f"summary candidate v5 {name}: gates @ {CODE_COMMIT[:7]} ({SUMMARIZER})")


print("\n[setup 6/6] Repositories at their pinned commits", flush=True)
pins = dict(EXTRA_PINS)
for s in ("set2", "set3", "set4", "set5"):
    for repo, spec in json.load(open(f"{SCE_DIR}/harness/experiments/heldout_retrieval/{s}/dataset.json"))["repos"].items():
        pins[repo] = (spec["url"], spec["commit"], spec["root"])
OUT_A, OUT_B = os.path.join(OUT, "stage1"), os.path.join(OUT, "set6")
manifest_a = {}
for repo in REPOS:
    url, commit, root = pins[repo]
    dst = checkout(repo, url, commit)
    manifest_a[repo] = {"root": os.path.normpath(os.path.join(dst, root)), "cache": None, "live": None}
    os.makedirs(os.path.join(OUT_A, repo), exist_ok=True)
    for step in ("v2", "v4"):
        src, tgt = os.path.join(SCE_DIR, V4_FROM, repo, f"{step}.json"), os.path.join(OUT_A, repo, f"{step}.json")
        if not os.path.exists(tgt):
            shutil.copy(src, tgt)
        if open(src, "rb").read() != open(tgt, "rb").read():
            abort(f"{repo}: {tgt} differs from the v4 Stage 1 {step} index")
    print(f"[ok] {repo} @ {commit[:7]}, v2 + v4 indexes installed", flush=True)
set6 = json.load(open(os.path.join(SCE_DIR, SET6)))
sha = hashlib.sha256(json.dumps(set6["questions"], sort_keys=True).encode()).hexdigest()
if set6.get("status") != "frozen" or sha != SET6_SHA or set6.get("questions_sha256") != SET6_SHA:
    abort(f"set 6 is not the frozen set ({set6.get('status')}, {sha[:12]})")
manifest_b = {}
for repo, spec in set6["repos"].items():
    dst = checkout(f"set6_{repo}", spec["url"], spec["commit"])
    manifest_b[repo] = {"root": os.path.normpath(os.path.join(dst, spec["root"])), "cache": None, "live": None}
    print(f"[ok] set 6 {repo} @ {spec['commit'][:7]}", flush=True)

print("\n[A] stage 1: v4 -> v5 on ten repositories", flush=True)
stage("stage1", manifest_a, OUT_A, gate=True)
print("\n[B] set 6 summaries: v2 (fresh), v4, v5", flush=True)
stage("set6", manifest_b, OUT_B, gate=False)
print("\n=== DONE ===", flush=True)
