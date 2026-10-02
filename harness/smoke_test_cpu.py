"""CPU smoke test for M1. Prints a PASS/FAIL table and exits non-zero on any
FAIL or BLOCKED check.

    HARNESS_TOKENIZER_PATH=... python -m harness.smoke_test_cpu [--allow-blocked] [--json out.json]

BLOCKED means the check could not run in this environment (e.g. model
weights unreachable), which is not a pass: it must be re-run where the
weights are available (the Kaggle notebook runs this same script).
`--allow-blocked` only changes the exit code; the table still says BLOCKED.

Checks
 1. arm1_chunk_reassembly   real FastAPI file -> chunks; py_compile every
                            fragment; every non-blank line covered.
 2. arm1_onnx_reranker      bge-reranker-base on ONNX Runtime (CPU) loads
                            and ranks 50 pairs in < 3 s.
 3. arm1_dense_encoder      jina-embeddings-v2-base-code loads on CPU and
                            embeds.
 4. arm5_trimming           a PRISM output over 13,000 harness tokens is
                            trimmed from the lowest rank to <= 13,000.
 5. tokenizer_parity        HF tokenizer vs a (mocked) llama-server
                            /tokenize: divergence computed; a 2%+ drift is
                            detected.
 6. scorer_dispatch         five synthetic tasks (T1..T5): correct branch,
                            valid ScoreResult, every registry metric present.
 7. arm0_cleanliness_nan    empty delivered set -> cleanliness NaN, not 1.0.
 8. t5_fractional           8 of 10 affected named -> 0.8.
 9. bootstrap_paired        two arms over the same tasks get identical
                            resampled indices.
10. e2e_fastapi_5_tasks     5 real FastAPI tasks x {arm0, arm5, oracle}
                            end-to-end with a scripted model: valid
                            DeliveredContext + NormalizedAnswer + score.
"""
from __future__ import annotations

import argparse
import http.server
import json
import math
import os
import py_compile
import sys
import tempfile
import threading
import time
import traceback

from harness import config as C

FASTAPI_FILE = "fastapi/dependencies/utils.py"
NETWORK_HINTS = ("ConnectError", "ConnectionError", "LocalEntryNotFoundError", "OSError", "HTTPError",
                 "huggingface", "Max retries", "proxy", "403", "offline", "resolve", "Repository Not Found",
                 "We couldn't connect")


def _fastapi_root() -> str:
    from benchmarks.corpora.resolver import resolve
    return str(resolve("fastapi"))


def _blocked(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}"
    return any(h.lower() in text.lower() for h in NETWORK_HINTS)


# ------------------------------------------------------------------ checks
def check_arm1_chunk_reassembly(tok):
    from harness.ast_splitter import PythonSplitter
    root = _fastapi_root()
    path = os.path.join(root, FASTAPI_FILE)
    chunks = PythonSplitter(tok, C.RAG_MAX_CHUNK_TOKENS).split_file(path, root)
    errors = 0
    with tempfile.TemporaryDirectory() as d:
        for i, c in enumerate(chunks):
            f = os.path.join(d, f"chunk_{i}.py")
            with open(f, "w") as fh:
                fh.write(c.content)
            try:
                py_compile.compile(f, doraise=True)
            except py_compile.PyCompileError:
                errors += 1
    lines = open(path).read().split("\n")
    covered = set().union(*(c.source_rows for c in chunks))
    missing = [i for i, ln in enumerate(lines) if ln.strip() and i not in covered]
    ok = errors == 0 and not missing
    over = sum(1 for c in chunks if c.token_count > C.RAG_MAX_CHUNK_TOKENS)
    return ok, f"{len(chunks)} chunks, {errors} SyntaxErrors, {len(missing)} uncovered lines, {over} flagged oversized"


def check_arm1_onnx_reranker(tok):
    from harness.arms.arm1_rag import OnnxCrossEncoder
    ce = OnnxCrossEncoder()
    docs = [f"def handler_{i}(request):\n    return compute_total(request.items) + {i}" for i in range(50)]
    t0 = time.perf_counter()
    s = ce.score("How is the order total computed?", docs)
    dt = time.perf_counter() - t0
    ok = len(s) == 50 and all(math.isfinite(float(x)) for x in s) and dt < C.RAG_RERANK_SMOKE_SECONDS
    return ok, f"50 pairs in {dt:.2f}s (limit {C.RAG_RERANK_SMOKE_SECONDS}s); source: {ce.provenance}"


