# ============================================================
# PRISM two-stage validation - Kaggle, self-contained cell
# Paste this whole file into ONE Kaggle notebook cell and Run.
# Settings: Accelerator GPU T4 x2, Internet ON, secret GITHUB_TOKEN.
#
# Stage 1 - retrieval tournament (CPU + a small local SLM, ~1 h):
#   160 frozen held-out questions (set1 50 Python, set2 60 Python,
#   set3 50 JS/TS). Engines: plain_bm25, stemmed_bm25, dense_jina_code,
#   hybrid_rrf_rerank, prism_rule_intent, prism_slm_live, prism_oracle,
#   prism_shipped_ask, normal_oracle. Top-1 / recall@5 / MRR / separation,
#   paired sign tests + repo-stratified bootstrap.
# Stage 2 - causal-trace success rate (GPU, long):
#   90 debug tasks (fastapi 25, django 20, express 20, trpc 25) x 7 arms
#   (arm0_no_retrieval, arm1_hybrid_rag, repomap_pagerank, prism_full,
#   prism_ask, normal_oracle, prism_oracle) x SEEDS. TSR is the
#   `score_debug_causal` causal-trace success rate (same scorer as the
#   7,200-cell sweep), not a test-suite pass rate.
#
# Resumable: results are pushed to BRANCH under PUSH_PATH every
# PUSH_EVERY_S and after each stage/corpus. The cell stops itself before
# Kaggle's 12 h limit; re-run it in a new session and it restores the
# pushed results and continues (Stage 1 is skipped once complete; the
# Stage 2 runner skips every finished cell). Restored results are only
# accepted if they were produced by identical code (tree hashes of src/,
# harness/, benchmarks/); the results commits themselves do not count.
# ============================================================

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

# ---------- Config ----------
BRANCH = "eval/held-out-benchmark"
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
EXPECTED_GITHUB_USER = "rohith-hash-stack"
MODEL = "qwen2.5-coder:14b-instruct-q8_0"           # Stage 2 answering model
MODEL_FALLBACK = "qwen2.5-coder:14b-instruct-q4_K_M"
SLM_MODEL = "qwen2.5-coder:7b-instruct-q8_0"        # Stage 1 intent extractor (prism_slm_live)
OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_VERSION = "0.34.0"
NUM_CTX = 24576                                     # Stage 2 prompts reach ~9-12K tokens
PARALLEL = 2                                        # concurrent Stage 2 requests (OLLAMA_NUM_PARALLEL)

RUN_STAGE1 = True
RUN_STAGE2 = True
STAGE1_SETS = "set1,set2,set3"
STAGE2_CORPORA = ["trpc", "express", "fastapi", "django"]   # cheapest first
SEEDS = "42,43,44,45,46,47,48,49,50,51"             # fewer seeds = proportionally shorter Stage 2

SCE_DIR = "/kaggle/working/SCE"
WORK_DIR = "/kaggle/working/two_stage"
PUSH_PATH = "reports/two_stage"
PUSH_EVERY_S = 1800
MAX_RUNTIME_HOURS = 11.3                            # stop + push before Kaggle's 12 h limit

TRANSFORMERS_PINS = [
    ("transformers==4.45.2", "sentence-transformers>=3.0,<4.0"),
    ("transformers==4.46.3", "sentence-transformers>=3.0,<4.0"),
    ("transformers==4.47.1", "sentence-transformers>=3.0,<4.0"),
    ("transformers==4.44.2", "sentence-transformers<3.0"),
]

EXPECTED_DEBUG_TASKS = {"fastapi": 25, "django": 20, "express": 20, "trpc": 25}
CODE_PATHS = ("src", "harness", "benchmarks")
START = time.time()

print("=" * 64)
print("PRISM two-stage validation")
print("=" * 64, flush=True)


MAX_PUSH_FILE_MB = 90
# git never waits for a password prompt (a hung prompt looks like a stall)
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")


def mask(text):
    tok = os.environ.get("GITHUB_TOKEN")
    return text.replace(tok, "***") if tok else text


