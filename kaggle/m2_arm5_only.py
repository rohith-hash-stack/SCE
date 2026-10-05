# Kaggle cell: M2 single-arm baseline for Arm 5 (PRISM) after the Turn-1
# fence-parse fix (commit 54b127c), on the M2 task set: Gate A (5 synthetic)
# and Gate B (5 real FastAPI T2), seed 42, Arms 0, 5 and the Oracle only.
#
# Run this AFTER the M2 notebook's own setup cells, in the same session:
# clone of harness/eight-arm-slm at SCE_DIR, Ollama serving the q8_0 model,
# the transformers pin with its fallback, onnxruntime, pyright, the FastAPI
# editable install and the sitecustomize shim. Apart from the apt step
# below (zstd, curl, ripgrep: Arm 4's grep tool and the hallucination
# scorer call `rg`), this cell installs nothing: it checks that setup's
# effects (preflight) and stops if one is missing.
#
# Then: CPU smoke test -> gate (HARNESS_ACTIVE_ARMS=arm0,arm5,oracle) ->
# diff_vs_m2.json -> push to reports/harness_m2/arm5_only/ (tar.gz first,
# then the raw directory, as M2 did).
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

# ---- settings ----
BRANCH = "harness/eight-arm-slm"
SCE_DIR = "/kaggle/working/SCE"
MODEL = "qwen2.5-coder:14b-instruct-q8_0"
SEED = 42
ARMS = "arm0,arm5,oracle"                 # not 1, 2, 3, 4
OUT = "/kaggle/working/m2_arm5"
DEST = "reports/harness_m2/arm5_only"
M2_RUN = "reports/harness_m2/kaggle_smoke"
FIX_COMMIT = "54b127c"                    # Turn-1 fence-parse fix: must be in the checkout
PUSH_RESULTS = True                       # needs GITHUB_TOKEN (as in the M2 notebook)


def run(cmd, cwd=None, check=True, env=None, capture=False):
    shown = cmd if isinstance(cmd, str) else " ".join(cmd)
    tok = os.environ.get("GITHUB_TOKEN")
    print("$", shown.replace(tok, "***") if tok else shown, flush=True)
    return subprocess.run(cmd, cwd=cwd, check=check, env=env, shell=isinstance(cmd, str),
                          capture_output=capture, text=True)


# ---- environment: apt packages (same line for the M3 script and later single-arm scripts) ----
run("apt-get update -qq && apt-get install -y -qq zstd curl ripgrep", check=False)
rg_path = shutil.which("rg")
print("rg:", rg_path)
if rg_path is None:
    raise SystemExit("ripgrep (rg) is not on PATH after apt-get install: Arm 4's grep tool cannot run")

# ---- preflight: the M2 setup's effects, verified, never installed here ----
problems = []
if not os.path.isdir(f"{SCE_DIR}/.git"):
    problems.append(f"no clone at {SCE_DIR} (run the M2 setup cells first)")
else:
    run(["git", "fetch", "origin", BRANCH], cwd=SCE_DIR, check=False)
    run(["git", "checkout", "-B", BRANCH, f"origin/{BRANCH}"], cwd=SCE_DIR, check=False)
    head = run(["git", "log", "--oneline", "-1"], cwd=SCE_DIR, capture=True).stdout.strip()
    print("checkout:", head)
    if run(["git", "merge-base", "--is-ancestor", FIX_COMMIT, "HEAD"], cwd=SCE_DIR, check=False).returncode != 0:
        problems.append(f"checkout does not contain the fence-parse fix {FIX_COMMIT}")
    if not os.path.isfile(f"{SCE_DIR}/{M2_RUN}/cells.parquet"):
        problems.append(f"M2 baseline {M2_RUN}/cells.parquet missing from the checkout")
try:
    tags = json.loads(urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=10).read())
    if MODEL not in {m.get("name") for m in tags.get("models", [])}:
        problems.append(f"Ollama is up but {MODEL} is not pulled")
except Exception as exc:  # noqa: BLE001
    problems.append(f"Ollama not reachable on 127.0.0.1:11434: {exc}")
for mod in ("transformers", "onnxruntime"):
    if run([sys.executable, "-c", f"import {mod}; print({mod}.__version__)"], check=False, capture=True).returncode:
        problems.append(f"python package {mod} not importable")