def check_arm1_dense_encoder(tok):
    from harness.arms.arm1_rag import JinaCodeEmbedder
    emb = JinaCodeEmbedder()
    v = emb.encode(["def f(x): return x + 1", "class A: pass"])
    ok = v.shape[0] == 2 and abs(float((v[0] ** 2).sum()) - 1.0) < 1e-3
    return ok, f"embedding dim {v.shape[1]}, L2-normalised"


def check_arm5_trimming(tok):
    import json as _json

    from harness.arms.arm5_prism import Arm5Prism
    from harness.llm import Completion
    from harness.scoring.adapters import finalize_context
    from harness.scoring.fairness import verify_ranking
    from harness.tasks.loaders import load_tasks

    root = _fastapi_root()
    holder = {}

    def request_everything(system, user, max_tokens=0, seed=None, purpose=""):
        return Completion(_json.dumps({"requested_symbols": sorted(holder["universe"])}), 1, 10, 0.0)

    arm = Arm5Prism(llm=request_everything, tokenizer=tok, budget=4 * C.RETRIEVAL_BUDGET)
    arm.index(root, {})
    # the first FastAPI task whose full candidate universe, hydrated, really
    # exceeds 13,000 harness tokens (a small universe has nothing to trim)
    raw = None
    for task in load_tasks("fastapi", repo_root=root):
        _m, holder["universe"] = arm.engine.build_candidate_manifest(task.seed_symbol)
        cand = arm.retrieve(task.query, task.seed_dict())
        if cand.total_tokens > C.RETRIEVAL_BUDGET:
            raw = cand
            break
    if raw is None:
        return False, "no FastAPI task's full universe exceeds 13,000 tokens; trimming not exercised"
    out = finalize_context(raw, tok, budget=C.RETRIEVAL_BUDGET)
    verify_ranking(out.items)
    kept_prefix = [i.source_id for i in out.items] == [i.source_id for i in raw.ranked_items()][:len(out.items)]
    ok = raw.total_tokens > C.RETRIEVAL_BUDGET and out.total_tokens <= C.RETRIEVAL_BUDGET and kept_prefix \
        and out.build_meta["over_budget"]
    return ok, (f"{raw.task_id}: PRISM delivered {raw.total_tokens} harness tokens over {len(raw.items)} items -> trimmed to "
                f"{out.total_tokens} over {len(out.items)} (lowest ranks dropped: {len(out.build_meta['budget_dropped'])})")