def run(cmd, cwd=None, check=True, env=None, capture=False, timeout=None):
    shown = mask(cmd if isinstance(cmd, str) else " ".join(cmd))
    print(f"$ {shown}", flush=True)
    return subprocess.run(cmd, cwd=cwd, check=check, env=env,
                          shell=isinstance(cmd, str),
                          capture_output=capture, text=True, timeout=timeout)


def abort(msg):
    print(f"[ABORT] {msg}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def head():
    return run(["git", "rev-parse", "HEAD"], cwd=SCE_DIR,
               capture=True).stdout.strip()


def code_identity(rev="HEAD"):
    """Git tree hashes of the code paths - equal iff the code is equal."""
    return {p: run(["git", "rev-parse", f"{rev}:{p}"], cwd=SCE_DIR,
                   capture=True).stdout.strip() for p in CODE_PATHS}


def out_of_time():
    return time.time() - START > MAX_RUNTIME_HOURS * 3600


def push(paths, message, max_attempts=4):
    if not os.environ.get("GITHUB_TOKEN"):
        print("[push] skipped (no GITHUB_TOKEN)", flush=True)
        return False
    run(["git", "config", "user.email", "kaggle@local"], cwd=SCE_DIR)
    run(["git", "config", "user.name", "Kaggle"], cwd=SCE_DIR)
    run(["git", "add", "-f", *paths], cwd=SCE_DIR)
    # GitHub rejects the whole push if any file is over 100 MB: unstage those.
    staged = run(["git", "diff", "--cached", "--name-only"], cwd=SCE_DIR, capture=True).stdout.split()
    for f in staged:
        p = os.path.join(SCE_DIR, f)
        if os.path.isfile(p) and os.path.getsize(p) > MAX_PUSH_FILE_MB * 2**20:
            print(f"[push] [warn] not pushing {f} ({os.path.getsize(p) / 2**20:.0f} MB > {MAX_PUSH_FILE_MB} MB)", flush=True)
            run(["git", "reset", "-q", "--", f], cwd=SCE_DIR, check=False)
    if run(["git", "diff", "--cached", "--quiet"], cwd=SCE_DIR, check=False).returncode == 0:
        print("[push] nothing new", flush=True)
        return True
    run(["git", "commit", "-q", "-m", message], cwd=SCE_DIR, check=False)
    for attempt in range(max_attempts):
        r = run(["git", "push", "origin", f"HEAD:{BRANCH}"], cwd=SCE_DIR,
                check=False, capture=True, env=GIT_ENV)
        if r.returncode == 0:
            print("[push] ok", flush=True)
            return True
        said = mask((r.stderr or "") + (r.stdout or "")).strip()
        print(f"[push] git push exit {r.returncode}:\n{said[-1500:]}", flush=True)
        if "non-fast-forward" in said or "fetch first" in said:
            run(["git", "pull", "--rebase", "origin", BRANCH], cwd=SCE_DIR,
                check=False, env=GIT_ENV)
        time.sleep(2 ** (attempt + 1))
    print("[push] FAILED after retries - results are still in WORK_DIR", flush=True)
    return False


# ============================================================
# Setup
# ============================================================
print("\n[setup 1/7] GitHub token", flush=True)
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

whoami = run(["curl", "-s", "-H", f"Authorization: token {github_token}",
              "https://api.github.com/user"], check=True, capture=True)
try:
    who = json.loads(whoami.stdout or "{}")
except json.JSONDecodeError:
    who = {}
if who.get("login") != EXPECTED_GITHUB_USER:
    abort(f"token is {who.get('login')!r}")
print(f"[ok] token: {who['login']}", flush=True)

print("\n[setup 2/7] GPU check", flush=True)
gpu = run(["nvidia-smi", "--query-gpu=name,memory.total",
           "--format=csv,noheader"], check=False, capture=True)
gpu_lines = [ln for ln in (gpu.stdout or "").strip().splitlines() if ln.strip()]
if sum("T4" in ln for ln in gpu_lines) < 2:
    abort(f"expected GPU T4 x2: {gpu_lines}")
print(f"[ok] GPU: {gpu_lines}", flush=True)

print("\n[setup 3/7] apt: ripgrep, zstd, curl, git", flush=True)
run("apt-get update -qq && apt-get install -y -qq zstd curl ripgrep git",
    check=False)

print("\n[setup 4/7] Ollama (one server across both T4s) + models", flush=True)
if subprocess.run(["which", "ollama"], capture_output=True).returncode != 0:
    run(f"curl -L https://github.com/ollama/ollama/releases/download/"
        f"v{OLLAMA_VERSION}/ollama-linux-amd64.tar.zst -o /tmp/ollama.tar.zst",
        check=True)
    run("tar -I zstd -xf /tmp/ollama.tar.zst -C /usr/local", check=True)
    run("chmod 755 /usr/local/bin/ollama", check=True)

log_path = "/kaggle/working/ollama.log"
ollama_env = dict(os.environ, OLLAMA_HOST="127.0.0.1:11434", OLLAMA_KEEP_ALIVE="1h",
                  OLLAMA_CONTEXT_LENGTH=str(NUM_CTX), OLLAMA_NUM_PARALLEL=str(PARALLEL),
                  OLLAMA_FLASH_ATTENTION="1", OLLAMA_KV_CACHE_TYPE="q8_0",
                  OLLAMA_MAX_LOADED_MODELS="2")
if subprocess.run(["pgrep", "-f", "ollama serve"],
                  capture_output=True).returncode != 0:
    subprocess.Popen(["ollama", "serve"], env=ollama_env,
                     stdout=open(log_path, "a"), stderr=subprocess.STDOUT)
    time.sleep(10)

for _ in range(30):
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/version",
                                    timeout=2) as r:
            if r.status == 200:
                print(f"[ok] ollama: {r.read().decode()}", flush=True)
                break
    except Exception:
        time.sleep(2)
