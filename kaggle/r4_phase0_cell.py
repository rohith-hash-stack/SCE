# ============================================================
# PRISM R4 Phase 0 (harness/experiments/r4/AMENDMENTS.md, "Phase 0 on a local model"): can devstral-small-2 complete
# the sanity tasks on the fixed harness? The 20 tasks of sanity/tasks_v3.json, both arms, seed 42, turn cap 28, gate
# active, ledger on, no plan field, thinking not requested. One Ollama lane on both T4s.
#
# STAGE picks how far to go; each stage stops at its checkpoint (the user's go-ahead comes between stages):
#   "pilot"   checkpoint 2: 1 task, both arms (2 runs)
#   "first10" checkpoint 3: 5 tasks, both arms (10 runs), one task per family in turn
#   "all"     the rest: 20 tasks, 40 runs
# Finished runs are kept and never repeated: moving to the next stage, or a new session, continues where it stopped.
#
# Order:
#   1. token, the code at the pinned commit, the Qwen2.5 tokenizer (sha256-checked);
#   2. the sanity task list (sha256-checked) and its repositories;
#   3. the JS/TS builder smoke test (install only) if the stage has a JS/TS task;
#   4. GPU, Ollama, the model, and a one-call JSON tool smoke test;
#   5. the runs, repository by repository (each run in its own process, `pilot_run.py`; within a task PRISM then
#      BASELINE), with the same disk guards as sanity 4;
#   6. `sanity_report` over the runs so far.
# Results go to RESULTS_PATH on BRANCH, pushed every CHECKPOINT_MIN minutes. "=== STAGE DONE ===" ends a stage.
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
CODE_COMMIT = "ac0d77ac8665575720b98d473d3028f3823c1b37"
CODE_TREES = {"src": "82963a7414f3425c789548097ae3cc1d88402091",
              "harness": "6f6806981ec060500170f1eb9e5e8bb67fc3267d"}
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
EXPECTED_GITHUB_USER = "rohith-hash-stack"
STAGE = "pilot"                       # "pilot" (2 runs) -> "first10" (10 runs) -> "all" (40 runs)
STAGE_TASKS = {"pilot": 1, "first10": 5, "all": None}
SUMMARIZER = "devstral-small-2:24b-instruct-2512-q4_K_M"   # Phase 0 model (amendment); JSON mode, no think flag
MODEL_FLAGS = ["--model", SUMMARIZER, "--ledger"]
# If the ornith diagnostic confirms JSON-mode interference and the user switches models (amendment, variant B):
# SUMMARIZER = "ornith:35b-q4_K_M"; MODEL_FLAGS = ["--model", SUMMARIZER, "--think", "off", "--no-json-format", "--ledger"]
OLLAMA_VERSION = "0.34.0"
PORTS = (11434,)
GPU_GROUPS = ("0,1",)
PARALLEL_PER_SERVER = 1
TOKENIZER_URL = "https://registry.npmjs.org/@lenml/tokenizer-qwen2_5/-/tokenizer-qwen2_5-3.7.2.tgz"
TOKENIZER_SHA = "c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539"
TASKS = "harness/experiments/r4/sanity/tasks_v3.json"   # amendments R4-SANITY-2 (gate) and R4-SANITY-3 (fixes 1-4)
TASKS_SHA = "a187478c05ffad4c85d63f5f693f3e4213eafcb90f743082e30f5c8e546f28da"           # sha256 of the task list (json, sorted keys); also stored in the file
LAYER1_POOL = "harness/experiments/r4/sanity/layer1_pool.json"
SEEDS = (42,)
TURN_CAP = 28                         # R3a's
SESSION_HOURS = 11.5
STOP_MARGIN_MIN = 30                  # no run starts later than this before the limit (non-thinking runs: ~5-10 min)
DISK_CLEAN_GB = 5                     # under this before a repository (or during runs): clear the download caches
DISK_STOP_GB = 2                      # still under this after clearing: stop the session between runs
CELL_START = time.time()
CHECKPOINT_MIN = 10
WORK = "/kaggle/working"
SCE_DIR = f"{WORK}/SCE"
PUSH_WT = f"{WORK}/SCE_results"
RESULTS_PATH = "reports/r4/phase0"
BIG = "/tmp/r4p0"
CACHE = f"{BIG}/cache"
PUSH_RESULTS = True
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")
print("=" * 64)
print(f"PRISM R4 Phase 0 ({SUMMARIZER}) - stage {STAGE}, both arms, one seed")
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
print(f"[ok] code {CODE_COMMIT[:7]} {got}", flush=True)
if PUSH_RESULTS and not os.path.isdir(PUSH_WT):
    run(["git", "worktree", "add", "-f", "--detach", PUSH_WT, f"origin/{BRANCH}"], cwd=SCE_DIR, check=True)
