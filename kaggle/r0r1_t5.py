# Kaggle cell: R1 vs R0 T5 ablation, all four corpora, one session.
#
# Scope (approved): Arm 5 only, T5 only, seeds 42, 43, 44, corpora fastapi,
# django, express, trpc. Two configs, 96 cells each, 192 total:
#   R1  the pipeline as shipped (flags off)
#   R0  HARNESS_PRISM_T5_RULE_SELECTOR=1: on T5, Turn 1 requests every `caller`
#       row of the same manifest without a model call
# B0M4 is the stored M4 data (no re-run). R2 (routing) is NOT run.
#
# Run AFTER the M3/M4 notebook's own setup cells in the same session: clone of
# harness/eight-arm-slm at SCE_DIR, Ollama serving the q8_0 model, the
# transformers pin. This cell checks those effects (preflight) and stops if
# one is missing.
#
# Order: for each corpus, R1 then R0, each its own process and its own --out
# directory. Before every config run an isolation check verifies the commit,
# the flags the process will see, the Arm 5 class it will build, and that the
# output directory belongs to that config only; any failure stops the cell.
#
# Results go to reports/harness_r0r1/{r1,r0}/<corpus>/ (checkpoint pushed
# mid-run; archive + raw directory at the end), then the ablation report is
# built in-session (CPU only) and pushed to reports/harness_r0r1/report/.
# If the session dies, re-run this cell: pushed checkpoints are restored and
# the runner skips completed cells. A restored directory is accepted only if
# it was produced by identical code (tree hashes of src/, harness/,
# benchmarks/); results commits pushed by this cell do not count as a change.
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

# ---- settings ----
CORPORA = ["fastapi", "django", "express", "trpc"]
SEEDS = "42,43,44"
BRANCH = "harness/eight-arm-slm"
SCE_DIR = "/kaggle/working/SCE"
MODEL = "qwen2.5-coder:14b-instruct-q8_0"
OLLAMA = "http://127.0.0.1:11434"
WORK = "/kaggle/working/r0r1"
DEST = "reports/harness_r0r1"
PUSH_RESULTS = True                       # needs GITHUB_TOKEN (as in the M3 notebook)
PUSH_EVERY_S = 1800
CONFIGS = {                               # name -> experiment env (everything else identical)
    "r1": {"HARNESS_PRISM_T5_RULE_SELECTOR": "0", "HARNESS_PRISM_PRODUCTION_ROUTING": "0"},
    "r0": {"HARNESS_PRISM_T5_RULE_SELECTOR": "1", "HARNESS_PRISM_PRODUCTION_ROUTING": "0"},
}
EXPECTED_ARM = {"r1": "Arm5Prism", "r0": "RuleSelectorArm5"}
EXPECTED_T5_TASKS = {"fastapi": 8, "django": 8, "express": 2, "trpc": 14}


def run(cmd, cwd=None, check=True, env=None, capture=False):
    shown = cmd if isinstance(cmd, str) else " ".join(cmd)
    tok = os.environ.get("GITHUB_TOKEN")
    print("$", shown.replace(tok, "***") if tok else shown, flush=True)
    return subprocess.run(cmd, cwd=cwd, check=check, env=env, shell=isinstance(cmd, str),
                          capture_output=capture, text=True)


def push(paths, message):
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


def head():
    return run(["git", "rev-parse", "HEAD"], cwd=SCE_DIR, capture=True).stdout.strip()


#: The code a run executes. Results commits pushed by this cell (reports/)
#: move HEAD but never change these trees.
CODE_PATHS = ("src", "harness", "benchmarks")


def code_identity(rev="HEAD"):
    """Git tree hashes of the code paths at `rev` - equal iff the code is equal."""
    return {p: run(["git", "rev-parse", f"{rev}:{p}"], cwd=SCE_DIR, capture=True).stdout.strip() for p in CODE_PATHS}


# ---- preflight ----
problems = []
if not os.path.isdir(f"{SCE_DIR}/.git"):
    problems.append(f"no clone at {SCE_DIR} (run the setup cells first)")
