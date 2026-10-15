# Kaggle cell - PRISM two-stage validation (local open-weights models)
#
# Stage 1, retrieval tournament: can Prism, with its intent read off the
#   question by a live SLM, beat BM25, dense (jina-code) and hybrid RAG
#   (BM25 + jina, RRF, bge-reranker) at finding the function that answers
#   a developer's question? 160 frozen held-out questions: set 1 (50, Python),
#   set 2 (60, Python), set 3 (50, JavaScript/TypeScript - express, tRPC).
#   Metrics: top-1, recall@5, MRR, gold-over-decoy separation; paired sign
#   tests and a repo-stratified bootstrap.
#
# Stage 2, downstream effect: does Prism's context (and its entry-point
#   breadcrumbs) raise an LLM's causal-trace success rate on the 90 debug
#   tasks of the final sweep (FastAPI 25, Django 20, Express 20, tRPC 25)?
#   Arms: arm0_no_retrieval, arm1_hybrid_rag, repomap_pagerank, prism_full,
#   prism_ask, normal_oracle, prism_oracle. TSR = `score_debug_causal`
#   (the same scorer as the 7,200-cell sweep) - a causal-trace success
#   rate, not a test-suite pass rate. Paired tests use tasks as the unit.
#
# Paste this whole file into one Kaggle notebook cell (GPU T4 x2, internet ON).
# Secrets (Kaggle "Add-ons > Secrets", or env vars):
#   GITHUB_TOKEN - only if RESULTS_BRANCH is set, to push progress
#
# Resumable: Stage 1 caches every SLM reply (slm_cache.jsonl) and the
# embeddings; Stage 2's per-corpus cells.jsonl is its checkpoint. With
# RESULTS_BRANCH set, reports/two_stage/ is pulled from that branch before
# running and pushed back after Stage 1 and after every corpus, so a
# session that hits Kaggle's time limit continues where it stopped when the
# cell is re-run. Stage 2 at the defaults is ~6,300 cells: expect several
# sessions (the 14B model at q8 runs split across both T4s).

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

# --------------------------------------------------------------------- #
# User-editable
# --------------------------------------------------------------------- #
CODE_REF = "eval/held-out-benchmark"  # branch or commit of SCE to run (must contain this file)
RESULTS_BRANCH = ""  # e.g. "two-stage-validation-results"; "" = don't push
RUN_STAGE1 = True
RUN_STAGE2 = True
SLM_MODEL = "qwen2.5-coder:7b-instruct-q8_0"  # Stage 1 intent extractor (prism_slm_live)
ANSWER_MODEL = "qwen2.5-coder:14b-instruct-q8_0"  # Stage 2 answering model
NUM_CTX = 24_576  # Stage 2 context window (Ollama options.num_ctx)
STAGE1_SETS = "set1,set2,set3"
STAGE2_REPOS = ["trpc", "express", "fastapi", "django"]  # cheapest first
SEEDS = ",".join(str(s) for s in range(42, 52))
WORKERS = 2  # concurrent Stage 2 requests (matches OLLAMA_NUM_PARALLEL)

REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
SCE_DIR = "/kaggle/working/SCE"
WORK = "/kaggle/working/two_stage"
OUT_ROOT = "reports/two_stage"  # inside SCE_DIR; what RESULTS_BRANCH carries
OLLAMA_URL = "http://localhost:11434"


def run(cmd, cwd=None, check=True, env=None):
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=cwd, check=check, env=env)


def secret(name):
    value = os.environ.get(name)
    if value:
        return value
    try:
        from kaggle_secrets import UserSecretsClient

        return UserSecretsClient().get_secret(name)
    except Exception:
        return None


# --------------------------------------------------------------------- #
# 1. SCE at the pinned ref, its dependencies
# --------------------------------------------------------------------- #
github_token = secret("GITHUB_TOKEN") if RESULTS_BRANCH else None
if RESULTS_BRANCH and not github_token:
    raise SystemExit("RESULTS_BRANCH is set but GITHUB_TOKEN is not")
clone_url = REPO_URL.replace("https://", f"https://{github_token}@") if github_token else REPO_URL
if not os.path.isdir(SCE_DIR):
    run(["git", "clone", clone_url, SCE_DIR])
run(["git", "fetch", "origin", CODE_REF], cwd=SCE_DIR)
run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=SCE_DIR)
code_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SCE_DIR, text=True).strip()
run([sys.executable, "-m", "pip", "install", "-q", "-e", ".[dev,bench,baselines]", "onnxruntime", "pyarrow"], cwd=SCE_DIR)
sys.path.insert(0, SCE_DIR)

