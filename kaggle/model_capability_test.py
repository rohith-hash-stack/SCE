# ============================================================
# Model capability test for Phase 0 on Kaggle 2x T4 (harness/experiments/r4/MODEL_CAPABILITY_TEST.md).
# Standalone: no repository, no GitHub token, no PRISM harness. Settings: Accelerator GPU T4 x2, Internet ON.
#
# STAGE controls what runs (set it below, then Run):
#   "load"     (default) Q1/Q2/Q5: pull each model, load it with the Phase 0 context (num_ctx = 20,480) and report
#              VRAM per GPU, load time, GPU/CPU split. Also tries one-GPU loads for models whose weights could fit
#              on one T4. NO GENERATION beyond loading.
#   "generate" Q3/Q4/Q6: a ~16K-token prompt with a 500-token answer (completes? VRAM peak? tok/s), then a 10-turn
#              tool-calling loop on a small synthetic repository. Run only after the "load" results are reviewed.
# Resumable: results go to RESULTS after every step; a rerun skips finished steps. No step starts within
# STOP_MARGIN_MIN of the session limit. Disk: a finished model is removed before the next is pulled if space is
# short.
# Send back: the printed summary table, or the file /kaggle/working/model_capability_results.json.
# ============================================================

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

STAGE = "load"                       # "load" first; "generate" only after review
MODELS = [                           # exact Ollama IDs (verified on the registry, 2026-11-13)
    "qwen3-coder:30b-a3b-q4_K_M",    # Qwen3-Coder-30B-A3B Instruct, MoE ~3.3B active, 18.56 GB
    "devstral-small-2:24b-instruct-2512-q4_K_M",   # Devstral Small 2 (24B, dense), 15.18 GB
    "qwen3.8:27b-q4_K_M",            # Qwen3.8 27B (dense, hybrid attention, vision projector), 16.81 + 0.93 GB
    "ornith:35b-q4_K_M",             # Ornith 1.0 35B (MoE, hybrid attention), 21.17 GB -- provenance unverified
]
NUM_CTX = 20480                      # the Phase 0 loop's num_ctx (16,384-token prompt window + 4,096 answer)
PROMPT_TOKENS = 16000                # Q3: target prompt size
OUT_TOKENS = 500                     # Q3/Q4: answer size
LOOP_TURNS = 10                      # Q6
CALL_TIMEOUT = 900                   # seconds per model call
OLLAMA_VERSION = "0.34.0"            # the version the sanity cells used
SESSION_HOURS, STOP_MARGIN_MIN = 11.5, 30
WORK = "/kaggle/working" if os.path.isdir("/kaggle/working") else os.getcwd()
RESULTS = os.path.join(WORK, "model_capability_results.json")
T4_USABLE_GB = 14.5                  # of a T4's 15 GB, after the CUDA context
CELL_START = time.time()
DEADLINE = CELL_START + SESSION_HOURS * 3600 - STOP_MARGIN_MIN * 60


def sh(cmd, check=False, capture=True, timeout=None):
    return subprocess.run(cmd, shell=isinstance(cmd, str), check=check, capture_output=capture, text=True,
                          timeout=timeout)


def load_results():
    try:
        return json.load(open(RESULTS))
    except Exception:
        return {"models": {}}


def save(res):
    json.dump(res, open(RESULTS + ".tmp", "w"), indent=1)
    os.replace(RESULTS + ".tmp", RESULTS)


def gpus():
    out = sh(["nvidia-smi", "--query-gpu=index,name,memory.used,memory.total", "--format=csv,noheader,nounits"])
    rows = []
    for ln in (out.stdout or "").strip().splitlines():
        i, name, used, total = [x.strip() for x in ln.split(",")]
        rows.append({"gpu": int(i), "name": name, "used_mb": int(used), "total_mb": int(total)})
    return rows


