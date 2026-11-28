# ============================================================
# PRISM P3b task authoring + freeze - Kaggle, self-contained cell
# Paste this whole file into ONE Kaggle notebook cell and Run.
# Settings: Accelerator NONE (CPU is enough: no model runs), Internet ON,
# secret GITHUB_TOKEN. Run it as an interactive session.
#
# harness/experiments/retrieval_pivot/P3.md, "Task authoring and freezing".
# The blind draw is already committed (p3b_draw.py, seed 20261024):
# benchmarks/layer4/specs_p3b.py (96 specs per corpus, draw order - never
# edited by hand) and benchmarks/layer4_p3b/draw.json. Amendment 1 (P3.md),
# after the pilot run (9 of 96 traced specs eligible; its record is in
# benchmarks/layer4_p3b/pilot1/): gold 2..15, Django never traces gis_tests /
# postgres_tests, root-only permission tests are skipped, a failed trace is
# re-run untraced to tell an environment fault from the candidate's failure,
# and tRPC is traced as source (every @trpc/* import aliased to src/). This
# cell:
#   step 1  author - per corpus, the specs in draw order, each traced on the
#           corpus's own tests (python -m harness.experiments.layer4.author
#           --specs ... --eligibility p3b): kept when the trace exits cleanly,
#           the focal runs, and 2..15 gold callers lie within 2 call hops;
#           otherwise skipped with a reason code, and the next spec in draw
#           order is traced. A trace that fails with nothing traced too (or
#           times out) is an environment fault: set aside as `env_fault`, not
#           counted as ineligible; more than 5 in a corpus abort it. The first 8 kept per corpus are the tasks. A
#           corpus that runs out of specs keeps its natural yield (approved:
#           no backfill from another corpus). Every decision is logged to
#           benchmarks/layer4_p3b/authoring_log_<corpus>.json. A tooling
#           fault (an invalid task, an unknown error) aborts the corpus and
#           logs nothing for that spec: a bug never skips a draw.
#   step 2  freeze - python -m harness.experiments.retrieval_pivot.p3b_freeze
#           freeze writes benchmarks/layer4_p3b/FROZEN.json: the seed, the
#           draw/specs/log hashes, every task's and trace manifest's sha256,
#           all skipped candidates (draw skips and trace skips). It refuses
#           below 24 tasks; the logs are pushed anyway, and the specs are then
#           extended from the same draw (p3b_draw --n) - never a new draw.
#   step 3  verify - the guard every P3b run applies (p3b_freeze verify, and
#           runner.load_tasks on the frozen directory) must pass on the
#           freshly frozen files; AUTHORING.md summarises the outcome.
#
# Two lanes run side by side (django + trpc, fastapi + express): the corpora
# are independent checkouts. Resumable: every decision is written at once
# and benchmarks/layer4_p3b/ is pushed to BRANCH every PUSH_EVERY_S and after
# each corpus; re-run the cell in a new session and the decided specs are
# not traced again (a spec stopped half-way has no decision and is traced
# again). Decisions are only resumed across identical authoring code (tree
# hashes of src/ and harness/, and the specs and draw files).
# ============================================================

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

# ---------- Config ----------
BRANCH = "feature/option3-contract-axis"
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
EXPECTED_GITHUB_USER = "rohith-hash-stack"
LANES = [["django", "trpc"], ["fastapi", "express"]]
CORPORA = [c for lane in LANES for c in lane]
PER_CORPUS = 8                              # = p3b_freeze.PER_CORPUS
MIN_GOLD = 2                                # = author.P3B_MIN_GOLD (amendment 1)
AMENDMENT = 1                               # = p3b_freeze.AMENDMENT
MIN_TASKS = 24                              # = p3b_freeze.MIN_TASKS
SEED = 20261024                             # = specs_p3b.SEED
RETRY_PASSES = 2                            # reruns of a corpus whose author process died
NODE_VERSION = "20.18.1"
PNPM_VERSION = "8.5.1"
TRPC_PACKAGES = ["server", "client", "react-query", "next"]   # dependency order