OUT = os.path.join(SCE_DIR, RESULTS_PATH)
os.makedirs(OUT, exist_ok=True)
if run(["git", "cat-file", "-e", f"origin/{BRANCH}:{RESULTS_PATH}"], cwd=SCE_DIR, check=False).returncode == 0:
    run(f"git archive origin/{BRANCH} {RESULTS_PATH} | tar -x -C {SCE_DIR}", cwd=SCE_DIR, check=True)
    print(f"[resume] restored {sorted(os.listdir(OUT))[:20]}", flush=True)

print("\n[setup] Install prism, the tokenizer library and uv", flush=True)
run([sys.executable, "-m", "pip", "install", "-q", "-e", ".", "tokenizers", "uv", "packaging"], cwd=SCE_DIR, check=True)
child_env = dict(os.environ, PYTHONPATH=os.pathsep.join([SCE_DIR, os.path.join(SCE_DIR, "src")]),
                 PYTHONDONTWRITEBYTECODE="1",
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

print("\n[tasks] The sanity task list", flush=True)
doc = json.load(open(os.path.join(SCE_DIR, TASKS)))
got_sha = hashlib.sha256(json.dumps(doc["tasks"], sort_keys=True).encode()).hexdigest()
if got_sha != TASKS_SHA or doc.get("tasks_sha256") != TASKS_SHA:
    abort(f"the sanity task list is not the committed one: {got_sha}")
STASKS = doc["tasks"]
REPOS = doc["repo_urls"]
if {t["repo"] for t in STASKS} - set(REPOS):
    abort("a task's repository has no URL")
ALL_TASKS = STASKS
_fams, _queue = [], {}
for t in ALL_TASKS:
    if t["family"] not in _queue:
        _fams.append(t["family"])
    _queue.setdefault(t["family"], []).append(t)
_rr = []
while any(_queue.values()):
    for f in _fams:
        if _queue[f]:
            _rr.append(_queue[f].pop(0))
if STAGE not in STAGE_TASKS:
    abort(f"STAGE must be one of {list(STAGE_TASKS)}")
_n = STAGE_TASKS[STAGE] or len(_rr)
_pick = {t["id"] for t in _rr[:_n]}
STASKS = [t for t in ALL_TASKS if t["id"] in _pick]     # the stage's tasks, still in the list's (repository) order
N_RUNS = len(STASKS) * 2 * len(SEEDS)
print(f"[stage] {STAGE}: tasks {[t['id'] for t in _rr[:_n]]}", flush=True)
print(f"[ok] {len(STASKS)} tasks ({', '.join(sorted({t['family'] for t in STASKS}))}), {N_RUNS} runs, "
      f"sha {got_sha[:12]}", flush=True)
os.makedirs(BIG, exist_ok=True)


def free_gb():
    return shutil.disk_usage(BIG).free / 2 ** 30


def clear_caches(why):
    for d in (f"{CACHE}/py/uv-cache", f"{CACHE}/js/pnpm-store", f"{CACHE}/js/pnpm-home",
              os.path.expanduser("~/.cache/uv"), os.path.expanduser("~/.local/share/pnpm/store")):
        shutil.rmtree(d, ignore_errors=True)
    print(f"[disk] {why}: cleared the download caches, {free_gb():.1f} GB free", flush=True)


def run_ids(t):
    return [f"{t['id']}__{v}__{s}" for s in SEEDS for v in ("PRISM", "BASELINE")]


def finished(t):
    return all(os.path.exists(os.path.join(OUT, r + ".score.json")) for r in run_ids(t))


def fetch(name):
    path = f"{BIG}/repos/{name}"
    if not os.path.isdir(f"{path}/.git"):
        run(["git", "clone", "-q", REPOS[name], path], check=True, env=GIT_ENV)
    need = [t for t in STASKS if t["repo"] == name]
    if any(run(["git", "-C", path, "cat-file", "-e", t["commit"] + "^{commit}"], check=False).returncode for t in need):
        run(["git", "-C", path, "fetch", "-q", "origin"], check=True, env=GIT_ENV)
    missing = [t["id"] for t in need
               if run(["git", "-C", path, "cat-file", "-e", t["commit"] + "^{commit}"], check=False).returncode]
    if missing:
        abort(f"{name}: commits not found after fetching: {missing}")
    return path


js = [t for t in STASKS if t["language"] == "js" and not finished(t)]
if js:
    print("\n[smoke] JS/TS builder: install only, no test run", flush=True)
    clone = fetch(js[0]["repo"])
    smoke = run([sys.executable, "-m", "harness.experiments.r3.jsbuild", "smoke", clone, js[0]["parent"], "--cache",
                 f"{CACHE}/js", "--scratch", f"{BIG}/scratch"], cwd=SCE_DIR, env=child_env, check=False, capture=True)
    print(smoke.stdout[-3000:], smoke.stderr[-2000:], flush=True)
    open(os.path.join(OUT, "smoke.json"), "w").write(smoke.stdout.strip().splitlines()[-1] if smoke.stdout.strip() else "{}")
    if smoke.returncode == 2:
        push_results(f"r4 phase0: JS/TS smoke test FAILED (structural) @ {CODE_COMMIT[:7]}")
        abort("the JS/TS builder smoke test failed structurally - see smoke.json; nothing else was run")
    if smoke.returncode != 0:
        abort("the JS/TS builder smoke test failed on a network or package error - run the cell again")
    print("[ok] JS/TS builder installs on Kaggle", flush=True)

print("\n[setup] GPU check + apt", flush=True)
gpu = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], check=False, capture=True)
gpu_lines = [ln for ln in (gpu.stdout or "").strip().splitlines() if ln.strip()]
if sum("T4" in ln for ln in gpu_lines) < 2:
    abort(f"expected GPU T4 x2: {gpu_lines}")
