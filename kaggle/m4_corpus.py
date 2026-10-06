# Kaggle cell: M4, one corpus per session. Set CORPUS below and run.
#
# Scope: T2 + T5 on CORPUS, seeds 42, 43, 44, all seven arms (T1/T3/T4 are
# deferred). One session per corpus; each should fit in Kaggle's 12 h limit.
# Cells per corpus (tasks x 7 arms x 3 seeds): fastapi 672 (24 T2 + 8 T5),
# django 588 (20 + 8), express 462 (20 + 2), trpc 819 (25 + 14).
#
# Run this AFTER the M3 notebook's own setup cells, in the same session:
# clone of harness/eight-arm-slm at SCE_DIR, Ollama serving the q8_0 model,
# the transformers pin with its fallback, onnxruntime/onnx/onnxscript, the
# FastAPI editable install and the sitecustomize shim. This cell does not
# repeat that setup: it checks its effects (preflight) and stops if one is
# missing. It adds only what M4 needs on top:
#   - apt: ripgrep (Arm 4's grep tool, the hallucination scorer), zstd, curl
#   - npm: pyright, typescript, typescript-language-server (Arm 3)
#   - the corpus's editable install for Python corpora (Arm 3's charter
#     check: the package must import from the pinned checkout)
#
# Then: CPU smoke test -> M4 run (harness.kaggle_m1 --corpus ...) ->
# push to reports/harness_m4/<corpus>/ (tar.gz first, then the raw
# directory, as M2/M3 did).
#
# Long-session safety: while the run is going, checkpoint.json is pushed
# every PUSH_EVERY_S seconds. If the session dies, start a new one: this
# cell restores the pushed checkpoint into OUT and the runner resumes,
# skipping every completed cell.
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

# ---- settings ----
CORPUS = "fastapi"                        # fastapi | django | express | trpc
SEEDS = "42,43,44"
TASK_TYPES = "T2,T5"
BRANCH = "harness/eight-arm-slm"
SCE_DIR = "/kaggle/working/SCE"
MODEL = "qwen2.5-coder:14b-instruct-q8_0"
OLLAMA = "http://127.0.0.1:11434"
OUT = f"/kaggle/working/m4_{CORPUS}"
DEST = f"reports/harness_m4/{CORPUS}"
PUSH_RESULTS = True                       # needs GITHUB_TOKEN (as in the M3 notebook)
PUSH_EVERY_S = 2 * 3600                   # mid-run checkpoint pushes; 0 disables
RUNNER_MARKER = "--task-types"            # the checkout must contain the M4 runner


def run(cmd, cwd=None, check=True, env=None, capture=False):
    shown = cmd if isinstance(cmd, str) else " ".join(cmd)
    tok = os.environ.get("GITHUB_TOKEN")
    print("$", shown.replace(tok, "***") if tok else shown, flush=True)
    return subprocess.run(cmd, cwd=cwd, check=check, env=env, shell=isinstance(cmd, str),
                          capture_output=capture, text=True)


def push(paths, message):
    """Commit `paths` (relative to SCE_DIR) and push, retrying with rebase."""
    if not (PUSH_RESULTS and os.environ.get("GITHUB_TOKEN")):
        print("push skipped (PUSH_RESULTS off or no GITHUB_TOKEN)")
        return False
    run(["git", "config", "user.email", "kaggle-runner@users.noreply.github.com"], cwd=SCE_DIR)
    run(["git", "config", "user.name", "kaggle-runner"], cwd=SCE_DIR)
    run(["git", "add", "-f", *paths], cwd=SCE_DIR)
    run(["git", "commit", "-m", message], cwd=SCE_DIR, check=False)
    for attempt in range(4):
        if run(["git", "push", "origin", f"HEAD:{BRANCH}"], cwd=SCE_DIR, check=False).returncode == 0:
            return True
        run(["git", "pull", "--rebase", "origin", BRANCH], cwd=SCE_DIR, check=False)
        time.sleep(2 ** (attempt + 1))
    print("PUSH FAILED after 4 attempts")
    return False


assert CORPUS in ("fastapi", "django", "express", "trpc"), CORPUS

# ---- environment: apt + npm (idempotent) ----
run("apt-get update -qq && apt-get install -y -qq zstd curl ripgrep", check=False)
# fail fast: without typescript-language-server, Arm 3 cannot index Express or tRPC
run(["npm", "install", "-g", "pyright", "typescript", "typescript-language-server"], check=True)
for tool in ("rg", "pyright-langserver", "typescript-language-server"):
    print(f"{tool}: {shutil.which(tool)}")

# ---- preflight: the M3 setup's effects, verified, never installed here ----
problems = []
if not os.path.isdir(f"{SCE_DIR}/.git"):
    problems.append(f"no clone at {SCE_DIR} (run the M3 setup cells first)")
else:
    run(["git", "fetch", "origin", BRANCH], cwd=SCE_DIR, check=False)
    run(["git", "checkout", "-B", BRANCH, f"origin/{BRANCH}"], cwd=SCE_DIR, check=False)
    print("checkout:", run(["git", "log", "--oneline", "-1"], cwd=SCE_DIR, capture=True).stdout.strip())
    if RUNNER_MARKER not in open(f"{SCE_DIR}/harness/kaggle_m1.py").read():
        problems.append("checkout has no M4 runner (harness/kaggle_m1.py --task-types)")
