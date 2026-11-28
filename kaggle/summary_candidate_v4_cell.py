# ============================================================
# PRISM summarizer candidate v4 - Stage 1 conversion (Kaggle, one cell)
#
# Stage 1 of harness/experiments/purpose_search/CANDIDATE_V4.md: for the same
# ten repositories as the v3 Stage 1, the v4 index made with the production
# command (harness/experiments/purpose_search/stage1.py --steps v2,v4):
#   v2   the v2 indexes from the v3 Stage 1 (reports/summary_candidates_stage1,
#        same engine), installed unchanged - pass 1;
#   v4   `prism summarize --candidate v4` from v2: the 886 summaries the
#        restates_name rule flags (24.8%) are written again from the card plus
#        the function's source (<= 60 lines), effects first - pass 2.
# Then the gates, pooled: after escalation restates_name <= 10%, every v2 flag
# <= 5%, no summary lost, every entry checked.
#
# No search or ranking runs. Output: RESULTS_PATH/STAGE1.md, stage1.json,
# <repo>/{v2,v4}.json and logs, pushed to BRANCH every CHECKPOINT_MIN minutes;
# a re-run of this cell in a new session resumes from the pushed files.
#
# Paste this whole file into ONE Kaggle notebook cell and Run.
# Settings: Accelerator GPU T4 x2, Internet ON, secret GITHUB_TOKEN.
# Expected time on T4 x2: about 40-80 minutes (886 single escalation calls).
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
CODE_COMMIT = "e02fa67f14c2cacf2f5d2a900e24497e64f432cc"
CODE_TREES = {"src": "e0121784821b074e9e5116ffa28035a0c7def967",
              "harness": "a7f7ec41835ddc9852ea2071b92e45095a6304b6"}
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
EXPECTED_GITHUB_USER = "rohith-hash-stack"
SUMMARIZER = "qwen2.5-coder:14b-instruct-q8_0"   # the locked model
OLLAMA_VERSION = "0.34.0"
PORTS = (11434,)          # 14B q8_0 (~15.7 GB) needs both T4s: one server across them
GPU_GROUPS = ("0,1",)
PARALLEL_PER_SERVER = 2
REPOS = ("fastapi_template", "styleguide", "express", "trpc", "flask", "httpie", "fastify", "zod", "click", "hono")
V2_FROM = "reports/summary_candidates_stage1"   # <repo>/v2.json: pass 1, from the v3 Stage 1 (same engine)
STEPS = "v2,v4"
#: Repositories that are not in a held-out dataset.json: url, commit, root.
EXTRA_PINS = {"click": ("https://github.com/pallets/click", "934813e4d421071a1b3db3973c02fe2721359a6e", "src")}
CHECKPOINT_EVERY = 25           # batches between prism summarize checkpoints
CHECKPOINT_MIN = 15             # push RESULTS_PATH this often while Stage 1 runs
WORK = "/kaggle/working"
SCE_DIR = f"{WORK}/SCE"
PUSH_WT = f"{WORK}/SCE_results"
APPS_DIR = f"{WORK}/stage1_apps"
RESULTS_PATH = "reports/summary_candidate_v4_stage1"
PUSH_RESULTS = True
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")

print("=" * 64)
print("PRISM summarizer candidate v4 - Stage 1 conversion")
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
run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_summary_v4.py", "tests/test_summary_lint_v3.py",
     "tests/test_stage1_conversion.py", "tests/test_summarize_checkpoints.py"], cwd=SCE_DIR, env=child_env, check=True)

print("\n[setup 6/6] Repositories at their pinned commits", flush=True)
pins = dict(EXTRA_PINS)
for s in ("set2", "set3", "set4", "set5"):
    for repo, spec in json.load(open(f"{SCE_DIR}/harness/experiments/heldout_retrieval/{s}/dataset.json"))["repos"].items():
        pins[repo] = (spec["url"], spec["commit"], spec["root"])
manifest = {}
for repo in REPOS:
    url, commit, root = pins[repo]
    dst = os.path.join(APPS_DIR, repo)
    if not os.path.isdir(f"{dst}/.git"):
        run(["git", "init", "-q", dst], check=True)
        run(["git", "fetch", "-q", "--depth", "1", url, commit], cwd=dst, check=True, env=GIT_ENV)
    run(["git", "checkout", "-q", "-f", commit], cwd=dst, check=True)
    head = run(["git", "rev-parse", "HEAD"], cwd=dst, capture=True).stdout.strip()
    if head != commit:
        abort(f"{repo} at {head}, expected {commit}")
    manifest[repo] = {"root": os.path.normpath(os.path.join(dst, root)), "cache": None, "live": None}
    os.makedirs(os.path.join(OUT, repo), exist_ok=True)
    v2_src, v2_dst = os.path.join(SCE_DIR, V2_FROM, repo, "v2.json"), os.path.join(OUT, repo, "v2.json")
    if not os.path.exists(v2_dst):
        shutil.copy(v2_src, v2_dst)
    if open(v2_src, "rb").read() != open(v2_dst, "rb").read():
        abort(f"{repo}: {v2_dst} differs from the Stage 1 v2 index")
    print(f"[ok] {repo} @ {head[:7]}, v2 index installed", flush=True)
MANIFEST = os.path.join(WORK, "stage1_manifest.json")
json.dump(manifest, open(MANIFEST, "w"), indent=1)

print("\n[stage 1] conversion: v2 -> v4 (escalation only)", flush=True)
START = time.time()
cmd = [sys.executable, "-m", "harness.experiments.purpose_search.stage1", "run", MANIFEST, OUT,
       "--base-urls", BASE_URLS, "--threads", THREADS, "--checkpoint-every", str(CHECKPOINT_EVERY), "--steps", STEPS]
print(f"$ {' '.join(cmd)}", flush=True)
with open(os.path.join(OUT, "run.log"), "a") as log:
    proc = subprocess.Popen(cmd, cwd=SCE_DIR, env=child_env, stdout=log, stderr=subprocess.STDOUT)
    last_push = time.time()
    while proc.poll() is None:
        time.sleep(30)
        if time.time() - last_push > CHECKPOINT_MIN * 60:
            done = sorted(f"{r}/{f}" for r in os.listdir(OUT) if os.path.isdir(os.path.join(OUT, r))
                          for f in os.listdir(os.path.join(OUT, r)) if f.endswith(".json"))
            push_results(f"summary candidate v4 stage 1: checkpoint ({len(done)} index files) @ {CODE_COMMIT[:7]}")
            last_push = time.time()
print(open(os.path.join(OUT, "run.log")).read()[-3000:], flush=True)
print(f"[stage 1] run exit {proc.returncode} after {(time.time() - START) / 60:.0f} min", flush=True)
push_results(f"summary candidate v4 stage 1: conversion @ {CODE_COMMIT[:7]} ({SUMMARIZER})")
if proc.returncode != 0:
    abort("stage 1 run failed - re-run this cell to resume (finished steps are kept); see run.log")

print("\n[stage 1] gates", flush=True)
g = run([sys.executable, "-m", "harness.experiments.purpose_search.stage1", "gate", MANIFEST, OUT, "--steps", STEPS],
        cwd=SCE_DIR, env=child_env, check=False, capture=True)
print(g.stdout, g.stderr[-2000:] if g.returncode not in (0, 1) else "", flush=True)
push_results(f"summary candidate v4 stage 1: gates @ {CODE_COMMIT[:7]} ({SUMMARIZER})")
print("\n=== DONE ===", flush=True)
