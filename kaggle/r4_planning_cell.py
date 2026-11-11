# ============================================================
# PRISM R4 planning investigation (harness/experiments/r4/PLANNING_INVESTIGATION.md): does planning or thinking help?
# 3 sanity tasks (instructor delete, pydantic delete, typer feature) x model variants, PRISM arm, seed 42, R3a's loop
# and turn cap 28. The "Qwen 14B, current prompt" row is the sanity run itself (not re-run).
#
# Variants, in this order (each model is pulled, run on the 3 tasks, then removed to free the disk):
#   1. qwen14b-plan       qwen2.5-coder:14b-instruct-q8_0, each reply states a one-sentence plan (in the JSON);
#   2. qwen3-14b-nothink  qwen3:14b-q8_0, thinking off (the control for 3: the same weights);
#   3. qwen3-14b-think    qwen3:14b-q8_0, thinking on (answer limit 12,288 tokens, thinking included);
#   4. qwq-32b            qwq:32b (Q4_K_M, ~20 GB over both T4s), thinking on. Slowest; last, so it can be cut.
# Before each model's runs, one smoke call checks it answers a JSON tool call with that thinking setting; if forcing
# JSON breaks it, its runs go without the JSON constraint (recorded in smoke_<variant>.json).
# No run starts within STOP_MARGIN_MIN of the session limit; run the cell again in a new session to continue (finished
# runs are kept). "=== ALL DONE ===" at the end means finished.
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
CODE_COMMIT = "f7a7a0f9eb75fd2194cc5bea42b2391d325f3740"
CODE_TREES = {"src": "82963a7414f3425c789548097ae3cc1d88402091",
              "harness": "87f4840eaea15330aa36f49453368ee29817f86f"}
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
EXPECTED_GITHUB_USER = "rohith-hash-stack"
SUMMARIZER = "qwen2.5-coder:14b-instruct-q8_0"   # the locked model (R3a's)
OLLAMA_VERSION = "0.34.0"
PORTS = (11434,)
GPU_GROUPS = ("0,1",)
PARALLEL_PER_SERVER = 1
TOKENIZER_URL = "https://registry.npmjs.org/@lenml/tokenizer-qwen2_5/-/tokenizer-qwen2_5-3.7.2.tgz"
TOKENIZER_SHA = "c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539"
TASKS = "harness/experiments/r4/sanity/tasks.json"
TASKS_SHA = "8d89c2d27cd9e2dddcf377b26edfc8be68f44fea83f848847f1fcddc8caf1dec"           # sha256 of the task list (json, sorted keys); also stored in the file
TASK_IDS = ("r4s-delete-02", "r4s-delete-06", "r4s-feature-04")
VARIANTS = ("qwen14b-plan", "qwen3-14b-nothink", "qwen3-14b-think", "qwq-32b")
MODELS = {"qwen14b-plan": "qwen2.5-coder:14b-instruct-q8_0", "qwen3-14b-nothink": "qwen3:14b-q8_0",
          "qwen3-14b-think": "qwen3:14b-q8_0", "qwq-32b": "qwq:32b"}
THINK = {"qwen14b-plan": None, "qwen3-14b-nothink": False, "qwen3-14b-think": True, "qwq-32b": True}
TURN_CAP = 28                         # R3a's
SESSION_HOURS = 11.5
STOP_MARGIN_MIN = 45                  # no run starts later than this before the limit
DISK_CLEAN_GB = 5                     # under this before a repository (or during runs): clear the download caches
DISK_STOP_GB = 2                      # still under this after clearing: stop the session between runs
CELL_START = time.time()
CHECKPOINT_MIN = 10
WORK = "/kaggle/working"
SCE_DIR = f"{WORK}/SCE"
PUSH_WT = f"{WORK}/SCE_results"
RESULTS_PATH = "reports/r4/planning"
BIG = "/tmp/r4p"
CACHE = f"{BIG}/cache"
PUSH_RESULTS = True
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")
print("=" * 64)
print("PRISM R4 planning investigation")
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
STASKS = [t for t in doc["tasks"] if t["id"] in TASK_IDS]
REPOS = doc["repo_urls"]
if {t["repo"] for t in STASKS} - set(REPOS):
    abort("a task's repository has no URL")
N_RUNS = len(STASKS) * len(VARIANTS)
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


def finished_run(var, t):
    return os.path.exists(os.path.join(OUT, var, f"{t['id']}__PRISM__42.score.json"))


def finished(t):
    return all(finished_run(v, t) for v in VARIANTS)


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
        push_results(f"r4 sanity: JS/TS smoke test FAILED (structural) @ {CODE_COMMIT[:7]}")
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


def pull(model):
    for attempt in range(8):
        if model in (run(["ollama", "list"], capture=True, env=first).stdout or ""):
            return
        if run(["ollama", "pull", model], check=False, env=first).returncode == 0:
            return
        time.sleep(min(15 * 2 ** attempt, 240))
    abort(f"could not pull {model}")


