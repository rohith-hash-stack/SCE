# ============================================================
# Ornith diagnostic (harness/experiments/r4/MODEL_CAPABILITY_TEST.md, section 10): why did ornith:35b never edit in the
# capability test's 10-turn loop? JSON-mode interference (fixable) or the model itself (not fixable)?
# Standalone: no repository, no GitHub token. Settings: Accelerator GPU T4 x2, Internet ON. About 15-25 minutes.
#
# The same loop as the capability test (calc.py with two bugs, its tests, a large notes.py; 10 turns, think off),
# under three reply modes, three seeds each, with EVERY reply text, thinking text, native tool call and tool output
# logged per turn:
#   A "json"   Ollama format: "json" (the capability test's mode, and OllamaChat's default in the harness)
#   B "free"   no format constraint; the JSON object is parsed out of free text (OllamaChat json_format=False)
#   C "native" Ollama native tool calling: the four tools as function schemas, no format constraint
# devstral-small-2 runs the same 9 loops as a control (it passed mode A in the capability test).
#
# Verdict (pre-registered in section 10, applied by the code at the end; a mode "passes" when >= 2 of 3 seeds end
# with done AND the tests passing):
#   ornith A fails and B passes            -> JSON-mode interference confirmed
#   ornith A and B fail, C passes          -> tool-protocol interference (fixable with native tools; not JSON mode)
#   ornith A, B and C all fail             -> the model itself
#   ornith A passes                        -> the original failure did not reproduce
# Send back: /kaggle/working/ornith_diagnostic.json (or the printed output).
# ============================================================

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

MODELS = ["ornith:35b-q4_K_M", "devstral-small-2:24b-instruct-2512-q4_K_M"]   # the second is the control
MODES = ["json", "free", "native"]
SEEDS = [42, 43, 44]
NUM_CTX = 20480
LOOP_TURNS = 10
CALL_TIMEOUT = 300
OLLAMA_VERSION = "0.34.0"
WORK = "/kaggle/working" if os.path.isdir("/kaggle/working") else os.getcwd()
RESULTS = os.path.join(WORK, "ornith_diagnostic.json")


def sh(cmd, timeout=None):
    return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)


def api(path, body=None, timeout=CALL_TIMEOUT):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:11434{path}", data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def save(res):
    json.dump(res, open(RESULTS + ".tmp", "w"), indent=1)
    os.replace(RESULTS + ".tmp", RESULTS)


# ---------------------------------------------------------------- setup
print("=" * 64, "\nOrnith JSON-mode diagnostic\n" + "=" * 64, flush=True)
if shutil.which("ollama") is None:
    sh(f"curl -fsSL https://github.com/ollama/ollama/releases/download/v{OLLAMA_VERSION}/ollama-linux-amd64.tar.zst "
       "-o /tmp/ollama.tar.zst && (apt-get install -y -qq zstd >/dev/null 2>&1; true) && "
       "tar -I zstd -xf /tmp/ollama.tar.zst -C /usr/local && chmod 755 /usr/local/bin/ollama")
    if shutil.which("ollama") is None:
        subprocess.run("curl -fsSL https://ollama.com/install.sh | sh", shell=True)
try:
    api("/api/version", timeout=3)
except Exception:
    env = dict(os.environ, OLLAMA_HOST="127.0.0.1:11434", CUDA_VISIBLE_DEVICES="0,1", OLLAMA_KEEP_ALIVE="30m",
               OLLAMA_NUM_PARALLEL="1", OLLAMA_FLASH_ATTENTION="1", OLLAMA_KV_CACHE_TYPE="q8_0",
               OLLAMA_MAX_LOADED_MODELS="1")
    subprocess.Popen(["ollama", "serve"], env=env, stdout=open(f"{WORK}/ollama_diag.log", "a"), stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            api("/api/version", timeout=3)
            break
        except Exception:
            time.sleep(2)
    else:
        raise SystemExit("ollama did not start")
try:
    res = json.load(open(RESULTS))
except Exception:
    res = {"runs": {}}
res["ollama"] = api("/api/version", timeout=10).get("version")
print("[ok] ollama", res["ollama"], flush=True)

# ---------------------------------------------------------------- the loop (as in model_capability_test.py)
LOOP_SYSTEM = """You are a software engineer working in a small repository. Each turn, reply with exactly one JSON
object calling one tool: {"tool": "<name>", "args": {...}}. No other text.
Tools:
- read_file {"path": str, "start": int = 1, "end": int | null}: read a file's numbered lines.
- edit_file {"path": str, "old": str, "new": str}: replace one exact, unique span of a file.
- run_tests {}: run the repository's tests.
- done {"summary": str}: finish, when the tests pass."""
NATIVE_SYSTEM = ("You are a software engineer working in a small repository. Each turn, call exactly one of the tools "
                 "you are given.")
LOOP_TASK = ("`calc.py` has bugs: `add` subtracts, and `mean` divides by the wrong number. Fix them so that the tests "
             "in `test_calc.py` pass. `notes.py` is a large module you may ignore.")


def fn(name, desc, props, req):
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": {"type": "object", "properties": props, "required": req}}}


TOOLS = [fn("read_file", "Read a file's numbered lines.",
            {"path": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}}, ["path"]),
         fn("edit_file", "Replace one exact, unique span of a file.",
            {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}}, ["path", "old", "new"]),
         fn("run_tests", "Run the repository's tests.", {}, []),
         fn("done", "Finish, when the tests pass.", {"summary": {"type": "string"}}, ["summary"])]


