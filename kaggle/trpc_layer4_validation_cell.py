# ============================================================
# PRISM Layer 4 - tRPC / Vitest tooling validation - Kaggle, self-contained cell
# Paste this whole file into ONE Kaggle notebook cell and Run.
# Settings: CPU is enough (no GPU, no model), Internet ON,
# secret GITHUB_TOKEN (clone + push of the report; optional if the repo is
# public and PUSH_RESULTS is False).
#
# Validates the Layer 4 Node tooling on the pinned tRPC checkout
# (v10.45.4, 2ec29bfa) before any tRPC task is authored:
#   1. environment - Node 20 (static tarball) + pnpm 8.5.1 (the repo's
#      `packageManager`), `pnpm install --frozen-lockfile --ignore-scripts`,
#      `@trpc/server` built (client tests import its dist/ entry points)
#   2. tracer - one Vitest file per target, `--run --no-threads` (Vitest
#      0.32: child-process forks, so the tracer's same-thread inspector
#      session can read V8 coverage), the runtime in NODE_OPTIONS
#      (`--require trace_node_runtime.cjs --enable-source-maps`), the
#      focal function instrumented on disk and restored; the traced run
#      must report the same test counts as an untraced baseline
#   3. TypeScript source maps - stack frames and coverage must resolve to
#      the original .ts file and line (the caller frame in the test file
#      points at a line that calls the target; callees are .ts functions
#      that Prism's index knows), plus a run without --enable-source-maps
#      as a diagnostic
#   4. validity gates - negative: the body stubbed with
#      `throw new Error("STUB_IMPLEMENTATION")` makes the file fail;
#      positive: the original passes with 0 failures; plus the splice
#      grader on the original definition (pass) and a wrong body (fail)
#
# Targets:
#   primary        getTRPCErrorFromUnknown  packages/server/src/error/TRPCError.ts:4
#                  (inside Prism's tRPC index, packages/server/src) over
#                  server/TRPCError.test.ts; callees TRPCError's
#                  constructor and getCauseFromUnknown
#   cross_package  createChain  packages/client/src/links/internals/createChain.ts:10
#                  over its own test; callee `observable` comes from
#                  @trpc/server's rollup dist/ (no source maps): resolved
#                  by name to packages/server/src (`LineMap.dist_name`)
#
# Output: reports/layer4/trpc_validation/{validation.json, VALIDATION.md,
# manifests/} pushed to BRANCH. About 15-25 min (the install dominates).
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
PUSH_PATH = "reports/layer4/trpc_validation"
INDEX_PREFIX = "packages/server/src"  # Prism's tRPC corpus root (benchmarks/corpora/pinned_commits.json subdir)
STUB = "STUB_IMPLEMENTATION"
MAX_PUSH_FILE_MB = 90
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")

TARGETS = [
    {
        "id": "primary",
        "file": "packages/server/src/error/TRPCError.ts", "line": 4, "name": "getTRPCErrorFromUnknown",
        "qname": "error.TRPCError.getTRPCErrorFromUnknown",
        "test": "server/TRPCError.test.ts",  # Vitest filter, relative to its root (packages/)
        "test_file": "packages/tests/server/TRPCError.test.ts",
        "expect_callees": ["shared.getCauseFromUnknown.getCauseFromUnknown"],  # Prism qualified names
        "expect_callee_files": ["packages/server/src/shared/getCauseFromUnknown.ts"],
        "wrong_body": "return new TRPCError({ code: 'BAD_REQUEST' });",
    },
    {
        "id": "cross_package",
        "file": "packages/client/src/links/internals/createChain.ts", "line": 10, "name": "createChain",
        "qname": None,  # outside Prism's tRPC index: raw locations only
        "test": "client/src/links/internals/createChain.test.ts",
        "test_file": "packages/client/src/links/internals/createChain.test.ts",
        "expect_dist_names": {"observable": "observable.observable.observable"},
        "expect_callees": [],
        "expect_callee_files": [],
        "wrong_body": "return opts.links[0]({ op: opts.op, next: () => { throw new Error('x'); } });",
    },
]

START = time.time()
print("=" * 64)
print("PRISM Layer 4 - tRPC / Vitest validation")
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
sys.path.insert(0, SCE_DIR)

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
# Validation
# ============================================================
OUT = os.path.join(SCE_DIR, PUSH_PATH)
os.makedirs(os.path.join(OUT, "manifests"), exist_ok=True)
WORK = "/kaggle/working/l4_trpc"
os.makedirs(WORK, exist_ok=True)
CRASH = None  # any exception below is written to the report and pushed, never lost

