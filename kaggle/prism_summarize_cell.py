# ============================================================
# PRISM summarize - live run of the shipped summarizer (Kaggle, one cell)
#
# Runs the production command, `prism summarize --repo <root> --base-urls ...`,
# with the locked 14B model on repositories no earlier run summarized, and
# writes each repository's `.prism/summaries.json` directly - the file
# `prism.ask` uses (weight 0.5 when it covers >= 95% of the functions).
# (kaggle/purpose_search_cell.py is the evaluation harness; this is not.)
#
# Per repository:
#   1. kill test (first run only): start `prism summarize` with a short
#      checkpoint interval, SIGKILL it once KILL_AFTER_ENTRIES summaries are
#      checkpointed, and check the file on disk is a valid index;
#   2. resume: the same command again, to completion - it must keep every
#      checkpointed entry and summarize only the rest; the index is pushed
#      every CHECKPOINT_MIN minutes, so a re-run of this cell in a new
#      session resumes from the pushed file;
#   3. live_check (harness/experiments/purpose_search/live_check.py):
#      coverage, the weight prism.ask applies, lint rates of every summary
#      against the gate, a dry run with nothing left, sample `prism ask`s.
# Output: RESULTS_PATH/REPORT.md, RESULTS_PATH/<repo>/{summaries.json,
# live_check.json, *.log}, pushed to BRANCH.
#
# Paste this whole file into ONE Kaggle notebook cell and Run.
# Settings: Accelerator GPU T4 x2, Internet ON, secret GITHUB_TOKEN.
# Expected time on T4 x2: about 10 min for Hono (~555 library functions) after setup.
# Rehearsed end to end against a local fake Ollama server (kill at >= 120
# checkpointed entries, resume keeps all of them, live_check passes).
# ============================================================

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

# ---------- Config ----------
BRANCH = "feature/option3-contract-axis"
CODE_COMMIT = "90ce565b016cfafd7593487e9e380acd8f827590"
CODE_TREES = {"src": "377fac8a252cff27869577e7082eb0e08a933c76",
              "harness": "11bcb376d6d0e5b4d55dc5bd92e2e410ed1cd82c"}
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
EXPECTED_GITHUB_USER = "rohith-hash-stack"
SUMMARIZER = "qwen2.5-coder:14b-instruct-q8_0"   # the locked model; another model's index is used at weight 0
OLLAMA_VERSION = "0.34.0"
PORTS = (11434,)          # 14B q8_0 (~15.7 GB) needs both T4s: one server across them
GPU_GROUPS = ("0,1",)
PARALLEL_PER_SERVER = 2
#: Repositories to summarize: never summarized by an earlier run. click (512
#: functions) was summarized by the first live run (reports/prism_summarize_live).
#: Hono: needs the const-binding parser fix (3cd55ed) and the test-file search
#: exclusion (4f76eb1). No sample questions: held-out set 5 is asked of Hono,
#: so no ranking on it is printed before that set is frozen and scored.
TARGETS = {
    "hono": {"url": "https://github.com/honojs/hono", "commit": "16eb88269ffb0ae68590ad55ac9ac58807850f26",
             "root": "src", "questions": []},
    # "click": {"url": "https://github.com/pallets/click", "commit": "934813e4d421071a1b3db3973c02fe2721359a6e",
    #           "root": "src", "questions": ["how are command line options parsed from argv",
    #                                        "where is the help text formatted", "click.echo"]},
}
KILL_TEST = True                # SIGKILL the first run once KILL_AFTER_ENTRIES are checkpointed
KILL_CHECKPOINT_EVERY = 2       # batches between checkpoints during the kill test (12 functions per batch)
KILL_AFTER_ENTRIES = 120
CHECKPOINT_EVERY = 25           # batches between checkpoints in the real run (the prism default)
CHECKPOINT_MIN = 15             # push summaries.json this often while a repo runs
WORK = "/kaggle/working"
SCE_DIR = f"{WORK}/SCE"
PUSH_WT = f"{WORK}/SCE_results"
APPS_DIR = f"{WORK}/live_apps"
RESULTS_PATH = "reports/prism_summarize_live"
PUSH_RESULTS = True
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")