SCE_DIR = "/kaggle/working/SCE"
NODE_DIR = "/kaggle/working/node20"
VENV_DIR = "/kaggle/working/venvs"
NAMES_DIR = "/kaggle/working/p3b_names"     # cache of the corpora's indexed names (not pushed)
PUSH_PATH = "benchmarks/layer4_p3b"
SPECS = "benchmarks/layer4/specs_p3b.py"
PUSH_EVERY_S = 600
MAX_RUNTIME_HOURS = 11.3
# benchmarks/layer4/ENVIRONMENT.md
FASTAPI_PINS = ["anyio[trio]==4.6.2", "pydantic==2.11.7", "bcrypt==4.0.1", "python-multipart==0.0.20",
                "pydantic-settings", "fastapi-cli", "email-validator", "flask>=1.1.2,<4.0.0", "orjson", "ujson",
                "sqlmodel==0.0.24", "PyJWT==2.8.0", "passlib[bcrypt]>=1.7.2,<2.0.0", "inline-snapshot>=0.21.1",
                "dirty-equals==0.9.0", "pyyaml>=5.3.1,<7.0.0", "pytest"]
DJANGO_PINS = ["tblib"]
CORPUS_PYTHON = "3.11"                      # the version the Layer 4 tasks were authored with
# the authoring code; benchmarks/ is not in it - this cell pushes its outputs there
CODE_PATHS = ("src", "harness")
CODE_FILES = (SPECS, f"{PUSH_PATH}/draw.json")
MAX_PUSH_FILE_MB = 90
START = time.time()
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")

print("=" * 64)
print("PRISM P3b task authoring + freeze")
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


def code_identity(rev="HEAD"):
    ident = {p: run(["git", "rev-parse", f"{rev}:{p}"], cwd=SCE_DIR, capture=True).stdout.strip() for p in CODE_PATHS}
    ident.update({f: run(["git", "rev-parse", f"{rev}:{f}"], cwd=SCE_DIR, capture=True).stdout.strip() for f in CODE_FILES})
    return ident


def out_of_time():
    return time.time() - START > MAX_RUNTIME_HOURS * 3600


PUSH_LOCK = threading.Lock()


def push(paths, message, max_attempts=4):
    if not os.environ.get("GITHUB_TOKEN"):
        print("[push] skipped (no GITHUB_TOKEN)", flush=True)
        return False
    with PUSH_LOCK:
        run(["git", "config", "user.email", "kaggle@local"], cwd=SCE_DIR)
        run(["git", "config", "user.name", "Kaggle"], cwd=SCE_DIR)
        run(["git", "add", "-f", *paths], cwd=SCE_DIR)
        staged = run(["git", "diff", "--cached", "--name-only"], cwd=SCE_DIR, capture=True).stdout.split()
        for f in staged:
            p = os.path.join(SCE_DIR, f)
            if os.path.isfile(p) and os.path.getsize(p) > MAX_PUSH_FILE_MB * 2**20:
                print(f"[push] [warn] not pushing {f} ({os.path.getsize(p) / 2**20:.0f} MB)", flush=True)
                run(["git", "reset", "-q", "--", f], cwd=SCE_DIR, check=False)
        if run(["git", "diff", "--cached", "--quiet"], cwd=SCE_DIR, check=False).returncode == 0:
            print("[push] nothing new", flush=True)
            return True
        run(["git", "commit", "-q", "-m", message], cwd=SCE_DIR, check=False)
        for attempt in range(max_attempts):
            r = run(["git", "push", "origin", f"HEAD:{BRANCH}"], cwd=SCE_DIR, check=False, capture=True, env=GIT_ENV)
            if r.returncode == 0:
                print("[push] ok", flush=True)
                return True
            said = mask((r.stderr or "") + (r.stdout or "")).strip()
            print(f"[push] git push exit {r.returncode}:\n{said[-1500:]}", flush=True)
            if "non-fast-forward" in said or "fetch first" in said:
                run(["git", "pull", "--rebase", "origin", BRANCH], cwd=SCE_DIR, check=False, env=GIT_ENV)
            time.sleep(2 ** (attempt + 1))
        print("[push] FAILED after retries - results are still on local disk", flush=True)
        return False


