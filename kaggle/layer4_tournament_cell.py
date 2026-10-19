# ============================================================
# PRISM Layer 4 tournament - Kaggle, self-contained cell
# Paste this whole file into ONE Kaggle notebook cell and Run.
# Settings: Accelerator GPU T4 x2, Internet ON, secret GITHUB_TOKEN.
# Run it as an interactive session (not "Save & Run All").
#
# The 16 Layer 4 tasks (benchmarks/layer4/tasks/: 2 forward blast radius +
# 2 backward contract adaptation per corpus - fastapi, django, express,
# trpc) x 7 Stage 2 arms (arm0_no_retrieval, arm1_hybrid_rag,
# repomap_pagerank, prism_full, prism_ask, normal_oracle, prism_oracle)
# x SEEDS, through harness/experiments/layer4/runner.py:
#   step 1  contamination - arm0_no_retrieval alone on all 16 tasks first:
#           the zero-context floor; contamination.json flags the tasks it
#           solves (backward pass rate / forward mean F1 >= 0.5)
#   step 2  run - the full matrix (arm0's cells are already done and skipped)
#   step 3  summarize - summary.json and TOURNAMENT.md: forward P/R/F1 and
#           Django table / side-effect matches, backward pass rate, syntax
#           validity and token cost, over all tasks and the unflagged ones
#
# Same answering model, quantization, server, context window, sampling
# (runner defaults: temperature 0.4, max_tokens 2048, budget 8000, ceiling
# 8800) and seeds as Stage 2. Backward replies are spliced into the corpus
# checkouts and graded by the corpora's own tests, so the cell also builds
# each corpus's test environment (the pins of benchmarks/layer4/ENVIRONMENT.md).
#
# Resumable: every finished cell is appended to its corpus's cells.jsonl at
# once; everything under PUSH_PATH is pushed to BRANCH every PUSH_EVERY_S
# and after each step. The cell stops itself before Kaggle's 12 h limit;
# re-run it in a new session and it restores the pushed cells and goes on
# (the runner skips finished cells and retries failed ones). A cell
# (task x arm x seed) stopped half-way leaves no record and is the one that
# runs again; every finished seed is skipped. Re-running in the same session
# after the interrupt button works too: the interrupted lanes are stopped
# cleanly and pushed, leftover runners are stopped at setup 0, and cells
# not yet pushed are merged back after the branch reset. Pushed results
# are only accepted if they were produced by identical code (tree hashes of
# src/, harness/, benchmarks/). The hybrid-RAG embedding cache is shared
# with the two-stage run (eval/two-stage-rag-cache).
# ============================================================

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request

# ---------- Config ----------
BRANCH = "eval/held-out-benchmark"
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
EXPECTED_GITHUB_USER = "rohith-hash-stack"
MODEL = "qwen2.5-coder:14b-instruct-q8_0"   # must match Stage 2: no fallback
OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_VERSION = "0.34.0"
NUM_CTX = 24576
PARALLEL = 2                                # concurrent requests = corpus lanes
SEEDS = "42,43,44,45,46,47,48,49,50,51"
# two lanes run side by side (one Ollama request each); a lane runs its
# corpora one after the other - splices edit a corpus checkout in place
LANES = [["django", "trpc"], ["fastapi", "express"]]
RETRY_PASSES = 3                            # reruns of a corpus while cells still end in error
NODE_VERSION = "20.18.1"
PNPM_VERSION = "8.5.1"
TRPC_PACKAGES = ["server", "client", "react-query", "next"]   # dependency order

SCE_DIR = "/kaggle/working/SCE"
NODE_DIR = "/kaggle/working/node20"
VENV_DIR = "/kaggle/working/venvs"
PUSH_PATH = "reports/layer4/tournament"
CORPORA_DIR = f"{PUSH_PATH}/corpora"        # per-corpus cells.jsonl + logs, merged into PUSH_PATH/cells.jsonl
PUSH_EVERY_S = 600                          # a hard kill loses at most this much finished work
MAX_RUNTIME_HOURS = 11.3
TRANSFORMERS_PINS = [
    ("transformers==4.45.2", "sentence-transformers>=3.0,<4.0"),
    ("transformers==4.46.3", "sentence-transformers>=3.0,<4.0"),
    ("transformers==4.47.1", "sentence-transformers>=3.0,<4.0"),
    ("transformers==4.44.2", "sentence-transformers<3.0"),
]
# benchmarks/layer4/ENVIRONMENT.md
FASTAPI_PINS = ["anyio[trio]==4.6.2", "pydantic==2.11.7", "bcrypt==4.0.1", "python-multipart==0.0.20",
                "pydantic-settings", "fastapi-cli", "email-validator", "flask>=1.1.2,<4.0.0", "orjson", "ujson",
                "sqlmodel==0.0.24", "PyJWT==2.8.0", "passlib[bcrypt]>=1.7.2,<2.0.0", "inline-snapshot>=0.21.1",
                "dirty-equals==0.9.0", "pyyaml>=5.3.1,<7.0.0", "pytest"]