try:
    tags = json.loads(urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=10).read())
    if MODEL not in {m.get("name") for m in tags.get("models", [])}:
        problems.append(f"Ollama is up but {MODEL} is not pulled")
except Exception as exc:  # noqa: BLE001
    problems.append(f"Ollama not reachable on {OLLAMA}: {exc}")
for mod in ("transformers", "onnxruntime", "tree_sitter_typescript"):
    if run([sys.executable, "-c", f"import {mod}"], check=False, capture=True).returncode:
        problems.append(f"python package {mod} not importable")
for tool in ("rg", "pyright-langserver", "typescript-language-server"):
    if shutil.which(tool) is None:
        problems.append(f"{tool} not on PATH")
if problems:
    raise SystemExit("preflight failed:\n  - " + "\n  - ".join(problems))
print("preflight OK")

harness_env = dict(os.environ, CUDA_VISIBLE_DEVICES="", PYTHONPATH=SCE_DIR, HARNESS_WORKDIR="/kaggle/working",
                   LLM_BASE_URL=f"{OLLAMA}/v1")

# ---- corpus: resolve (clones the pinned commit), editable install for Python corpora ----
root = run([sys.executable, "-c", f"from benchmarks.corpora.resolver import resolve; print(resolve({CORPUS!r}))"],
           cwd=SCE_DIR, env=harness_env, capture=True).stdout.strip().splitlines()[-1]
print("corpus root:", root)
if CORPUS in ("fastapi", "django"):
    run(f"{sys.executable} -m pip install -q -e {root}")
    origin = run([sys.executable, "-c", f"import {CORPUS}, os; print(os.path.dirname({CORPUS}.__file__))"],
                 cwd="/", capture=True).stdout.strip()
    print(f"{CORPUS} imports from: {origin}")
    if not origin.startswith(root):
        raise SystemExit(f"{CORPUS} does not import from the pinned checkout {root}")

# ---- resume: restore a checkpoint pushed by an earlier session ----
os.makedirs(OUT, exist_ok=True)
pushed = f"{SCE_DIR}/{DEST}/checkpoint.json"
if os.path.exists(pushed) and not os.path.exists(f"{OUT}/checkpoint.json"):
    shutil.copytree(f"{SCE_DIR}/{DEST}", OUT, dirs_exist_ok=True)
    n = json.load(open(f"{OUT}/checkpoint.json"))["n_cells"]
    print(f"restored {n} checkpointed cells from {DEST}: the runner skips the completed ones")

# ---- CPU smoke test (recorded; a BLOCKED check is reported, not fatal here) ----
smoke = run([sys.executable, "-m", "harness.smoke_test_cpu", "--corpus", CORPUS, "--json", f"{OUT}/smoke_cpu.json"],
            cwd=SCE_DIR, env=harness_env, check=False)
print("CPU smoke exit code:", smoke.returncode)

# ---- M4 run, with mid-run checkpoint pushes ----
cmd = [sys.executable, "-m", "harness.kaggle_m1", "--corpus", CORPUS, "--seeds", SEEDS, "--task-types", TASK_TYPES,
       "--out", OUT, "--model", MODEL, "--llm-url", f"{OLLAMA}/v1", "--ollama-url", OLLAMA]
print("$", " ".join(cmd), flush=True)
t0 = time.time()
proc = subprocess.Popen(cmd, cwd=SCE_DIR, env=harness_env)
last_push = time.time()
while proc.poll() is None:
    time.sleep(60)
    if PUSH_EVERY_S and time.time() - last_push >= PUSH_EVERY_S and os.path.exists(f"{OUT}/checkpoint.json"):
        os.makedirs(f"{SCE_DIR}/{DEST}", exist_ok=True)
        shutil.copy(f"{OUT}/checkpoint.json", f"{SCE_DIR}/{DEST}/checkpoint.json")
        n = json.load(open(f"{OUT}/checkpoint.json"))["n_cells"]
        push([f"{DEST}/checkpoint.json"], f"M4 {CORPUS}: mid-run checkpoint ({n} cells)")
        last_push = time.time()
print(f"M4 {CORPUS} exit code: {proc.returncode}  ({(time.time() - t0) / 3600:.2f} h)")

# ---- summary ----
rep = json.load(open(f"{OUT}/gate_report.json"))
print("totals:", rep.get("totals"))
print("tokenizer parity:", rep.get("tokenizer_parity", {}).get("status"))
print("index failures:", list(rep.get("index_failures", {})))
if rep.get("t5_bias_control"):
    print("t5 bias control:", rep["t5_bias_control"])

# ---- push: tar.gz first, then the raw directory ----
dest = f"{SCE_DIR}/{DEST}"
os.makedirs(dest, exist_ok=True)
tar = f"{dest}/m4_{CORPUS}.tar.gz"
run(f"tar -czf {tar} -C /kaggle/working m4_{CORPUS}")
push([f"{DEST}/m4_{CORPUS}.tar.gz"], f"M4 {CORPUS}: results archive (exit {proc.returncode})")
run(f"cp -r {OUT}/. {dest}/")
push([DEST], f"M4 {CORPUS}: results (exit {proc.returncode}, seeds {SEEDS}, {TASK_TYPES})")
