# ============================================================
# PRISM Layer 4 - tRPC task authoring - Kaggle, self-contained cell
# Paste this whole file into ONE Kaggle notebook cell and Run.
# Settings: CPU is enough (no GPU, no model), Internet ON, secret GITHUB_TOKEN
# (clone + push).
#
# Authors the 4 tRPC tasks of the 16-task Layer 4 suite (the other 12 were
# authored locally, d300b7a) - they need the tRPC workspace, whose install
# needs registry.npmjs.com:
#   l4_fwd_trpc_01  forward   getTRPCErrorFromUnknown  (fallback transformTRPCResponse)
#   l4_fwd_trpc_02  forward   getErrorShape            (fallback callProcedure)
#   l4_bwd_trpc_01  backward  mergeRouters             (fallback getCauseFromUnknown)
#   l4_bwd_trpc_02  backward  share                    (fallback tap)
# all in packages/server/src (Prism's tRPC index root); the specs, with the
# changes, test files and depths, are in benchmarks/layer4/specs.py.
#
# Steps: the environment of the validated tRPC cell (Node 20.18.1, pnpm
# 8.5.1, frozen install, @trpc/server built), then
#   python -m harness.experiments.layer4.author --corpus trpc --node <node20>
# which for each task traces the focal with Vitest 0.32 in fork mode
# (`--run --no-threads`, the runtime in NODE_OPTIONS), derives the forward
# gold (callers within max_depth call hops) or runs both gates (stub with
# STUB_IMPLEMENTATION fails, original passes) and traces the gating tests for
# the backward oracle, validates the task against task.schema.json and
# tRPC's Prism names, and falls back to the next candidate if one yields no
# valid task. Then the complete suite is checked: 16 tasks, 2 forward + 2
# backward per corpus, every task valid, and the Layer 4 test files pass.
#
# Output (pushed to BRANCH): benchmarks/layer4/{tasks,manifests,gates}/
# l4_*_trpc_*.json and reports/layer4/trpc_authoring/{AUTHORING.md,
# authoring.log, suite.json}. The tasks are pushed only when the whole
# suite validates; otherwise only the report (with the log) is pushed.
# About 15-25 min.
# ============================================================

import json
import os
import shutil
import subprocess
import sys
import time
import traceback

# ---------- Config ----------
BRANCH = "eval/held-out-benchmark"
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
EXPECTED_GITHUB_USER = "rohith-hash-stack"
PUSH_RESULTS = True
NODE_VERSION = "20.18.1"
PNPM_VERSION = "8.5.1"
TRPC_COMMIT = "2ec29bfa2e3a170901be5201962f30e9faf73f96"
SCE_DIR = "/kaggle/working/SCE"
NODE_DIR = "/kaggle/working/node20"
PUSH_PATH = "reports/layer4/trpc_authoring"
INDEX_PREFIX = "packages/server/src"  # Prism's tRPC corpus root (benchmarks/corpora/pinned_commits.json subdir)
STUB = "STUB_IMPLEMENTATION"
MAX_PUSH_FILE_MB = 90
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")


START = time.time()
print("=" * 64)
print("PRISM Layer 4 - tRPC task authoring")
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


def push(paths, message, max_attempts=4):
    if not (PUSH_RESULTS and os.environ.get("GITHUB_TOKEN")):
        print("[push] skipped", flush=True)
        return False
    run(["git", "config", "user.email", "kaggle@local"], cwd=SCE_DIR)
    run(["git", "config", "user.name", "Kaggle"], cwd=SCE_DIR)
    run(["git", "add", "-f", *paths], cwd=SCE_DIR)
    for f in run(["git", "diff", "--cached", "--name-only"], cwd=SCE_DIR, capture=True).stdout.split():
        p = os.path.join(SCE_DIR, f)
        if os.path.isfile(p) and os.path.getsize(p) > MAX_PUSH_FILE_MB * 2**20:
            print(f"[push] [warn] not pushing {f}", flush=True)
            run(["git", "reset", "-q", "--", f], cwd=SCE_DIR, check=False)
    if run(["git", "diff", "--cached", "--quiet"], cwd=SCE_DIR, check=False).returncode == 0:
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
    print("[push] FAILED after retries - the report is still on local disk", flush=True)
    return False