else:
    run(["git", "fetch", "origin", BRANCH], cwd=SCE_DIR, check=False)
    run(["git", "checkout", "-B", BRANCH, f"origin/{BRANCH}"], cwd=SCE_DIR, check=False)
    print("checkout:", run(["git", "log", "--oneline", "-1"], cwd=SCE_DIR, capture=True).stdout.strip())
    if not os.path.exists(f"{SCE_DIR}/harness/experiments/production_routing/arms.py"):
        problems.append("checkout has no harness/experiments/production_routing (wrong branch/commit)")
try:
    tags = json.loads(urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=10).read())
    if MODEL not in {m.get("name") for m in tags.get("models", [])}:
        problems.append(f"Ollama is up but {MODEL} is not pulled")
except Exception as exc:  # noqa: BLE001
    problems.append(f"Ollama not reachable on {OLLAMA}: {exc}")
for mod in ("transformers", "tree_sitter_typescript"):
    if run([sys.executable, "-c", f"import {mod}"], check=False, capture=True).returncode:
        problems.append(f"python package {mod} not importable")
for name in ("HARNESS_PRISM_T5_RULE_SELECTOR", "HARNESS_PRISM_PRODUCTION_ROUTING", "HARNESS_ACTIVE_ARMS"):
    if name in os.environ:
        problems.append(f"{name} is set in the notebook environment ({os.environ[name]!r}); unset it - this cell sets it per run")
if problems:
    raise SystemExit("preflight failed:\n  - " + "\n  - ".join(problems))
COMMIT = head()
CODE = code_identity()
print("preflight OK, commit", COMMIT, "code", CODE)

base_env = dict(os.environ, CUDA_VISIBLE_DEVICES="", PYTHONPATH=SCE_DIR, HARNESS_WORKDIR="/kaggle/working",
                LLM_BASE_URL=f"{OLLAMA}/v1", HARNESS_ACTIVE_ARMS="arm5")

PROBE = """
import json
from harness import config as C
from harness.arms import build_arm
class _Tok:                                   # the probe only needs the arm's class, not a real tokenizer
    name = "probe"
    def count(self, text):
        return len(text.split())
arm = build_arm("arm5", tokenizer=_Tok())
print(json.dumps({"arm": type(arm).__name__, "rule": C.PRISM_T5_RULE_SELECTOR, "routing": C.PRISM_PRODUCTION_ROUTING,
                  "blast_mode": C.PRISM_BLAST_MODE, "active_arms": C.ACTIVE_ARMS}))
"""