print("=" * 64)
print("PRISM summarize - live run")
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
    """Copy OUT into a clean worktree of BRANCH and push it there."""
    if not PUSH_RESULTS:
        return False
    for attempt in range(max_attempts):
        run(["git", "fetch", "origin", BRANCH], cwd=SCE_DIR, check=False, env=GIT_ENV)
        run(["git", "checkout", "-q", "--detach", f"origin/{BRANCH}"], cwd=PUSH_WT, check=True)
        dst = os.path.join(PUSH_WT, RESULTS_PATH)
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(OUT, dst, ignore=shutil.ignore_patterns("*.sqlite", "*.tmp"))
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


def read_index(path):
    """The index if it is a complete, valid file (format 1), else None."""
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("format") == 1 and isinstance(data.get("functions"), dict) else None


print("\n[setup 1/6] GitHub token", flush=True)
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

print("\n[setup 2/6] GPU check + apt", flush=True)
gpu = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], check=False, capture=True)
gpu_lines = [ln for ln in (gpu.stdout or "").strip().splitlines() if ln.strip()]
if sum("T4" in ln for ln in gpu_lines) < 2:
    abort(f"expected GPU T4 x2: {gpu_lines}")
print(f"[ok] GPU: {gpu_lines}", flush=True)
run("apt-get update -qq && apt-get install -y -qq zstd curl git", check=False)

print("\n[setup 3/6] Ollama + the summarizer model", flush=True)
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
THREADS = str(len(PORTS) * PARALLEL_PER_SERVER)
for port in PORTS:  # warm the server (loads the model onto the GPUs)
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/chat", data=json.dumps({
        "model": SUMMARIZER, "messages": [{"role": "user", "content": "Reply with {\"ok\": true}"}], "format": "json",
        "stream": False, "options": {"num_predict": 10}}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        print(f"[ok] port {port}: {json.loads(r.read())['message']['content']!r}", flush=True)

print("\n[setup 4/6] Clone and check out the code commit", flush=True)
clone_url = REPO_URL.replace("https://", f"https://x-access-token:{github_token}@")
if not os.path.isdir(f"{SCE_DIR}/.git"):
    run(["git", "clone", clone_url, SCE_DIR], check=True, env=GIT_ENV)
run(["git", "remote", "set-url", "origin", clone_url], cwd=SCE_DIR)
run(["git", "fetch", "origin", BRANCH], cwd=SCE_DIR, check=True, env=GIT_ENV)
run(["git", "checkout", "-q", "-f", "--detach", CODE_COMMIT], cwd=SCE_DIR, check=True)
run(["git", "clean", "-fdx", "-e", RESULTS_PATH], cwd=SCE_DIR, check=True)
got = {p: run(["git", "rev-parse", f"HEAD:{p}"], cwd=SCE_DIR, capture=True).stdout.strip() for p in CODE_TREES}
if got != CODE_TREES:
    abort(f"checked-out code differs from the reviewed commit: {got}")
print(f"[ok] code {CODE_COMMIT[:7]} {got}", flush=True)
if PUSH_RESULTS and not os.path.isdir(PUSH_WT):
    run(["git", "worktree", "add", "-f", "--detach", PUSH_WT, f"origin/{BRANCH}"], cwd=SCE_DIR, check=True)
OUT = os.path.join(SCE_DIR, RESULTS_PATH)
os.makedirs(OUT, exist_ok=True)
if run(["git", "cat-file", "-e", f"origin/{BRANCH}:{RESULTS_PATH}"], cwd=SCE_DIR, check=False).returncode == 0:
    run(f"git archive origin/{BRANCH} {RESULTS_PATH} | tar -x -C {SCE_DIR}", cwd=SCE_DIR, check=True)
    print(f"[resume] restored {sorted(os.listdir(OUT))}", flush=True)