DJANGO_PINS = ["tblib"]
CORPUS_PYTHON = "3.11"                      # the version the tasks were authored and gated with
CODE_PATHS = ("src", "harness", "benchmarks")
# Code changes a run may resume across (setup 10). Each names the tree
# hashes of CODE_PATHS before and after: anything else still aborts. The
# change must leave every finished cell valid; what it invalidates is redone.
AMENDMENTS = [{
    "name": "leak check v2",
    "reason": "v1 counted what the mask keeps (docstring, multi-line signature) and lines common across the "
              "repository as body leaks, and matched gating directories by leaf name; 310 cells were wrongly "
              "held back as leaks. The check gates sending, never the context or the prompt; the one context "
              "change - nodes nested inside the focal (only l4_bwd_trpc_02 has any) are dropped with its body - "
              "touches no ok cell (that task's retrieval cells were all leaks). So ok cells stay, leak cells "
              "from v1 are re-evaluated.",
    "from": {"src": "e0f7d295eca7a95d3c0f3763f63257e174b012b6", "harness": "798fa2501140427f418f3a329dc4a060c50fd4ba",
             "benchmarks": "ad66927ad7d1bb95d4f3552bb4c2e2e1491508fa"},
    "to": {"src": "e0f7d295eca7a95d3c0f3763f63257e174b012b6", "harness": "c7f4d9b42a7d4e13fba80174f82f34b5db2f5eab",
           "benchmarks": "ad66927ad7d1bb95d4f3552bb4c2e2e1491508fa"},
}]
LEAK_CHECK_VERSION = 2                      # = harness.experiments.layer4.context.LEAK_CHECK_VERSION (checked at setup 10)
MAX_PUSH_FILE_MB = 90
START = time.time()
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")

print("=" * 64)
print("PRISM Layer 4 tournament")
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


PUSH_LOCK = threading.Lock()


def push(paths, message, max_attempts=4):
    if not os.environ.get("GITHUB_TOKEN"):
        print("[push] skipped (no GITHUB_TOKEN)", flush=True)
        return False
    with PUSH_LOCK:
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
            print("[push] nothing new", flush=True)
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


RUNNER_PATTERN = r"-m harness\.experiments\.layer4\.(runner|report)( |$)"  # as lane_worker starts them


def stop_runner(pid, grace_s=120):
    """SIGTERM the runner alone (it restores its spliced file and writes no
    record for the cell it was in), then SIGKILL its process group: a test
    process killed first would grade the reply as a fail and the cell would
    be recorded - and skipped on resume - with a wrong score."""
    try:
        pgid = os.getpgid(pid)  # before it exits: a gone leader's group can no longer be looked up
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + grace_s
    while time.time() < deadline:
        try:
            if open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[0] == "Z":
                break  # exited, not yet reaped by its parent
        except (OSError, IndexError):
            break
        time.sleep(1)
    try:
        if pgid != os.getpgid(0):  # a runner from this cell has its own group; never the kernel's
            os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def stop_stale_runners():
    """A previous run of this cell in the same kernel (stopped with the
    interrupt button) leaves its lane runners going; they would splice the
    same checkouts and append the same cells as this run."""
    found = run(["pgrep", "-f", "--", RUNNER_PATTERN], check=False, capture=True).stdout.split()
    mine, pid = set(), os.getpid()
    while pid > 1:  # never this kernel or what started it
        mine.add(pid)
        try:
            pid = int(open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    pids = [int(p) for p in found if int(p) not in mine]
    for pid in pids:
        print(f"[resume] stopping runner {pid} left by an interrupted run", flush=True)
        stop_runner(pid)
    return pids


def cell_done(r):
    """As the runner's is_done: ok, or a leak the current leak check found."""
    if r.get("status") == "leak":
        return (r.get("detail") or {}).get("leak_check") == LEAK_CHECK_VERSION
    return r.get("status") == "ok"


def cell_rank(r):
    """Which of two records of one cell to keep: a finished one, then the latest."""
    return (cell_done(r), r.get("finished_at") or "")


def merge_cell_files(target, *sources):
    """Union of cells.jsonl files into `target`, one record per cell key."""
    best = {}
    for path in (target, *sources):
        if not os.path.exists(path):
            continue
        for line in open(path):
            try:
                r = json.loads(line)
                key = (r["task_id"], r["arm"], r["seed"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue  # a line cut off by a kill: that cell runs again
            if key not in best or cell_rank(r) >= cell_rank(best[key]):
                best[key] = r
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target + ".tmp", "w") as fh:
        for key in sorted(best, key=str):
            fh.write(json.dumps(best[key], default=str) + "\n")
    os.replace(target + ".tmp", target)
    return len(best)


# ============================================================
# Setup
# ============================================================
print("\n[setup 0/10] Stop runners left by an interrupted run of this cell", flush=True)
print(f"[ok] stopped {len(stop_stale_runners())}", flush=True)

print("\n[setup 1/10] GitHub token", flush=True)
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

print("\n[setup 2/10] GPU check + apt", flush=True)
gpu = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], check=False, capture=True)
gpu_lines = [ln for ln in (gpu.stdout or "").strip().splitlines() if ln.strip()]
if sum("T4" in ln for ln in gpu_lines) < 2:
    abort(f"expected GPU T4 x2: {gpu_lines}")