else:
    abort(f"ollama not responding (see {log_path})")

tags = run(["ollama", "list"], capture=True, env=ollama_env).stdout or ""
if RUN_STAGE2 and MODEL not in tags:
    r = run(["ollama", "pull", MODEL], check=False, env=ollama_env)
    if r.returncode != 0:
        run(["ollama", "pull", MODEL_FALLBACK], check=True, env=ollama_env)
        MODEL = MODEL_FALLBACK
if RUN_STAGE1 and SLM_MODEL not in tags:
    run(["ollama", "pull", SLM_MODEL], check=True, env=ollama_env)
print(f"[ok] answer model: {MODEL}   SLM: {SLM_MODEL}", flush=True)

print("\n[setup 5/7] Clone repo", flush=True)
clone_url = REPO_URL.replace("https://", f"https://x-access-token:{github_token}@")
if not os.path.isdir(f"{SCE_DIR}/.git"):
    run(["git", "clone", clone_url, SCE_DIR], check=True, env=GIT_ENV)
run(["git", "remote", "set-url", "origin", clone_url], cwd=SCE_DIR)
run(["git", "fetch", "origin", BRANCH], cwd=SCE_DIR, check=True, env=GIT_ENV)
# Push access, checked now rather than after hours of GPU time: GitHub
# authorizes a dry-run push exactly as a real one (403 without write access).
probe = run(["git", "push", "--dry-run", "origin", f"origin/{BRANCH}:refs/heads/{BRANCH}"],
            cwd=SCE_DIR, check=False, capture=True, env=GIT_ENV)
if probe.returncode != 0:
    abort("this GITHUB_TOKEN cannot push to "
          f"{BRANCH} - checkpoints would be lost:\n{mask(probe.stderr or probe.stdout).strip()[-1500:]}\n"
          "Fix: a classic token with the 'repo' scope, or a fine-grained token on this repository "
          "with Contents: Read and write.")