try:
    from harness.experiments.layer4 import splice as S
    from harness.experiments.layer4.source_edit import locate
    from harness.experiments.layer4.trace_node import RUNTIME, LineMap, map_manifest, rebase, run_traced
    from prism.engine import PrismEngine

    print("\n[index] Prism index of packages/server/src", flush=True)
    t = time.time()
    builder = PrismEngine.from_repo(index_root).builder
    LINES = LineMap(builder)
    print(f"[ok] {len(builder.symbol_table)} symbols ({time.time() - t:.0f}s)", flush=True)
except (Exception, SystemExit):
    CRASH = traceback.format_exc()
    print(f"[CRASH] imports / index:\n{CRASH}", flush=True)


def vitest_cmd(test, report):
    return [NODE, VITEST_BIN, "--run", "--no-threads", "--reporter=json", f"--outputFile={report}", test]


def counts(report):
    try:
        r = json.load(open(report))
    except Exception as exc:
        return {"error": f"no JSON report: {exc}"}
    failures = [m for f in r.get("testResults", []) for a in f.get("assertionResults", [])
                for m in a.get("failureMessages", [])]
    return {"total": r.get("numTotalTests"), "passed": r.get("numPassedTests"), "failed": r.get("numFailedTests"),
            "success": r.get("success"), "stub_seen": any(STUB in m for m in failures),
            "first_failure": failures[0][:300] if failures else None}


def line_text(rel, line):
    try:
        return open(os.path.join(TRPC, rel)).read().splitlines()[line - 1]
    except Exception:
        return ""


def frame(entry):  # "<abs file>:<line>:<name>" from the runtime's raw_stack
    path, line, name = (entry.split(":", 2) + ["", ""])[:3]
    rel = os.path.relpath(path, TRPC) if path.startswith(TRPC + os.sep) else path
    return rel.replace(os.sep, "/"), int(line) if line.isdigit() else 0, name