# ============================================================
# Setup
# ============================================================
print("\n[setup 1/6] GitHub token", flush=True)
github_token = os.environ.get("GITHUB_TOKEN")
if not github_token:
    try:
        from kaggle_secrets import UserSecretsClient
        github_token = UserSecretsClient().get_secret("GITHUB_TOKEN")
    except Exception as exc:
        print(f"[warn] GITHUB_TOKEN unavailable ({exc}): anonymous clone, no push", flush=True)
if github_token:
    os.environ["GITHUB_TOKEN"] = github_token
    who = json.loads(run(["curl", "-s", "-H", f"Authorization: token {github_token}", "https://api.github.com/user"],
                         capture=True).stdout or "{}")
    if who.get("login") != EXPECTED_GITHUB_USER:
        abort(f"token is {who.get('login')!r}")
    print(f"[ok] token: {who['login']}", flush=True)

print("\n[setup 2/6] Clone SCE", flush=True)
clone_url = REPO_URL.replace("https://", f"https://x-access-token:{github_token}@") if github_token else REPO_URL
if not os.path.isdir(f"{SCE_DIR}/.git"):
    run(["git", "clone", clone_url, SCE_DIR], check=True, env=GIT_ENV)
run(["git", "remote", "set-url", "origin", clone_url], cwd=SCE_DIR)
run(["git", "fetch", "origin", BRANCH], cwd=SCE_DIR, check=True, env=GIT_ENV)
if PUSH_RESULTS and github_token:
    probe = run(["git", "push", "--dry-run", "origin", f"origin/{BRANCH}:refs/heads/{BRANCH}"],
                cwd=SCE_DIR, check=False, capture=True, env=GIT_ENV)
    if probe.returncode != 0:
        abort(f"this GITHUB_TOKEN cannot push to {BRANCH}:\n{mask(probe.stderr or probe.stdout).strip()[-1500:]}")
run(["git", "checkout", "-B", BRANCH, f"origin/{BRANCH}"], cwd=SCE_DIR, check=True)
run(["git", "clean", "-fdx", "-e", ".benchmarks"], cwd=SCE_DIR, check=True)
HEAD = run(["git", "rev-parse", "HEAD"], cwd=SCE_DIR, capture=True).stdout.strip()
for f in ("harness/experiments/layer4/trace_node_runtime.cjs", "harness/experiments/layer4/splice.py"):
    if not os.path.exists(os.path.join(SCE_DIR, f)):
        abort(f"{BRANCH} has no {f} (wrong branch/commit)")
runtime_src = open(os.path.join(SCE_DIR, "harness/experiments/layer4/trace_node_runtime.cjs")).read()
if "L4_MULTI" not in runtime_src or "decodeMappings" not in runtime_src:
    abort("the checked-out tracer runtime predates per-process output / source maps")
print(f"[ok] SCE HEAD {HEAD}", flush=True)

print("\n[setup 3/6] Python package (Prism index for name mapping)", flush=True)
run([sys.executable, "-m", "pip", "install", "-q", "-e", "."], cwd=SCE_DIR, check=True)
# the running kernel never reads the .pth file `pip install -e .` just wrote: put the
# packages on sys.path directly (prism lives in src/, harness and benchmarks at the root)
for path in (os.path.join(SCE_DIR, "src"), SCE_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)
import importlib.util  # noqa: E402

importlib.invalidate_caches()
for mod in ("prism", "harness", "benchmarks"):
    if importlib.util.find_spec(mod) is None:
        abort(f"`{mod}` is not importable from {SCE_DIR}")

print(f"\n[setup 4/6] Node {NODE_VERSION} + pnpm {PNPM_VERSION}", flush=True)
node_bin = os.path.join(NODE_DIR, "bin")
if not os.path.exists(os.path.join(node_bin, "node")):
    tarball = f"node-v{NODE_VERSION}-linux-x64.tar.xz"
    run(f"curl -fsSL https://nodejs.org/dist/v{NODE_VERSION}/{tarball} -o /tmp/{tarball}", check=True)
    os.makedirs(NODE_DIR, exist_ok=True)
    run(f"tar -xJf /tmp/{tarball} -C {NODE_DIR} --strip-components=1", check=True)
os.environ["PATH"] = node_bin + os.pathsep + os.environ["PATH"]
NODE = os.path.join(node_bin, "node")
node_version = run([NODE, "--version"], capture=True).stdout.strip()
if not node_version.startswith("v20."):
    abort(f"node is {node_version}")