print("\n[setup 5/6] Install prism + checkpoint tests", flush=True)
run([sys.executable, "-m", "pip", "install", "-q", "-e", ".[dev,bench]", "jsonschema"], cwd=SCE_DIR, check=True)  # tests/conftest.py imports benchmarks.runner (rank_bm25)
child_env = dict(os.environ, PYTHONPATH=os.pathsep.join([SCE_DIR, os.path.join(SCE_DIR, "src")]),
                 CUDA_VISIBLE_DEVICES="", PYTHONDONTWRITEBYTECODE="1")
child_env.pop("GITHUB_TOKEN", None)
run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_summarize_checkpoints.py",
     "tests/test_query_routing.py", "tests/test_search_excludes_test_files.py", "tests/test_js_const_bound_functions.py"],
    cwd=SCE_DIR, env=child_env, check=True)
PRISM = [sys.executable, "-m", "prism.cli"]

print("\n[setup 6/6] Target repositories at their pinned commits", flush=True)
roots = {}
for repo, spec in TARGETS.items():
    dst = os.path.join(APPS_DIR, repo)
    if not os.path.isdir(f"{dst}/.git"):
        run(["git", "init", "-q", dst], check=True)
        run(["git", "fetch", "-q", "--depth", "1", spec["url"], spec["commit"]], cwd=dst, check=True, env=GIT_ENV)
    run(["git", "checkout", "-q", "-f", spec["commit"]], cwd=dst, check=True)
    head = run(["git", "rev-parse", "HEAD"], cwd=dst, capture=True).stdout.strip()
    if head != spec["commit"]:
        abort(f"{repo} at {head}, expected {spec['commit']}")
    roots[repo] = os.path.join(dst, spec["root"])
    print(f"[ok] {repo} @ {head[:7]}", flush=True)


def summarize(repo, root, checkpoint_every, log_name, kill_after=None):
    """Run `prism summarize`; push the index while it runs. With
    `kill_after`, SIGKILL it once that many entries are checkpointed.
    Returns (exit code or 'killed', entries on disk, log text)."""
    index = os.path.join(root, ".prism", "summaries.json")
    log_path = os.path.join(OUT, repo, log_name)
    cmd = PRISM + ["summarize", "--repo", root, "--base-urls", BASE_URLS, "--threads", THREADS,
                   "--checkpoint-every", str(checkpoint_every)]
    print(f"$ {' '.join(cmd)}", flush=True)
    with open(log_path, "w") as log:
        proc = subprocess.Popen(cmd, cwd=SCE_DIR, env=child_env, stdout=log, stderr=subprocess.STDOUT)
        last_push, outcome = time.time(), None
        while proc.poll() is None:
            time.sleep(5)
            data = read_index(index)
            n = len(data["functions"]) if data else 0
            if kill_after is not None and n >= kill_after:
                os.kill(proc.pid, signal.SIGKILL)  # no cleanup in the child
                proc.wait()
                outcome = "killed"
                break
            if time.time() - last_push > CHECKPOINT_MIN * 60 and data:
                shutil.copy(index, os.path.join(OUT, repo, "summaries.json"))
                push_results(f"prism summarize live: {repo} checkpoint ({n} summaries) @ {CODE_COMMIT[:7]}")
                last_push = time.time()
    data = read_index(index)
    if data:
        shutil.copy(index, os.path.join(OUT, repo, "summaries.json"))
    return (outcome or proc.returncode), (len(data["functions"]) if data else 0), open(log_path).read()