AUTHOR_PATTERN = r"-m harness\.experiments\.layer4\.author( |$)"


def stop_proc(pid, grace_s=60):
    """SIGTERM the author process, then SIGKILL its group (the traced test run)."""
    try:
        pgid = os.getpgid(pid)
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + grace_s
    while time.time() < deadline:
        try:
            if open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[0] == "Z":
                break
        except (OSError, IndexError):
            break
        time.sleep(1)
    try:
        if pgid != os.getpgid(0):
            os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def stop_stale_authors():
    """A previous run of this cell in the same kernel leaves its author processes going."""
    found = run(["pgrep", "-f", "--", AUTHOR_PATTERN], check=False, capture=True).stdout.split()
    mine, pid = set(), os.getpid()
    while pid > 1:
        mine.add(pid)
        try:
            pid = int(open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    pids = [int(p) for p in found if int(p) not in mine]
    for pid in pids:
        print(f"[resume] stopping author {pid} left by an interrupted run", flush=True)
        stop_proc(pid)
    return pids


def decisions_of(path):
    try:
        return json.load(open(path))["decisions"]
    except (OSError, ValueError, KeyError):
        return {}


# ============================================================
# Setup
# ============================================================
print("\n[setup 0/8] Stop author processes left by an interrupted run of this cell", flush=True)
print(f"[ok] stopped {len(stop_stale_authors())}", flush=True)

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

print("\n[setup 2/8] apt", flush=True)
run("apt-get update -qq && apt-get install -y -qq curl git ripgrep xz-utils", check=False)

print("\n[setup 3/8] Clone repo", flush=True)
clone_url = REPO_URL.replace("https://", f"https://x-access-token:{github_token}@")
if not os.path.isdir(f"{SCE_DIR}/.git"):
    run(["git", "clone", clone_url, SCE_DIR], check=True, env=GIT_ENV)
run(["git", "remote", "set-url", "origin", clone_url], cwd=SCE_DIR)
run(["git", "fetch", "origin", BRANCH], cwd=SCE_DIR, check=True, env=GIT_ENV)
probe = run(["git", "push", "--dry-run", "origin", f"origin/{BRANCH}:refs/heads/{BRANCH}"],
            cwd=SCE_DIR, check=False, capture=True, env=GIT_ENV)
if probe.returncode != 0:
    abort(f"this GITHUB_TOKEN cannot push to {BRANCH} - results would be lost:\n"
          f"{mask(probe.stderr or probe.stdout).strip()[-1500:]}")
# a rerun in the same session: keep the decisions made since the last push
# (the reset and clean below would discard them) and restore them after
UNPUSHED = "/kaggle/working/p3b_unpushed"
local_out = os.path.join(SCE_DIR, PUSH_PATH)
shutil.rmtree(UNPUSHED, ignore_errors=True)
if os.path.isdir(local_out):
    shutil.copytree(local_out, UNPUSHED)
run(["git", "checkout", "-B", BRANCH, f"origin/{BRANCH}"], cwd=SCE_DIR, check=True)
run(["git", "clean", "-fdx", "-e", ".benchmarks"], cwd=SCE_DIR, check=True)
for c in CORPORA:  # a corpus's local log wins when it has decided more than the pushed one
    name = f"authoring_log_{c}.json"
    mine, pushed = decisions_of(os.path.join(UNPUSHED, name)), decisions_of(os.path.join(local_out, name))
    if len(mine) > len(pushed) and set(pushed) <= set(mine):
        for sub in ("tasks", "manifests"):
            for tid, d in mine.items():
                src = os.path.join(UNPUSHED, sub, f"{tid}.json")
                if d["status"] == "kept" and os.path.exists(src):
                    os.makedirs(os.path.join(local_out, sub), exist_ok=True)
                    shutil.copy2(src, os.path.join(local_out, sub, f"{tid}.json"))
        shutil.copy2(os.path.join(UNPUSHED, name), os.path.join(local_out, name))
        print(f"[resume] {c}: {len(mine)} decisions (pushed {len(pushed)} + this session's)", flush=True)
HEAD = run(["git", "rev-parse", "HEAD"], cwd=SCE_DIR, capture=True).stdout.strip()
CODE = code_identity()
for f in ("harness/experiments/layer4/author.py", "harness/experiments/retrieval_pivot/p3b_freeze.py",
          SPECS, f"{PUSH_PATH}/draw.json"):
    if not os.path.exists(os.path.join(SCE_DIR, f)):
        abort(f"{BRANCH} has no {f} (wrong branch/commit)")
FROZEN_PATH = os.path.join(local_out, "FROZEN.json")
ALREADY_FROZEN = os.path.exists(FROZEN_PATH)
CODE_FILE = os.path.join(local_out, "authoring_code.json")
if os.path.exists(CODE_FILE):
    before = json.load(open(CODE_FILE))
    if before != CODE:
        abort("the authoring code changed since the pushed decisions were made - they cannot be resumed:\n"
              f"  then {before}\n  now  {CODE}\nDecisions of two code versions are never mixed: to author with the "
              f"new code, remove {PUSH_PATH}/authoring_log_*.json, tasks/, manifests/ and authoring_code.json from "
              "the branch and re-run (same draw, same order - the outcome is reproducible).")
    print("[resume] authoring code identical to the pushed decisions'", flush=True)
else:
    os.makedirs(local_out, exist_ok=True)
    json.dump(CODE, open(CODE_FILE, "w"), indent=1)
print(f"[ok] HEAD {HEAD}  code {CODE}", flush=True)

print("\n[setup 4/8] Python deps", flush=True)
run([sys.executable, "-m", "pip", "install", "-q", "-e", ".[dev,bench]", "jsonschema", "uv"], cwd=SCE_DIR, check=True)

print(f"\n[setup 5/8] Node {NODE_VERSION} + pnpm {PNPM_VERSION}", flush=True)
node_bin = os.path.join(NODE_DIR, "bin")
if not os.path.exists(os.path.join(node_bin, "node")):
    tarball = f"node-v{NODE_VERSION}-linux-x64.tar.xz"
    run(f"curl -fsSL https://nodejs.org/dist/v{NODE_VERSION}/{tarball} -o /tmp/{tarball}", check=True)
    os.makedirs(NODE_DIR, exist_ok=True)
    run(f"tar -xJf /tmp/{tarball} -C {NODE_DIR} --strip-components=1", check=True)
os.environ["PATH"] = node_bin + os.pathsep + os.environ["PATH"]
NODE = os.path.join(node_bin, "node")
NPM = os.path.join(node_bin, "npm")
node_version = run([NODE, "--version"], capture=True).stdout.strip()
if not node_version.startswith("v20."):
    abort(f"node is {node_version}")
PNPM = os.path.join(node_bin, "pnpm")
if not os.path.exists(PNPM) or run([PNPM, "--version"], check=False, capture=True).stdout.strip() != PNPM_VERSION:
    run([NPM, "install", "-g", "--silent", f"pnpm@{PNPM_VERSION}"], check=True)
if not os.path.exists(PNPM) or run([PNPM, "--version"], capture=True).stdout.strip() != PNPM_VERSION:
    abort(f"pnpm {PNPM_VERSION} not installed at {PNPM}")
print(f"[ok] node {node_version}, pnpm {PNPM_VERSION}", flush=True)

for path in (os.path.join(SCE_DIR, "src"), SCE_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)
child_env = dict(os.environ, PYTHONPATH=os.pathsep.join([SCE_DIR, os.path.join(SCE_DIR, "src")]),
                 CUDA_VISIBLE_DEVICES="", HARNESS_WORKDIR="/kaggle/working", PYTHONDONTWRITEBYTECODE="1")
child_env.pop("GITHUB_TOKEN", None)
child_env.pop("PRISM_BREADCRUMB_STRICT", None)


def resolve_corpus(name):
    return run([sys.executable, "-c", f"from benchmarks.corpora.resolver import resolve; print(resolve({name!r}))"],
               cwd=SCE_DIR, env=child_env, capture=True).stdout.strip().splitlines()[-1]


print("\n[setup 6/8] Corpora: checkouts + Node test environments", flush=True)
ROOTS = {c: resolve_corpus(c) for c in ("fastapi", "django", "express", "trpc")}
TRPC = run(["git", "rev-parse", "--show-toplevel"], cwd=ROOTS["trpc"], capture=True).stdout.strip()
for c, root in ROOTS.items():
    top = TRPC if c == "trpc" else root
    dirty = run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=top, capture=True).stdout.strip()
    if dirty:
        print(f"[note] {c} checkout had tracked changes, restoring:\n{dirty}", flush=True)
        run(["git", "checkout", "--", "."], cwd=top, check=True)