PNPM = os.path.join(node_bin, "pnpm")  # installed next to this node, never a system pnpm
if not os.path.exists(PNPM) or run([PNPM, "--version"], check=False, capture=True).stdout.strip() != PNPM_VERSION:
    run([os.path.join(node_bin, "npm"), "install", "-g", "--silent", f"pnpm@{PNPM_VERSION}"], check=True)
if not os.path.exists(PNPM):
    abort(f"npm install -g pnpm@{PNPM_VERSION} left no {PNPM} (PATH: {shutil.which('pnpm')})")
pnpm_version = run([PNPM, "--version"], capture=True).stdout.strip()
if pnpm_version != PNPM_VERSION:
    abort(f"pnpm is {pnpm_version}")
print(f"[ok] node {node_version}, pnpm {pnpm_version}", flush=True)

print("\n[setup 5/6] tRPC checkout + frozen install", flush=True)
from benchmarks.corpora.resolver import resolve  # noqa: E402

index_root = str(resolve("trpc"))
TRPC = run(["git", "rev-parse", "--show-toplevel"], cwd=index_root, capture=True).stdout.strip()
trpc_head = run(["git", "rev-parse", "HEAD"], cwd=TRPC, capture=True).stdout.strip()
if trpc_head != TRPC_COMMIT:
    abort(f"tRPC checkout is at {trpc_head}, expected {TRPC_COMMIT}")
if os.path.realpath(index_root) != os.path.realpath(os.path.join(TRPC, INDEX_PREFIX)):
    abort(f"Prism's tRPC index root is {index_root}, expected <clone>/{INDEX_PREFIX}")
dirty = run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=TRPC, capture=True).stdout.strip()
if dirty:
    abort(f"tRPC checkout has tracked changes:\n{dirty}")
install_env = dict(os.environ, CI="1", HUSKY="0", PUPPETEER_SKIP_DOWNLOAD="1", PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD="1",
                   PRISMA_SKIP_POSTINSTALL_GENERATE="1", NEXT_TELEMETRY_DISABLED="1", TURBO_TELEMETRY_DISABLED="1")
t = time.time()
install = run([PNPM, "install", "--frozen-lockfile", "--ignore-scripts", "--reporter=append-only"], cwd=TRPC,
              check=False, env=install_env, timeout=3600)
install_s = round(time.time() - t)
if install.returncode != 0:
    abort("pnpm install --frozen-lockfile failed (no unfrozen fallback: it would drift versions)")
run([PNPM, "rebuild", "esbuild", "@swc/core"], cwd=TRPC, check=False, env=install_env)  # native postinstalls only
print(f"[ok] installed in {install_s}s", flush=True)

print("\n[setup 6/6] Build @trpc/server (client tests import its dist/)", flush=True)
t = time.time()
built = False
run([PNPM, "--filter", "@trpc/server", "codegen-entrypoints"], cwd=TRPC, check=False, env=install_env)
if run([PNPM, "--filter", "@trpc/server", "build"], cwd=TRPC, check=False, env=install_env).returncode == 0:
    built = True
elif run([PNPM, "exec", "turbo", "--filter", "@trpc/server", "build"], cwd=TRPC, check=False,
         env=install_env).returncode == 0:
    built = True
built = built and os.path.exists(os.path.join(TRPC, "packages/server/dist/observable/index.mjs"))
build_s = round(time.time() - t)
codegen_diff = run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=TRPC, capture=True).stdout.strip()
if codegen_diff:  # codegen rewrote tracked files: keep dist/, restore the sources
    print(f"[note] build touched tracked files, restoring:\n{codegen_diff}", flush=True)
    run(["git", "checkout", "--", "."], cwd=TRPC, check=True)
print(f"[{'ok' if built else 'warn'}] @trpc/server dist {'built' if built else 'MISSING'} ({build_s}s)", flush=True)

TESTS_DIR = os.path.join(TRPC, "packages/tests")
vitest_pkg = json.load(open(os.path.join(TESTS_DIR, "node_modules/vitest/package.json")))
VITEST_BIN = os.path.join(TESTS_DIR, "node_modules/vitest", vitest_pkg["bin"]["vitest"]
                          if isinstance(vitest_pkg["bin"], dict) else vitest_pkg["bin"])
VITEST_VERSION = vitest_pkg["version"]
print(f"[ok] vitest {VITEST_VERSION} ({VITEST_BIN})", flush=True)