print(f"[ok] push access to {BRANCH}", flush=True)
run(["git", "checkout", "-B", BRANCH, f"origin/{BRANCH}"],
    cwd=SCE_DIR, check=True)
run(["git", "clean", "-fdx", "-e", ".benchmarks"], cwd=SCE_DIR, check=True)
head_before = head()
CODE = code_identity()
for f in ("harness/experiments/final_validation/stage1.py",
          "harness/experiments/final_validation/stage2.py",
          "harness/experiments/heldout_retrieval/set3/dataset.json"):
    if not os.path.exists(os.path.join(SCE_DIR, f)):
        abort(f"{BRANCH} has no {f} (wrong branch/commit)")
print(f"[ok] HEAD: {head_before}  code: {CODE}", flush=True)

# ---------- hybrid-RAG embedding cache (not results) ----------
# arm1_hybrid_rag embeds a whole corpus on the CPU before its first cell
# (~3 h for Django). The vectors live in .benchmarks/cache/hybrid_rag/,
# which a new session does not have, so they are kept on CACHE_BRANCH
# (an orphan branch, split into <50 MB parts) and restored here. File
# names carry a digest of the chunk texts + encoder: a stale file is
# simply never used.
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
    files = sorted(f for f in (os.listdir(RAG_CACHE) if os.path.isdir(RAG_CACHE) else []) if f.endswith(".npy"))
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
                 "two-stage validation: hybrid-RAG embedding cache (not results)"],
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

print("\n[setup 6/7] Python deps + transformers pin", flush=True)
run([sys.executable, "-m", "pip", "install", "-q", "-e", ".[dev,bench,baselines]", "pyarrow"],
    cwd=SCE_DIR, check=True)

installed_pin = None
for tf_pin, st_pin in TRANSFORMERS_PINS:
    r = run([sys.executable, "-m", "pip", "install", "-q", tf_pin, st_pin],
            check=False)
    if r.returncode == 0:
        installed_pin = tf_pin
        break
if not installed_pin:
    abort("all transformers pins failed")
print(f"[ok] transformers: {installed_pin}", flush=True)

run([sys.executable, "-m", "pip", "install", "-q",
     "onnxruntime", "onnx", "onnxscript", "einops"], check=True)

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

print("\n[setup 7/7] Env vars + warm-up + preflight", flush=True)
# Child processes: SCE on PYTHONPATH (so sitecustomize.py loads), the local
# endpoint for every LLM client, the RAG models on CPU (GPUs serve Ollama).
child_env = dict(os.environ, PYTHONPATH=SCE_DIR, CUDA_VISIBLE_DEVICES="",
                 LLM_BASE_URL=f"{OLLAMA_URL}/v1", LLM_MODEL=MODEL,
                 LLM_API_KEY_ENV="OLLAMA_API_KEY", OLLAMA_API_KEY="ollama",
                 OPENAI_API_KEY="ollama", HARNESS_WORKDIR="/kaggle/working")
child_env.pop("GITHUB_TOKEN", None)
child_env.pop("OPENAI_BASE_URL", None)


def http_json(url, payload=None, timeout=900):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


if RUN_STAGE2:
    # A long prompt must reach the model intact: if the context window were
    # not applied, Ollama would silently drop the head of every prompt.
    probe_words = 12000
    t0 = time.time()
    reply = http_json(f"{OLLAMA_URL}/v1/chat/completions", {
        "model": MODEL, "max_tokens": 5, "temperature": 0, "options": {"num_ctx": NUM_CTX},
        "messages": [{"role": "user", "content": "apple " * probe_words + "\nReply with the single word OK."}]})
    seen = reply["usage"]["prompt_tokens"]
    if seen < 0.9 * probe_words:
        abort(f"sent ~{probe_words} tokens, the server saw {seen}: context window not applied")
    on_gpu = "cuda" in open(log_path).read().lower()
    print(f"[ok] warm-up {MODEL}: {seen} prompt tokens, {time.time() - t0:.0f}s, "
          f"CUDA {'detected' if on_gpu else 'NOT detected'}", flush=True)
    if not on_gpu:
        abort(f"Ollama is not using the GPU (see {log_path})")