print(f"[ok] corpora: {ROOTS}", flush=True)
express = ROOTS["express"]
if not os.path.exists(os.path.join(express, "node_modules", ".bin", "mocha")):
    run([NPM, "install", "--no-audit", "--no-fund", "--ignore-scripts"], cwd=express, check=True)
if not os.path.exists(os.path.join(express, "node_modules", ".bin", "mocha")):
    abort("express: node_modules/.bin/mocha missing after npm install")
install_env = dict(os.environ, CI="1", HUSKY="0", PUPPETEER_SKIP_DOWNLOAD="1", PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD="1",
                   PRISMA_SKIP_POSTINSTALL_GENERATE="1", NEXT_TELEMETRY_DISABLED="1", TURBO_TELEMETRY_DISABLED="1")


def dist_ok(pkg):
    return os.path.exists(os.path.join(TRPC, "packages", pkg, "dist", "index.mjs"))


if not all(dist_ok(p) for p in TRPC_PACKAGES):
    if run([PNPM, "install", "--frozen-lockfile", "--ignore-scripts", "--reporter=append-only"], cwd=TRPC,
           check=False, env=install_env, timeout=3600).returncode != 0:
        abort("trpc: pnpm install --frozen-lockfile failed")
    run([PNPM, "rebuild", "esbuild", "@swc/core"], cwd=TRPC, check=False, env=install_env)
    run([PNPM, "run", "build"], cwd=TRPC, check=False, env=install_env)
    for pkg in TRPC_PACKAGES:
        if not dist_ok(pkg):
            run([PNPM, "--filter", f"@trpc/{pkg}", "codegen-entrypoints"], cwd=TRPC, check=False, env=install_env)
            run([PNPM, "--filter", f"@trpc/{pkg}", "build"], cwd=TRPC, check=False, env=install_env)
    if run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=TRPC, capture=True).stdout.strip():
        run(["git", "checkout", "--", "."], cwd=TRPC, check=True)