def check(results, key, ok, detail=""):
    results.append({"check": key, "ok": bool(ok), "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] {key}{(' - ' + detail) if detail else ''}", flush=True)
    return ok


report = {"sce_head": HEAD, "trpc_commit": trpc_head, "node": node_version, "pnpm": pnpm_version,
          "vitest": VITEST_VERSION, "install_s": install_s, "server_dist_built": built, "build_s": build_s,
          "targets": {}}

def validate_target(target):
    tid = target["id"]
    print(f"\n{'=' * 64}\n[target {tid}] {target['name']}  {target['file']}:{target['line']}\n{'=' * 64}", flush=True)
    results = []
    focal_abs = os.path.join(TRPC, target["file"])
    original_bytes = open(focal_abs, "rb").read()
    span = locate(focal_abs, target["line"])
    span_lines = (original_bytes[:span.start].count(b"\n") + 1, original_bytes[:span.end].count(b"\n") + 1)
    if tid == "cross_package" and not built:
        check(results, "server dist built (needed by this target)", False)
        report["targets"][tid] = {"results": results, "valid": False}
        return

    # -- baseline (untraced) --
    base_json = f"{WORK}/{tid}_baseline.json"
    base = S._run(S.SpliceTask(tid, TRPC, target["file"], target["line"], vitest_cmd(target["test"], base_json),
                               cwd="packages/tests", name=target["name"]))
    base_counts = counts(base_json)
    check(results, "baseline passes", base["passed"] and base_counts.get("failed") == 0 and (base_counts.get("passed") or 0) > 0,
          f"{base_counts} in {base['seconds']}s")

    # -- traced runs: with and without --enable-source-maps --
    traced = {}
    for mode, node_opts in (("enable_source_maps", f"--require {RUNTIME} --enable-source-maps"),
                            ("no_flag", f"--require {RUNTIME}")):
        rep = f"{WORK}/{tid}_traced_{mode}.json"
        raw_out = f"{WORK}/{tid}_{mode}_raw.json"
        t = time.time()
        raw = run_traced(TRPC, target["file"], target["line"], vitest_cmd(target["test"], rep), raw_out,
                         env={"NODE_OPTIONS": node_opts}, cwd="packages/tests", multi=True, timeout=1800)
        raw["seconds"] = round(time.time() - t, 1)
        raw["counts"] = counts(rep)
        traced[mode] = raw
        json.dump(raw, open(os.path.join(OUT, "manifests", f"{tid}_{mode}_raw.json"), "w"), indent=1, sort_keys=True)
    raw = traced["enable_source_maps"]
    c = raw["counts"]
    check(results, "runner not corrupted (traced exit 0, same counts as baseline)",
          raw["exit_code"] == 0 and c.get("total") == base_counts.get("total") and c.get("passed") == base_counts.get("passed"),
          f"exit {raw['exit_code']}, {c}")
    check(results, "focal function ran in a forked worker", raw.get("focal_calls", 0) > 0 and raw.get("processes", 0) >= 1,
          f"{raw.get('focal_calls')} calls in {raw.get('processes')} process(es)")
    check(results, "tests attributed (expect.getState)", len(raw.get("tests", [])) > 0,
          f"{len(raw.get('tests', []))} tests, e.g. {raw.get('tests', [''])[:2]}")
    file_restored = open(focal_abs, "rb").read() == original_bytes
    check(results, "focal file restored byte-for-byte", file_restored)

    # -- source maps: stack --
    stack = [frame(f) for f in (raw.get("raw_stack") or [])]
    top = stack[0] if stack else ("", 0, "")
    check(results, "stack: focal frame is the original .ts file inside the definition",
          top[0] == target["file"] and span_lines[0] <= top[1] <= span_lines[1], f"{top} vs lines {span_lines}")
    callers = [f for f in stack if f[0] == target["test_file"]]
    caller_ok = any(target["name"] in line_text(f[0], f[1]) for f in callers)
    check(results, "stack: caller frame in the test file points at a line calling the target", caller_ok,
          "; ".join(f"{f[0]}:{f[1]} `{line_text(f[0], f[1]).strip()[:80]}`" for f in callers[:3]) or f"stack: {stack[:6]}")
    js_frames = [f for f in stack if f[0].startswith("packages/") and "/src/" in f[0] and f[0].endswith(".js")]
    check(results, "stack: no transpiled .js frames for repo sources", not js_frames, str(js_frames[:3]))

    # -- source maps: coverage --
    sm = raw.get("source_mapping", {})
    desc = raw.get("descendants", {})
    src_js = [k for k in desc if "/src/" in k and k.split(":")[0].endswith(".js")]
    check(results, "coverage: callees resolve to original .ts sources", desc and not src_js,
          f"{len(desc)} callees, mapped {sm.get('mapped')} / unmapped {sm.get('unmapped')}; .js: {src_js[:3]}")
    for f in target["expect_callee_files"]:
        hits = [k for k in desc if k.startswith(f + ":")]
        check(results, f"coverage: expected callee in {f}", hits, str(hits))
        for k in hits:  # the mapped line must hold that function's definition
            rel, line, name = k.split(":", 2)
            check(results, f"coverage: {k} line holds `{name}`", name in line_text(rel, int(line)) or name == "<anonymous>",
                  f"`{line_text(rel, int(line)).strip()[:80]}`")

    # -- Prism names --
    rb = rebase(raw, INDEX_PREFIX)
    mapped = map_manifest(rb, builder, target["qname"] or "<outside-index>")
    json.dump(mapped, open(os.path.join(OUT, "manifests", f"{tid}_mapped.json"), "w"), indent=1, sort_keys=True)
    for q in target["expect_callees"]:
        check(results, f"Prism name: {q} among descendants", q in mapped["descendants"], str(sorted(mapped["descendants"])[:8]))
    if target["qname"]:
        check(results, "Prism name: focal location maps to the focal symbol",
              LINES.name(target["file"].removeprefix(INDEX_PREFIX + "/"), span_lines[0]) == target["qname"])
    for name, q in target.get("expect_dist_names", {}).items():
        dist = [k for k in desc if k.startswith("packages/server/dist/") and k.endswith(":" + name)]
        src = [k for k in desc if k.startswith("packages/server/src/") and k.endswith(":" + name)]
        check(results, f"cross-package callee `{name}` recorded (dist/ or mapped src/)", dist or src, str(dist or src))
        check(results, f"cross-package callee `{name}` -> Prism {q}", q in mapped["descendants"],
              f"resolved_by_name: {mapped.get('resolved_by_name', {})}")

    alt = traced["no_flag"]
    alt_stack = [frame(f) for f in (alt.get("raw_stack") or [])]
    results.append({"check": "diagnostic: without --enable-source-maps", "ok": None,
                    "detail": f"exit {alt['exit_code']}, top frame {alt_stack[:1]}, descendants {len(alt.get('descendants', {}))}, "
                              f"same descendants as with the flag: {alt.get('descendants') == raw.get('descendants')}"})
    print(f"  [info] {results[-1]['detail']}", flush=True)

    # -- validity gates --
    gate_json = f"{WORK}/{tid}_gate.json"
    task = S.SpliceTask(f"trpc_{tid}", TRPC, target["file"], target["line"], vitest_cmd(target["test"], gate_json),
                        cwd="packages/tests", name=target["name"], timeout=900)
    g = S.gates(task)
    neg = counts(gate_json)  # the last run of gates() is the stubbed one
    check(results, "negative gate: STUB_IMPLEMENTATION body fails the suite",
          not g["negative"]["passed"] and g["negative"]["exit_code"] is not None and (neg.get("failed") or 0) > 0,
          f"exit {g['negative']['exit_code']}, {neg}")
    check(results, "negative gate: failures carry STUB_IMPLEMENTATION", neg.get("stub_seen") or g["negative"]["stub_seen"])
    check(results, "positive gate: original passes with 0 failures", g["positive"]["passed"] and base_counts.get("failed") == 0,
          f"exit {g['positive']['exit_code']}")
    check(results, "gates valid", g["valid"])
    definition = original_bytes[span.start:span.end].decode()
    good = S.evaluate(task, f"```ts\n{definition}\n```")
    check(results, "splice: original definition as a candidate passes", good["passed"] and good["mode"] == "function",
          f"mode {good['mode']}, exit {good['exit_code']}")
    bad = S.evaluate(task, f"```ts\n{target['wrong_body']}\n```")
    check(results, "splice: wrong body fails", not bad["passed"] and bad["mode"] == "body", f"exit {bad['exit_code']}")
    check(results, "focal file restored after gates and splices", open(focal_abs, "rb").read() == original_bytes)

    hard = [r for r in results if r["ok"] is not None]
    report["targets"][tid] = {
        "target": target, "span_lines": span_lines, "baseline": base_counts, "results": results,
        "valid": all(r["ok"] for r in hard),
        "trace": {k: raw.get(k) for k in ("focal_calls", "processes", "tests", "ancestors", "descendants",
                                          "source_mapping", "raw_stack", "seconds", "async_window")},
        "mapped": {k: mapped.get(k) for k in ("descendants", "ancestors", "descendants_unmapped", "resolved_by_name",
                                             "outside_ancestors", "outside_descendants")},
    }

report["crash"] = CRASH
for target in TARGETS if CRASH is None else []:
    try:
        validate_target(target)
    except (Exception, SystemExit):
        tb = traceback.format_exc()
        print(f"[CRASH] target {target['id']}:\n{tb}", flush=True)
        report["targets"][target["id"]] = {"target": target, "valid": False, "crash": tb, "results": [
            {"check": "target ran to completion", "ok": False, "detail": tb.strip().splitlines()[-1]}]}
    finally:  # whatever happened, the focal file goes back
        run(["git", "checkout", "--", target["file"]], cwd=TRPC, check=False)

dirty = run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=TRPC, capture=True).stdout.strip()
report["trpc_clean_after"] = not dirty
report["valid"] = (CRASH is None and len(report["targets"]) == len(TARGETS)
                   and all(t["valid"] for t in report["targets"].values()) and not dirty)