report = {}
START = time.time()
for repo, spec in TARGETS.items():
    root = roots[repo]
    os.makedirs(os.path.join(OUT, repo), exist_ok=True)
    index = os.path.join(root, ".prism", "summaries.json")
    pushed = os.path.join(OUT, repo, "summaries.json")
    resumed = read_index(pushed) is not None
    if resumed:  # an earlier session pushed a checkpoint: continue from it
        os.makedirs(os.path.dirname(index), exist_ok=True)
        shutil.copy(pushed, index)
    r = {"repo": repo, "commit": spec["commit"], "resumed_from_push": resumed,
         "dry_run": json.loads(run(PRISM + ["summarize", "--repo", root, "--dry-run"], cwd=SCE_DIR, env=child_env,
                                   capture=True).stdout)}
    print(f"\n[{repo}] dry run: {r['dry_run']}", flush=True)

    if KILL_TEST and not resumed and r["dry_run"]["to_summarize"] > KILL_AFTER_ENTRIES:
        t = time.time()
        code, n, log = summarize(repo, root, KILL_CHECKPOINT_EVERY, "kill_test.log", kill_after=KILL_AFTER_ENTRIES)
        valid = read_index(index) is not None
        leftovers = [f for f in os.listdir(os.path.dirname(index)) if f.endswith(".tmp")]
        r["kill_test"] = {"outcome": code, "entries_on_disk": n, "index_valid": valid,
                          "tmp_leftovers": leftovers, "seconds": round(time.time() - t)}
        print(f"[{repo}] kill test: {r['kill_test']}", flush=True)
        if code != "killed" or not valid or n < KILL_AFTER_ENTRIES:
            push_results(f"prism summarize live: {repo} kill test FAILED @ {CODE_COMMIT[:7]}")
            abort(f"kill test failed: {r['kill_test']}")

    t = time.time()
    code, n, log = summarize(repo, root, CHECKPOINT_EVERY, "run.log")
    r["run"] = {"exit": code, "entries": n, "seconds": round(time.time() - t),
                "summary_line": next((ln for ln in log.splitlines() if ln.startswith("Wrote ")), None)}
    if "kill_test" in r:
        kept = r["run"]["summary_line"] or ""
        r["run"]["kept_all_checkpointed"] = f"kept {r['kill_test']['entries_on_disk']}," in kept
    print(f"[{repo}] run: {r['run']}", flush=True)

    check = run([sys.executable, "-m", "harness.experiments.purpose_search.live_check", root,
                 os.path.join(OUT, repo, "live_check.json"), *spec["questions"]],
                cwd=SCE_DIR, env=child_env, check=False, capture=True)
    print(check.stdout, check.stderr[-2000:] if check.returncode not in (0, 1) else "", flush=True)
    r["live_check"] = json.load(open(os.path.join(OUT, repo, "live_check.json"))) \
        if os.path.exists(os.path.join(OUT, repo, "live_check.json")) else {"error": check.stderr[-2000:]}
    report[repo] = r
    push_results(f"prism summarize live: {repo} @ {CODE_COMMIT[:7]} ({SUMMARIZER})")

lines = ["# prism summarize: live run", "", f"Code `{CODE_COMMIT[:7]}`, model `{SUMMARIZER}`, Kaggle T4 x2.", "",
         "| repo | functions | summarized | coverage | weight | lint any | gate | left | kill test | resume kept all | minutes |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
for repo, r in report.items():
    lc = r["live_check"]
    st = lc.get("status", {})
    kt = r.get("kill_test")
    lines.append(
        f"| {repo} | {st.get('functions')} | {st.get('summarized')} | {st.get('coverage')} | {st.get('summary_weight')} | "
        f"{lc.get('lint', {}).get('any')} | {'pass' if lc.get('gate_ok') else 'FAIL'} | {lc.get('left_to_summarize')} | "
        f"{'%s, %s on disk, valid %s' % (kt['outcome'], kt['entries_on_disk'], kt['index_valid']) if kt else '-'} | "
        f"{r['run'].get('kept_all_checkpointed', '-')} | {round(r['run']['seconds'] / 60, 1)} |")
for repo, r in report.items():
    lines += ["", f"## {repo}", "", "```", json.dumps({k: r["live_check"].get(k) for k in ("lint", "asks")}, indent=1), "```",
              "", "Sample summaries:", ""]
    lines += [f"- `{q}`: {s}" for q, s in r["live_check"].get("samples", {}).items()]
open(os.path.join(OUT, "REPORT.md"), "w").write("\n".join(lines) + "\n")
json.dump(report, open(os.path.join(OUT, "report.json"), "w"), indent=1)
push_results(f"prism summarize live: report @ {CODE_COMMIT[:7]} ({SUMMARIZER})")
print("\n=== DONE ===", flush=True)
print(open(os.path.join(OUT, "REPORT.md")).read())