if not all(dist_ok(p) for p in TRPC_PACKAGES):
    abort(f"trpc: dist/ missing for {[p for p in TRPC_PACKAGES if not dist_ok(p)]}")
print("[ok] express node_modules (Mocha), trpc workspace built", flush=True)

print(f"\n[setup 7/8] Python {CORPUS_PYTHON} venvs for fastapi + django (ENVIRONMENT.md pins)", flush=True)
UV = [sys.executable, "-m", "uv"]
PYTHONS = {}
EDITABLE = {"fastapi": "[all]", "django": ""}
for corpus, pins in (("fastapi", FASTAPI_PINS), ("django", DJANGO_PINS)):
    venv = os.path.join(VENV_DIR, corpus)
    py = os.path.join(venv, "bin", "python")
    if not os.path.exists(py):
        run([*UV, "venv", "-q", "-p", CORPUS_PYTHON, venv], check=True)
        cons = os.path.join(VENV_DIR, f"{corpus}_constraints.txt")
        open(cons, "w").write("\n".join(p for p in pins if "==" in p) + "\n")
        run([*UV, "pip", "install", "-q", "-p", py, "-c", cons, "-e", ROOTS[corpus] + EDITABLE[corpus], *pins],
            check=True)
    PYTHONS[corpus] = py
    print(f"[ok] {corpus}: {run([py, '--version'], capture=True).stdout.strip()}", flush=True)
PYTHON_ARGS = [arg for c, py in PYTHONS.items() for arg in ("--python", f"{c}={py}")]