report["elapsed_s"] = round(time.time() - START)

# ============================================================
# Report
# ============================================================
json.dump(report, open(os.path.join(OUT, "validation.json"), "w"), indent=1, sort_keys=True, default=str)
md = [f"# Layer 4 tRPC / Vitest validation: {'PASS' if report['valid'] else 'FAIL'}", "",
      f"SCE `{HEAD[:10]}`, tRPC `{trpc_head[:10]}`, Node {node_version}, pnpm {pnpm_version}, Vitest {VITEST_VERSION}; "
      f"install {install_s}s, server build {build_s}s ({'ok' if built else 'missing'}); "
      f"tRPC checkout clean afterwards: {report['trpc_clean_after']}.", ""]
if report["crash"]:
    md += ["## Crash before the targets ran", "", "```", report["crash"].strip()[-3000:], "```", ""]
for tid, t in report["targets"].items():
    tg = t.get("target", {})
    md += [f"## {tid}: `{tg.get('name')}` ({tg.get('file')}:{tg.get('line')}) - {'valid' if t['valid'] else 'INVALID'}", "",
           "| check | result | detail |", "|---|---|---|"]
    for r in t["results"]:
        res = "info" if r["ok"] is None else ("pass" if r["ok"] else "**FAIL**")
        md.append(f"| {r['check']} | {res} | {str(r['detail']).replace('|', '/')[:220]} |")
    if t.get("crash"):
        md += ["", "```", t["crash"].strip()[-3000:], "```"]
    if t.get("mapped"):
        md += ["", f"Descendants (Prism names): {', '.join(sorted(t['mapped']['descendants'] or {})) or '-'}",
               f"Resolved by name from dist/: {t['mapped'].get('resolved_by_name') or '-'}", ""]
open(os.path.join(OUT, "VALIDATION.md"), "w").write("\n".join(md) + "\n")
print("\n" + "\n".join(md), flush=True)
push([PUSH_PATH], f"Layer 4: tRPC/Vitest tooling validation ({'PASS' if report['valid'] else 'FAIL'})")
print(f"\n[done] {'PASS' if report['valid'] else 'FAIL'} in {report['elapsed_s']}s", flush=True)
