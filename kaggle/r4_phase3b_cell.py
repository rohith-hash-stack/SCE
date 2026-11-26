# ============================================================
# >>> SET THIS FOR EACH NEW KAGGLE SESSION: the shard to run, 1-5 (16 runs each; see SHARDS below)
SHARD_ID = 1
# ============================================================
# PRISM Phase 3b (harness/experiments/r4/AMENDMENTS.md, "Phase 3b: the whitespace root cause", pre-registered
# 2026-11-26, and its "note 1"): the tasks of harness/experiments/r4/phase3/phase3b.json, 4 arms, devstral-small-2:
#   PRISM_GW      arm A: POC 2's PRISM_GW exactly (PRISM's tools + the gateway)
#   PRISM_GW_C    arm C: A + after a refused edit, the gateway gives the exact old/new text of the nearest place
#   PRISM_GW_E    arm E: A + PRISM's printed source lines keep their own indentation
#   BASELINE_LSP  the IDE-agent baseline of POC 2, unchanged
# p3-hidden-15 (litestar) seeds 301-315 and p3-hidden-03 (werkzeug) seeds 301-305, x 4 arms = 80 runs.
# Every arm: POC 2's flags (--ledger --phase1 --raw-tools --feedback --guards), turn cap 60, budget 1,000,000.
#
# SHARDS: shard k = seeds 3k-2..3k of p3-hidden-15 and seed k of p3-hidden-03, 16 runs, in the order written in the
# task file (`shards`): each task-and-seed block runs all 4 arms, their order rotating by one per block. Results go to
# reports/r4/phase3b_shard<k>. Shards can run in parallel in different Kaggle sessions: each pushes only its own folder.
# A shard stopped by the session limit continues when the same SHARD_ID is run again (finished runs are kept and never
# repeated). Expect about 2.5-3.5 hours per shard.
#
# Before the first run, every shard checks its environment against the pinned values (Ollama version, the model's
# manifest layers, 2 x T4, the cell configuration) and stops on any difference; then the raw-template pre-flight.
# After its runs, the health check (more than 2 infrastructure deaths, or parse errors >= 2% of turns: failed).
# No outcome analysis runs here: results stay blinded until all 80 runs are in.
#
# Paste this whole file into ONE Kaggle notebook cell and Run.
# Settings: Accelerator GPU T4 x2, Internet ON, secret GITHUB_TOKEN.
# ============================================================

import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

# ---------- Config (pinned) ----------
BRANCH = "feature/option3-contract-axis"
CODE_COMMIT = "f77411d46ad66e625dcc32cd70d32b337fa7a0ae"
CODE_TREES = {"src": "227e75652451519c58c15a328d512c20a8768b00",
              "harness": "6a95788045c0f4e1214ee543a08c9259aff82cda"}
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
EXPECTED_GITHUB_USER = "rohith-hash-stack"
N_SHARDS = 5
RUNS_PER_SHARD = 16
if SHARD_ID not in range(1, N_SHARDS + 1):
    raise SystemExit(f"SHARD_ID must be 1-{N_SHARDS}, got {SHARD_ID!r}")
STAGE = f"phase3b_shard{SHARD_ID}"
ARMS = ("PRISM_GW", "PRISM_GW_C", "PRISM_GW_E", "BASELINE_LSP")
SUMMARIZER = "devstral-small-2:24b-instruct-2512-q4_K_M"
MODEL_FLAGS = ["--model", SUMMARIZER, "--ledger", "--phase1", "--raw-tools", "--feedback", "--guards"]
OLLAMA_VERSION = "0.34.0"
# the registry's manifest for SUMMARIZER, fetched 2026-11-26 (AMENDMENTS "Phase 3b note 1")
MODEL_LAYERS = {"model": "sha256:c580819bed79c92d01a42227bd6d8fd66b9ec60d5329f5eb73f812f156af7807",
                "template": "sha256:580f051b6624bd17f29bb37554ec19feb4ab18cda4f948739016b6a5f300b026",
                "params": "sha256:e0daf17ff83eace4813f9e8554b262f6cc33ad880ff8df41a156ff9ef5522ddb"}