print("\n[setup 8/8] Preflight: the P3b tests, the rule in force, tRPC as source", flush=True)
if run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_p3b_draw.py",
        "tests/test_p3b_authoring.py"], cwd=SCE_DIR, env=child_env, check=False).returncode != 0:
    abort("the P3b authoring tests fail on this checkout")
from benchmarks.layer4 import specs_p3b  # noqa: E402
if specs_p3b.SEED != SEED or json.load(open(os.path.join(local_out, "draw.json")))["seed"] != SEED:
    abort(f"specs/draw seed is not {SEED}")
rule = run([sys.executable, "-c", "from harness.experiments.layer4.author import P3B_MIN_GOLD as g; "
            "from harness.experiments.retrieval_pivot.p3b_freeze import AMENDMENT as a; print(g, a)"],
           cwd=SCE_DIR, env=child_env, capture=True).stdout.strip().splitlines()[-1]
if rule != f"{MIN_GOLD} {AMENDMENT}":
    abort(f"the harness's rule ({rule}: min gold, amendment) is not the cell's ({MIN_GOLD} {AMENDMENT})")
# tRPC is traced under a generated Vitest config that aliases every @trpc/* export to its source.
# The whole suite runs untraced under the corpus's own config and under it: a test file failing only
# as source would turn every trace of it into an environment fault, so that aborts here.
src_check = run([sys.executable, "-c", f"""
import json, re, subprocess
from harness.experiments.layer4 import author as A
A.NODE["bin"] = {NODE!r}
clone = A.clone_root("trpc")
def suite(extra):
    cmd = A.expand(["{{node}}", "{{vitest}}", *extra, "--run", "--no-threads"], clone, None)
    p = subprocess.run(cmd, cwd=clone + "/" + A.TRPC_CWD, capture_output=True, text=True, timeout=3600)
    out = re.sub(r"\\x1b\\[[0-9;]*m", "", p.stdout + p.stderr)
    return {{"exit": p.returncode, "failed": sorted(set(re.findall(r"FAIL\\s+(\\S+\\.test\\.tsx?)", out))),
            "files": (re.findall(r"Test Files\\s+(.*)", out) or ["?"])[-1].strip(), "tail": out[-1500:]}}
base = suite([])
cfg = A.trpc_source_config(clone)
try:
    src = suite(["--config", str(cfg)])
finally:
    cfg.unlink(missing_ok=True)
print("SRC_CHECK " + json.dumps({{"base": base, "source": src, "aliases": len(A.trpc_source_aliases(clone))}}))
"""], cwd=SCE_DIR, env=child_env, check=False, capture=True)
try:
    sc = json.loads([ln for ln in src_check.stdout.splitlines() if ln.startswith("SRC_CHECK ")][-1][10:])
except Exception:
    abort(f"tRPC source-config check crashed:\n{(src_check.stderr or src_check.stdout)[-3000:]}")
only_source = sorted(set(sc["source"]["failed"]) - set(sc["base"]["failed"]))
print(f"[trpc] own config: exit {sc['base']['exit']}, files {sc['base']['files']}, failing {sc['base']['failed']}", flush=True)
print(f"[trpc] as source ({sc['aliases']} exports aliased): exit {sc['source']['exit']}, files {sc['source']['files']}, "
      f"failing {sc['source']['failed']}", flush=True)
if only_source or (sc["source"]["exit"] != 0 and not sc["source"]["failed"]):
    abort(f"the tRPC suite fails as source where it passes as built: {only_source or 'no test file ran'}\n"
          f"{sc['source']['tail']}")
TRPC_SRC_CHECK = {k: {"exit": v["exit"], "files": v["files"], "failed": v["failed"]} for k, v in sc.items() if k != "aliases"}
SPEC_IDS = {c: [s["task_id"] for s in specs_p3b.SPECS if s["corpus"] == c] for c in CORPORA}
print(f"[ok] specs per corpus: { {c: len(v) for c, v in SPEC_IDS.items()} }", flush=True)


# ============================================================
# Step 1: author, two lanes
# ============================================================
STOP = threading.Event()