print(f"[ok] GPU: {gpu_lines}", flush=True)
run("apt-get update -qq && apt-get install -y -qq zstd curl git ripgrep xz-utils", check=False)

print("\n[setup 3/10] Ollama (one server across both T4s) + model", flush=True)
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
        abort(f"could not pull {MODEL} - the tournament must use Stage 2's exact model, no fallback")
print(f"[ok] model: {MODEL}", flush=True)

print("\n[setup 4/10] Clone repo", flush=True)
clone_url = REPO_URL.replace("https://", f"https://x-access-token:{github_token}@")
if not os.path.isdir(f"{SCE_DIR}/.git"):
    run(["git", "clone", clone_url, SCE_DIR], check=True, env=GIT_ENV)
run(["git", "remote", "set-url", "origin", clone_url], cwd=SCE_DIR)
run(["git", "fetch", "origin", BRANCH], cwd=SCE_DIR, check=True, env=GIT_ENV)
probe = run(["git", "push", "--dry-run", "origin", f"origin/{BRANCH}:refs/heads/{BRANCH}"],
            cwd=SCE_DIR, check=False, capture=True, env=GIT_ENV)
if probe.returncode != 0:
    abort(f"this GITHUB_TOKEN cannot push to {BRANCH} - results would be lost:\n"
          f"{mask(probe.stderr or probe.stdout).strip()[-1500:]}")
# a rerun in the same session: keep the cells written since the last push
# (the reset and clean below would discard them) and merge them back after
UNPUSHED = "/kaggle/working/l4_unpushed"
local_out = os.path.join(SCE_DIR, PUSH_PATH)
shutil.rmtree(UNPUSHED, ignore_errors=True)
if os.path.isdir(local_out):
    shutil.copytree(local_out, UNPUSHED)
run(["git", "checkout", "-B", BRANCH, f"origin/{BRANCH}"], cwd=SCE_DIR, check=True)
run(["git", "clean", "-fdx", "-e", ".benchmarks"], cwd=SCE_DIR, check=True)
if os.path.isdir(UNPUSHED):
    for root, _dirs, files in os.walk(UNPUSHED):
        for name in files:
            src = os.path.join(root, name)
            dst = os.path.join(local_out, os.path.relpath(src, UNPUSHED))
            if name == "cells.jsonl":
                print(f"[resume] {os.path.relpath(dst, SCE_DIR)}: {merge_cell_files(dst, src)} cells "
                      "(pushed + this session's)", flush=True)
            elif name.endswith(".log") and (not os.path.exists(dst) or os.path.getsize(src) > os.path.getsize(dst)):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
HEAD = run(["git", "rev-parse", "HEAD"], cwd=SCE_DIR, capture=True).stdout.strip()
CODE = code_identity()
for f in ("harness/experiments/layer4/runner.py", "harness/experiments/layer4/report.py",
          "benchmarks/layer4/tasks/l4_fwd_trpc_01.json"):
    if not os.path.exists(os.path.join(SCE_DIR, f)):
        abort(f"{BRANCH} has no {f} (wrong branch/commit)")
print(f"[ok] HEAD {HEAD}  code {CODE}", flush=True)

# ---------- hybrid-RAG embedding cache (shared with the two-stage run) ----------
CACHE_BRANCH = "eval/two-stage-rag-cache"
RAG_CACHE = os.path.join(SCE_DIR, ".benchmarks", "cache", "hybrid_rag")


def restore_rag_cache():
    tmp = "/kaggle/working/rag_cache_pull"
    shutil.rmtree(tmp, ignore_errors=True)
    got = run(["git", "clone", "-q", "--depth", "1", "--branch", CACHE_BRANCH, clone_url, tmp],
              check=False, capture=True, env=GIT_ENV)
    if got.returncode:
        print(f"[cache] no {CACHE_BRANCH} yet - embeddings are computed on first use", flush=True)
        return
    os.makedirs(RAG_CACHE, exist_ok=True)
    parts = sorted(p for p in os.listdir(tmp) if ".npy.part" in p)
    for name in sorted({p.split(".part")[0] for p in parts}):
        dst = os.path.join(RAG_CACHE, name)
        if not os.path.exists(dst):
            with open(dst, "wb") as out:
                for p in parts:
                    if p.split(".part")[0] == name:
                        out.write(open(os.path.join(tmp, p), "rb").read())
        print(f"[cache] restored {name} ({os.path.getsize(dst) / 2**20:.0f} MB)", flush=True)
    shutil.rmtree(tmp, ignore_errors=True)