PORT = 11434
GPU_GROUP = "0,1"
TOKENIZER_URL = "https://registry.npmjs.org/@lenml/tokenizer-qwen2_5/-/tokenizer-qwen2_5-3.7.2.tgz"
TOKENIZER_SHA = "c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539"
TASKS = "harness/experiments/r4/phase3/phase3b.json"
TASKS_SHA = "506e4047266c032876e04b97dd587aebdf7dd246093442d2d22f290dc00a9a39"
TURN_CAP = 60
BUDGET_TOKENS = "1000000"
SESSION_HOURS = 11.5
STOP_MARGIN_MIN = 30                  # no run starts later than this before the session limit
DISK_CLEAN_GB = 5
DISK_STOP_GB = 2
CELL_START = time.time()
CHECKPOINT_MIN = 10
WORK = "/kaggle/working"
SCE_DIR = f"{WORK}/SCE"
PUSH_WT = f"{WORK}/SCE_results"
RESULTS_PATH = f"reports/r4/{STAGE}"
BIG = "/tmp/r4p3b"
CACHE = f"{BIG}/cache"
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")
CONFIG = {"code": CODE_COMMIT, "trees": CODE_TREES, "tasks": TASKS, "tasks_sha256": TASKS_SHA, "arms": ARMS,
          "model": SUMMARIZER, "model_flags": MODEL_FLAGS, "turn_cap": TURN_CAP, "budget_tokens": BUDGET_TOKENS,
          "ollama": OLLAMA_VERSION, "model_layers": MODEL_LAYERS,
          "ollama_env": {"OLLAMA_NUM_PARALLEL": "1", "OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0",
                         "OLLAMA_MAX_LOADED_MODELS": "1", "OLLAMA_CONTEXT_LENGTH": "20480"}}
CONFIG_SHA = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()
print("=" * 64)
print(f"PRISM Phase 3b ({SUMMARIZER}): SHARD {SHARD_ID}/{N_SHARDS}, {len(ARMS)} arms -> {RESULTS_PATH}")
print(f"config sha {CONFIG_SHA[:12]}")
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


def push_results(message, max_attempts=10):
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
        time.sleep(min(2 ** (attempt + 1), 60) + 3 * SHARD_ID)    # shards push their own folders: no conflicts
    print(f"[push] FAILED - results are still in {OUT}", flush=True)
    return False


print("\n[setup] GitHub token", flush=True)
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

print("\n[setup] Clone and check out the code commit", flush=True)
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
print(f"[ok] code {CODE_COMMIT[:8]} {got}", flush=True)
if not os.path.isdir(PUSH_WT):
    run(["git", "worktree", "add", "-f", "--detach", PUSH_WT, f"origin/{BRANCH}"], cwd=SCE_DIR, check=True)
OUT = os.path.join(SCE_DIR, RESULTS_PATH)
os.makedirs(OUT, exist_ok=True)
if run(["git", "cat-file", "-e", f"origin/{BRANCH}:{RESULTS_PATH}"], cwd=SCE_DIR, check=False).returncode == 0:
    run(f"git archive origin/{BRANCH} {RESULTS_PATH} | tar -x -C {SCE_DIR}", cwd=SCE_DIR, check=True)
    print(f"[resume] restored {len(os.listdir(OUT))} files", flush=True)

print("\n[setup] Install prism, the tokenizer library and uv", flush=True)
run([sys.executable, "-m", "pip", "install", "-q", "-e", ".", "tokenizers", "uv", "packaging",
     "pyflakes==3.2.0", "jedi==0.19.2", "ast-grep-cli==0.39.6"], cwd=SCE_DIR, check=True)
child_env = dict(os.environ, R3_BUDGET_TOKENS=BUDGET_TOKENS,
                 PYTHONPATH=os.pathsep.join([SCE_DIR, os.path.join(SCE_DIR, "src")]), PYTHONDONTWRITEBYTECODE="1",
                 PATH=os.pathsep.join([os.path.dirname(sys.executable), os.path.expanduser("~/.local/bin"),
                                       os.environ.get("PATH", "")]))
if shutil.which("uv", path=child_env["PATH"]) is None:
    abort("uv is not on PATH after installing it")
child_env.pop("GITHUB_TOKEN", None)
os.makedirs(CACHE, exist_ok=True)

print("\n[setup] The Qwen2.5 tokenizer (npm registry tarball, sha256-checked)", flush=True)
TOK_DIR = f"{WORK}/qwen-tokenizer"
TOKENIZER = f"{TOK_DIR}/package/models/tokenizer.json"
if not os.path.exists(TOKENIZER):
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

print("\n[tasks] The Phase 3b task list and this shard's runs", flush=True)
doc = json.load(open(os.path.join(SCE_DIR, TASKS)))
got_sha = hashlib.sha256(json.dumps(doc["tasks"], sort_keys=True).encode()).hexdigest()
if got_sha != TASKS_SHA or doc.get("tasks_sha256") != TASKS_SHA:
    abort(f"the Phase 3b task list is not the committed one: {got_sha}")