def api(port, path, body=None, timeout=CALL_TIMEOUT):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def start_server(port, devices):
    try:
        api(port, "/api/version", timeout=3)
        return
    except Exception:
        pass
    env = dict(os.environ, OLLAMA_HOST=f"127.0.0.1:{port}", CUDA_VISIBLE_DEVICES=devices, OLLAMA_KEEP_ALIVE="30m",
               OLLAMA_NUM_PARALLEL="1", OLLAMA_FLASH_ATTENTION="1", OLLAMA_KV_CACHE_TYPE="q8_0",
               OLLAMA_MAX_LOADED_MODELS="1")
    subprocess.Popen(["ollama", "serve"], env=env, stdout=open(f"{WORK}/ollama_{port}.log", "a"),
                     stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            api(port, "/api/version", timeout=3)
            return
        except Exception:
            time.sleep(2)
    raise SystemExit(f"ollama on port {port} did not start (see {WORK}/ollama_{port}.log)")


def unload(port, model):
    try:
        api(port, "/api/generate", {"model": model, "keep_alive": 0}, timeout=120)
    except Exception:
        pass
    time.sleep(3)


def kv_estimate_gb(info, arch, ctx, bytes_per=1.0625):
    """KV cache at `ctx` tokens with a q8_0 cache: 2 x attention layers x KV heads x head size x bytes. Hybrid
    models keep a cache only on every `full_attention_interval`-th layer."""
    g = lambda k: info.get(f"{arch}.{k}")
    layers, kvh, kl, vl = g("block_count"), g("attention.head_count_kv"), g("attention.key_length"), g("attention.value_length")
    if not all([layers, kvh]):
        return None
    kl = kl or (g("embedding_length") // g("attention.head_count"))
    vl = vl or kl
    interval = g("full_attention_interval") or 1
    attn_layers = -(-layers // interval)
    return round(attn_layers * kvh * (kl + vl) * bytes_per * ctx / 1e9, 2)


# ---------------------------------------------------------------- setup
print("=" * 64, f"\nModel capability test, STAGE={STAGE}\n" + "=" * 64, flush=True)
res = load_results()
res.setdefault("setup", {})["gpus"] = gpus()
print(sh("nvidia-smi").stdout, flush=True)
if len([g for g in res["setup"]["gpus"] if "T4" in g["name"]]) < 2:
    print("[warn] expected 2x T4:", res["setup"]["gpus"], flush=True)
if shutil.which("ollama") is None:
    r = sh(f"curl -fsSL https://github.com/ollama/ollama/releases/download/v{OLLAMA_VERSION}/ollama-linux-amd64.tar.zst "
           "-o /tmp/ollama.tar.zst && (apt-get install -y -qq zstd >/dev/null 2>&1; true) && "
           "tar -I zstd -xf /tmp/ollama.tar.zst -C /usr/local && chmod 755 /usr/local/bin/ollama")
    if shutil.which("ollama") is None:          # the pinned release is unavailable: the official installer
        sh("curl -fsSL https://ollama.com/install.sh | sh", capture=False)
res["setup"]["ollama"] = sh(["ollama", "--version"]).stdout.strip()
print("[ok]", res["setup"]["ollama"], flush=True)
start_server(11434, "0,1")
save(res)


def pull(model):
    t = time.time()
    api(11434, "/api/pull", {"model": model, "stream": False}, timeout=7200)
    return round(time.time() - t, 1)


def free_disk_gb():
    p = os.path.expanduser("~/.ollama")
    os.makedirs(p, exist_ok=True)
    return shutil.disk_usage(p).free / 1e9


# ---------------------------------------------------------------- stage: load (Q1, Q2, Q5)
def stage_load(model, m):
    if "load" in m:
        return
    size_hint = {"qwen3-coder": 18.6, "devstral-small-2": 15.2, "qwen3.8": 17.8, "ornith": 21.2}.get(model.split(":")[0], 22)
    if free_disk_gb() < size_hint + 5:
        for other in list(res["models"]):
            if other != model:
                sh(["ollama", "rm", other])
    print(f"\n[pull] {model} (disk free {free_disk_gb():.0f} GB)", flush=True)
    m["pull_seconds"] = pull(model)
    show = api(11434, "/api/show", {"model": model}, timeout=120)
    info, details = show.get("model_info") or {}, show.get("details") or {}
    arch = info.get("general.architecture")
    m["details"] = details
    m["arch"] = {k.split(".", 1)[1]: v for k, v in info.items() if k.startswith(f"{arch}.") and not isinstance(v, list)}
    m["kv_q8_gb_at_ctx"] = kv_estimate_gb(info, arch, NUM_CTX)
    tags = api(11434, "/api/tags", timeout=60).get("models", [])
    m["download_gb"] = round(next((x["size"] for x in tags if x["name"] == model), 0) / 1e9, 2)
    before = gpus()
    t = time.time()
    api(11434, "/api/generate", {"model": model, "prompt": "", "keep_alive": "30m", "options": {"num_ctx": NUM_CTX}},
        timeout=1800)                                   # loads the model and allocates the cache; generates nothing
    load_s = round(time.time() - t, 1)
    ps = next((x for x in api(11434, "/api/ps", timeout=60).get("models", []) if x["name"] == model), {})
    after = gpus()
    m["load"] = {"num_ctx": NUM_CTX, "load_seconds": load_s, "size_gb": round(ps.get("size", 0) / 1e9, 2),
                 "size_vram_gb": round(ps.get("size_vram", 0) / 1e9, 2),
                 "gpu_share": round(ps.get("size_vram", 0) / max(1, ps.get("size", 1)), 3),
                 "context_length_reported": ps.get("context_length"),
                 "vram_mb_per_gpu": {str(a["gpu"]): a["used_mb"] - b["used_mb"] for a, b in zip(after, before)},
                 "fits_on_gpu": ps.get("size_vram", 0) >= ps.get("size", 1)}
    print(f"[load] {model}: {json.dumps(m['load'])}", flush=True)
    unload(11434, model)
    # Q5: one T4 alone (a second server on GPU 0 only), for models whose weights could fit
    weights = m["download_gb"]
    single = {}
    if weights + 0.5 > T4_USABLE_GB:
        single = {"skipped": f"weights {weights} GB exceed one T4 (~{T4_USABLE_GB} GB usable)"}
    else:
        start_server(11435, "0")
        for ctx in (NUM_CTX, 8192, 4096):
            api(11435, "/api/generate", {"model": model, "prompt": "", "keep_alive": "5m", "options": {"num_ctx": ctx}},
                timeout=1800)
            ps1 = next((x for x in api(11435, "/api/ps", timeout=60).get("models", []) if x["name"] == model), {})
            single[str(ctx)] = {"gpu_share": round(ps1.get("size_vram", 0) / max(1, ps1.get("size", 1)), 3),
                                "size_gb": round(ps1.get("size", 0) / 1e9, 2)}
            unload(11435, model)
    m["single_t4"] = single
    print(f"[single T4] {model}: {json.dumps(single)}", flush=True)


# ---------------------------------------------------------------- stage: generate (Q3, Q4, Q6)
class Sampler(threading.Thread):
    """Peak VRAM per GPU while a call runs."""
    def __init__(self):
        super().__init__(daemon=True)
        self.peak, self.stop = {}, False

    def run(self):
        while not self.stop:
            for g in gpus():
                self.peak[str(g["gpu"])] = max(self.peak.get(str(g["gpu"]), 0), g["used_mb"])
            time.sleep(1)


def chat(model, messages, num_predict, fmt=None, think=False):
    body = {"model": model, "messages": messages, "stream": False, "keep_alive": "30m",
            "options": {"num_ctx": NUM_CTX, "num_predict": num_predict, "temperature": 0.2, "seed": 42}}
    if fmt:
        body["format"] = fmt
    if think is not None:
        body["think"] = think
    return api(11434, "/api/chat", body, timeout=CALL_TIMEOUT)


def filler(target_tokens):
    """Deterministic Python-like text, about 3.3 characters per token."""
    out, i = [], 0
    while sum(len(x) for x in out) < target_tokens * 3.3:
        out.append(f"def handler_{i}(request, retries={i % 5}):\n    \"\"\"Handle case {i}: validate, then dispatch.\"\"\"\n"
                   f"    if request.get('id') == {i}:\n        return dispatch(request, attempt={i % 3})\n"
                   f"    return None\n\n")
        i += 1
    return "".join(out), i


def stage_q3q4(model, m):
    if "q3q4" in m:
        return
    text, n = filler(PROMPT_TOKENS)
    msgs = [{"role": "user", "content": text + f"\n\nThe file above defines {n} handlers. Describe, one line each, "
                                               f"what handlers 0 to 60 do. Write at least {OUT_TOKENS} tokens."}]
    s = Sampler(); s.start()
    t = time.time()
    try:
        r = chat(model, msgs, OUT_TOKENS, think=False)
        ok, err = True, None
    except Exception as exc:
        r, ok, err = {}, False, f"{type(exc).__name__}: {str(exc)[:300]}"
    s.stop = True; s.join(3)
    wall = time.time() - t
    ec, ed = r.get("eval_count", 0), r.get("eval_duration", 0) / 1e9
    pc, pd = r.get("prompt_eval_count", 0), r.get("prompt_eval_duration", 0) / 1e9
    ps = next((x for x in api(11434, "/api/ps", timeout=60).get("models", []) if x["name"] == model), {})
    m["q3q4"] = {"completed": ok and ec > 0, "error": err, "prompt_tokens": pc, "answer_tokens": ec,
                 "done_reason": r.get("done_reason"), "wall_seconds": round(wall, 1),
                 "gen_tok_per_s": round(ec / ed, 1) if ed else None, "prompt_tok_per_s": round(pc / pd, 1) if pd else None,
                 "vram_peak_mb_per_gpu": s.peak, "gpu_share_during": round(ps.get("size_vram", 0) / max(1, ps.get("size", 1)), 3)}
    print(f"[Q3/Q4] {model}: {json.dumps(m['q3q4'])}", flush=True)


LOOP_SYSTEM = """You are a software engineer working in a small repository. Each turn, reply with exactly one JSON
object calling one tool: {"tool": "<name>", "args": {...}}. No other text.
Tools:
- read_file {"path": str, "start": int = 1, "end": int | null}: read a file's numbered lines.
- edit_file {"path": str, "old": str, "new": str}: replace one exact, unique span of a file.
- run_tests {}: run the repository's tests.
- done {"summary": str}: finish, when the tests pass."""
LOOP_TASK = ("`calc.py` has bugs: `add` subtracts, and `mean` divides by the wrong number. Fix them so that the tests "
             "in `test_calc.py` pass. `notes.py` is a large module you may ignore.")


def make_repo(root):
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    open(f"{root}/calc.py", "w").write("def add(a, b):\n    return a - b\n\n\ndef mean(xs):\n    return sum(xs) / (len(xs) + 1)\n")
    open(f"{root}/test_calc.py", "w").write("from calc import add, mean\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
                                            "def test_mean():\n    assert mean([2, 4]) == 3\n")
    open(f"{root}/notes.py", "w").write(filler(9000)[0])           # makes prompts large once it is read


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


def stage_q6(model, m):
    if "q6" in m:
        return
    root = "/tmp/cap_repo"
    make_repo(root)
    msgs = [{"role": "system", "content": LOOP_SYSTEM}, {"role": "user", "content": LOOP_TASK}]
    turns, infra, bad, rec = 0, None, 0, []
    for turn in range(1, LOOP_TURNS + 1):
        t = time.time()
        try:
            r = chat(model, msgs, 1024, fmt="json", think=False)
        except Exception as exc:
            infra = f"turn {turn}: {type(exc).__name__}: {str(exc)[:200]}"
            break
        turns = turn
        text = (r.get("message") or {}).get("content", "")
        call = parse(text)
        out = tool(root, *call) if call else 'error: reply with one JSON object: {"tool": ..., "args": {...}}'
        bad += call is None
        rec.append({"turn": turn, "tool": call[0] if call else None, "seconds": round(time.time() - t, 1),
                    "prompt_tokens": r.get("prompt_eval_count"), "answer_tokens": r.get("eval_count")})
        msgs += [{"role": "assistant", "content": text}, {"role": "user", "content": out}]
        if call and call[0] == "done":
            break
    tests = tool(root, "run_tests", {})
    m["q6"] = {"turns": turns, "infra_failure": infra, "parse_failures": bad, "ended_with_done": bool(rec and rec[-1]["tool"] == "done"),
               "tests_pass_at_end": " passed" in tests and "failed" not in tests,
               "max_prompt_tokens": max((x["prompt_tokens"] or 0) for x in rec) if rec else 0,
               "mean_turn_seconds": round(sum(x["seconds"] for x in rec) / len(rec), 1) if rec else None, "turn_log": rec}
    print(f"[Q6] {model}: {json.dumps({k: v for k, v in m['q6'].items() if k != 'turn_log'})}", flush=True)


# ---------------------------------------------------------------- run
for model in MODELS:
    m = res["models"].setdefault(model, {})
    try:
        if time.time() > DEADLINE:
            print("[deadline] stopping; run the cell again in a new session", flush=True)
            break
        stage_load(model, m)
        save(res)
        if STAGE == "generate":
            have = {x["name"] for x in api(11434, "/api/tags", timeout=60).get("models", [])}
            if model not in have:                   # removed for disk space, or a new session: pull it again
                if free_disk_gb() < (m.get("download_gb") or 22) + 5:
                    for other in have:
                        sh(["ollama", "rm", other])
                print(f"[pull] {model} again for the generate stage", flush=True)
                m["repull_seconds"] = pull(model)
            api(11434, "/api/generate", {"model": model, "prompt": "", "keep_alive": "30m",
                                         "options": {"num_ctx": NUM_CTX}}, timeout=1800)
            for step in (stage_q3q4, stage_q6):
                if time.time() > DEADLINE:
                    break
                step(model, m)
                save(res)
            unload(11434, model)
            if "q6" in m:                           # finished with this model: free its disk for the next
                sh(["ollama", "rm", model])
    except Exception as exc:
        m.setdefault("errors", []).append(f"{type(exc).__name__}: {str(exc)[:300]}")
        print(f"[error] {model}: {exc}", flush=True)
        save(res)

print("\n" + "=" * 64 + "\nSUMMARY\n" + "=" * 64)
hdr = f"{'model':45} {'GB':>6} {'load s':>7} {'GPU %':>6} {'VRAM MB (gpu0/gpu1)':>20} {'1-T4':>8} {'16K ok':>7} {'tok/s':>6} {'Q6 turns':>9} {'Q6 ok':>6}"
print(hdr)
for model, m in res["models"].items():
    ld, q, q6 = m.get("load") or {}, m.get("q3q4") or {}, m.get("q6") or {}
    v = ld.get("vram_mb_per_gpu") or {}
    one = "no" if (m.get("single_t4") or {}).get("skipped") else ",".join(f"{c}:{x['gpu_share']}" for c, x in (m.get("single_t4") or {}).items()) or "-"
    print(f"{model:45} {m.get('download_gb', '-'):>6} {ld.get('load_seconds', '-'):>7} {round(100 * ld['gpu_share']) if ld else '-':>6} "
          f"{str(v.get('0', '-')) + '/' + str(v.get('1', '-')):>20} {one:>8} {str(q.get('completed', '-')):>7} {str(q.get('gen_tok_per_s', '-')):>6} "
          f"{str(q6.get('turns', '-')):>9} {str(bool(q6) and not q6.get('infra_failure')) if q6 else '-':>6}")
print(f"\nResults: {RESULTS}")