def save_rag_cache():
    files = sorted(f for f in (os.listdir(RAG_CACHE) if os.path.isdir(RAG_CACHE) else [])
                   if f.endswith(".npy") and time.time() - os.path.getmtime(os.path.join(RAG_CACHE, f)) > 120)
    marker = os.path.join(RAG_CACHE, ".pushed")
    pushed = set(open(marker).read().split()) if os.path.exists(marker) else set()
    if not files or set(files) <= pushed:
        return
    stage = "/kaggle/working/rag_cache_push"
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    for name in files:
        with open(os.path.join(RAG_CACHE, name), "rb") as src:
            i = 0
            while chunk := src.read(45 * 2**20):
                with open(f"{stage}/{name}.part{i:03d}", "wb") as out:
                    out.write(chunk)
                i += 1
    ok = True
    for cmd in (["git", "init", "-q", "-b", "cache"], ["git", "add", "."],
                ["git", "-c", "user.name=Kaggle", "-c", "user.email=kaggle@local", "commit", "-q", "-m",
                 "hybrid-RAG embedding cache (not results)"],
                ["git", "push", "-q", "--force", clone_url, f"HEAD:refs/heads/{CACHE_BRANCH}"]):
        r = run(cmd, cwd=stage, check=False, capture=True, env=GIT_ENV)
        if r.returncode:
            print(f"[cache] [warn] {cmd[1]} failed: {mask(r.stderr or r.stdout).strip()[-500:]}", flush=True)
            ok = False
            break
    if ok:
        open(marker, "w").write("\n".join(files))
        print(f"[cache] saved {len(files)} file(s) to {CACHE_BRANCH}", flush=True)
    shutil.rmtree(stage, ignore_errors=True)


restore_rag_cache()

print("\n[setup 5/10] Python deps + transformers pin", flush=True)
run([sys.executable, "-m", "pip", "install", "-q", "-e", ".[dev,bench,baselines]", "pyarrow", "jsonschema", "uv"],
    cwd=SCE_DIR, check=True)
installed_pin = None
for tf_pin, st_pin in TRANSFORMERS_PINS:
    if run([sys.executable, "-m", "pip", "install", "-q", tf_pin, st_pin], check=False).returncode == 0:
        installed_pin = tf_pin
        break
if not installed_pin:
    abort("all transformers pins failed")
run([sys.executable, "-m", "pip", "install", "-q", "onnxruntime", "onnx", "onnxscript", "einops"], check=True)
# jina-embeddings-v2's remote code imports a helper newer transformers dropped
with open(os.path.join(SCE_DIR, "sitecustomize.py"), "w") as f:
    f.write('''\
import sys as _sys
try:
    import transformers.pytorch_utils as _ptu
    if not hasattr(_ptu, "find_pruneable_heads_and_indices"):
        import torch as _torch
        def _f(heads, n_heads, head_size, already_pruned_heads):
            mask = _torch.ones(n_heads, head_size)
            heads = set(heads) - already_pruned_heads
            for head in heads:
                head = head - sum(1 if h < head else 0 for h in already_pruned_heads)
                mask[head] = 0
            mask = mask.view(-1).contiguous().eq(1)
            index = _torch.arange(len(mask))[mask].long()
            return heads, index
        _ptu.find_pruneable_heads_and_indices = _f
except Exception as _e:
    print(f"[sitecustomize] skipped: {_e}", file=_sys.stderr, flush=True)
''')
print(f"[ok] transformers: {installed_pin}", flush=True)

print(f"\n[setup 6/10] Node {NODE_VERSION} + pnpm {PNPM_VERSION}", flush=True)
node_bin = os.path.join(NODE_DIR, "bin")
if not os.path.exists(os.path.join(node_bin, "node")):
    tarball = f"node-v{NODE_VERSION}-linux-x64.tar.xz"
    run(f"curl -fsSL https://nodejs.org/dist/v{NODE_VERSION}/{tarball} -o /tmp/{tarball}", check=True)
    os.makedirs(NODE_DIR, exist_ok=True)
    run(f"tar -xJf /tmp/{tarball} -C {NODE_DIR} --strip-components=1", check=True)
os.environ["PATH"] = node_bin + os.pathsep + os.environ["PATH"]
NODE = os.path.join(node_bin, "node")
NPM = os.path.join(node_bin, "npm")
node_version = run([NODE, "--version"], capture=True).stdout.strip()
if not node_version.startswith("v20."):
    abort(f"node is {node_version}")
PNPM = os.path.join(node_bin, "pnpm")
if not os.path.exists(PNPM) or run([PNPM, "--version"], check=False, capture=True).stdout.strip() != PNPM_VERSION:
    run([NPM, "install", "-g", "--silent", f"pnpm@{PNPM_VERSION}"], check=True)