def isolation_check(config, corpus, out, env):
    """Stop the cell unless this run is isolated: same commit as the session
    start, the expected flags and Arm 5 class in the child environment, Arm 5
    the only active arm, and an output directory owned by this config."""
    errs = []
    if code_identity() != CODE:
        errs.append(f"code changed since the session started: {code_identity()} != {CODE}")
    probe = run([sys.executable, "-c", PROBE], cwd=SCE_DIR, env=env, capture=True, check=False)
    try:
        p = json.loads(probe.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        raise SystemExit(f"isolation probe failed:\n{probe.stdout}\n{probe.stderr}")
    if p["arm"] != EXPECTED_ARM[config]:
        errs.append(f"arm class {p['arm']} != {EXPECTED_ARM[config]}")
    if p["routing"]:
        errs.append("production routing flag is on (R2 must not run)")
    if p["rule"] != (config == "r0"):
        errs.append(f"rule selector flag {p['rule']} wrong for {config}")
    if not p["blast_mode"]:
        errs.append("PRISM_BLAST_MODE is off (both configs use Design C for T5)")
    if p["active_arms"] != ["arm5"]:
        errs.append(f"active arms {p['active_arms']} != ['arm5']")
    marker = f"{out}/ablation_config.json"
    if os.path.exists(marker):
        m = json.load(open(marker))
        m_code = m.get("code") or code_identity(m["commit"])        # markers written before "code" existed
        if (m["config"], m["corpus"]) != (config, corpus) or m_code != CODE:
            errs.append(f"{out} belongs to {m} - refusing to mix configs or code versions")
    elif os.path.isdir(out) and os.listdir(out):
        errs.append(f"{out} is not empty and has no ablation marker")
    if errs:
        raise SystemExit(f"isolation check FAILED for {config}/{corpus}:\n  - " + "\n  - ".join(errs))
    os.makedirs(out, exist_ok=True)
    json.dump({"config": config, "corpus": corpus, "commit": COMMIT, "code": CODE, "env": CONFIGS[config]}, open(marker, "w"))
    print(f"isolation OK: {config}/{corpus} -> {p}")


summary = {}
for corpus in CORPORA:
    root = run([sys.executable, "-c", f"from benchmarks.corpora.resolver import resolve; print(resolve({corpus!r}))"],
               cwd=SCE_DIR, env=base_env, capture=True).stdout.strip().splitlines()[-1]
    print("corpus root:", root)
    for config, flags in CONFIGS.items():
        out, dest = f"{WORK}/{config}/{corpus}", f"{DEST}/{config}/{corpus}"
        pushed = f"{SCE_DIR}/{dest}/checkpoint.json"
        if os.path.exists(pushed) and not os.path.exists(f"{out}/checkpoint.json"):
            shutil.copytree(f"{SCE_DIR}/{dest}", out, dirs_exist_ok=True)
            print(f"restored checkpoint for {config}/{corpus}")
        env = dict(base_env, **flags)
        isolation_check(config, corpus, out, env)
        cmd = [sys.executable, "-m", "harness.kaggle_m1", "--corpus", corpus, "--seeds", SEEDS, "--task-types", "T5",
               "--out", out, "--model", MODEL, "--llm-url", f"{OLLAMA}/v1", "--ollama-url", OLLAMA]
        print(f"=== {config} / {corpus} ===\n$", " ".join(cmd), flush=True)
        t0, last = time.time(), time.time()
        proc = subprocess.Popen(cmd, cwd=SCE_DIR, env=env)
        while proc.poll() is None:
            time.sleep(60)
            if PUSH_EVERY_S and time.time() - last >= PUSH_EVERY_S and os.path.exists(f"{out}/checkpoint.json"):
                os.makedirs(f"{SCE_DIR}/{dest}", exist_ok=True)
                shutil.copy(f"{out}/checkpoint.json", f"{SCE_DIR}/{dest}/checkpoint.json")
                push([f"{dest}/checkpoint.json"], f"R0/R1 T5 {config} {corpus}: mid-run checkpoint")
                last = time.time()
        rep = json.load(open(f"{out}/gate_report.json"))
        summary[f"{config}/{corpus}"] = {"exit": proc.returncode, "hours": round((time.time() - t0) / 3600, 2),
                                         "totals": rep.get("totals"), "n_tasks": rep.get("n_tasks"),
                                         "index_failures": list(rep.get("index_failures", {}))}
        print(summary[f"{config}/{corpus}"], flush=True)
        if rep.get("n_tasks", {}).get("T5_blast_radius") != EXPECTED_T5_TASKS[corpus]:
            print(f"WARNING: {config}/{corpus} ran {rep.get('n_tasks')} (expected {EXPECTED_T5_TASKS[corpus]} T5 tasks)")
        os.makedirs(f"{SCE_DIR}/{dest}", exist_ok=True)
        run(f"tar -czf {SCE_DIR}/{dest}.tar.gz -C {WORK}/{config} {corpus}")
        run(f"cp -r {out}/. {SCE_DIR}/{dest}/")
        push([f"{dest}.tar.gz", dest], f"R0/R1 T5 {config} {corpus}: results (exit {proc.returncode})")

# ---- ablation report (CPU, in-session) ----
rep_dir = f"{SCE_DIR}/{DEST}/report"
run([sys.executable, "-m", "harness.experiments.production_routing.ablation_report",
     "--config", f"R1={WORK}/r1", "--config", f"R0={WORK}/r0", "--out", rep_dir], cwd=SCE_DIR, env=base_env, check=False)
json.dump(summary, open(f"{rep_dir}/run_summary.json", "w"), indent=1)
push([f"{DEST}/report"], "R0/R1 T5 ablation: report")
print(json.dumps(summary, indent=1))
