# ============================================================
# PRISM round R1: is it PRISM's context, or just a longer prompt? (Kaggle, one cell, several sessions)
#
# harness/experiments/r1/README.md (pre-registered). The suite's frozen tasks, plus token-matched baselines
# (harness/experiments/r1/pads, frozen and listed with their sha256 in pads/manifest.json), at seeds 42, 1001, 2002.
#   phase 1  padded versions, seed 42                       588 calls
#   phase 2  determinism replay (5 tasks per type)            30 calls
#   phase 3  every version, seed 1001                      1,380 calls
#   phase 4  every version, seed 2002                      1,380 calls
# Answers go to RESULTS_PATH/<repo>/<type>/answers.json, pushed to BRANCH every CHECKPOINT_MIN minutes.
#
# It will not finish in one Kaggle session (about 3,400 model calls). Run the cell again in a new session:
# it restores what was pushed and continues where it stopped. "=== ALL DONE ===" at the end means finished.
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
CODE_COMMIT = "2d4839560e86a2fba1561d3ee59484027afe36d5"
CODE_TREES = {"src": "95e45dc83b9be4861bc6ceb298bff0e07b0f31c9",
              "harness": "2a55ed6ff19a73ee45ef0c16d0ef9740f0c642c2"}
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
EXPECTED_GITHUB_USER = "rohith-hash-stack"
SUMMARIZER = "qwen2.5-coder:14b-instruct-q8_0"   # the locked model
OLLAMA_VERSION = "0.34.0"
PORTS = (11434,)          # 14B q8_0 (~15.7 GB) needs both T4s: one server across them
GPU_GROUPS = ("0,1",)
PARALLEL_PER_SERVER = 1   # long prompts (up to ~11k tokens): one request at a time
MANIFEST = "harness/experiments/r1/pads/manifest.json"
MANIFEST_SHA = "97735e6ecd0a2978874d9858e45a9d8b1be3debbefa798aa7a7a430f3d92d155"
SESSION_HOURS = 11.0     # stop starting new calls after this (Kaggle stops a session at 12 h)
CHECKPOINT_MIN = 10
WORK = "/kaggle/working"
SCE_DIR = f"{WORK}/SCE"
PUSH_WT = f"{WORK}/SCE_results"
RESULTS_PATH = "reports/r1"
PUSH_RESULTS = True
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")
print("=" * 64)
print("PRISM round R1 - token-matched baselines, seeds 42/1001/2002")
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
     "tests/test_suite_tasks.py", "tests/test_r1.py"], cwd=SCE_DIR, env=child_env, check=True)



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
                push_results(f"r1: {label} checkpoint @ {CODE_COMMIT[:7]}")
                last_push = time.time()
    print(open(os.path.join(OUT, f"{label}.log")).read()[-3000:], flush=True)
    print(f"[{label}] exit {proc.returncode} after {(time.time() - start) / 60:.0f} min", flush=True)
    return proc.returncode


print("\n[setup 6/6] Frozen tasks and pads", flush=True)
START = time.time()
man_text = open(os.path.join(SCE_DIR, MANIFEST)).read()
if hashlib.sha256(man_text.encode()).hexdigest() != MANIFEST_SHA:
    abort("the pads manifest is not the reviewed one")
manifest = json.loads(man_text)
for entry in manifest["files"]:
    if hashlib.sha256(open(os.path.join(SCE_DIR, entry["path"]), "rb").read()).hexdigest() != entry["sha256"]:
        abort(f"{entry['path']} differs from the manifest")
if run([sys.executable, "-m", "harness.experiments.suite.manifest", "check"], cwd=SCE_DIR, env=child_env,
       check=False).returncode:
    abort("a suite task file differs from the suite manifest")
plan = json.loads(run([sys.executable, "-m", "harness.experiments.r1.run", "calls"], cwd=SCE_DIR, env=child_env,
                      capture=True).stdout)
print(f"[ok] {len(manifest['files'])} pad files; calls per phase {plan['per_phase']}", flush=True)


def done_calls(kind, repo, phase):
    path = os.path.join(OUT, repo, kind, "answers.json")
    try:
        answers = json.load(open(path))
    except (OSError, ValueError):
        return 0
    suffix = {1: "@42", 2: "@42replay", 3: "@1001", 4: "@2002"}[phase]
    return sum(1 for k, v in answers.items() if k.endswith(suffix) and (v.get("raw") or v.get("error"))
               and (phase != 1 or k.split("/")[-1].split("@")[0].endswith("PAD")))


print("\n[A] model answers: phase by phase, then type and repository", flush=True)
for phase in (1, 2, 3, 4):
    for kind in ("forward", "blast", "newfn"):
        for repo, calls in sorted(plan["plan"][str(phase)][kind].items()):
            if not calls or done_calls(kind, repo, phase) >= calls:
                continue
            if (time.time() - START) / 3600 > SESSION_HOURS:
                push_results(f"r1: session limit @ {CODE_COMMIT[:7]}")
                print("\n=== SESSION LIMIT: run this cell again in a new session to continue ===", flush=True)
                raise SystemExit(0)
            out = os.path.join(OUT, repo, kind)
            os.makedirs(out, exist_ok=True)
            print(f"\n--- phase {phase} {kind} / {repo}: {done_calls(kind, repo, phase)}/{calls} calls done", flush=True)
            rc = module(["generate", str(phase), kind, repo, out, "--base-url", BASE_URLS.split(",")[0],
                         "--model", SUMMARIZER, "--threads", THREADS], f"p{phase}-{kind}-{repo}", push_while=True,
                        name="harness.experiments.r1.run")
            push_results(f"r1: phase {phase} {kind} {repo} @ {CODE_COMMIT[:7]} ({SUMMARIZER})")
            if rc != 0:
                abort(f"generation failed for phase {phase} {kind}/{repo} - run the cell again to resume")
    print(f"\n[phase {phase} done]", flush=True)

push_results(f"r1: all answers @ {CODE_COMMIT[:7]} ({SUMMARIZER})")
print("\n=== ALL DONE ===", flush=True)