if not os.path.exists(PNPM) or run([PNPM, "--version"], capture=True).stdout.strip() != PNPM_VERSION:
    abort(f"pnpm {PNPM_VERSION} not installed at {PNPM}")
print(f"[ok] node {node_version}, pnpm {PNPM_VERSION}", flush=True)

# the running kernel never reads the .pth file `pip install -e .` just wrote
for path in (os.path.join(SCE_DIR, "src"), SCE_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)
child_env = dict(os.environ, PYTHONPATH=os.pathsep.join([SCE_DIR, os.path.join(SCE_DIR, "src")]),
                 CUDA_VISIBLE_DEVICES="", OPENAI_API_KEY="ollama", OLLAMA_API_KEY="ollama",
                 HARNESS_WORKDIR="/kaggle/working", PYTHONDONTWRITEBYTECODE="1")
child_env.pop("GITHUB_TOKEN", None)
child_env.pop("OPENAI_BASE_URL", None)
child_env.pop("PRISM_BREADCRUMB_STRICT", None)  # production behaviour: drop, never raise


def resolve_corpus(name):
    return run([sys.executable, "-c", f"from benchmarks.corpora.resolver import resolve; print(resolve({name!r}))"],
               cwd=SCE_DIR, env=child_env, capture=True).stdout.strip().splitlines()[-1]


print("\n[setup 7/10] Corpora: checkouts + Node test environments", flush=True)
ROOTS = {c: resolve_corpus(c) for c in ("fastapi", "django", "express", "trpc")}
TRPC = run(["git", "rev-parse", "--show-toplevel"], cwd=ROOTS["trpc"], capture=True).stdout.strip()
for c, root in ROOTS.items():
    top = TRPC if c == "trpc" else root
    dirty = run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=top, capture=True).stdout.strip()
    if dirty:  # a previous session's splice was interrupted mid-edit
        print(f"[note] {c} checkout had tracked changes, restoring:\n{dirty}", flush=True)
        run(["git", "checkout", "--", "."], cwd=top, check=True)
print(f"[ok] corpora: {ROOTS}", flush=True)
express = ROOTS["express"]
if not os.path.exists(os.path.join(express, "node_modules", ".bin", "mocha")):
    run([NPM, "install", "--no-audit", "--no-fund", "--ignore-scripts"], cwd=express, check=True)  # with dev deps (Mocha)
if not os.path.exists(os.path.join(express, "node_modules", ".bin", "mocha")):
    abort("express: node_modules/.bin/mocha missing after npm install")
install_env = dict(os.environ, CI="1", HUSKY="0", PUPPETEER_SKIP_DOWNLOAD="1", PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD="1",
                   PRISMA_SKIP_POSTINSTALL_GENERATE="1", NEXT_TELEMETRY_DISABLED="1", TURBO_TELEMETRY_DISABLED="1")


def dist_ok(pkg):
    return os.path.exists(os.path.join(TRPC, "packages", pkg, "dist", "index.mjs"))


if not all(dist_ok(p) for p in TRPC_PACKAGES):
    if run([PNPM, "install", "--frozen-lockfile", "--ignore-scripts", "--reporter=append-only"], cwd=TRPC,
           check=False, env=install_env, timeout=3600).returncode != 0:
        abort("trpc: pnpm install --frozen-lockfile failed")
    run([PNPM, "rebuild", "esbuild", "@swc/core"], cwd=TRPC, check=False, env=install_env)
    # every @trpc/* package: the tests import them by their dist/ entries
    run([PNPM, "run", "build"], cwd=TRPC, check=False, env=install_env)
    for pkg in TRPC_PACKAGES:
        if not dist_ok(pkg):
            run([PNPM, "--filter", f"@trpc/{pkg}", "codegen-entrypoints"], cwd=TRPC, check=False, env=install_env)
            run([PNPM, "--filter", f"@trpc/{pkg}", "build"], cwd=TRPC, check=False, env=install_env)
    if run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=TRPC, capture=True).stdout.strip():
        run(["git", "checkout", "--", "."], cwd=TRPC, check=True)  # codegen rewrote sources: keep dist/ only
if not all(dist_ok(p) for p in TRPC_PACKAGES):
    abort(f"trpc: dist/ missing for {[p for p in TRPC_PACKAGES if not dist_ok(p)]}")
print("[ok] express node_modules (Mocha), trpc workspace built", flush=True)