pre = run([sys.executable, "-c", (
    "from harness.arms.arm1_rag import JinaCodeEmbedder, OnnxCrossEncoder;"
    "v = JinaCodeEmbedder().encode(['def add(a, b): return a + b']);"
    "s = OnnxCrossEncoder().score('add two numbers', ['def add(a, b): return a + b']);"
    "assert v.shape[0] == 1 and len(s) == 1; print('rag preflight ok', v.shape)")],
    cwd=SCE_DIR, env=child_env, check=False)
if pre.returncode != 0:
    abort("jina-code / bge-reranker failed to load - dense, hybrid and arm1_hybrid_rag cannot run")

# ---------- restore pushed progress (identical code only) ----------
os.makedirs(WORK_DIR, exist_ok=True)
pushed_root = os.path.join(SCE_DIR, PUSH_PATH)
manifest_path = os.path.join(pushed_root, "run_manifest.json")
if os.path.exists(manifest_path):
    m = json.load(open(manifest_path))
    if m.get("code") != CODE:
        abort(f"{PUSH_PATH} on {BRANCH} was produced by other code ({m.get('code')} != {CODE}); "
              f"move it aside or set PUSH_PATH to a new directory")
    if RUN_STAGE2 and m.get("answer_model") not in (None, MODEL):
        abort(f"{PUSH_PATH} holds {m['answer_model']} cells but this session serves {MODEL}")
    for sub in ("stage1", "stage2"):
        src, dst = os.path.join(pushed_root, sub), os.path.join(WORK_DIR, sub)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
            print(f"[ok] restored {PUSH_PATH}/{sub}", flush=True)
os.makedirs(pushed_root, exist_ok=True)
json.dump({"head": head_before, "code": CODE, "answer_model": MODEL if RUN_STAGE2 else None,
           "slm_model": SLM_MODEL, "num_ctx": NUM_CTX, "seeds": SEEDS, "transformers": installed_pin,
           "stage1_sets": STAGE1_SETS, "stage2_corpora": STAGE2_CORPORA},
          open(manifest_path, "w"), indent=1)


# ============================================================
# Checkpoint helper: WORK_DIR -> PUSH_PATH -> BRANCH
# ============================================================
def push_checkpoint(tag):
    for sub, skip in (("stage1", {"embeddings"}), ("stage2", set())):
        src = os.path.join(WORK_DIR, sub)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(pushed_root, sub), dirs_exist_ok=True,
                            ignore=lambda d, names: [n for n in names if n in skip or n.endswith(".db")])
    push([PUSH_PATH], f"two-stage validation: {tag} @ {head_before[:7]}")


def run_streaming(cmd, tag):
    """Run `cmd`, pushing a checkpoint every PUSH_EVERY_S; stop before the
    session limit. Returns (exit code or None if stopped, stopped_early)."""
    print("$", " ".join(cmd), flush=True)
    proc = subprocess.Popen(cmd, cwd=SCE_DIR, env=child_env)
    last = time.time()
    while proc.poll() is None:
        time.sleep(30)
        if out_of_time():
            print("[warn] approaching Kaggle's time limit - stopping; re-run this cell to resume", flush=True)
            proc.terminate()
            proc.wait(timeout=120)
            return None, True
        if time.time() - last >= PUSH_EVERY_S:
            push_checkpoint(f"{tag} (periodic)")
            last = time.time()
    return proc.returncode, False


stopped_early = False

# ============================================================
# Stage 1 - retrieval tournament
# ============================================================
STAGE1_OUT = os.path.join(WORK_DIR, "stage1")
if RUN_STAGE1 and os.path.exists(os.path.join(STAGE1_OUT, "stage1.md")):
    print("\n[stage 1] already complete (restored) - skipping", flush=True)