print(f"[ok] GPU: {gpu_lines}", flush=True)
run("apt-get update -qq && apt-get install -y -qq zstd curl git", check=False)

print("\n[setup] Ollama + the model", flush=True)
if subprocess.run(["which", "ollama"], capture_output=True).returncode != 0:
    run(f"curl -L https://github.com/ollama/ollama/releases/download/v{OLLAMA_VERSION}/ollama-linux-amd64.tar.zst "
        "-o /tmp/ollama.tar.zst", check=True)
    run("tar -I zstd -xf /tmp/ollama.tar.zst -C /usr/local", check=True)
    run("chmod 755 /usr/local/bin/ollama", check=True)
for port, gpus in zip(PORTS, GPU_GROUPS):
    env = dict(os.environ, OLLAMA_HOST=f"127.0.0.1:{port}", CUDA_VISIBLE_DEVICES=gpus, OLLAMA_KEEP_ALIVE="4h",
               OLLAMA_NUM_PARALLEL=str(PARALLEL_PER_SERVER), OLLAMA_FLASH_ATTENTION="1", OLLAMA_KV_CACHE_TYPE="q8_0",
               OLLAMA_MAX_LOADED_MODELS="1", OLLAMA_CONTEXT_LENGTH="20480")
    try:
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
for port in PORTS:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/chat", data=json.dumps({
        "model": SUMMARIZER, "messages": [{"role": "user", "content": "Reply with {\"ok\": true}"}], "format": "json",
        "stream": False, "options": {"num_predict": 10}}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        print(f"[ok] port {port}: {json.loads(r.read())['message']['content']!r}", flush=True)


print("\n[smoke] one JSON tool call", flush=True)
_req = urllib.request.Request(f"http://127.0.0.1:{PORTS[0]}/api/chat", data=json.dumps({
    "model": SUMMARIZER, "stream": False, "format": "json",
    "options": {"num_predict": 256, "num_ctx": 8192, "temperature": 0.2},
    "messages": [{"role": "system", "content": 'Reply with exactly one JSON object: {"tool": "<name>", "args": {...}}. '
                  'No other text.'},
                 {"role": "user", "content": "Call the tool read_file with path a.py."}]}).encode(),
    headers={"Content-Type": "application/json"})
with urllib.request.urlopen(_req, timeout=900) as _r:
    _m = json.loads(_r.read())["message"]
try:
    _ok = json.loads(_m.get("content") or "{}").get("tool") == "read_file"
except Exception:
    _ok = False
open(os.path.join(OUT, "smoke_tool_call.json"), "w").write(json.dumps({"ok": _ok, "content": _m.get("content", "")[:300]}))
if not _ok:
    abort(f"the model did not return one JSON tool call: {_m}")
print("[ok] JSON tool call", flush=True)