if doc.get("design") != "phase3b" or tuple(doc.get("arms", ())) != ARMS or doc.get("expected_runs") != 80:
    abort("the task list is not marked as the Phase 3b design (4 arms, 80 runs)")
TASK_BY_ID = {t["id"]: t for t in doc["tasks"]}
SHARD = [tuple(x) for x in doc["shards"][str(SHARD_ID)]]
if len(SHARD) != RUNS_PER_SHARD or len(set(SHARD)) != RUNS_PER_SHARD or any(x[0] not in TASK_BY_ID for x in SHARD):
    abort(f"shard {SHARD_ID} is not 16 distinct runs of the task list's tasks")
if sum(len(v) for v in doc["shards"].values()) != 80:
    abort("the shards do not hold 80 runs")
BLOCKS = []                           # (task, seed, arms in this block's order), in the shard's order
for tid, seed, arm in SHARD:
    if BLOCKS and BLOCKS[-1][:2] == (tid, seed):
        BLOCKS[-1][2].append(arm)
    else:
        BLOCKS.append((tid, seed, [arm]))
if any(sorted(b[2]) != sorted(ARMS) for b in BLOCKS):
    abort("a task-and-seed block does not run every arm once")
SEEDS = sorted({s for _, s, _ in SHARD})
REPOS = doc["repo_urls"]
N_RUNS = len(SHARD)
for tid, seed, arms in BLOCKS:
    print(f"  {tid} seed {seed}: {' -> '.join(arms)}", flush=True)
print(f"[ok] {N_RUNS} runs, sha {got_sha[:12]}", flush=True)
os.makedirs(BIG, exist_ok=True)


def run_id(tid, seed, arm):
    return f"{tid}__{arm}__{seed}"


def is_done(tid, seed, arm):
    return os.path.exists(os.path.join(OUT, run_id(tid, seed, arm) + ".score.json"))


def n_done():
    return sum(is_done(*x) for x in SHARD)


def free_gb():
    return shutil.disk_usage(BIG).free / 2 ** 30


def clear_caches(why):
    for d in (f"{CACHE}/py/uv-cache", f"{CACHE}/js/pnpm-store", f"{CACHE}/js/pnpm-home",
              os.path.expanduser("~/.cache/uv"), os.path.expanduser("~/.local/share/pnpm/store")):
        shutil.rmtree(d, ignore_errors=True)
    print(f"[disk] {why}: cleared the download caches, {free_gb():.1f} GB free", flush=True)


def fetch(name):
    path = f"{BIG}/repos/{name}"
    if not os.path.isdir(f"{path}/.git"):
        run(["git", "clone", "-q", REPOS[name], path], check=True, env=GIT_ENV)
    need = {TASK_BY_ID[tid]["commit"] for tid, _, _ in SHARD if TASK_BY_ID[tid]["repo"] == name}
    if any(run(["git", "-C", path, "cat-file", "-e", c + "^{commit}"], check=False).returncode for c in need):
        run(["git", "-C", path, "fetch", "-q", "origin"], check=True, env=GIT_ENV)
    missing = [c for c in need if run(["git", "-C", path, "cat-file", "-e", c + "^{commit}"], check=False).returncode]
    if missing:
        abort(f"{name}: commits not found after fetching: {missing}")
    return path


print("\n[setup] GPU check + apt", flush=True)
gpu = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], check=False, capture=True)
gpu_lines = [ln.strip() for ln in (gpu.stdout or "").strip().splitlines() if ln.strip()]
run("apt-get update -qq && apt-get install -y -qq zstd curl git", check=False)

print("\n[setup] Ollama + the model", flush=True)
if subprocess.run(["which", "ollama"], capture_output=True).returncode != 0:
    run(f"curl -L https://github.com/ollama/ollama/releases/download/v{OLLAMA_VERSION}/ollama-linux-amd64.tar.zst "
        "-o /tmp/ollama.tar.zst", check=True)
    run("tar -I zstd -xf /tmp/ollama.tar.zst -C /usr/local", check=True)
    run("chmod 755 /usr/local/bin/ollama", check=True)
env = dict(os.environ, OLLAMA_HOST=f"127.0.0.1:{PORT}", CUDA_VISIBLE_DEVICES=GPU_GROUP, OLLAMA_KEEP_ALIVE="4h",
           **CONFIG["ollama_env"])
try:
    urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/version", timeout=2).close()
except Exception:
    subprocess.Popen(["ollama", "serve"], env=env, stdout=open(f"{WORK}/ollama_{PORT}.log", "a"),
                     stderr=subprocess.STDOUT)