if RESULTS_BRANCH:
    fetched = run(["git", "fetch", "origin", RESULTS_BRANCH], cwd=SCE_DIR, check=False)
    if fetched.returncode == 0:
        run(["git", "checkout", "FETCH_HEAD", "--", OUT_ROOT], cwd=SCE_DIR, check=False)


def push_progress(label):
    if not RESULTS_BRANCH:
        return
    keep = [p for p in (f"{OUT_ROOT}/stage1/stage1_results.json", f"{OUT_ROOT}/stage1/stage1.md",
                        f"{OUT_ROOT}/stage1/slm_cache.jsonl", f"{OUT_ROOT}/stage1/sites_report.json",
                        f"{OUT_ROOT}/stage2", f"{OUT_ROOT}/RESULTS.md", f"{OUT_ROOT}/run_manifest.json")
            if os.path.exists(os.path.join(SCE_DIR, p))]
    if not keep:
        return
    run(["git", "add", "-f", *keep], cwd=SCE_DIR, check=False)
    run(["git", "-c", "user.name=kaggle", "-c", "user.email=kaggle@localhost", "commit", "-q", "-m",
         f"two-stage validation progress: {label}"], cwd=SCE_DIR, check=False)
    run(["git", "push", "-q", "origin", f"HEAD:refs/heads/{RESULTS_BRANCH}", "--force"], cwd=SCE_DIR, check=False)


os.makedirs(os.path.join(SCE_DIR, OUT_ROOT), exist_ok=True)
with open(os.path.join(SCE_DIR, OUT_ROOT, "run_manifest.json"), "w") as fh:
    json.dump({"code_commit": code_commit, "slm_model": SLM_MODEL, "answer_model": ANSWER_MODEL, "num_ctx": NUM_CTX,
               "seeds": SEEDS, "stage1_sets": STAGE1_SETS, "stage2_repos": STAGE2_REPOS}, fh, indent=1)

# --------------------------------------------------------------------- #
# 2. Ollama (both GPUs, one server: a 14B q8 model does not fit one T4)
# --------------------------------------------------------------------- #
if not shutil.which("ollama"):
    run(["bash", "-c", "curl -fsSL https://ollama.com/install.sh | sh"])
ollama_env = dict(os.environ, OLLAMA_NUM_PARALLEL=str(WORKERS), OLLAMA_MAX_LOADED_MODELS="2",
                  OLLAMA_KEEP_ALIVE="24h", OLLAMA_HOST="127.0.0.1:11434")


def ollama_up():
    try:
        urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3)
        return True
    except Exception:
        return False


if not ollama_up():
    subprocess.Popen(["ollama", "serve"], env=ollama_env, stdout=open(f"{WORK}-ollama.log", "a"), stderr=subprocess.STDOUT)
    for _ in range(60):
        if ollama_up():
            break
        time.sleep(2)
    else:
        raise SystemExit("ollama serve did not come up (see the -ollama.log file)")
for model in {SLM_MODEL if RUN_STAGE1 else None, ANSWER_MODEL if RUN_STAGE2 else None} - {None}:
    run(["ollama", "pull", model], env=ollama_env)

# --------------------------------------------------------------------- #
# 3. Preflight: the RAG encoder and reranker load and run (fail in minutes,
#    not hours into the run)
# --------------------------------------------------------------------- #
run([sys.executable, "-c", (
    "from harness.arms.arm1_rag import JinaCodeEmbedder, OnnxCrossEncoder;"
    "v = JinaCodeEmbedder().encode(['def add(a, b): return a + b']); assert v.shape[0] == 1;"
    "s = OnnxCrossEncoder().score('add two numbers', ['def add(a, b): return a + b']); assert len(s) == 1;"
    "print('preflight ok', v.shape)"
)], cwd=SCE_DIR)

