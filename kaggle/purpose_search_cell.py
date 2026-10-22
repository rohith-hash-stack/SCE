# ============================================================
# PRISM purpose-enriched search - Kaggle, self-contained cell
# This configuration: held-out set 3 (express + trpc, 50 JavaScript/TypeScript
# questions), 14B summarizer, card/prompt VARIANT v2 (no axes line in the
# card, plain-English prompt that forbids identifiers, callee lists, axis
# labels and filler). First a lint gate: the 43 worst v1 offenders
# (purpose_search/lint_sample.json) are summarized and the run stops if any
# lint rate exceeds LINT_GATE. FIELD_WEIGHTS stay frozen at 0.5,1.0. Set 3
# was already seen twice (7B, 14B v1): a diagnostic, not a fresh validation.
# Paste this whole file into ONE Kaggle notebook cell and Run.
# Settings: Accelerator GPU T4 x2, Internet ON, secret GITHUB_TOKEN.
#
# Summarizer: a local open-weight model (SUMMARIZER, Ollama), one server per
# GPU group (GPU_GROUPS), no API quota. For every function/method in NLQIndex's candidate pool a
# context card (harness/experiments/purpose_search/cards.py) is summarized in
# batches of 12 with the same prompt as the earlier Gemini run; then
#   held-out set 2 (60 questions): fastapi_template + styleguide
#   Q2E unseeded behavioural questions q09-q14: fastapi + django (full index)
# are ranked by BASE (the shipped NLQIndex) and by +RES / +SUM / +SUM+RES
# (its BM25 documents extended by static resources and/or the summary).
# Output: RESULTS_PATH/SUMMARY.md - Recall@5 / Recall@20 / pool recall / MRR
# per arm, the q09-q14 subject ranks (q11 was rank 48, q13 rank 30) and pool
# recall, identifiability and query matching on the summaries alone.
#
# Checks out CODE_COMMIT of BRANCH and verifies its tree hashes. Results and
# the summary caches are pushed to RESULTS_PATH on BRANCH after each repo
# from a separate clean worktree; re-running resumes from the pushed caches.
# Expected time on T4 x2: set 3 (~1.4k functions) with the 14B about 35-45 min
# plus setup (the 7B took ~10 min of LLM time); the full set-2 + Q2E run with
# the 7B was about 2-2.5 h (Django ~14.9k).
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
CODE_COMMIT = "630f8ce065f18470faee01bdd99c1f8f82303aed"
CODE_TREES = {"src": "36c9fb5fb938051c6ddea3e8ddad272459f8bf27",
              "harness": "3cd485606dd780957d7474b95a1e31c0e3aa6cd3",
              "benchmarks": "293edfba1be06149afde2250d3d91a26386598cf"}
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
EXPECTED_GITHUB_USER = "rohith-hash-stack"
SUMMARIZER = "qwen2.5-coder:14b-instruct-q8_0"   # the 7B set-3 run used qwen2.5-coder:7b-instruct-q8_0
OLLAMA_VERSION = "0.34.0"
# 14B q8_0 (~15.7 GB) does not fit one 16 GB T4: one server across both GPUs.
# The 7B runs used PORTS = (11434, 11435), GPU_GROUPS = ("0", "1") - one server per T4.
PORTS = (11434,)
GPU_GROUPS = ("0,1",)
PARALLEL_PER_SERVER = 2
REPOS = ("express", "trpc")     # set 3; the set-2 / Q2E run used ("fastapi_template", "styleguide", "fastapi", "django")
FIELD_WEIGHTS = "0.5,1.0"       # frozen: never tuned on set 3
VARIANT = "v2"                  # card + prompt version; the earlier runs were v1
LINT_GATE = 0.05                # max share of gate-sample summaries per lint flag
SCOPE = {"django": "full"}      # e.g. {"django": "top:300"} for the cheaper re-rank design
WORK = "/kaggle/working"
SCE_DIR = f"{WORK}/SCE"
PUSH_WT = f"{WORK}/SCE_results"
APPS_DIR = f"{WORK}/apps"
SITES_DIR = f"{WORK}/sites"
RESULTS_PATH = "reports/purpose_search_set3_14b_v2"
PUSH_RESULTS = True
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")

print("=" * 64)
print("PRISM purpose-enriched search (local summarizer)")
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
    """Copy OUT (results + summary caches, not the dependency sqlite files)
    into a clean worktree of BRANCH and push it there."""
    if not PUSH_RESULTS:
        return False
    for attempt in range(max_attempts):
        run(["git", "fetch", "origin", BRANCH], cwd=SCE_DIR, check=False, env=GIT_ENV)
        run(["git", "checkout", "-q", "--detach", f"origin/{BRANCH}"], cwd=PUSH_WT, check=True)
        dst = os.path.join(PUSH_WT, RESULTS_PATH)
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(OUT, dst, ignore=shutil.ignore_patterns("*.sqlite", "*.log"))
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