time.sleep(10)
server_version = None
for _ in range(30):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/version", timeout=2) as r:
            server_version = json.loads(r.read()).get("version")
            break
    except Exception:
        time.sleep(2)
else:
    abort(f"ollama on port {PORT} not responding (see {WORK}/ollama_{PORT}.log)")
first = dict(os.environ, OLLAMA_HOST=f"127.0.0.1:{PORT}")
for attempt in range(8):
    if SUMMARIZER in (run(["ollama", "list"], capture=True, env=first).stdout or ""):
        break
    if run(["ollama", "pull", SUMMARIZER], check=False, env=first).returncode == 0:
        break
    wait = min(15 * 2 ** attempt, 240)
    print(f"[retry] pull failed (attempt {attempt + 1}/8), waiting {wait}s", flush=True)
    time.sleep(wait)
else:
    abort(f"could not pull {SUMMARIZER} after 8 attempts")

print("\n[env] The pinned environment (AMENDMENTS 'Phase 3b' section 8 and note 1)", flush=True)
name, tag = SUMMARIZER.split(":")
models_dir = os.environ.get("OLLAMA_MODELS", os.path.expanduser("~/.ollama/models"))
manifests = glob.glob(os.path.join(models_dir, "manifests", "*", "library", name, tag))
layers = {}
if len(manifests) == 1:
    for layer in json.load(open(manifests[0])).get("layers", []):
        layers[layer["mediaType"].rsplit(".", 1)[-1]] = layer["digest"]
with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/tags", timeout=30) as r:
    tag_digest = next((m.get("digest") for m in json.loads(r.read()).get("models", []) if m.get("name") == SUMMARIZER),
                      None)
environment = {"shard": SHARD_ID, "config_sha256": CONFIG_SHA, "config": CONFIG, "ollama_server": server_version,
               "model_manifest": manifests, "model_layers": layers, "model_tag_digest": tag_digest, "gpus": gpu_lines,
               "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
problems = []
if server_version != OLLAMA_VERSION:
    problems.append(f"Ollama server {server_version!r}, pinned {OLLAMA_VERSION}")
if layers != MODEL_LAYERS:
    problems.append(f"model layers {layers} (manifest {manifests}), pinned {MODEL_LAYERS}")
if len(gpu_lines) != 2 or not all("T4" in ln for ln in gpu_lines):
    problems.append(f"GPUs {gpu_lines}, pinned 2 x T4")
environment["ok"] = not problems
environment["problems"] = problems
with open(os.path.join(OUT, "environment.jsonl"), "a") as fh:     # one line per session of this shard
    fh.write(json.dumps(environment) + "\n")
if problems:
    push_results(f"r4 {STAGE}: STOPPED by the environment check @ {CODE_COMMIT[:8]}")
    abort("the environment differs from the pinned one: " + "; ".join(problems))
print(f"[ok] Ollama {server_version}, model layers pinned, GPUs {gpu_lines}, config {CONFIG_SHA[:12]}", flush=True)

req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/chat", data=json.dumps({
    "model": SUMMARIZER, "messages": [{"role": "user", "content": "Reply with {\"ok\": true}"}], "format": "json",
    "stream": False, "options": {"num_predict": 10}}).encode(), headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=900) as r:
    print(f"[ok] model answers: {json.loads(r.read())['message']['content']!r}", flush=True)

print("\n[preflight] the harness renders devstral's template as Ollama does (all 4 arms)", flush=True)
_pf = subprocess.run([sys.executable, "-m", "harness.experiments.r4.raw_preflight", "--base-url",
                      f"http://127.0.0.1:{PORT}", "--model", SUMMARIZER, "--out", os.path.join(OUT, "preflight.json"),
                      "--versions", *ARMS, "--guards"],
                     cwd=SCE_DIR, env=child_env, capture_output=True, text=True)
print(_pf.stdout[-3000:], _pf.stderr[-2000:], flush=True)
if _pf.returncode != 0:
    push_results(f"r4 {STAGE}: STOPPED by the raw-template pre-flight @ {CODE_COMMIT[:8]}")
    abort("the pre-flight found a prompt mismatch (or failed) - see preflight.json; report back")
print("[ok] pre-flight: prompt token counts match", flush=True)