def check_tokenizer_parity(tok):
    from harness.tokenizer import LlamaServerTokenizer, verify_tokenizer_parity

    class Handler(http.server.BaseHTTPRequestHandler):
        drift = 0

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            ids = list(range(tok.count(body["content"]) + Handler.drift))
            out = json.dumps({"tokens": ids}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        server = LlamaServerTokenizer(f"http://127.0.0.1:{srv.server_address[1]}")
        root = _fastapi_root()
        samples = [open(os.path.join(root, p)).read()[:4000] for p in
                   (FASTAPI_FILE, "fastapi/routing.py", "fastapi/applications.py")]
        same = verify_tokenizer_parity(tok, server, samples)
        Handler.drift = 40     # ~4% of a ~1,000-token sample
        drifted = verify_tokenizer_parity(tok, server, samples)
    finally:
        srv.shutdown()
    ok = same.within_tolerance and same.max_divergence == 0 and not drifted.within_tolerance
    return ok, (f"identical server: max divergence {same.max_divergence:.3%}; drifted server: "
                f"{drifted.max_divergence:.2%} -> fallback flagged={not drifted.within_tolerance} (tokenizer {tok.name})")


def _synthetic_scored():
    from harness.scoring.canonical import DeliveredContext, DeliveredItem, NormalizedAnswer
    from harness.scoring.scorer import score
    from harness.tasks.synthetic import CALLERS, TARGET, synthetic_tasks
    root = _fastapi_root()
    out = {}
    for t in synthetic_tasks(root):
        items = [DeliveredItem("fastapi/dependencies/utils.py:247-251", "def get_typed_annotation(...): ...", 8, 1,
                               "code_chunk", [TARGET]),
                 DeliveredItem("fastapi/dependencies/utils.py:231-244", "def get_typed_signature(...): ...", 8, 2,
                               "code_chunk", [CALLERS[0]])]
        ctx = DeliveredContext("arm1", t.task_id, items, 16, C.RETRIEVAL_BUDGET, {})
        ans = NormalizedAnswer("arm1", t.task_id, "", "", [TARGET, CALLERS[0]], "code_block", True, 5, 0.1)
        out[t.task_type] = score(t, ctx, ans)
    return out


def check_scorer_dispatch(tok):
    from harness.scoring import registry as R
    scored = _synthetic_scored()
    problems = []
    for tt, res in scored.items():
        if res.task_type != tt:
            problems.append(f"{tt}: wrong branch")
        row = res.to_dict()
        for m in R.universal_metrics():
            if row.get(m) is None:
                problems.append(f"{tt}: {m} missing")
        for m in R.task_specific_metrics(tt):
            if m not in res.task_specific:
                problems.append(f"{tt}: {m} missing")
    ok = len(scored) == 5 and not problems
    branches = ", ".join(f"{tt.split('_')[0]}: tsr={r.tsr:.2f}" if r.tsr == r.tsr else f"{tt.split('_')[0]}: tsr=NaN"
                         for tt, r in scored.items())
    return ok, (branches + ("" if ok else f"; problems: {problems}"))


def check_arm0_cleanliness_nan(tok):
    from harness.scoring.canonical import DeliveredContext, NormalizedAnswer
    from harness.scoring.scorer import score
    from harness.tasks.synthetic import TARGET, synthetic_tasks
    t2 = synthetic_tasks(_fastapi_root())[1]
    res = score(t2, DeliveredContext("arm0", t2.task_id, [], 0, 0, {}),
                NormalizedAnswer("arm0", t2.task_id, "", "", [TARGET], "code_block", True, 5, 0.1))
    return math.isnan(res.cleanliness), f"cleanliness={res.cleanliness}"


def check_t5_fractional(tok):
    from harness.scoring.canonical import DeliveredContext, NormalizedAnswer
    from harness.scoring.scorer import score
    from harness.tasks.schema import BlastRadiusTask, GroundTruth
    gold = [f"pkg.mod.caller_{i}" for i in range(10)]
    t = BlastRadiusTask(task_id="t5", repo_id="r", repo_root=tempfile.gettempdir(), query="q",
                        ground_truth=GroundTruth(pipeline_symbols=gold))
    res = score(t, DeliveredContext("arm1", "t5", [], 0, C.RETRIEVAL_BUDGET, {}),
                NormalizedAnswer("arm1", "t5", "", "", gold[:8], "code_block", True, 5, 0.1))
    return abs(res.tsr - 0.8) < 1e-12, f"8/10 named -> tsr={res.tsr}, task_success(secondary)={res.task_success}"


def check_bootstrap_paired(tok):
    import numpy as np

    from harness.scoring.bootstrap import resample_plan, task_values
    from types import SimpleNamespace as NS
    a = [NS(arm="arm1", repo_id=r, task_id=f"{r}{i}", task_type="T2_localization", tsr=float(i % 2), task_specific={})
         for r in ("fastapi", "django") for i in range(6)]
    b = [NS(arm="arm5", repo_id=s.repo_id, task_id=s.task_id, task_type=s.task_type, tsr=1.0, task_specific={}) for s in a]
    ta = {r: sorted(v) for r, v in task_values(a, "tsr").items()}
    tb = {r: sorted(v) for r, v in task_values(b, "tsr").items()}
    pa, pb = resample_plan(ta, 1000), resample_plan(tb, 1000)
    same = all(np.array_equal(pa[r], pb[r]) for r in pa) and pa.keys() == pb.keys()
    return same, f"{len(pa)} fixed repos x 1000 reps: identical index matrices across arms = {same}"


def check_e2e_fastapi_5_tasks(tok):
    from harness.arms import build_arm
    from harness.llm import Completion
    from harness.pipeline import Pipeline
    from harness.scoring.fairness import verify_ranking
    from harness.tasks.loaders import load_tasks

    def scripted(system, user, max_tokens=0, seed=None, purpose=""):
        if purpose in ("turn1", "turn2b"):
            return Completion(json.dumps({"requested_symbols": []}), 1, 5, 0.0, purpose=purpose)
        return Completion('```json\n{"reasoning": "scripted", "symbols": ["fastapi.dependencies.utils.get_dependant"]}\n```',
                          1, 9, 0.0, purpose=purpose)

    root = _fastapi_root()
    arm5 = build_arm("arm5", llm=scripted, tokenizer=tok)
    arm5.index(root, {})
    oracle = build_arm("oracle", tokenizer=tok, builder=arm5.engine.builder)
    oracle.index(root, {})
    pipe = Pipeline({"arm0": build_arm("arm0"), "arm5": arm5, "oracle": oracle}, scripted, tok)
    tasks = load_tasks("fastapi", repo_root=root, limit=5)
    n = 0
    for t in tasks:
        for a in ("arm0", "arm5", "oracle"):
            out = pipe.run_cell(a, t, seed=42)
            verify_ranking(out.ctx.items)
            assert out.ctx.total_tokens <= out.ctx.budget_tokens and out.ans.extraction_success
            n += 1
    return n == 15, f"{n}/15 cells: valid context + answer + ScoreResult (scripted model; real model runs on Kaggle)"


CHECKS = [
    ("arm1_chunk_reassembly", check_arm1_chunk_reassembly),
    ("arm1_onnx_reranker", check_arm1_onnx_reranker),
    ("arm1_dense_encoder", check_arm1_dense_encoder),
    ("arm5_trimming", check_arm5_trimming),
    ("tokenizer_parity", check_tokenizer_parity),
    ("scorer_dispatch", check_scorer_dispatch),
    ("arm0_cleanliness_nan", check_arm0_cleanliness_nan),
    ("t5_fractional", check_t5_fractional),
    ("bootstrap_paired", check_bootstrap_paired),
    ("e2e_fastapi_5_tasks", check_e2e_fastapi_5_tasks),
]


def run(only: list[str] | None = None) -> list[dict]:
    from harness.tokenizer import get_tokenizer
    tok = get_tokenizer()
    rows = []
    for name, fn in CHECKS:
        if only and name not in only:
            continue
        t0 = time.perf_counter()
        try:
            ok, detail = fn(tok)
            status = "PASS" if ok else "FAIL"
        except Exception as exc:  # noqa: BLE001 - every check must report, not crash the table
            status = "BLOCKED" if _blocked(exc) else "FAIL"
            detail = f"{type(exc).__name__}: {str(exc).splitlines()[0][:220] if str(exc) else ''}"
            if status == "FAIL":
                detail += " | " + traceback.format_exc(limit=2).strip().splitlines()[-1][:200]
        rows.append({"check": name, "status": status, "seconds": round(time.perf_counter() - t0, 1), "detail": detail})
        print(f"{status:8s} {name:24s} {rows[-1]['seconds']:6.1f}s  {detail}", flush=True)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-blocked", action="store_true")
    ap.add_argument("--json", default=None)
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args(argv)
    rows = run(args.only)
    print("\n" + "-" * 72)
    print(f"{'CHECK':26s}{'STATUS':10s}")
    for r in rows:
        print(f"{r['check']:26s}{r['status']:10s}")
    counts = {s: sum(r["status"] == s for r in rows) for s in ("PASS", "FAIL", "BLOCKED")}
    print(f"PASS {counts['PASS']}  FAIL {counts['FAIL']}  BLOCKED {counts['BLOCKED']}")
    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"rows": rows, "counts": counts}, fh, indent=1)
    if counts["FAIL"] or (counts["BLOCKED"] and not args.allow_blocked):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