def corpus_state(corpus):
    """(kept, decided, finished, env faults): finished = 8 kept, or every spec of the corpus decided."""
    d = decisions_of(os.path.join(local_out, f"authoring_log_{corpus}.json"))
    kept = sum(1 for v in d.values() if v["status"] == "kept")
    env = sum(1 for v in d.values() if v["status"] == "env_fault")
    return kept, len(d), kept >= PER_CORPUS or set(SPEC_IDS[corpus]) <= set(d), env


def push_outputs(message):
    push([PUSH_PATH], message)


def lane_worker(corpora, status, procs):
    for corpus in corpora:
        for attempt in range(1, RETRY_PASSES + 1):
            if STOP.is_set() or out_of_time():
                status[corpus] = "stopped"
                return
            if corpus_state(corpus)[2]:
                break
            cmd = [sys.executable, "-u", "-m", "harness.experiments.layer4.author", "--specs", SPECS,
                   "--eligibility", "p3b", "--per-corpus", str(PER_CORPUS), "--corpus", corpus,
                   "--out", PUSH_PATH, "--names-dir", NAMES_DIR, "--node", NODE, *PYTHON_ARGS]
            log = open(os.path.join(local_out, f"author_{corpus}.log"), "a")
            log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} pass {attempt} ===\n")
            proc = subprocess.Popen(cmd, cwd=SCE_DIR, env=child_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, start_new_session=True)
            procs[corpus] = proc
            for line in proc.stdout:
                log.write(line)
                log.flush()
                if line.startswith(("[keep]", "[skip]", "[env]", "[p3b]", "Traceback")) or "eligibility failure" in line \
                        or "environment is broken" in line:
                    print(f"[{corpus}] {line}", end="", flush=True)
            code = proc.wait()
            log.write(f"=== exit {code} ===\n")
            log.close()
            kept, decided, finished, env = corpus_state(corpus)
            status[corpus] = f"exit {code}, kept {kept}, decided {decided}, env faults {env}, finished {finished}"
            print(f"[{corpus}] pass {attempt}: {status[corpus]}", flush=True)
            if STOP.is_set():
                return
            if finished:
                break
            said = open(os.path.join(local_out, f"author_{corpus}.log")).read()[-4000:]
            if code != 0 and ("eligibility failure" in said or "environment is broken" in said):
                status[corpus] += " - TOOLING FAULT, see the log"
                break  # a rerun would hit the same fault: it needs a code fix
        push_outputs(f"P3b authoring: {corpus} {status.get(corpus, '')}")


if ALREADY_FROZEN:
    print("\n[step 1] skipped: FROZEN.json exists - the set is frozen, nothing is authored again", flush=True)
else:
    print("\n[step 1] author", flush=True)
    for c in CORPORA:
        print(f"  {c}: kept {corpus_state(c)[0]}, decided {corpus_state(c)[1]} of {len(SPEC_IDS[c])} specs", flush=True)
    status, procs = {}, {}
    threads = [threading.Thread(target=lane_worker, args=(lane, status, procs), daemon=True) for lane in LANES]
    for t in threads:
        t.start()
    last_push = time.time()
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(15)
            if out_of_time():
                print("[time] stopping before the session limit - re-run the cell to resume", flush=True)
                STOP.set()
                for p in list(procs.values()):
                    if p.poll() is None:
                        stop_proc(p.pid)
            if time.time() - last_push > PUSH_EVERY_S:
                push_outputs("P3b authoring: progress")
                last_push = time.time()
    except KeyboardInterrupt:
        print("[interrupt] stopping the lanes and pushing", flush=True)
        STOP.set()
        for p in list(procs.values()):
            if p.poll() is None:
                stop_proc(p.pid)
        for t in threads:
            t.join(timeout=120)
        push_outputs("P3b authoring: interrupted")
        raise
    for t in threads:
        t.join()
    push_outputs("P3b authoring: lanes done")
    print(f"[step 1] {status}", flush=True)