def module(args, label, name, push_while=False):
    cmd = [sys.executable, "-m", name, *args]
    print(f"$ {' '.join(cmd)}", flush=True)
    start = time.time()
    with open(os.path.join(OUT, f"{label}.log"), "a") as log:
        proc = subprocess.Popen(cmd, cwd=SCE_DIR, env=child_env, stdout=log, stderr=subprocess.STDOUT)
        last_push = time.time()
        while proc.poll() is None:
            time.sleep(30)
            if free_gb() < DISK_CLEAN_GB and subprocess.run(["pgrep", "-x", "npm|pnpm|yarn|uv|node"],
                                                            capture_output=True).returncode != 0:
                clear_caches("during runs")
            if push_while and time.time() - last_push > CHECKPOINT_MIN * 60:
                push_results(f"r4 {STAGE}: {label} checkpoint @ {CODE_COMMIT[:8]}")
                last_push = time.time()
    print(open(os.path.join(OUT, f"{label}.log")).read()[-1500:], flush=True)
    print(f"[{label}] exit {proc.returncode} after {(time.time() - start) / 60:.0f} min", flush=True)
    return proc.returncode


deadline = CELL_START + SESSION_HOURS * 3600 - STOP_MARGIN_MIN * 60
print(f"\n[runs] {n_done()}/{N_RUNS} runs done", flush=True)
rc, failed = 0, False
for i, (tid, seed, arms) in enumerate(BLOCKS, 1):
    todo = [a for a in arms if not is_done(tid, seed, a)]
    if not todo:
        continue
    if time.time() > deadline:
        rc = 3
        break
    if free_gb() < DISK_CLEAN_GB:
        clear_caches(f"before block {i}")
    if free_gb() < DISK_STOP_GB:
        print(f"[disk] {free_gb():.1f} GB free after clearing: stopping the session before block {i}", flush=True)
        rc = 3
        break
    task = TASK_BY_ID[tid]
    clone = fetch(task["repo"])
    print(f"\n[block {i}/{len(BLOCKS)}] {tid} seed {seed}: {' -> '.join(arms)}", flush=True)
    part = os.path.join(OUT, f"tasks_block{i}.json")
    json.dump({"tasks": [task]}, open(part, "w"))
    r = module([part, OUT, "--cache-py", f"{CACHE}/py", "--cache-js", f"{CACHE}/js",
                "--base-url", f"http://127.0.0.1:{PORT}", "--seeds", str(seed), "--turn-cap", str(TURN_CAP),
                "--deadline", str(deadline), "--versions", *arms, *MODEL_FLAGS, f"{task['repo']}={clone}"],
               f"runs_block{i}", "harness.experiments.r3.pilot_run", push_while=True)
    os.remove(part)
    if r == 3:
        rc = 3
        break
    failed = failed or r != 0
    push_results(f"r4 {STAGE}: {n_done()}/{N_RUNS} runs @ {CODE_COMMIT[:8]}")

if rc != 3 and n_done() == N_RUNS:          # the shard's health check, once its runs are done
    hc = module([OUT, os.path.join(SCE_DIR, TASKS), "--health", "--seeds", *map(str, SEEDS)], "health",
                "harness.experiments.r4.rebaseline_report")
    if hc == 4:
        push_results(f"r4 {STAGE}: FAILED the health check @ {CODE_COMMIT[:8]}")
        abort(f"shard {SHARD_ID} failed the health check (infrastructure deaths > 2 or parse errors >= 2%) - see "
              f"{RESULTS_PATH}/health.log; report back")
open(os.path.join(OUT, "progress.json"), "w").write(json.dumps({
    "stage": STAGE, "shard": SHARD_ID, "arms": ARMS, "runs_done": n_done(), "runs_total": N_RUNS, "seeds": SEEDS,
    "code": CODE_COMMIT, "config_sha256": CONFIG_SHA, "tasks_sha256": TASKS_SHA, "model": SUMMARIZER,
    "model_flags": MODEL_FLAGS,
    "run_hours": round(sum(json.loads(l)["seconds"] for l in open(os.path.join(OUT, "pilot_runs.jsonl"))) / 3600, 2)
    if os.path.exists(os.path.join(OUT, "pilot_runs.jsonl")) else None}, indent=1))
push_results(f"r4 {STAGE}: {n_done()}/{N_RUNS} runs @ {CODE_COMMIT[:8]} ({SUMMARIZER})")
print(f"\n[runs] {n_done()}/{N_RUNS} runs done", flush=True)
if rc == 3:
    print(f"\n=== SESSION LIMIT: {STAGE} is not finished; run the cell again with SHARD_ID = {SHARD_ID} ===",
          flush=True)
elif failed:
    abort("some runs exited with an error - see pilot_runs.jsonl; run the cell again to retry them")
elif n_done() < N_RUNS:
    abort("runs are missing - run the cell again")
else:
    print(f"\n=== {STAGE} DONE ({N_RUNS} runs, health check passed) ===", flush=True)