def chat(model, think, json_format):
    body = {"model": model, "stream": False, "options": {"num_predict": 4096, "num_ctx": 8192, "temperature": 0.2},
            "messages": [{"role": "system", "content": 'Reply with exactly one JSON object: {"tool": "<name>", '
                          '"args": {...}}. No other text.'},
                         {"role": "user", "content": "Call the tool read_file with path a.py."}]}
    if json_format:
        body["format"] = "json"
    if think is not None:
        body["think"] = think
    req = urllib.request.Request(f"http://127.0.0.1:{PORTS[0]}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=1800) as r:
            m = json.loads(r.read())
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:400]}
    text = m.get("message", {}).get("content", "")
    thinking = m.get("message", {}).get("thinking") or ("<think>" in text)
    tail = text.rsplit("</think>", 1)[-1]
    try:
        ok = json.loads(tail[tail.index("{"):tail.rindex("}") + 1]).get("tool") == "read_file"
    except Exception:
        ok = False
    return {"ok": ok, "thinking": bool(thinking), "seconds": round(time.time() - t, 1), "tokens": m.get("eval_count"),
            "tok_per_s": round((m.get("eval_count") or 0) / max(0.1, (m.get("eval_duration") or 1) / 1e9), 1),
            "text": text[-300:]}


deadline = CELL_START + SESSION_HOURS * 3600 - STOP_MARGIN_MIN * 60
CLONES = {name: fetch(name) for name in sorted({t["repo"] for t in STASKS})}
rc, failed = 0, False
for var in VARIANTS:
    if all(finished_run(var, t) for t in STASKS):
        continue
    if time.time() >= deadline:
        rc = 3
        break
    if free_gb() < DISK_CLEAN_GB:
        clear_caches(f"before {var}")
    pull(MODELS[var])
    smoke_path = os.path.join(OUT, f"smoke_{var}.json")
    smoke = {"json": chat(MODELS[var], THINK[var], True)}
    extra = []
    if not smoke["json"]["ok"]:                    # forcing JSON breaks this model/setting: try without it
        smoke["plain"] = chat(MODELS[var], THINK[var], False)
        if not smoke["plain"]["ok"]:
            json.dump(smoke, open(smoke_path, "w"), indent=1)
            print(f"[smoke] {var}: no usable tool call either way - skipped: {smoke}", flush=True)
            failed = True
            continue
        extra = ["--no-json-format"]
    json.dump(smoke, open(smoke_path, "w"), indent=1)
    print(f"[smoke] {var}: {smoke}", flush=True)
    with open(os.path.join(OUT, f"runs_{var}.log"), "a") as log:
        cmd = [sys.executable, "-m", "harness.experiments.r4.planning_run", os.path.join(SCE_DIR, TASKS), OUT,
               "--variants", var, "--cache-py", f"{CACHE}/py", "--cache-js", f"{CACHE}/js",
               "--base-url", f"http://127.0.0.1:{PORTS[0]}", "--deadline", str(deadline), *extra,
               *[f"{n}={p}" for n, p in CLONES.items()]]
        print("$ " + " ".join(cmd), flush=True)
        proc = subprocess.Popen(cmd, cwd=SCE_DIR, env=child_env, stdout=log, stderr=subprocess.STDOUT)
        last_push = time.time()
        while proc.poll() is None:
            time.sleep(30)
            if time.time() - last_push > CHECKPOINT_MIN * 60:
                push_results(f"r4 planning: {var} checkpoint @ {CODE_COMMIT[:7]}")
                last_push = time.time()
    print(open(os.path.join(OUT, f"runs_{var}.log")).read()[-2500:], flush=True)
    if proc.returncode == 3:
        rc = 3
        break
    failed = failed or proc.returncode != 0
    if var != VARIANTS[-1] and MODELS[var] not in [MODELS[v] for v in VARIANTS[VARIANTS.index(var) + 1:]]:
        run(["ollama", "rm", MODELS[var]], check=False, env=first)      # free the disk for the next model
    push_results(f"r4 planning: {var} done @ {CODE_COMMIT[:7]}")

rep = run([sys.executable, "-m", "harness.experiments.r4.planning_report", OUT, "--sanity",
           os.path.join(SCE_DIR, "reports/r4/sanity")], cwd=SCE_DIR, env=child_env, check=False, capture=True)
print(rep.stdout, rep.stderr[-1500:], flush=True)
open(os.path.join(OUT, "planning_report.md"), "w").write(rep.stdout)
n_done = sum(finished_run(v, t) for v in VARIANTS for t in STASKS)
push_results(f"r4 planning: {n_done}/{N_RUNS} runs @ {CODE_COMMIT[:7]}")
if rc == 3:
    print("\n=== SESSION DONE: run the cell again in a new session to continue ===", flush=True)
elif failed:
    abort("some runs or a smoke check failed - see planning_runs.jsonl and smoke_*.json")
else:
    print("\n=== ALL DONE ===", flush=True)
