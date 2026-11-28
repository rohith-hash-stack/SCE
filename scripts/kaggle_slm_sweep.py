# Kaggle runner - PRISM 8-arm sweep on a local SLM (Ollama, Kaggle GPUs)
#
# One Kaggle session = one corpus x one 5-seed batch:
#     Batch 1: SEEDS = "1,2,3,4,5"     Batch 2: SEEDS = "6,7,8,9,10"
#     fastapi/trpc: 25 tasks x 8 arms x 5 seeds = 1,000 cells
#     express/django: 20 tasks x 8 arms x 5 seeds = 800 cells
#
# Paste this whole file into one Kaggle notebook cell. Settings: GPU T4 x2,
# Internet ON, and a Kaggle secret GITHUB_TOKEN with push access to the repo.
# Edit REPO and SEEDS below, then run.
#
# Resumable: the session checks out the branch (including any cells
# already pushed), and the sweep skips every cell that already has an ok
# record. Progress is committed and pushed every PUSH_EVERY_MIN minutes and
# at the end, so a session that hits Kaggle's time limit loses at most
# that much work - just re-run the cell with the same REPO/SEEDS.
#
# Don't run two sessions on the same REPO at the same time: both would
# append to the same cells.jsonl and their pushes would conflict. Different
# repos in parallel sessions are fine.

import json
import os
import subprocess
import sys
import time
import urllib.request

# --------------------------------------------------------------------- #
# User-editable
# --------------------------------------------------------------------- #
REPO = "fastapi"  # fastapi | trpc | express | django
SEEDS = "1,2,3,4,5"  # batch 1; batch 2 = "6,7,8,9,10"
ARMS = "all"  # "all" = the 8 protocol arms; or comma-separated engine ids
TASKS = ""  # "" = every task; or space-separated task ids (e.g. a smoke subset)
# PageRank repo-map smoke test (5 FastAPI tasks, seed 1) - uncomment:
# REPO, SEEDS, ARMS = "fastapi", "1", "baseline_pagerank_repomap"
# TASKS = ("fastapi_t02_001_dependant_tree_construction fastapi_t02_002_solve_dependencies_runtime_resolution "
#          "fastapi_t02_003_request_params_coercion fastapi_t02_004_route_registration_pipeline "
#          "fastapi_t02_005_request_validation_error_response")

MODEL = "qwen2.5-coder:7b-instruct-q8_0"
TEMPERATURE = 0.4
NUM_CTX = 24576  # context window per request; prompts reach ~9-12K tokens
PARALLEL_PER_GPU = 2  # concurrent requests per Ollama server (one server per GPU)
PUSH_EVERY_MIN = 20
MAX_RUNTIME_HOURS = 11.3  # stop and push before Kaggle's 12h limit
OLLAMA_VERSION = "0.34.0"  # same pin as kaggle/pilot_4_cell.py; "" = latest

BRANCH = "claude/prism-final-empirical-8arms-kp3tpt"
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
OUT_ROOT = "reports/final_sweep/slm_qwen7b"  # kept apart from the gpt-4o-mini cells in reports/final_sweep/full/
if ARMS != "all" or TASKS:
    # Non-protocol runs (smoke tests, experimental arms) never write into the
    # sweep's own cell files.
    OUT_ROOT = "reports/final_sweep/smoke_qwen7b"
SCE_DIR = "/kaggle/working/SCE"
EXPECTED_TASKS = {"fastapi": 25, "trpc": 25, "express": 20, "django": 20}

START = time.time()
if REPO not in EXPECTED_TASKS:
    raise SystemExit(f"REPO must be one of {sorted(EXPECTED_TASKS)}, got {REPO!r}")
OUT_DIR = f"{OUT_ROOT}/{REPO}"
print(f"=== PRISM SLM sweep: repo={REPO} seeds={SEEDS} model={MODEL} T={TEMPERATURE}", flush=True)


def run(cmd, cwd=None, check=True, env=None, capture=False):
    shown = cmd if isinstance(cmd, str) else " ".join(cmd)
    print("$", shown.replace(os.environ.get("GITHUB_TOKEN", "\0"), "***"), flush=True)
    return subprocess.run(cmd, cwd=cwd, check=check, env=env, shell=isinstance(cmd, str),
                          capture_output=capture, text=True)


def secret(name):
    if os.environ.get(name):
        return os.environ[name]
    try:
        from kaggle_secrets import UserSecretsClient

        return UserSecretsClient().get_secret(name)
    except Exception:
        return None