# ============================================================
# Authoring
# ============================================================
OUT = os.path.join(SCE_DIR, PUSH_PATH)
os.makedirs(OUT, exist_ok=True)
LOG = os.path.join(OUT, "authoring.log")
TRPC_IDS = ["l4_fwd_trpc_01", "l4_fwd_trpc_02", "l4_bwd_trpc_01", "l4_bwd_trpc_02"]
LAYER4_TESTS = ["tests/test_layer4_tasks.py", "tests/test_layer4_author.py"]
child_env = dict(os.environ, PYTHONPATH=os.pathsep.join([SCE_DIR, os.path.join(SCE_DIR, "src")]),
                 PYTHONDONTWRITEBYTECODE="1")
child_env.pop("GITHUB_TOKEN", None)

print("\n[deps] jsonschema (task validation)", flush=True)
run([sys.executable, "-m", "pip", "install", "-q", "jsonschema", "pytest"], check=True)

for tid in TRPC_IDS:  # a rerun authors from scratch
    for sub in ("tasks", "manifests", "gates"):
        p = os.path.join(SCE_DIR, "benchmarks", "layer4", sub, f"{tid}.json")
        if os.path.exists(p):
            os.remove(p)
REJECTED = os.path.join(SCE_DIR, "benchmarks", "layer4", "rejected")
for name in (os.listdir(REJECTED) if os.path.isdir(REJECTED) else []):
    if "_trpc_" in name:
        os.remove(os.path.join(REJECTED, name))

print("\n[author] python -m harness.experiments.layer4.author --corpus trpc", flush=True)
t = time.time()
with open(LOG, "w") as log:
    proc = subprocess.Popen([sys.executable, "-u", "-m", "harness.experiments.layer4.author", "--corpus", "trpc",
                             "--node", NODE], cwd=SCE_DIR, env=child_env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        print(line, end="", flush=True)
        log.write(line)
    author_code = proc.wait()
author_s = round(time.time() - t)
print(f"[author] exit {author_code} in {author_s}s", flush=True)

trpc_dirty = run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=TRPC, capture=True).stdout.strip()
if trpc_dirty:
    print(f"[warn] tRPC checkout not clean after authoring:\n{trpc_dirty}", flush=True)

# ============================================================
# Complete-suite validation (all 16 tasks)
# ============================================================
print("\n[suite] validate all tasks", flush=True)
check = run([sys.executable, "-c", """
import json, sys
from pathlib import Path
from harness.experiments.layer4 import tasks as T
paths = sorted(Path('benchmarks/layer4/tasks').glob('l4_*.json'))
tasks = [json.loads(p.read_text()) for p in paths]
per_task = {t['task_id']: T.validate_task(t) for t in tasks}
missing = [t['task_id'] for t in tasks if not Path('benchmarks/layer4/manifests', t['task_id'] + '.json').exists()
           or (t['family'] == 'backward_adaptation' and not Path('benchmarks/layer4/gates', t['task_id'] + '.json').exists())]
out = {'n_tasks': len(tasks), 'suite_errors': T.validate_suite(tasks), 'task_errors': {k: v for k, v in per_task.items() if v},
       'missing_artifacts': missing,
       'tasks': {t['task_id']: {'focal': t['focal']['qname'], 'file': t['focal']['file'],
                                'n_gold': len(t['gold'].get('affected_symbols') or []),
                                'n_oracle': len(t['arm_view']['oracle_symbols'])} for t in tasks}}
print(json.dumps(out))
"""], cwd=SCE_DIR, env=child_env, check=False, capture=True)
try:
    suite = json.loads(check.stdout.strip().splitlines()[-1])
except Exception:
    suite = {"n_tasks": 0, "suite_errors": [f"validation crashed: {(check.stderr or check.stdout)[-1500:]}"],
             "task_errors": {}, "missing_artifacts": [], "tasks": {}}
# --noconftest: tests/conftest.py's autouse fixture imports benchmarks.runner (the bench
# extras, not installed here); the Layer 4 tests need none of it
pytest = run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--noconftest", *LAYER4_TESTS], cwd=SCE_DIR,
             env=child_env, check=False, capture=True)
suite["layer4_tests"] = {"exit_code": pytest.returncode, "tail": (pytest.stdout + pytest.stderr)[-1500:]}
authored = [tid for tid in TRPC_IDS if os.path.exists(os.path.join(SCE_DIR, "benchmarks", "layer4", "tasks", f"{tid}.json"))]
suite_ok = (author_code == 0 and len(authored) == 4 and suite["n_tasks"] == 16 and not suite["suite_errors"]
            and not suite["task_errors"] and not suite["missing_artifacts"] and pytest.returncode == 0 and not trpc_dirty)