def module(args, label, name, push_while=False):
    cmd = [sys.executable, "-m", name, *args]
    print(f"$ {' '.join(cmd)}", flush=True)
    start = time.time()
    with open(os.path.join(OUT, f"{label}.log"), "a") as log:
        proc = subprocess.Popen(cmd, cwd=SCE_DIR, env=child_env, stdout=log, stderr=subprocess.STDOUT)
        last_push = time.time()
        while proc.poll() is None:
            time.sleep(30)
            # mid-run guard: clear the caches only while no installer runs (every run installs into its own worktree)
            if free_gb() < DISK_CLEAN_GB and subprocess.run(["pgrep", "-x", "npm|pnpm|yarn|uv|node"],
                                                            capture_output=True).returncode != 0:
                clear_caches("during runs")
            if push_while and time.time() - last_push > CHECKPOINT_MIN * 60:
                push_results(f"r4 phase0 {STAGE}: {label} checkpoint @ {CODE_COMMIT[:7]}")
                last_push = time.time()
    print(open(os.path.join(OUT, f"{label}.log")).read()[-3000:], flush=True)
    print(f"[{label}] exit {proc.returncode} after {(time.time() - start) / 60:.0f} min", flush=True)
    return proc.returncode


def n_done():
    return sum(os.path.exists(os.path.join(OUT, r + ".score.json")) for t in STASKS for r in run_ids(t))


deadline = CELL_START + SESSION_HOURS * 3600 - STOP_MARGIN_MIN * 60
print(f"\n[p0] {STAGE}: {n_done()}/{N_RUNS} runs done", flush=True)
order = []                            # repositories in the task list's order
for t in STASKS:
    if t["repo"] not in order:
        order.append(t["repo"])
rc, failed = 0, False
for name in order:
    todo = [t for t in STASKS if t["repo"] == name and not finished(t)]
    if not todo:
        continue
    if free_gb() < DISK_CLEAN_GB:
        clear_caches(f"before {name}")
    if free_gb() < DISK_STOP_GB:
        print(f"[disk] {free_gb():.1f} GB free after clearing: stopping the session before {name}", flush=True)
        rc = 3
        break
    clone = fetch(name)
    part = os.path.join(OUT, f"tasks_{name}.json")
    json.dump({"tasks": todo}, open(part, "w"))
    r = module([part, OUT, "--cache-py", f"{CACHE}/py", "--cache-js", f"{CACHE}/js",
                "--base-url", f"http://127.0.0.1:{PORTS[0]}", "--seeds", *map(str, SEEDS), "--turn-cap", str(TURN_CAP),
                "--deadline", str(deadline), *MODEL_FLAGS, f"{name}={clone}"], f"runs_{name}", "harness.experiments.r3.pilot_run",
               push_while=True)
    os.remove(part)
    if r == 3:
        rc = 3
        break
    failed = failed or r != 0
    clear_caches(f"after {name}")      # between repositories, always
    if all(finished(t) for t in STASKS if t["repo"] == name):
        shutil.rmtree(clone, ignore_errors=True)
    push_results(f"r4 phase0 {STAGE}: {name} done, {n_done()}/{N_RUNS} runs @ {CODE_COMMIT[:7]}")

module([OUT, os.path.join(SCE_DIR, TASKS), "--seeds", *map(str, SEEDS), "--layer1-pool",
        os.path.join(SCE_DIR, LAYER1_POOL), "--out", os.path.join(OUT, "sanity_report.json")], "sanity_report",
       "harness.experiments.r4.sanity_report")
open(os.path.join(OUT, "progress.json"), "w").write(json.dumps({
    "stage": STAGE, "runs_done": n_done(), "runs_total": N_RUNS, "code": CODE_COMMIT, "tasks_sha256": TASKS_SHA, "model": SUMMARIZER,
    "model_flags": MODEL_FLAGS, "run_hours": round(sum(json.loads(l)["seconds"] for l in
                                                     open(os.path.join(OUT, "pilot_runs.jsonl"))) / 3600, 2)
    if os.path.exists(os.path.join(OUT, "pilot_runs.jsonl")) else None}, indent=1))
push_results(f"r4 phase0 {STAGE}: {n_done()}/{N_RUNS} runs @ {CODE_COMMIT[:7]} ({SUMMARIZER})")
print(f"\n[p0] {STAGE}: {n_done()}/{N_RUNS} runs done", flush=True)
if rc == 3:
    print("\n=== SESSION DONE: run the cell again in a new session to continue ===", flush=True)
elif failed:
    abort("some runs exited with an error - see pilot_runs.jsonl; run the cell again to retry them")
elif n_done() < N_RUNS:
    abort("runs are missing - run the cell again")
else:
    print(f"\n=== STAGE DONE: {STAGE} ({n_done()} runs) - report back before the next stage ===", flush=True)