# --------------------------------------------------------------------- #
# 1. GitHub token
# --------------------------------------------------------------------- #
github_token = secret("GITHUB_TOKEN")
if not github_token:
    raise SystemExit("GITHUB_TOKEN is not set (add it under Add-ons > Secrets)")
os.environ["GITHUB_TOKEN"] = github_token  # only so run() can mask it in logs

# --------------------------------------------------------------------- #
# 2. GPUs, Ollama (one server per GPU, fixed context window)
# --------------------------------------------------------------------- #
gpus = run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture=True).stdout.split("\n")
gpus = [g for g in gpus if g.strip()]
if not gpus:
    raise SystemExit("no GPU visible - set Accelerator to GPU T4 x2")
print(f"[ok] GPUs: {gpus}", flush=True)

run("apt-get -y -qq install zstd > /dev/null", check=False)
if subprocess.run(["which", "ollama"], capture_output=True).returncode != 0:
    pin = f"OLLAMA_VERSION={OLLAMA_VERSION} " if OLLAMA_VERSION else ""
    run(f"curl -fsSL https://ollama.com/install.sh | {pin}sh")

ports = [11434 + i for i in range(len(gpus))]
for i, port in enumerate(ports):
    env = dict(
        os.environ,
        CUDA_VISIBLE_DEVICES=str(i),
        OLLAMA_HOST=f"127.0.0.1:{port}",
        OLLAMA_CONTEXT_LENGTH=str(NUM_CTX),
        OLLAMA_NUM_PARALLEL=str(PARALLEL_PER_GPU),
        OLLAMA_KEEP_ALIVE="-1",
        OLLAMA_MODELS="/kaggle/working/ollama-models",
    )
    subprocess.Popen(["ollama", "serve"], env=env, stdout=open(f"/kaggle/working/ollama_{port}.log", "w"),
                     stderr=subprocess.STDOUT)


def http_json(url, payload=None, timeout=600):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


for port in ports:
    for _ in range(60):
        try:
            http_json(f"http://127.0.0.1:{port}/api/version", timeout=5)
            break
        except Exception:
            time.sleep(2)
    else:
        raise SystemExit(f"Ollama on port {port} did not come up - see /kaggle/working/ollama_{port}.log")
print(f"[ok] Ollama servers up on ports {ports}", flush=True)

run(["ollama", "pull", MODEL], env=dict(os.environ, OLLAMA_HOST=f"127.0.0.1:{ports[0]}"))

# Probe: a long prompt must reach the model intact on every server. If the
# context window were not applied, Ollama would silently drop the prompt
# head and report far fewer prompt tokens than were sent.
probe_words = 12000
for port in ports:
    t0 = time.time()
    reply = http_json(f"http://127.0.0.1:{port}/v1/chat/completions", {
        "model": MODEL,
        "messages": [{"role": "user", "content": "apple " * probe_words + "\nReply with the single word OK."}],
        "max_tokens": 5, "temperature": 0, "options": {"num_ctx": NUM_CTX},
    })
    seen = reply["usage"]["prompt_tokens"]
    if seen < 0.9 * probe_words:
        raise SystemExit(f"port {port}: sent ~{probe_words} tokens but the server saw {seen} - "
                         f"context window not applied; do not run the sweep like this")
    ollama_log = open(f"/kaggle/working/ollama_{port}.log").read()
    on_gpu = "CUDA" in ollama_log or "cuda" in ollama_log
    print(f"[ok] port {port}: {seen} prompt tokens accepted, warm-up {time.time() - t0:.0f}s, "
          f"CUDA {'detected' if on_gpu else 'NOT detected in log'}", flush=True)
    if not on_gpu:
        raise SystemExit(f"Ollama on port {port} is not using the GPU - see /kaggle/working/ollama_{port}.log")

# --------------------------------------------------------------------- #
# 3. Code + prior results (the branch carries both)
# --------------------------------------------------------------------- #
clone_url = REPO_URL.replace("https://", f"https://x-access-token:{github_token}@")
if not os.path.isdir(SCE_DIR):
    run(["git", "clone", "--branch", BRANCH, clone_url, SCE_DIR])
else:
    run(["git", "fetch", "origin", BRANCH], cwd=SCE_DIR)
    run(["git", "checkout", "-B", BRANCH, f"origin/{BRANCH}"], cwd=SCE_DIR)
run(["git", "config", "user.name", "prism-kaggle"], cwd=SCE_DIR)
run(["git", "config", "user.email", "prism-kaggle@users.noreply.github.com"], cwd=SCE_DIR)
print(f"[ok] {BRANCH} at {run(['git', 'rev-parse', '--short', 'HEAD'], cwd=SCE_DIR, capture=True).stdout.strip()}")

