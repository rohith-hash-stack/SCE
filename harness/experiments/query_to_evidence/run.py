"""Run the query-to-evidence evaluation offline and write results + report.

    TIKTOKEN_CACHE_DIR=<dir with cl100k> python -m harness.experiments.query_to_evidence.run [--out DIR] [--corpus C]

No model is called. Two retrieval modes, reported separately:

  production      each question's own inputs, as the existing harness hands
                  them to Arm 5 (only seeded controls have a seed; Prism has
                  no natural-language entry point, so every other question
                  stops at subject resolution).
  counterfactual  NOT a production path: each requirement's gold `subject`
                  is supplied as the seed, to measure what the stages after
                  resolution would deliver given a perfect resolver. Turn 1
                  is the production rule selector (R0) on T5 and an
                  every-row upper bound on other task types (no model).

Seeded controls replay their stored s42 cell: T5 runs the R0 rule (no model
in Turn 1); T2 replays the stored Turn-1 text. Answers are graded only from
stored completions, against the stored model input that produced them.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from harness.experiments.query_to_evidence import dataset as D
from harness.experiments.query_to_evidence.accounting import GraphProbe, requirement_account, symbol_account
from harness.experiments.query_to_evidence.grading import grade_stored_answer
from harness.experiments.query_to_evidence.trace import AllRowsLLM, Cl100kCounter, ReplayLLM, run_case

REPO = Path(__file__).resolve().parents[3]
BASELINE_DIRS = {"r0": REPO / "reports/harness_r0r1/r0", "m4": REPO / "reports/harness_m4"}
BASELINE_COMMITS = {"r0": "bcb1197", "m4": "e69f0ce"}
SEED = 42


def build_engine(root: str):
    """Fresh in-memory index: the two calls `PrismEngine.from_repo` makes,
    without reading on-disk graph/contract caches a different code version
    may have written."""
    from prism.cli import build_pipeline
    from prism.engine import PrismEngine
    from prism.runtime.contract_cache import compute_contracts
    builder, _tags = build_pipeline(root, use_cache=False)
    return PrismEngine(builder, root, contracts=compute_contracts(builder))


def load_baseline(run: str, corpus: str, task_id: str) -> dict | None:
    base = BASELINE_DIRS[run] / corpus
    bpath = base / "bundles" / f"arm5_{task_id}_s{SEED}.json"
    if not bpath.exists():
        return None
    stored = json.loads(bpath.read_text())
    cpath = base / "completions" / f"arm5_{task_id}_s{SEED}.json"
    stored["completion"] = json.loads(cpath.read_text()) if cpath.exists() else None
    return stored


def compare_to_baseline(case: dict, stored: dict) -> dict:
    b = stored["bundle"]
    syms = lambda items, kind=None: sorted({s for i in items for s in i["symbols"] if kind is None or i["kind"] == kind})
    meta = b["build_meta"]
    ours, theirs = case["build_meta"], meta
    out = {
        "same_requested": list(ours.get("requested_symbols") or []) == list(theirs.get("requested_symbols") or []),
        "same_delivered_symbols": syms(case["items"]) == syms(b["items"]),
        "same_stub_symbols": syms(case["items"], "signature_stub") == syms(b["items"], "signature_stub"),
        "same_item_content": [i["content"] for i in case["items"]] == [i["content"] for i in b["items"]],
        "same_prompt": case["prompt"] == stored.get("prompt"),
        "stored_budget_dropped": len(theirs.get("budget_dropped") or []),
        "replay_budget_dropped": len(ours.get("budget_dropped") or []),
        "stored_pre_trim_tokens_qwen": theirs.get("pre_trim_tokens"),
        "replay_pre_trim_tokens_cl100k": ours.get("pre_trim_tokens"),
        "stored_manifest_candidates": theirs.get("manifest_candidates"),
        "replay_manifest_candidates": ours.get("manifest_candidates"),
    }
    if not out["same_delivered_symbols"]:
        a, s = set(syms(case["items"])), set(syms(b["items"]))
        out["only_in_replay"], out["only_in_stored"] = sorted(a - s), sorted(s - a)
    if not out["same_requested"]:
        a, s = set(ours.get("requested_symbols") or []), set(theirs.get("requested_symbols") or [])
        out["requested_only_in_replay"], out["requested_only_in_stored"] = sorted(a - s), sorted(s - a)
    out["equivalent"] = all(out[k] for k in ("same_requested", "same_delivered_symbols", "same_stub_symbols",
                                             "same_item_content", "same_prompt"))
    return out


def stored_case(stored: dict) -> dict:
    """The stored cell as an accounting `case` (no trace: manifest and
    hydration stages are then unknown, not lost)."""
    b = stored["bundle"]
    items = [{"symbols": i["symbols"], "kind": i["kind"], "source_id": i["source_id"], "content": i["content"],
              "tokens": i["token_count"]} for i in b["items"]]
    from harness.arms.base import SYSTEM_PROMPT
    return {"trace": None, "raw_items": items, "items": items, "build_meta": b["build_meta"],
            "prompt": stored.get("prompt"), "model_input": SYSTEM_PROMPT + "\n\n" + (stored.get("prompt") or "")}


def _case_summary(case: dict) -> dict:
    meta = case["build_meta"]
    tr = case.get("trace")
    used = sum(i["tokens"] for i in case["items"])
    return {
        "anchor": meta.get("anchor"), "no_seed": bool(meta.get("no_seed")),
        "turn1_source": meta.get("turn1_source"),
        "turn1_model": (meta.get("retrieval_turns") or [{}])[0].get("model") if meta.get("retrieval_turns") else None,
        "manifest_rows": len(tr.manifest["universe"]) if tr is not None and tr.manifest else None,
        "requested": len(meta.get("requested_symbols") or []),
        "skipped_requested_not_in_manifest": list(meta.get("skipped_hallucinated") or []),
        "hydrated_nodes": len(tr.hydration["nodes"]) if tr is not None and tr.hydration else None,
        "hydrated_edges": len(tr.hydration["edges"]) if tr is not None and tr.hydration else None,
        "hydration_warnings": tr.hydration["warnings"] if tr is not None and tr.hydration else None,
        "delivered_items": len(case["items"]),
        "delivered_full": sum(1 for i in case["items"] if i["kind"] == "code_chunk"),
        "delivered_stub": sum(1 for i in case["items"] if i["kind"] == "signature_stub"),
        "budget_dropped": len(meta.get("budget_dropped") or []),
        "context_tokens": used, "budget": case["budget"], "budget_use": round(used / case["budget"], 3) if case["budget"] else None,
        "prompt_tokens": case["prompt_tokens"], "retrieve_ms": case["retrieve_ms"],
    }


def _graph_only_account(req: dict, builder, probe) -> dict:
    """Production-mode requirement with no subject: every symbol stops at
    resolution; graph status of the edges is still reported."""
    empty = {"trace": None, "raw_items": [], "items": [], "build_meta": {}, "prompt": None, "model_input": None}
    return requirement_account(req, empty, builder, probe)


class Evaluation:
    def __init__(self, corpora: list[str] | None = None) -> None:
        from harness import config as C
        from harness.arms import build_arm
        from harness.tasks.loaders import _repo_root, load_tasks
        assert C.PRISM_T5_RULE_SELECTOR and not C.PRISM_PRODUCTION_ROUTING, "expects the default R0 configuration"
        self.questions = [q for q in D.load_questions() if corpora is None or q["corpus"] in corpora]
        self.gold = D.load_gold()
        self.tok = Cl100kCounter()
        self.arms, self.probes, self.tasks = {}, {}, {}
        for corpus in sorted({q["corpus"] for q in self.questions}):
            root = str(_repo_root(corpus))
            arm = build_arm("arm5", llm=AllRowsLLM(), tokenizer=self.tok, engine=build_engine(root))
            arm.index(root, {})
            self.arms[corpus] = arm
            self.probes[corpus] = GraphProbe(arm.engine.builder)
            self.tasks[corpus] = {t.task_id: t for t in load_tasks(corpus, ["T2_localization", "T5_blast_radius"])}

    def builders(self) -> dict:
        return {c: a.engine.builder for c, a in self.arms.items()}

    def _run(self, corpus: str, query: str, seed: dict, llm, trace: bool = True) -> dict:
        arm = self.arms[corpus]
        arm.llm = llm
        return run_case(arm, query, seed, self.tok, trace=trace)

    def control_inputs(self, q: dict) -> tuple[dict, dict | None, object]:
        g = self.gold[q["id"]]
        task = self.tasks[q["corpus"]][g["provenance"]["source_task"]]
        seed = {**task.seed_dict(), "llm_seed": SEED}
        assert seed["seed_symbol"] == q["inputs"]["seed_symbol"] and seed["task_type"] == q["inputs"]["task_type"]
        stored = load_baseline(g["provenance"]["baseline_run"], q["corpus"], task.task_id)
        llm = ReplayLLM(stored["bundle"]["build_meta"].get("retrieval_turns") or []) if stored else AllRowsLLM()
        return seed, stored, llm

    def evaluate(self) -> dict:
        results = []
        for q in self.questions:
            results.append(self.evaluate_question(q))
            print(f"{q['id']} {q['category']:20s} done", flush=True)
        return {"questions": results, "metrics": metrics(results)}

    def evaluate_question(self, q: dict) -> dict:
        g, corpus = self.gold[q["id"]], q["corpus"]
        builder, probe = self.arms[corpus].engine.builder, self.probes[corpus]
        out = {"id": q["id"], "corpus": corpus, "category": q["category"], "answerability": g["answerability"],
               "expected_behavior": g["expected_behavior"]}
        # ---- production mode -------------------------------------------
        if q["category"] == "seeded_control":
            seed, stored, llm = self.control_inputs(q)
            case = self._run(corpus, q["query"], seed, llm)
            prod = {"summary": _case_summary(case),
                    "requirements": [requirement_account(r, case, builder, probe) for r in g["requirements"]]}
            if stored is not None:
                run = g["provenance"]["baseline_run"]
                cmp_ = compare_to_baseline(case, stored)
                cmp_.update(run=run, commit=BASELINE_COMMITS[run])
                prod["baseline"] = cmp_
                prod["stored_cell"] = {"requirements": [requirement_account(r, stored_case(stored), builder, probe)
                                                        for r in g["requirements"]]}
                prod["answer"] = grade_stored_answer(stored, g, builder, q["inputs"]["seed_symbol"])
            out["production"] = prod
        else:
            seed = {"task_id": q["id"], "task_type": None, "seed_symbol": None}
            case = self._run(corpus, q["query"], seed, AllRowsLLM())
            out["production"] = {
                "summary": _case_summary(case),
                "requirements": [_graph_only_account(r, builder, probe) for r in g.get("requirements", [])],
                "answer": {"status": "not_measured", "reason": "answer stage not run (no model in this evaluation)"},
            }
        # ---- counterfactual mode (gold subject supplied) -----------------
        cf = []
        for r in g.get("requirements", []):
            if not r.get("counterfactual_task_type"):
                cf.append({"id": r["id"], "kind": r["kind"], "status": "n/a",
                           "reason": "absence requirement: nothing to retrieve; the pipeline has no way to report absence"})
                continue
            seed = {"task_id": f"{q['id']}_{r['id']}", "task_type": r["counterfactual_task_type"],
                    "seed_symbol": r["subject"], "llm_seed": SEED}
            case = self._run(corpus, q["query"], seed, AllRowsLLM())
            acc = requirement_account(r, case, builder, probe)
            acc["summary"] = _case_summary(case)
            acc["turn1_policy"] = ("production rule (R0: every caller row)" if r["counterfactual_task_type"] == "T5_blast_radius"
                                   else "every manifest row (upper bound; no model)")
            cf.append(acc)
        out["counterfactual"] = cf
        if g["answerability"] == "ambiguous":
            out["ambiguity"] = {
                "interpretations": [{"label": i["label"], "symbols": i["symbols"],
                                     "indexed": sum(1 for s in i["symbols"] if s in builder.symbol_table)}
                                    for i in g["ambiguity"]["interpretations"]],
                "pipeline_clarification_mechanism": False,
                "outcome": "not_resolved_from_query (no clarification path exists; retrieval returns an empty context)",
            }
        if g["answerability"] == "unanswerable":
            out["unanswerable"] = {"reason": g["unanswerable_reason"], "absence_is_expected": True,
                                   "retrieval_outcome": "empty context (no_seed)",
                                   "classification": "expected_absence (not a retrieval failure)",
                                   "abstention": "not_measured (answer stage not run; no production abstention path)"}
        return out

    def equivalence(self) -> list[dict]:
        """Tracer on vs off: identical requested symbols, delivered items,
        compression, budget trim and model input."""
        rows = []
        for q in self.questions:
            g = self.gold[q["id"]]
            if q["category"] == "seeded_control":
                runs = [("production", *self.control_inputs(q)[::2])]
            else:
                runs = [(f"counterfactual:{r['id']}", {"task_id": q["id"], "task_type": r["counterfactual_task_type"],
                                                       "seed_symbol": r["subject"], "llm_seed": SEED}, None)
                        for r in g.get("requirements", []) if r.get("counterfactual_task_type")]
            for label, seed, llm in runs:
                a = self._run(q["corpus"], q["query"], seed, llm or AllRowsLLM(), trace=True)
                if llm is not None:
                    llm = self.control_inputs(q)[2]
                b = self._run(q["corpus"], q["query"], seed, llm or AllRowsLLM(), trace=False)
                rows.append({"id": q["id"], "run": label, **equivalent(a, b)})
        return rows


def equivalent(a: dict, b: dict) -> dict:
    key = lambda c: [(tuple(i["symbols"]), i["kind"], i["content"], i["tokens"]) for i in c["items"]]
    raw = lambda c: [(tuple(i["symbols"]), i["kind"]) for i in c["raw_items"]]
    res = {
        "same_requested": a["build_meta"].get("requested_symbols") == b["build_meta"].get("requested_symbols"),
        "same_raw_items": raw(a) == raw(b),
        "same_delivered_items": key(a) == key(b),
        "same_budget_dropped": a["build_meta"].get("budget_dropped") == b["build_meta"].get("budget_dropped"),
        "same_model_input": a["model_input"] == b["model_input"],
    }
    res["identical"] = all(res.values())
    return res


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------
def _ratio(num: int, den: int) -> dict:
    return {"num": num, "den": den, "value": round(num / den, 3) if den else None}


def _symbols(reqs: list[dict]) -> list[dict]:
    return [s for r in reqs if isinstance(r, dict) and r.get("symbols") is not None for s in r["symbols"]]


def _edges(reqs: list[dict]) -> list[dict]:
    return [e for r in reqs if isinstance(r, dict) and r.get("edges") is not None for e in r["edges"]]


def retrieval_metrics(reqs: list[dict]) -> dict:
    syms, edges = _symbols(reqs), _edges(reqs)
    n = len(syms)
    if n == 0 and not edges:
        return {"status": "n/a (no required symbols or edges)"}
    ev = [e for r in reqs if isinstance(r, dict) for e in r.get("source_evidence", [])]
    def stage(key, pred):
        seen = [s for s in syms if s["reached"][key] is not None]
        return _ratio(sum(1 for s in seen if pred(s)), len(seen)) if seen or not syms else "not measured"

    def edge_stage(key):
        seen = [e for e in edges if e["stages"][key] is not None]
        return _ratio(sum(1 for e in seen if e["stages"][key]), len(seen)) if seen or not edges else "not measured"

    return {
        "subject_resolution": _ratio(sum(1 for s in syms if s["reached"]["resolution"]), n),
        "manifest_recall": stage("manifest", lambda s: s["reached"]["manifest"]),
        "requested_recall": _ratio(sum(1 for s in syms if s["requested"]), n),
        "selected_recall": stage("hydration", lambda s: s["reached"]["selection"]),
        "delivered_recall": _ratio(sum(1 for s in syms if s["delivery"]), n),
        "delivered_full": _ratio(sum(1 for s in syms if s["delivery"] == "full"), n),
        "delivered_stub_only": _ratio(sum(1 for s in syms if s["delivery"] == "stub"), n),
        "edges_in_graph": _ratio(sum(1 for e in edges if e["stages"]["graph"]), len(edges)),
        "edge_call_site_in_model_input": _ratio(sum(1 for e in edges if e["stages"]["call_site_in_model_input"]), len(edges)),
        "edge_constructed_in_package": edge_stage("hydrated_edge"),
        "relationship_record_delivered": _ratio(sum(1 for e in edges if e["relationship_record_delivered"]), len(edges)),
        "source_evidence_in_model_input": _ratio(sum(1 for e in ev if e["in_model_input"]), len(ev)) if ev else "n/a",
        "first_loss": dict(Counter(s["first_loss"] or "none (delivered)" for s in syms)),
        "states": dict(Counter(s["state"] for s in syms)),
        "secondary_causes": dict(Counter(c.split(" (")[0] for s in syms for c in s["secondary_causes"])),
        "edge_graph_status": dict(Counter(e["graph"]["status"] for e in edges)),
        "edge_first_loss": dict(Counter(e["first_loss"] or "none (call site delivered)" for e in edges)),
    }


def _merge_ratio(rows: list[dict], key: str) -> dict | str:
    vals = [r[key] for r in rows if isinstance(r.get(key), dict)]
    if not vals:
        return "not measured" if any(r.get(key) == "not measured" for r in rows) else "n/a"
    return _ratio(sum(v["num"] for v in vals), sum(v["den"] for v in vals))


def metrics(results: list[dict]) -> dict:
    per_q, by_cat = {}, defaultdict(lambda: {"production": [], "counterfactual": [], "stored_cell": []})
    for r in results:
        prod = retrieval_metrics(r["production"]["requirements"])
        cf = retrieval_metrics([x for x in r["counterfactual"] if "symbols" in x])
        stored = retrieval_metrics(r["production"]["stored_cell"]["requirements"]) if "stored_cell" in r["production"] else "not available"
        per_q[r["id"]] = {"category": r["category"], "production": prod, "counterfactual": cf, "stored_cell": stored,
                          "answer": r["production"].get("answer", {}).get("summary", r["production"].get("answer")),
                          "cost": r["production"]["summary"]}
        by_cat[r["category"]]["production"].append(prod)
        by_cat[r["category"]]["counterfactual"].append(cf)
        if isinstance(stored, dict):
            by_cat[r["category"]]["stored_cell"].append(stored)
    keys = ["subject_resolution", "manifest_recall", "requested_recall", "selected_recall", "delivered_recall", "delivered_full",
            "delivered_stub_only", "edges_in_graph", "edge_constructed_in_package", "edge_call_site_in_model_input",
            "relationship_record_delivered", "source_evidence_in_model_input"]
    cats = {}
    for cat, modes in by_cat.items():
        cats[cat] = {}
        for mode, rows in modes.items():
            rows = [x for x in rows if "status" not in x]
            cats[cat][mode] = {k: _merge_ratio(rows, k) for k in keys} if rows else "n/a"
            if rows:
                cats[cat][mode]["first_loss"] = dict(sum((Counter(x["first_loss"]) for x in rows), Counter()))
    answers = [r["production"]["answer"]["summary"] for r in results if "summary" in r["production"].get("answer", {})]
    cost = [r["production"]["summary"] for r in results if r["category"] == "seeded_control"]
    return {
        "per_question": per_q, "per_category": cats,
        "answer_controls": {
            "graded": len(answers),
            "symbol_recall": _ratio(sum(a["gold_named"] for a in answers), sum(a["gold_total"] for a in answers)),
            "symbol_precision": _ratio(sum(a["gold_named"] for a in answers), sum(a["named"] for a in answers)),
            "named_symbol_delivered_full": _ratio(sum(a["named_full"] for a in answers), sum(a["named"] for a in answers)),
            "named_symbol_stub_only": _ratio(sum(a["named_stub"] for a in answers), sum(a["named"] for a in answers)),
            "named_symbol_not_delivered": _ratio(sum(a["named_absent"] for a in answers), sum(a["named"] for a in answers)),
            "impact_claims_supported_by_delivered_path": _ratio(sum(a["path_supported"] for a in answers),
                                                                sum(a["path_claims"] for a in answers)),
        } if answers else "not measured",
        "cost_controls": {
            "retrieve_ms_median": statistics.median(c["retrieve_ms"] for c in cost) if cost else None,
            "prompt_tokens_cl100k_median": statistics.median(c["prompt_tokens"] for c in cost if c["prompt_tokens"]) if cost else None,
            "budget_use_median": statistics.median(c["budget_use"] for c in cost if c["budget_use"] is not None) if cost else None,
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(REPO / "reports/query_to_evidence"))
    ap.add_argument("--corpus", action="append")
    ap.add_argument("--skip-equivalence", action="store_true")
    ap.add_argument("--skip-envelope-modes", action="store_true",
                    help="skip the production-envelope modes (prism.slice / prism.blast_radius)")
    args = ap.parse_args(argv)
    from prism.slicer.tokenizer import active_backend, is_exact
    if not is_exact():
        raise SystemExit(f"exact cl100k tokenizer required (active: {active_backend()}); set TIKTOKEN_CACHE_DIR")
    ev = Evaluation(args.corpus)
    problems = D.validate(D.load_questions(), ev.gold, ev.builders())   # corpora not indexed: schema checks only
    result = ev.evaluate()
    result["dataset_problems"] = problems
    result["equivalence"] = [] if args.skip_equivalence else ev.equivalence()
    if not args.skip_envelope_modes:
        from harness.experiments.query_to_evidence import envelope as E
        modes = E.evaluate_modes({c: a.engine for c, a in ev.arms.items()}, ev.questions, ev.gold)
        result["delivery_modes"] = {
            "arm5_benchmark_production_inputs": {"kind": "benchmark prompt (Arm 5 items; no envelope)",
                                                 "summary": E.arm5_summary(result["questions"], "production")},
            "arm5_benchmark_gold_subject": {"kind": "benchmark prompt (Arm 5 items; no envelope)",
                                            "summary": E.arm5_summary(result["questions"], "counterfactual")},
            **{name: {"kind": "production envelope (real MCP tool)", **m} for name, m in modes.items()},
        }
    manual = json.loads((D.DATASET_DIR / "manual_grounding.json").read_text())
    result["manual_grounding"] = {"label": manual["label"], "verdicts": dict(Counter(c["verdict"] for c in manual["claims"])),
                                  "claims": manual["claims"]}
    result["environment"] = {"tokenizer": active_backend(), "harness_tokenizer_stand_in": ev.tok.name, "seed": SEED}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(result, indent=1, sort_keys=True, default=str))
    from harness.experiments.query_to_evidence.report import render
    (out / "report.md").write_text(render(result))
    print(f"wrote {out}/results.json and report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