print("\n[setup 1/8] GitHub token", flush=True)
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

print("\n[setup 2/8] GPU check + apt", flush=True)
gpu = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], check=False, capture=True)
gpu_lines = [ln for ln in (gpu.stdout or "").strip().splitlines() if ln.strip()]
if sum("T4" in ln for ln in gpu_lines) < 2:
    abort(f"expected GPU T4 x2: {gpu_lines}")
print(f"[ok] GPU: {gpu_lines}", flush=True)
run("apt-get update -qq && apt-get install -y -qq zstd curl git", check=False)

print("\n[setup 3/8] Ollama: one server per GPU + the summarizer model", flush=True)
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
for port in PORTS:  # warm both servers (loads the model onto each GPU)
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/chat", data=json.dumps({
        "model": SUMMARIZER, "messages": [{"role": "user", "content": "Reply with {\"ok\": true}"}], "format": "json",
        "stream": False, "options": {"num_predict": 10}}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        print(f"[ok] port {port}: {json.loads(r.read())['message']['content']!r}", flush=True)

print("\n[setup 4/8] Clone and check out the code commit", flush=True)
clone_url = REPO_URL.replace("https://", f"https://x-access-token:{github_token}@")
if not os.path.isdir(f"{SCE_DIR}/.git"):
    run(["git", "clone", clone_url, SCE_DIR], check=True, env=GIT_ENV)
run(["git", "remote", "set-url", "origin", clone_url], cwd=SCE_DIR)
run(["git", "fetch", "origin", BRANCH], cwd=SCE_DIR, check=True, env=GIT_ENV)
run(["git", "checkout", "-q", "-f", "--detach", CODE_COMMIT], cwd=SCE_DIR, check=True)
run(["git", "clean", "-fdx", "-e", ".benchmarks", "-e", RESULTS_PATH], cwd=SCE_DIR, check=True)
got = {p: run(["git", "rev-parse", f"HEAD:{p}"], cwd=SCE_DIR, capture=True).stdout.strip() for p in CODE_TREES}
if got != CODE_TREES:
    abort(f"checked-out code differs from the reviewed commit: {got}")
print(f"[ok] code {CODE_COMMIT[:7]} {got}", flush=True)
if PUSH_RESULTS and not os.path.isdir(PUSH_WT):
    run(["git", "worktree", "add", "-f", "--detach", PUSH_WT, f"origin/{BRANCH}"], cwd=SCE_DIR, check=True)
OUT = os.path.join(SCE_DIR, RESULTS_PATH)
os.makedirs(OUT, exist_ok=True)
if run(["git", "cat-file", "-e", f"origin/{BRANCH}:{RESULTS_PATH}"], cwd=SCE_DIR, check=False).returncode == 0:
    listing = run(["git", "ls-tree", "--name-only", f"origin/{BRANCH}:{RESULTS_PATH}"], cwd=SCE_DIR, capture=True).stdout
    for name in listing.split():
        data = run(["git", "show", f"origin/{BRANCH}:{RESULTS_PATH}/{name}"], cwd=SCE_DIR, capture=True).stdout
        with open(os.path.join(OUT, name), "w") as fh:
            fh.write(data)
    print(f"[resume] restored {listing.split()}", flush=True)

print("\n[setup 5/8] Python deps + tests", flush=True)
run([sys.executable, "-m", "pip", "install", "-q", "-e", ".[dev,bench]", "jsonschema"], cwd=SCE_DIR, check=True)
child_env = dict(os.environ, PYTHONPATH=os.pathsep.join([SCE_DIR, os.path.join(SCE_DIR, "src")]),
                 CUDA_VISIBLE_DEVICES="", HARNESS_WORKDIR=WORK, PYTHONDONTWRITEBYTECODE="1")
child_env.pop("GITHUB_TOKEN", None)
run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_purpose_search.py"],
    cwd=SCE_DIR, env=child_env, check=True)

print("\n[setup 6/8] Q2E corpora", flush=True)
for c in [r for r in REPOS if r in ("fastapi", "django")]:
    root = run([sys.executable, "-c", f"from benchmarks.corpora.resolver import resolve; print(resolve({c!r}))"],
               cwd=SCE_DIR, env=child_env, capture=True, check=True).stdout.strip().splitlines()[-1]
    print(f"[ok] {c}: {root}", flush=True)

print("\n[setup 7/8] Held-out repos at their pinned commits + pinned dependencies", flush=True)
HELD_OUT = {}
for s in ("set2", "set3"):
    HELD_OUT.update(json.load(open(f"{SCE_DIR}/harness/experiments/heldout_retrieval/{s}/dataset.json"))["repos"])