suite.update({"valid": suite_ok, "author_exit": author_code, "author_s": author_s, "authored_trpc": authored,
              "sce_head": HEAD, "trpc_commit": trpc_head, "node": node_version, "pnpm": pnpm_version,
              "vitest": VITEST_VERSION, "elapsed_s": round(time.time() - START)})
json.dump(suite, open(os.path.join(OUT, "suite.json"), "w"), indent=1, sort_keys=True)

# ============================================================
# Report + push
# ============================================================
md = [f"# Layer 4 tRPC task authoring: {'PASS' if suite_ok else 'FAIL'}", "",
      f"SCE `{HEAD[:10]}`, tRPC `{trpc_head[:10]}`, Node {node_version}, pnpm {pnpm_version}, Vitest {VITEST_VERSION}; "
      f"install {install_s}s, server build {build_s}s, authoring {author_s}s (exit {author_code}).", "",
      f"Suite: {suite['n_tasks']} tasks; suite errors {suite['suite_errors'] or 'none'}; task errors "
      f"{suite['task_errors'] or 'none'}; missing artifacts {suite['missing_artifacts'] or 'none'}; Layer 4 tests "
      f"exit {pytest.returncode}; tRPC checkout clean: {not trpc_dirty}.", ""]
for tid in TRPC_IDS:
    path = os.path.join(SCE_DIR, "benchmarks", "layer4", "tasks", f"{tid}.json")
    if not os.path.exists(path):
        md += [f"## {tid}: not authored", "", "See authoring.log.", ""]
        continue
    task = json.load(open(path))
    man = json.load(open(os.path.join(SCE_DIR, "benchmarks", "layer4", "manifests", f"{tid}.json")))
    md += [f"## {tid}: `{task['focal']['qname']}` ({task['focal']['file']}:{task['focal']['line']})", ""]
    if man.get("rejected_candidates"):
        md += ["Rejected candidates: " + "; ".join(f"`{r['qname']}` ({r['reason'].splitlines()[0][:160]})"
                                                    for r in man["rejected_candidates"]), ""]
    if task["family"] == "forward_blast":
        md += [f"Gold (max_depth {task['gold']['max_depth']}): " + ", ".join(f"`{q}`" for q in task["gold"]["affected_symbols"]),
               f"Focal calls {man.get('focal_calls')}, tests {len(man.get('tests', []))}, "
               f"trace attempts {man.get('trace_attempts')}.", ""]
    else:
        v = task["validity"]
        md += [f"Gates: positive {v['positive_gate']}, negative {v['negative_gate']}.",
               "Oracle: " + ", ".join(f"`{q}`" for q in task["arm_view"]["oracle_symbols"]),
               f"Excluded from every arm: {task['arm_view']['exclude_paths']}.", ""]
if rejected_files:
    md += ["## Rejected candidates (traces in benchmarks/layer4/rejected/)", ""]
    for f in rejected_files:
        r = json.load(open(os.path.join(REJECTED, f)))
        tr = r.get("trace") or {}
        md += [f"- `{r['qname']}` for {r['task_id']}: {r['reason'].splitlines()[0][:200]} "
               f"(exit {tr.get('exit_code')}, focal calls {tr.get('focal_calls')}, tests {tr.get('n_tests')}, "
               f"depths {dict(list((tr.get('ancestor_depth') or {}).items())[:6])})"]
    md.append("")
open(os.path.join(OUT, "AUTHORING.md"), "w").write("\n".join(md) + "\n")
print("\n" + "\n".join(md), flush=True)

paths = [PUSH_PATH]
rejected_files = sorted(f for f in (os.listdir(REJECTED) if os.path.isdir(REJECTED) else []) if "_trpc_" in f)
paths += [os.path.join("benchmarks", "layer4", "rejected", f) for f in rejected_files]  # diagnostics, pass or fail
if suite_ok:
    for sub in ("tasks", "manifests", "gates"):
        paths += [os.path.join("benchmarks", "layer4", sub, f"{tid}.json") for tid in TRPC_IDS
                  if os.path.exists(os.path.join(SCE_DIR, "benchmarks", "layer4", sub, f"{tid}.json"))]
push(paths, f"Layer 4: author the 4 tRPC tasks ({'16-task suite complete' if suite_ok else 'FAIL, report only'})")
print(f"\n[done] {'PASS' if suite_ok else 'FAIL'} in {suite['elapsed_s']}s", flush=True)