print(f"\n[setup 8/10] Python {CORPUS_PYTHON} venvs for fastapi + django (ENVIRONMENT.md pins)", flush=True)
UV = [sys.executable, "-m", "uv"]
PYTHONS = {}
# FastAPI as its requirements-tests.txt installs it (`-e .[all]`: httpx for the test
# client, jinja2, ...); a plain `-e .` fails every gating test at import
EDITABLE = {"fastapi": "[all]", "django": ""}
for corpus, pins in (("fastapi", FASTAPI_PINS), ("django", DJANGO_PINS)):
    venv = os.path.join(VENV_DIR, corpus)
    py = os.path.join(venv, "bin", "python")
    if not os.path.exists(py):
        run([*UV, "venv", "-q", "-p", CORPUS_PYTHON, venv], check=True)
        cons = os.path.join(VENV_DIR, f"{corpus}_constraints.txt")
        open(cons, "w").write("\n".join(p for p in pins if "==" in p) + "\n")
        run([*UV, "pip", "install", "-q", "-p", py, "-c", cons, "-e", ROOTS[corpus] + EDITABLE[corpus], *pins],
            check=True)
    PYTHONS[corpus] = py
    print(f"[ok] {corpus}: {run([py, '--version'], capture=True).stdout.strip()}", flush=True)
PYTHON_ARGS = [arg for c, py in PYTHONS.items() for arg in ("--python", f"{c}={py}")]

print("\n[setup 9/10] Preflight: model context window + GPU, RAG models, the gating tests", flush=True)


def http_json(url, payload=None, timeout=900):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


seen = http_json(f"{OLLAMA_URL}/v1/chat/completions", {
    "model": MODEL, "max_tokens": 5, "temperature": 0, "options": {"num_ctx": NUM_CTX},
    "messages": [{"role": "user", "content": "apple " * 12000 + "\nReply with the single word OK."}]})["usage"]["prompt_tokens"]
if seen < 10800:
    abort(f"context window not applied: the server saw {seen} of ~12000 prompt tokens")
if "cuda" not in open(log_path).read().lower():
    abort(f"Ollama is not using the GPU (see {log_path})")
print(f"[ok] {seen} prompt tokens accepted on GPU", flush=True)
# downloads (and caches) jina-embeddings-v2-base-code + bge-reranker-base for arm1_hybrid_rag
if run([sys.executable, "-c", (
        "from harness.arms.arm1_rag import JinaCodeEmbedder, OnnxCrossEncoder;"
        "v = JinaCodeEmbedder().encode(['def add(a, b): return a + b']);"
        "s = OnnxCrossEncoder().score('add two numbers', ['def add(a, b): return a + b']);"
        "assert v.shape[0] == 1 and len(s) == 1; print('rag preflight ok', v.shape)")],
        cwd=SCE_DIR, env=child_env, check=False).returncode != 0:
    abort("jina-code / bge-reranker failed to load - arm1_hybrid_rag cannot run")
# every backward task's gating tests must pass on the original code here, or no
# reply could ever pass: a broken environment, caught before any GPU time
gate = run([sys.executable, "-c", f"""
import json
from types import SimpleNamespace as NS
from benchmarks.corpora.resolver import resolve
from harness.experiments.layer4 import context as X, runner as R, splice as S
tasks = R.load_tasks()
out = {{}}
for tid, t in sorted(tasks.items()):
    if t["family"] != "backward_adaptation":
        continue
    corpus = NS(clone_root=X.clone_root(t["corpus"], str(resolve(t["corpus"]))))
    r = S._run(R.splice_task(t, corpus, {json.dumps(PYTHONS)}, {NODE!r}))
    out[tid] = {{"passed": r["passed"], "seconds": r["seconds"], "tail": r["output_tail"][-600:]}}
print("GATES " + json.dumps(out))
"""], cwd=SCE_DIR, env=child_env, check=False, capture=True)
try:
    gates = json.loads([ln for ln in gate.stdout.splitlines() if ln.startswith("GATES ")][-1][6:])
except Exception:
    abort(f"gating-test preflight crashed:\n{(gate.stderr or gate.stdout)[-3000:]}")
for tid, g in gates.items():
    print(f"  [{'ok' if g['passed'] else 'FAIL'}] {tid}: original passes its tests ({g['seconds']}s)", flush=True)
if len(gates) != 8 or not all(g["passed"] for g in gates.values()):
    abort("a backward task's gating tests fail on the original code: "
          + json.dumps({k: v["tail"] for k, v in gates.items() if not v["passed"]})[-3000:])