for repo, spec in [(r, HELD_OUT[r]) for r in REPOS if r in HELD_OUT]:
    dst = os.path.join(APPS_DIR, repo)
    if not os.path.isdir(f"{dst}/.git"):
        run(["git", "clone", "-q", spec["url"], dst], check=True, env=GIT_ENV)
    run(["git", "checkout", "-q", "-f", spec["commit"]], cwd=dst, check=True)
    head = run(["git", "rev-parse", "HEAD"], cwd=dst, capture=True).stdout.strip()
    if head != spec["commit"]:
        abort(f"{repo} at {head}, expected {spec['commit']}")
    site = os.path.join(SITES_DIR, repo)
    lock = f"{SCE_DIR}/harness/experiments/purpose_search/locks/{repo}.txt"
    if not os.path.exists(lock):  # JS/TS: dependency summaries are Python-only, the site stays empty
        os.makedirs(site, exist_ok=True)
    elif not os.path.isdir(site):
        r = run([sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--target", site, "-r", lock], check=False)
        if r.returncode != 0:  # install what can be installed; a missing package only thins dependency summaries
            for line in open(lock).read().split():
                run([sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--target", site, line], check=False)
    print(f"[ok] {repo} @ {head[:7]}, deps in {site}", flush=True)

print("\n[setup 8/8] Summarizer smoke test (one card)", flush=True)
run([sys.executable, "-c",
     "from harness.experiments.purpose_search.run import LLM, VARIANTS; "
     f"print(LLM('ollama', {SUMMARIZER!r}, {BASE_URLS!r}.split(',')).json(VARIANTS[{VARIANT!r}][1].format(n=1, cards="
     "'Qualified Name: app.crud.create_item\\nDirect Callees: unresolved calls on parameters/self: session.add, "
     "session.commit\\nDocstring: Create new item.'), 200))"], cwd=SCE_DIR, env=child_env, check=True)

def run_args(repo):
    cmd = [sys.executable, "-m", "harness.experiments.purpose_search.run", "--repo", repo, "--out-dir", OUT,
           "--backend", "ollama", "--model", SUMMARIZER, "--base-urls", BASE_URLS,
           "--threads", str(len(PORTS) * PARALLEL_PER_SERVER), "--scope", SCOPE.get(repo, "full"),
           "--field-weights", FIELD_WEIGHTS, "--variant", VARIANT]
    return cmd + (["--apps-dir", APPS_DIR, "--sites-dir", SITES_DIR] if repo in HELD_OUT else [])


print("\n[gate] lint the worst v1 offenders under the configured variant", flush=True)
sample = json.load(open(f"{SCE_DIR}/harness/experiments/purpose_search/lint_sample.json"))
gate = {}
for repo in [r for r in REPOS if r in sample]:
    run(run_args(repo) + ["--only", ",".join(sample[repo])], cwd=SCE_DIR, env=child_env, check=True)
    gate[repo] = json.load(open(os.path.join(OUT, f"{repo}_only.json")))
    print(f"[gate] {repo}: {gate[repo]['lint']}", flush=True)
    for q, v in list(gate[repo]["summaries"].items())[:5]:
        print(f"    {q.rsplit('.', 1)[-1]}: {v['summary']}", flush=True)
bad = {f"{r}.{k}": v for r, g in gate.items() for k, v in g["lint"].items() if k not in ("any", "n") and v > LINT_GATE}
if bad:
    push_results(f"Purpose-enriched search: lint gate FAILED {bad} @ {CODE_COMMIT[:7]} ({SUMMARIZER}, {VARIANT})")
    abort(f"lint gate failed (> {LINT_GATE}): {bad} - see {RESULTS_PATH}/<repo>_only.json")
print(f"[gate] passed (every lint rate <= {LINT_GATE})", flush=True)

START = time.time()
for repo in REPOS:
    cmd = run_args(repo)
    print(f"\n[run] {repo}", flush=True)
    r = run(cmd, cwd=SCE_DIR, env=child_env, check=False)
    print(f"[run] {repo} exit {r.returncode} after {(time.time() - START) / 60:.0f} min", flush=True)
    run([sys.executable, "-m", "harness.experiments.purpose_search.summarize", OUT], cwd=SCE_DIR, env=child_env,
        check=False, capture=True)
    push_results(f"Purpose-enriched search: {repo} @ {CODE_COMMIT[:7]} ({SUMMARIZER}, {VARIANT})")

print("\n=== DONE ===")
print(open(os.path.join(OUT, "SUMMARY.md")).read() if os.path.exists(os.path.join(OUT, "SUMMARY.md")) else "(no summary)")