run([sys.executable, "-m", "pip", "install", "-q", "-e", ".[dev,bench]", "pyarrow"], cwd=SCE_DIR)

# --------------------------------------------------------------------- #
# 4. Corpus + task verification
# --------------------------------------------------------------------- #
corpus = run([sys.executable, "-c", f"from benchmarks.corpora.resolver import resolve; print(resolve({REPO!r}))"],
             cwd=SCE_DIR, capture=True).stdout.strip().splitlines()[-1]
print(f"[ok] corpus {REPO} at {corpus}", flush=True)
if REPO == "express":
    corpus_root = os.path.join(SCE_DIR, ".benchmarks", "corpora", "express")
    run("npm install --omit=dev --no-audit --no-fund --ignore-scripts", cwd=corpus_root)
    if not os.path.isdir(os.path.join(corpus_root, "node_modules", "etag")):
        raise SystemExit("express node_modules/etag missing after npm install - external-dependency tasks would break")
n_tasks = int(run([sys.executable, "-c",
                   "from benchmarks.final_sweep.runner import load_debug_tasks as l; "
                   f"print(len(l({REPO!r})))"], cwd=SCE_DIR, capture=True).stdout.strip().splitlines()[-1])
if n_tasks != EXPECTED_TASKS[REPO]:
    raise SystemExit(f"{REPO}: {n_tasks} debug tasks loaded, expected {EXPECTED_TASKS[REPO]}")
print(f"[ok] {n_tasks} {REPO} tasks x 8 arms x {len(SEEDS.split(','))} seeds", flush=True)


# --------------------------------------------------------------------- #
# 5. Run, pushing progress periodically
# --------------------------------------------------------------------- #
def push(label):
    out = os.path.join(SCE_DIR, OUT_DIR)
    if not os.path.isdir(out):
        return
    run(["git", "add", "-f", OUT_DIR], cwd=SCE_DIR, check=False)
    if run(["git", "diff", "--cached", "--quiet"], cwd=SCE_DIR, check=False).returncode == 0:
        return  # nothing new
    run(["git", "commit", "-q", "-m", f"SLM sweep progress: {REPO} seeds {SEEDS} ({label})"], cwd=SCE_DIR, check=False)
    for delay in (0, 2, 4, 8, 16):
        time.sleep(delay)
        # Other sessions may have pushed other repos' results meanwhile; their
        # files never overlap this session's OUT_DIR, so a rebase is clean.
        run(["git", "pull", "-q", "--rebase", "origin", BRANCH], cwd=SCE_DIR, check=False)
        if run(["git", "push", "-q", "origin", f"HEAD:{BRANCH}"], cwd=SCE_DIR, check=False).returncode == 0:
            print(f"[ok] pushed progress ({label})", flush=True)
            return
    print("[warn] push failed after retries - results are still on local disk", flush=True)


cmd = [
    sys.executable, "-m", "benchmarks.final_sweep.runner",
    "--repo", REPO, "--seeds", SEEDS, "--arms", ARMS, *(["--tasks", *TASKS.split()] if TASKS else []),
    "--model", MODEL, "--temperature", str(TEMPERATURE),
    "--base-url", ",".join(f"http://127.0.0.1:{p}/v1" for p in ports),
    "--num-ctx", str(NUM_CTX), "--workers", str(PARALLEL_PER_GPU * len(ports)),
    "--out", OUT_DIR,
]
env = dict(os.environ, OPENAI_API_KEY="ollama")
env.pop("GITHUB_TOKEN", None)
print("$", " ".join(cmd), flush=True)
proc = subprocess.Popen(cmd, cwd=SCE_DIR, env=env)
last_push = time.time()
stopped_early = False
while proc.poll() is None:
    time.sleep(30)
    if time.time() - START > MAX_RUNTIME_HOURS * 3600:
        print("[warn] approaching Kaggle's time limit - stopping; re-run this cell to resume", flush=True)
        proc.terminate()
        proc.wait(timeout=120)
        stopped_early = True
        break
    if time.time() - last_push > PUSH_EVERY_MIN * 60:
        push("periodic")
        last_push = time.time()

push("stopped early" if stopped_early else f"runner exit {proc.returncode}")
summary = os.path.join(SCE_DIR, OUT_DIR, "summary.md")
if os.path.exists(summary):
    print(open(summary).read())
print(f"=== done in {(time.time() - START) / 3600:.1f}h: {REPO} seeds {SEEDS}"
      f"{' (INCOMPLETE - re-run to resume)' if stopped_early or proc.returncode not in (0, 1) else ''}", flush=True)