elif RUN_STAGE1:
    print("\n[stage 1] held-out repos at pinned commits + dependency sites", flush=True)
    raw_dir, repos_dir, sites_dir = f"{WORK_DIR}/raw", f"{WORK_DIR}/repos", f"{WORK_DIR}/sites"
    for d in (raw_dir, repos_dir, sites_dir, STAGE1_OUT):
        os.makedirs(d, exist_ok=True)
    sites_report = {}
    for ds in ("dataset.json", "set2/dataset.json", "set3/dataset.json"):
        meta_all = json.load(open(f"{SCE_DIR}/harness/experiments/heldout_retrieval/{ds}"))["repos"]
        for name, meta in meta_all.items():
            src = f"{raw_dir}/{name}"
            if not os.path.isdir(f"{src}/.git"):
                run(["git", "init", "-q", src])
                run(["git", "fetch", "-q", "--depth", "1", meta["url"], meta["commit"]], cwd=src)
                run(["git", "checkout", "-q", "--detach", "FETCH_HEAD"], cwd=src)
            root = os.path.normpath(os.path.join(src, meta.get("root", ".")))
            if not os.path.lexists(f"{repos_dir}/{name}"):
                os.symlink(root, f"{repos_dir}/{name}")
            site = f"{sites_dir}/{name}"
            if os.path.isdir(site):
                continue
            os.makedirs(site)
            if meta.get("language") in ("javascript", "typescript"):
                sites_report[name] = "none (JS/TS: dependency summaries are Python-only)"
                continue
            # The repo's requirements into a private --target site, never this
            # interpreter. Best effort: a failure leaves the site empty (Prism's
            # capability legs only) and is recorded in sites_report.json.
            reqs = next((os.path.join(root, r) for r in ("requirements.txt", "requirements/base.txt")
                         if os.path.exists(os.path.join(root, r))), None)
            cmd = [sys.executable, "-m", "pip", "install", "-q", "--no-compile", "--target", site]
            cmd += ["-r", reqs] if reqs else [root]
            got = run(cmd, cwd=root, check=False)
            sites_report[name] = f"{'ok' if got.returncode == 0 else 'FAILED'}: {' '.join(cmd[-2:])}"
            print(f"[site] {name}: {sites_report[name]}", flush=True)
    json.dump(sites_report, open(f"{STAGE1_OUT}/sites_report.json", "w"), indent=1)

    print("\n[stage 1] tournament", flush=True)
    code, stopped_early = run_streaming([
        sys.executable, "-m", "harness.experiments.final_validation.stage1",
        "--repos-dir", repos_dir, "--sites-dir", sites_dir, "--out", STAGE1_OUT,
        "--sets", STAGE1_SETS, "--slm-base-url", f"{OLLAMA_URL}/v1", "--slm-model", SLM_MODEL], "stage 1")
    print(f"=== STAGE 1 exit {code} ===", flush=True)
    push_checkpoint("stage 1")
    if code not in (0, None):
        print("[warn] stage 1 failed - see the traceback above; continuing with stage 2", flush=True)
    # free the SLM's VRAM for Stage 2
    try:
        http_json(f"{OLLAMA_URL}/api/generate", {"model": SLM_MODEL, "keep_alive": 0}, timeout=60)
    except Exception:
        pass