if shutil.which("pyright-langserver") is None:        # unused by Arm 5; part of the M2 environment
    print("note: pyright-langserver not on PATH (Arm 3 only; the CPU smoke check arm3_lsp_ready will say BLOCKED)")
if problems:
    raise SystemExit("preflight failed (run the M2 setup cells first):\n  - " + "\n  - ".join(problems))
print("preflight OK")

harness_env = dict(os.environ, CUDA_VISIBLE_DEVICES="", PYTHONPATH=SCE_DIR, HARNESS_WORKDIR="/kaggle/working",
                   LLM_BASE_URL="http://localhost:11434/v1", HARNESS_ACTIVE_ARMS=ARMS)
os.makedirs(OUT, exist_ok=True)

# ---- CPU smoke test first, as in M2 ----
smoke = run([sys.executable, "-m", "harness.smoke_test_cpu", "--json", f"{OUT}/smoke_cpu.json"],
            cwd=SCE_DIR, env=harness_env, check=False)
print("CPU smoke exit code:", smoke.returncode)

# ---- gate: Arms 0, 5 and the Oracle only ----
t0 = time.time()
gate = run([sys.executable, "-m", "harness.kaggle_m1", "--out", OUT, "--seed", str(SEED), "--model", MODEL,
            "--llm-url", "http://localhost:11434/v1", "--ollama-url", "http://localhost:11434"],
           cwd=SCE_DIR, env=harness_env, check=False)
print(f"gate exit code: {gate.returncode}  ({(time.time() - t0) / 60:.1f} min)")

# ---- per-cell diff against the M2 run (same tasks, same seed) ----
diff = run([sys.executable, "-m", "harness.reporting.compare_runs", OUT, f"{SCE_DIR}/{M2_RUN}",
            "--arms", ARMS, "--out", f"{OUT}/diff_vs_m2.json"], cwd=SCE_DIR, env=harness_env, check=False)
print("diff exit code:", diff.returncode)

rep = json.load(open(f"{OUT}/gate_report.json"))
print("rows:", sum(r["status"] == "PASS" for r in rep["rows"]), "PASS /", len(rep["rows"]),
      "| arms in run:", sorted({r["arm"] for r in rep["rows"]}))


# ---- push: tar.gz first (small), then the raw directory (as M2) ----
def commit_and_push(paths, message):
    run(["git", "add", "-f", *paths], cwd=SCE_DIR)
    if run(["git", "commit", "-m", message], cwd=SCE_DIR, check=False).returncode != 0:
        return False
    for attempt in range(4):
        if run(["git", "push", "origin", f"HEAD:{BRANCH}"], cwd=SCE_DIR, check=False).returncode == 0:
            return True
        run(["git", "pull", "--rebase", "origin", BRANCH], cwd=SCE_DIR, check=False)
        time.sleep(2 ** (attempt + 1))
    return False


if PUSH_RESULTS and os.environ.get("GITHUB_TOKEN"):
    run(["git", "config", "user.email", "kaggle-runner@users.noreply.github.com"], cwd=SCE_DIR)
    run(["git", "config", "user.name", "kaggle-runner"], cwd=SCE_DIR)
    tar = f"{SCE_DIR}/{DEST}.tar.gz"
    os.makedirs(os.path.dirname(tar), exist_ok=True)
    run(f"tar -czf {tar} -C {os.path.dirname(OUT)} {os.path.basename(OUT)}")
    ok_tar = commit_and_push([f"{DEST}.tar.gz"],
                             f"M2 Arm 5 post-fix baseline artifacts (tar.gz, {os.path.getsize(tar)} B)")
    dest = f"{SCE_DIR}/{DEST}"
    run(f"rm -rf {dest} && mkdir -p {dest} && cp -r {OUT}/. {dest}/")
    ok_raw = commit_and_push([DEST], "M2 Arm 5 post-fix baseline artifacts (raw)")
    print(f"pushed tar.gz: {ok_tar}  raw: {ok_raw}")
else:
    print(f"not pushed (PUSH_RESULTS={PUSH_RESULTS}, GITHUB_TOKEN set: {bool(os.environ.get('GITHUB_TOKEN'))}); "
          f"results stay in {OUT}")