def filler(target_tokens):
    out, i = [], 0
    while sum(len(x) for x in out) < target_tokens * 3.3:
        out.append(f"def handler_{i}(request, retries={i % 5}):\n    \"\"\"Handle case {i}: validate, then dispatch.\"\"\"\n"
                   f"    if request.get('id') == {i}:\n        return dispatch(request, attempt={i % 3})\n"
                   f"    return None\n\n")
        i += 1
    return "".join(out)


def make_repo(root):
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    open(f"{root}/calc.py", "w").write("def add(a, b):\n    return a - b\n\n\ndef mean(xs):\n    return sum(xs) / (len(xs) + 1)\n")
    open(f"{root}/test_calc.py", "w").write("from calc import add, mean\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
                                            "def test_mean():\n    assert mean([2, 4]) == 3\n")
    open(f"{root}/notes.py", "w").write(filler(9000))


def tool(root, name, args):
    p = lambda rel: os.path.join(root, os.path.basename(str(rel)))
    if name == "read_file":
        f = p(args.get("path", ""))
        if not os.path.isfile(f):
            return f"error: no file {args.get('path')!r}"
        lines = open(f).read().splitlines()
        s, e = int(args.get("start") or 1), args.get("end")
        e = len(lines) if e is None else min(len(lines), int(e))
        return "\n".join(f"{i}\t{lines[i - 1]}" for i in range(s, e + 1))[:40000]
    if name == "edit_file":
        f = p(args.get("path", ""))
        if not os.path.isfile(f):
            return f"error: no file {args.get('path')!r}"
        t, old = open(f).read(), args.get("old", "")
        if not old or t.count(old) != 1:
            return "error: the text to replace was not found exactly once"
        open(f, "w").write(t.replace(old, args.get("new", ""), 1))
        return "edited"
    if name == "run_tests":
        r = sh([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", root], timeout=120)
        return (r.stdout or "")[-1500:]
    if name == "done":
        return "done"
    return f"error: unknown tool {name!r}"


def parse(text):
    try:
        m = re.search(r"\{.*\}", text or "", re.S)
        o = json.loads(m.group(0)) if m else None
        return (o["tool"], o.get("args") or {}) if isinstance(o, dict) and isinstance(o.get("tool"), str) else None
    except Exception:
        return None


def call(model, mode, msgs, seed):
    body = {"model": model, "messages": msgs, "stream": False, "keep_alive": "30m", "think": False,
            "options": {"num_ctx": NUM_CTX, "num_predict": 1024, "temperature": 0.2, "seed": seed}}
    if mode == "json":
        body["format"] = "json"
    if mode == "native":
        body["tools"] = TOOLS
    return api("/api/chat", body)


def loop(model, mode, seed):
    root = "/tmp/diag_repo"
    make_repo(root)
    system = NATIVE_SYSTEM if mode == "native" else LOOP_SYSTEM
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": LOOP_TASK}]
    log, infra = [], None
    for turn in range(1, LOOP_TURNS + 1):
        t = time.time()
        try:
            r = call(model, mode, msgs, seed)
        except Exception as exc:
            err = exc.read().decode()[:500] if hasattr(exc, "read") else str(exc)[:500]
            infra = f"turn {turn}: {type(exc).__name__}: {err}"
            break
        m = r.get("message") or {}
        text, native = m.get("content") or "", m.get("tool_calls") or []
        if mode == "native" and native:
            f = native[0].get("function") or {}
            a = f.get("arguments")
            a = json.loads(a) if isinstance(a, str) else (a or {})
            parsed = (f.get("name"), a) if isinstance(f.get("name"), str) else None
            fed_back = json.dumps({"tool": f.get("name"), "args": a})          # the harness feeds calls back as text
        else:
            parsed, fed_back = parse(text), text
        out = tool(root, *parsed) if parsed else 'error: reply with one JSON object: {"tool": ..., "args": {...}}'
        log.append({"turn": turn, "reply_text": text, "thinking": m.get("thinking"), "native_tool_calls": native,
                    "parsed": list(parsed) if parsed else None, "tool_output": out[:600],
                    "prompt_tokens": r.get("prompt_eval_count"), "answer_tokens": r.get("eval_count"),
                    "done_reason": r.get("done_reason"), "seconds": round(time.time() - t, 1)})
        msgs += [{"role": "assistant", "content": fed_back}, {"role": "user", "content": out}]
        if parsed and parsed[0] == "done":
            break
    tests = tool(root, "run_tests", {})
    tools_used = [x["parsed"][0] if x["parsed"] else None for x in log]
    return {"turns": len(log), "infra_failure": infra, "tools": tools_used,
            "edits_attempted": tools_used.count("edit_file"),
            "edits_applied": sum(1 for x in log if x["parsed"] and x["parsed"][0] == "edit_file" and x["tool_output"] == "edited"),
            "tool_errors": sum(1 for x in log if x["tool_output"].startswith("error")),
            "parse_failures": tools_used.count(None),
            "ended_with_done": bool(tools_used and tools_used[-1] == "done"),
            "tests_pass_at_end": " passed" in tests and "failed" not in tests, "turn_log": log}


def passed(r):
    return bool(r) and r["ended_with_done"] and r["tests_pass_at_end"]


# ---------------------------------------------------------------- run
for model in MODELS:
    have = {x["name"] for x in api("/api/tags", timeout=60).get("models", [])}
    if model not in have:
        for other in have:                                    # one model on disk at a time
            sh(["ollama", "rm", other])
        print(f"\n[pull] {model}", flush=True)
        api("/api/pull", {"model": model, "stream": False}, timeout=7200)
    api("/api/generate", {"model": model, "prompt": "", "keep_alive": "30m", "options": {"num_ctx": NUM_CTX}}, timeout=1800)
    for mode in MODES:
        for seed in SEEDS:
            key = f"{model}|{mode}|{seed}"
            if key in res["runs"]:
                continue
            r = loop(model, mode, seed)
            res["runs"][key] = r
            save(res)
            print(f"[{model.split(':')[0]} {mode} seed {seed}] pass={passed(r)} turns={r['turns']} tools={r['tools']} "
                  f"edits {r['edits_applied']}/{r['edits_attempted']} tool_errors={r['tool_errors']} infra={r['infra_failure']}",
                  flush=True)
            for x in r["turn_log"]:
                shown = json.dumps(x["native_tool_calls"])[:200] if x["native_tool_calls"] else x["reply_text"][:200]
                print(f"    t{x['turn']}: {shown!r} -> {x['tool_output'][:120]!r}", flush=True)
    api("/api/generate", {"model": model, "keep_alive": 0}, timeout=120)

# ---------------------------------------------------------------- verdict (pre-registered)
def mode_pass(model, mode):
    return sum(passed(res["runs"].get(f"{model}|{mode}|{s}")) for s in SEEDS)


o = MODELS[0]
p = {m: mode_pass(o, m) for m in MODES}
ok = {m: p[m] >= 2 for m in MODES}
if ok["json"]:
    verdict = "not reproduced: ornith passes in JSON mode"
elif ok["free"]:
    verdict = "JSON-mode interference confirmed"
elif ok["native"]:
    verdict = "tool-protocol interference (fixable with native tools; not JSON mode itself)"
else:
    verdict = "the model itself: ornith fails in every mode"
res["summary"] = {m: {mode: f"{mode_pass(m, mode)}/{len(SEEDS)}" for mode in MODES} for m in MODELS}
res["verdict"] = verdict
save(res)
print("\n" + "=" * 64 + "\nPASSES (done + tests pass), per model and mode")
for m in MODELS:
    print(f"  {m:45} " + "  ".join(f"{mode}: {res['summary'][m][mode]}" for mode in MODES))
print(f"\nVERDICT: {verdict}\nResults: {RESULTS}")