print("\n[setup 10/10] Restore pushed progress (identical code, or a declared AMENDMENTS change)", flush=True)
OUT = os.path.join(SCE_DIR, PUSH_PATH)
CORPORA = os.path.join(SCE_DIR, CORPORA_DIR)
os.makedirs(CORPORA, exist_ok=True)
manifest_path = os.path.join(OUT, "tournament_manifest.json")
if os.path.exists(manifest_path):
    m = json.load(open(manifest_path))
    amendments = list(m.get("amendments", []))
    if m.get("code") != CODE:
        step = next((a for a in AMENDMENTS if a["from"] == m.get("code") and a["to"] == CODE), None)
        if step is None:
            abort(f"{PUSH_PATH} on {BRANCH} was produced by other code ({m.get('code')} != {CODE}) and no "
                  "AMENDMENTS entry covers the change; move it aside or set PUSH_PATH to a new directory")
        print(f"[ok] resuming across the declared code change '{step['name']}': {step['reason']}", flush=True)
        amendments.append({"name": step["name"], "from": step["from"], "to": step["to"], "from_head": m.get("head"),
                           "to_head": HEAD, "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    if m.get("model") != MODEL or m.get("seeds") != SEEDS:
        abort(f"{PUSH_PATH} holds a run with model {m.get('model')} / seeds {m.get('seeds')}")
    print(f"[ok] resuming {PUSH_PATH} from {m.get('head', '')[:10]}", flush=True)
else:
    amendments = []
check = run([sys.executable, "-c", "from harness.experiments.layer4.context import LEAK_CHECK_VERSION as v; print(v)"],
            cwd=SCE_DIR, env=child_env, capture=True).stdout.strip().splitlines()[-1]
if check != str(LEAK_CHECK_VERSION):
    abort(f"the cell's LEAK_CHECK_VERSION {LEAK_CHECK_VERSION} != the harness's {check}")
json.dump({"head": HEAD, "code": CODE, "amendments": amendments, "model": MODEL, "num_ctx": NUM_CTX, "seeds": SEEDS, "lanes": LANES,
           "transformers": installed_pin, "node": node_version, "pnpm": PNPM_VERSION, "corpus_python": CORPUS_PYTHON,
           "fastapi_pins": FASTAPI_PINS, "django_pins": DJANGO_PINS}, open(manifest_path, "w"), indent=1)


# ============================================================
# Pipeline
# ============================================================
def counts(corpus):
    path = os.path.join(CORPORA, corpus, "cells.jsonl")
    seen, n = {}, {}
    if os.path.exists(path):
        for line in open(path):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            seen[(r["task_id"], r["arm"], r["seed"])] = r["status"]
    for s in seen.values():
        n[s] = n.get(s, 0) + 1
    return n


REPORT_LOCK = threading.Lock()
STOP = threading.Event()  # set on the time limit or the interrupt button: lanes start nothing new
TASKS_PER_CORPUS = 4


def merge_and_push(tag):
    with REPORT_LOCK:  # lanes push when a corpus finishes, the monitor every PUSH_EVERY_S
        _merge_and_push(tag)


def _merge_and_push(tag):
    run([sys.executable, "-c", f"from pathlib import Path; from harness.experiments.layer4.report import merge; "
         f"print('[merge]', merge(Path({CORPORA!r}), Path({OUT!r})), 'cells')"], cwd=SCE_DIR, env=child_env, check=False)
    push([PUSH_PATH], f"Layer 4 tournament: {tag} @ {HEAD[:7]}")


def lane_worker(corpora, command, status, procs):
    """One lane: its corpora in order, each retried while cells end in error."""
    for corpus in corpora:
        out_dir = os.path.join(CORPORA, corpus)
        os.makedirs(out_dir, exist_ok=True)
        for attempt in range(1, RETRY_PASSES + 1):
            if STOP.is_set() or out_of_time():
                status[corpus] = "stopped"
                return
            cmd = [sys.executable, "-u", "-m", "harness.experiments.layer4.runner", command, "--corpus", corpus,
                   "--seeds", SEEDS, "--out", out_dir, *PYTHON_ARGS, "--node", NODE, "--model", MODEL,
                   "--base-url", f"{OLLAMA_URL}/v1", "--num-ctx", str(NUM_CTX)]
            if command == "run":
                cmd += ["--arms", "all"]
            log = open(os.path.join(out_dir, f"{command}.log"), "a")
            log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {command} pass {attempt} ===\n")
            proc = subprocess.Popen(cmd, cwd=SCE_DIR, env=child_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, start_new_session=True)  # own group: its test processes die with it
            procs[corpus] = proc
            for line in proc.stdout:
                log.write(line)
                log.flush()
                if line.startswith(("  [", "[corpus]", "Traceback", "[ABORT]")) or "Error" in line[:60]:
                    print(f"[{corpus}] {line}", end="", flush=True)
            code = proc.wait()
            log.close()
            n = counts(corpus)
            print(f"[{corpus}] {command} pass {attempt}: exit {code}, cells {n}", flush=True)
            status[corpus] = f"exit {code}, {n}"
            if STOP.is_set():  # stopped mid-pass: the cell it was in left no record and runs again on resume
                status[corpus] = f"stopped, {n}"
                return
            if not n.get("error"):
                break
        merge_and_push(f"{command} {corpus} done")


def stop_lanes(threads, procs, tag):
    STOP.set()
    for p in list(procs.values()):
        if p.poll() is None:
            stop_runner(p.pid)
    for t in threads:
        t.join(timeout=180)
    merge_and_push(f"{tag} (stopped early)")


def resume_status(command):
    """Finished cells per corpus before this step starts (the runner skips them)."""
    arms = None if command == "run" else {"arm0_no_retrieval"}  # step 2 reuses step 1's arm0 cells
    total = TASKS_PER_CORPUS * (1 if arms else 7) * len(SEEDS.split(","))
    for lane in LANES:
        for corpus in lane:
            mine = [r for r in cells_of(corpus) if not arms or r["arm"] in arms]
            done = sum(1 for r in mine if cell_done(r))
            recheck = sum(1 for r in mine if r["status"] == "leak" and not cell_done(r))
            print(f"[resume] {corpus}: {done}/{total} cells finished"
                  f"{' - skipped, the rest run now' if done else ''}"
                  f"{f' ({recheck} leak cells from an older leak check are re-evaluated)' if recheck else ''}", flush=True)


def cells_of(corpus):
    path = os.path.join(CORPORA, corpus, "cells.jsonl")
    out = {}
    if os.path.exists(path):
        for line in open(path):
            try:
                r = json.loads(line)
                out[(r["task_id"], r["arm"], r["seed"])] = r
            except (json.JSONDecodeError, KeyError):
                continue
    return list(out.values())


def run_step(command, tag):
    resume_status(command)
    status, procs = {}, {}
    threads = [threading.Thread(target=lane_worker, args=(lane, command, status, procs), daemon=True) for lane in LANES]
    for t in threads:
        t.start()
    last = time.time()
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(30)
            if out_of_time():
                print("[warn] approaching Kaggle's time limit - stopping; re-run this cell to resume", flush=True)
                stop_lanes(threads, procs, tag)
                return status, True
            if time.time() - last >= PUSH_EVERY_S:
                merge_and_push(f"{tag} (periodic)")
                save_rag_cache()
                last = time.time()
    except KeyboardInterrupt:  # the interrupt button: stop cleanly, push, re-run the cell to resume
        print("\n[interrupted] stopping the lanes and pushing finished cells; re-run this cell to resume", flush=True)
        stop_lanes(threads, procs, tag)
        raise
    merge_and_push(tag)
    save_rag_cache()
    return status, False


def report(header):
    run([sys.executable, "-m", "harness.experiments.layer4.report", "--out", OUT, "--merge", CORPORA,
         "--header", header], cwd=SCE_DIR, env=child_env, check=False)


stopped = False
print(f"\n[step 1/3] contamination: arm0_no_retrieval x 16 tasks x {len(SEEDS.split(','))} seeds", flush=True)
t0 = time.time()
status1, stopped = run_step("contamination", "step 1 contamination")
report(f"Step 1 (contamination) - code `{HEAD[:10]}`, model `{MODEL}`.")
push([PUSH_PATH], f"Layer 4 tournament: contamination report @ {HEAD[:7]}")
contamination = json.load(open(os.path.join(OUT, "contamination.json"))) if os.path.exists(
    os.path.join(OUT, "contamination.json")) else {}
print(f"[step 1] {(time.time() - t0) / 60:.0f} min, lanes {status1}, flagged {contamination.get('flagged')}", flush=True)

status2 = {}
if not stopped:
    print(f"\n[step 2/3] full matrix: 16 tasks x 7 arms x {len(SEEDS.split(','))} seeds", flush=True)
    t0 = time.time()
    status2, stopped = run_step("run", "step 2 full matrix")
    print(f"[step 2] {(time.time() - t0) / 60:.0f} min, lanes {status2}", flush=True)

print("\n[step 3/3] summarize", flush=True)
amended = "".join(f" Resumed across the declared code change '{a['name']}' ({a['from_head'][:10]} -> "
                  f"{a['to_head'][:10]}): leak cells from the older check were re-evaluated, ok cells kept."
                  for a in amendments)
header = (f"Code `{HEAD[:10]}`, model `{MODEL}` (Ollama {OLLAMA_VERSION}, num_ctx {NUM_CTX}), seeds {SEEDS}.{amended} "
          + ("**INCOMPLETE** - the session stopped before the time limit; re-run this cell to resume."
             if stopped else "Complete."))
report(header)
push([PUSH_PATH], f"Layer 4 tournament: {'stopped early' if stopped else 'final report'} @ {HEAD[:7]}")

md_path = os.path.join(OUT, "TOURNAMENT.md")
md = open(md_path).read() if os.path.exists(md_path) else "(no report)"
print("\n=== DONE ===" if not stopped else "\n=== STOPPED EARLY - re-run this cell to resume ===")
print(f"Head:     {HEAD}")
print(f"Elapsed:  {(time.time() - START) / 3600:.1f} h")
print(f"Step 1:   {status1}")
print(f"Step 2:   {status2}")
print(f"Output:   {BRANCH}:{PUSH_PATH}/ (TOURNAMENT.md, summary.json, contamination.json, cells.jsonl)\n")
print(md)
try:
    from IPython.display import Markdown, display
    display(Markdown(md))
except Exception:
    pass