# ============================================================
# Steps 2-3: freeze and verify
# ============================================================
states = {c: corpus_state(c) for c in CORPORA}
unfinished = [c for c, s in states.items() if not s[2]]
total = sum(s[0] for s in states.values())
report = ["# P3b task authoring", "", f"tRPC suite untraced: {json.dumps(TRPC_SRC_CHECK)}.", "", f"Branch `{BRANCH}`, HEAD `{HEAD}`, seed {SEED}, amendment {AMENDMENT} "
          f"(gold {MIN_GOLD}..15 within 2 hops). Code: `{json.dumps(CODE)}`.", "",
          "| corpus | kept | decided | env faults | specs | finished |", "|---|---|---|---|---|---|"]
for c, (kept, decided, finished, env) in states.items():
    report.append(f"| {c} | {kept} | {decided} | {env} | {len(SPEC_IDS[c])} | {finished} |")
report += ["", f"Total kept: {total} (target {PER_CORPUS * len(CORPORA)}, minimum {MIN_TASKS}).", ""]
for c in CORPORA:
    d = decisions_of(os.path.join(local_out, f"authoring_log_{c}.json"))
    report += [f"## {c}", "", "| task | focal | status | gold / reason |", "|---|---|---|---|"]
    for tid in SPEC_IDS[c]:
        if tid in d:
            v = d[tid]
            report.append(f"| {tid} | `{v['qname']}` | {v['status']} | {v.get('gold') if v['status'] == 'kept' else v.get('reason')} |")
    report.append("")
    report.append(f"Decided {len(d)} of {len(SPEC_IDS[c])} specs; the rest of the draw order was not traced.")
    report.append("")

outcome = None
if unfinished:
    outcome = f"NOT FROZEN: corpora not finished {unfinished} (stopped, or a tooling fault - see author_<corpus>.log)"
elif ALREADY_FROZEN:
    outcome = "already frozen"
else:
    print("\n[step 2] freeze", flush=True)
    fr = run([sys.executable, "-m", "harness.experiments.retrieval_pivot.p3b_freeze", "freeze"], cwd=SCE_DIR,
             env=child_env, check=False, capture=True)
    print(fr.stdout + fr.stderr, flush=True)
    if fr.returncode != 0:
        outcome = (f"NOT FROZEN: {(fr.stderr or fr.stdout).strip()[-800:]}\nExtend the specs from the same draw "
                   "(p3b_draw --n) and re-run this cell: decided specs are not traced again.")
    else:
        outcome = f"frozen: {fr.stdout.strip()}"

if os.path.exists(FROZEN_PATH):
    print("\n[step 3] verify the frozen set with the runner's guard", flush=True)
    vr = run([sys.executable, "-c", (
        "from pathlib import Path; from harness.experiments.retrieval_pivot import p3b_freeze as F;"
        "from harness.experiments.layer4 import runner as R;"
        f"f = F.verify(Path({PUSH_PATH!r}) / 'tasks'); t = R.load_tasks([Path({PUSH_PATH!r}) / 'tasks']);"
        "assert len(t) == f['total'], (len(t), f['total']); print('verified', f['total'], f['per_corpus'])")],
        cwd=SCE_DIR, env=child_env, check=False, capture=True)
    print(vr.stdout + vr.stderr, flush=True)
    if vr.returncode != 0:
        outcome += f"\nVERIFY FAILED: {(vr.stderr or vr.stdout).strip()[-800:]}"
    else:
        frozen = json.load(open(FROZEN_PATH))
        report += ["## Frozen", "", f"{frozen['total']} tasks, short corpora {frozen['short_corpora']}, "
                   f"trace skips {len(frozen['skipped']['trace'])}, environment faults "
                   f"{len(frozen['skipped']['environment'])}, draw skips "
                   f"{sum(len(v) for v in frozen['skipped']['draw'].values())}. Verified with the runner's guard.", ""]
report[1:1] = ["", f"**Outcome: {outcome}**"]
open(os.path.join(local_out, "AUTHORING.md"), "w").write("\n".join(report) + "\n")
push_outputs(f"P3b authoring: {outcome.splitlines()[0][:120]}")
print("\n" + "\n".join(report), flush=True)
print(f"\n[done] {outcome}", flush=True)