# ============================================================
# Stage 2 - causal-trace success rate, 7 arms
# ============================================================
STAGE2_OUT = os.path.join(WORK_DIR, "stage2")
stage2_status = {}
if RUN_STAGE2 and not stopped_early:
    arms = ",".join(json.loads(run([sys.executable, "-c",
        "import json; from benchmarks.final_sweep.config import STAGE2_ARMS as A; print(json.dumps(list(A.values())))"],
        cwd=SCE_DIR, env=child_env, capture=True).stdout.strip().splitlines()[-1]))
    print(f"\n[stage 2] arms: {arms}", flush=True)
    for corpus in STAGE2_CORPORA:
        if out_of_time():
            stopped_early = True
            break
        root = run([sys.executable, "-c", f"from benchmarks.corpora.resolver import resolve; print(resolve({corpus!r}))"],
                   cwd=SCE_DIR, env=child_env, capture=True).stdout.strip().splitlines()[-1]
        if corpus == "express" and not os.path.isdir(os.path.join(root, "node_modules", "etag")):
            run("npm install --omit=dev --no-audit --no-fund --ignore-scripts", cwd=root)
            if not os.path.isdir(os.path.join(root, "node_modules", "etag")):
                abort("express node_modules/etag missing after npm install")
        n_tasks = int(run([sys.executable, "-c", "from benchmarks.final_sweep.runner import load_debug_tasks as l; "
                           f"print(len(l({corpus!r})))"], cwd=SCE_DIR, env=child_env,
                          capture=True).stdout.strip().splitlines()[-1])
        if n_tasks != EXPECTED_DEBUG_TASKS[corpus]:
            abort(f"{corpus}: {n_tasks} debug tasks, expected {EXPECTED_DEBUG_TASKS[corpus]}")
        print(f"\n[stage 2] {corpus}: {n_tasks} tasks x 7 arms x {len(SEEDS.split(','))} seeds", flush=True)
        t0 = time.time()
        code, stopped_early = run_streaming([
            sys.executable, "-m", "benchmarks.final_sweep.runner",
            "--repo", corpus, "--arms", arms, "--seeds", SEEDS, "--workers", str(PARALLEL),
            "--out", os.path.join(STAGE2_OUT, corpus), "--model", MODEL,
            "--base-url", f"{OLLAMA_URL}/v1", "--num-ctx", str(NUM_CTX)], f"stage 2 {corpus}")
        stage2_status[corpus] = "stopped early" if stopped_early else f"exit {code}"
        print(f"=== STAGE 2 {corpus} {stage2_status[corpus]} in {(time.time() - t0) / 60:.1f} min ===", flush=True)
        push_checkpoint(f"stage 2 {corpus} {stage2_status[corpus]}")
        save_rag_cache()
        if stopped_early:
            break
        if code not in (0, 1):   # 1 = finished with schema-invalid rows, still pushed
            print(f"[warn] {corpus} runner exit {code}", flush=True)

    run([sys.executable, "-m", "harness.experiments.final_validation.stage2",
         "--out-root", STAGE2_OUT, "--model", MODEL], cwd=SCE_DIR, env=child_env, check=False)

# ============================================================
# Report - the two tables
# ============================================================
parts = [f"# PRISM two-stage validation\n\nCode `{head_before}`, answer model `{MODEL}`, SLM `{SLM_MODEL}`, "
         f"seeds {SEEDS}.\n"]
for path in (os.path.join(STAGE1_OUT, "stage1.md"), os.path.join(STAGE2_OUT, "stage2.md")):
    if os.path.exists(path):
        parts.append(open(path).read())
if stopped_early:
    parts.append("\n**INCOMPLETE** - the session stopped before the time limit; re-run this cell to resume.\n")
report = "\n".join(parts)
os.makedirs(pushed_root, exist_ok=True)
open(os.path.join(pushed_root, "RESULTS.md"), "w").write(report)
push_checkpoint("final tables" if not stopped_early else "stopped early")

print("\n=== DONE ===" if not stopped_early else "\n=== STOPPED EARLY - re-run this cell to resume ===")
print(f"Head:     {head_before}")
print(f"Elapsed:  {(time.time() - START) / 3600:.1f} h")
print(f"Stage 2:  {stage2_status}")
print(f"Output:   {BRANCH}:{PUSH_PATH}/ (RESULTS.md, stage1/, stage2/<corpus>/cells.jsonl)\n")
print(report)
print("\nBring back:")
print("  1. The two tables above (Stage 1 tournament, Stage 2 TSR)")
print("  2. The SLM intent line under the Stage 1 table")
print("  3. Any [warn], [site] FAILED or [ABORT] lines")
try:
    from IPython.display import Markdown, display
    display(Markdown(report))
except Exception:
    pass