# --------------------------------------------------------------------- #
# 4. Stage 1 - held-out repos at their pinned commits, dependency sites
# --------------------------------------------------------------------- #
STAGE1_OUT = os.path.join(SCE_DIR, OUT_ROOT, "stage1")
if RUN_STAGE1:
    raw_dir, repos_dir, sites_dir = f"{WORK}/raw", f"{WORK}/repos", f"{WORK}/sites"
    for d in (raw_dir, repos_dir, sites_dir, STAGE1_OUT):
        os.makedirs(d, exist_ok=True)
    datasets = ["harness/experiments/heldout_retrieval/dataset.json",
                "harness/experiments/heldout_retrieval/set2/dataset.json",
                "harness/experiments/heldout_retrieval/set3/dataset.json"]
    sites_report = {}
    for ds in datasets:
        for name, meta in json.load(open(os.path.join(SCE_DIR, ds)))["repos"].items():
            src = f"{raw_dir}/{name}"
            if not os.path.isdir(src):
                run(["git", "init", "-q", src])
                run(["git", "fetch", "-q", "--depth", "1", meta["url"], meta["commit"]], cwd=src)
                run(["git", "checkout", "-q", "--detach", "FETCH_HEAD"], cwd=src)
            root = os.path.normpath(os.path.join(src, meta.get("root", ".")))
            link = f"{repos_dir}/{name}"
            if not os.path.lexists(link):
                os.symlink(root, link)
            site = f"{sites_dir}/{name}"
            if os.path.isdir(site):
                continue
            os.makedirs(site)
            if meta.get("language") in ("javascript", "typescript"):
                sites_report[name] = "none (JS/TS: dependency summaries are Python-only)"
                continue
            # The repo's own requirements into a private --target site (never the
            # running interpreter). Best effort: a failure leaves the site empty
            # and is recorded, since it changes only Prism's capability legs.
            reqs = next((os.path.join(root, r) for r in ("requirements.txt", "requirements/base.txt", "requirements/production.txt")
                         if os.path.exists(os.path.join(root, r))), None)
            cmd = [sys.executable, "-m", "pip", "install", "-q", "--no-compile", "--target", site]
            cmd += ["-r", reqs] if reqs else [root]
            got = run(cmd, cwd=root, check=False)
            sites_report[name] = f"{'ok' if got.returncode == 0 else 'FAILED'}: {' '.join(cmd[-2:])}"
    with open(f"{STAGE1_OUT}/sites_report.json", "w") as fh:
        json.dump(sites_report, fh, indent=1)
    run([sys.executable, "-m", "harness.experiments.final_validation.stage1",
         "--repos-dir", repos_dir, "--sites-dir", sites_dir, "--out", STAGE1_OUT, "--sets", STAGE1_SETS,
         "--slm-base-url", f"{OLLAMA_URL}/v1", "--slm-model", SLM_MODEL], cwd=SCE_DIR)
    push_progress("stage 1")

# --------------------------------------------------------------------- #
# 5. Stage 2 - final-sweep runner, the seven Stage 2 arms, local model
# --------------------------------------------------------------------- #
STAGE2_OUT = os.path.join(SCE_DIR, OUT_ROOT, "stage2")
if RUN_STAGE2:
    from benchmarks.final_sweep.config import STAGE2_ARMS

    env = dict(os.environ, LLM_MODEL=ANSWER_MODEL, OPENAI_BASE_URL=f"{OLLAMA_URL}/v1")
    env.pop("OPENAI_API_KEY", None)  # a local endpoint takes any bearer token
    for repo in STAGE2_REPOS:
        result = run([
            sys.executable, "-m", "benchmarks.final_sweep.runner",
            "--repo", repo, "--arms", ",".join(STAGE2_ARMS.values()), "--seeds", SEEDS,
            "--workers", str(WORKERS), "--out", f"{STAGE2_OUT}/{repo}",
            "--model", ANSWER_MODEL, "--base-url", f"{OLLAMA_URL}/v1", "--num-ctx", str(NUM_CTX),
        ], cwd=SCE_DIR, check=False, env=env)
        push_progress(f"stage 2 {repo} (exit {result.returncode})")
        if result.returncode not in (0, 1):  # 1 = finished with schema-invalid rows, still worth pushing
            raise SystemExit(f"{repo} stage 2 failed with exit code {result.returncode}")
    run([sys.executable, "-m", "harness.experiments.final_validation.stage2", "--out-root", STAGE2_OUT,
         "--model", ANSWER_MODEL], cwd=SCE_DIR)

# --------------------------------------------------------------------- #
# 6. The two tables
# --------------------------------------------------------------------- #
parts = [f"# PRISM two-stage validation\n\nCode `{code_commit}`.\n"]
for path in (f"{STAGE1_OUT}/stage1.md", f"{STAGE2_OUT}/stage2.md"):
    if os.path.exists(path):
        parts.append(open(path).read())
report = "\n".join(parts)
with open(os.path.join(SCE_DIR, OUT_ROOT, "RESULTS.md"), "w") as fh:
    fh.write(report)
push_progress("final tables")
print(report)
try:
    from IPython.display import Markdown, display

    display(Markdown(report))
except Exception:
    pass
