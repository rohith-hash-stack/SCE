"""P0 evaluation run: validate the substrate and compute every metric that
the available artifacts support. Writes only under --out (never into the
canonical baselines or reports).

    python -m harness.experiments.query_to_evidence.nlq.run --out DIR            # no index: recorded lexical results
    TIKTOKEN_CACHE_DIR=... python -m harness.experiments.query_to_evidence.nlq.run --out DIR --index
        # builds the FastAPI and Django indexes (~5 min), re-measures lexical
        # search, diffs it against the recorded probe output, and measures
        # the clarification payload the MCP tools would return today (M5c)

No model is called. Oracle (gold-derived) interpretations are scored only as
a self-check of the scorers: their M1/M2/M5/M6 results are perfect by
construction and say nothing about natural-language understanding.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from harness.experiments.query_to_evidence import dataset as D
from harness.experiments.query_to_evidence.nlq import baseline_records as B
from harness.experiments.query_to_evidence.nlq import gold_fixtures as G
from harness.experiments.query_to_evidence.nlq import lexical_eval as L
from harness.experiments.query_to_evidence.nlq import metrics as M
from harness.experiments.query_to_evidence.nlq import schema as S

REPO = Path(__file__).resolve().parents[4]
PROTECTED = [REPO / "harness/experiments/query_to_evidence/design/baseline", REPO / "reports",
             REPO / "harness/experiments/query_to_evidence/dataset"]
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
#: Metrics this run computes on the gold-derived interpretations themselves
#: (perfect by construction). M5c is not one: it scores today's MCP payload.
ORACLE_SELF_CHECK_IDS = ("M1", "M2", "M5a", "M5b", "M6")


def _dump(x) -> str:
    return json.dumps(x, indent=1, sort_keys=True) + "\n"


def mcp_payloads(builders: dict, fixtures: list[dict]) -> dict:
    """What `_resolve_seed_or_raise` would list today for each ambiguous
    question's subject mention: the sorted matches, cut at the server's own
    limit. A mention that is not an identifier has no payload (None)."""
    from prism.mcp.server import _AMBIGUOUS_CANDIDATE_LIMIT
    from prism.query.locate import qualified_name_matches
    out = {}
    for fx in fixtures:
        it = fx["interpretation"]
        if it["answerability"] != "needs_clarification":
            continue
        mention = it["requirements"][0]["subject"]["mention"]
        out[it["question_id"]] = (qualified_name_matches(builders[it["corpus"]], mention)[:_AMBIGUOUS_CANDIDATE_LIMIT]
                                  if _IDENT.match(mention) else None)
    return out


def run(out: Path, with_index: bool) -> dict:
    fixtures = G.load()
    interps = [fx["interpretation"] for fx in fixtures]
    problems = {"fixture_schema": {i["question_id"]: S.validate_interpretation(i) for i in interps},
                "fixture_file_matches_builder": G.FIXTURE.read_text() == G.render(G.build()),
                "dataset": D.validate(D.load_questions(), D.load_gold())}
    recs = B.records()
    plans = {m: [p for p, _ in qs.values()] for m, qs in recs.items()}
    reports = {m: [r for _, r in qs.values()] for m, qs in recs.items()}
    problems["record_schema"] = [f"{m}/{x['question_id']}: {e}" for m in recs for x in plans[m] + reports[m]
                                 for e in (S.validate_plan(x) if x["schema"] == S.PLAN else S.validate_proof_report(x))]
    oracle = "oracle gold-derived fixtures (scorer self-check; perfect by construction)"
    base = "proof_baseline.json re-shaped (gold-subject counterfactual)"
    metrics = [M.m1_requirement_kind(interps, fixtures, oracle), M.m2_answerability(interps, fixtures, oracle),
               M.m6_decomposition(interps, fixtures, oracle)]
    metrics += [M.m2_plan_gate(plans[m], fixtures, f"{base}, {m}", label=m) for m in sorted(plans)]
    metrics.append(M.m4_subject_precision([p for m in plans for p in plans[m]], fixtures, base))
    metrics += M.m8_proof_states(reports, base) + M.m12_budget(reports, base)

    lexical = {"source": "recorded probe output (fixtures/lexical_recorded.json)", "result": L.load_recorded(),
               "diff_against_recorded": None}
    payloads = None
    pairs = {"gold_interpretations": (G.render(G.build()), G.render(G.build())),
             "baseline_records": (_dump(B.records()), _dump(B.records()))}
    if with_index:
        from harness.experiments.query_to_evidence.nlq.lexical import LexicalIndex
        from harness.experiments.query_to_evidence.run import build_engine
        from harness.tasks.loaders import _repo_root
        engines = {c: build_engine(str(_repo_root(c))) for c in ("fastapi", "django")}
        indexes = {c: L.recorded_index(e.builder, e.repo_root, c) for c, e in engines.items()}
        questions, gold = D.load_questions(), D.load_gold()
        first = L.measure(indexes, questions, gold)
        pairs["lexical_measurement"] = (_dump(first), _dump(L.measure(indexes, questions, gold)))
        lexical = {"source": "re-measured on fresh indexes", "result": first,
                   "diff_against_recorded": L.diff_against_recorded(first)}
        rel = L.measure({c: LexicalIndex(e.builder, e.repo_root, relative_paths=True) for c, e in engines.items()},
                        questions, gold)
        lexical["path_sensitivity"] = {
            "note": "same search with repo-relative file paths (not the recorded variant); shows how much the "
                    "absolute-path tokens of SymbolInfo.file move the ranks",
            "fields_differing_from_recorded": len(L.diff_against_recorded(rel)),
            "M3": [{"id": x["id"], "numerator": x["numerator"], "denominator": x["denominator"]}
                   for x in M.m3_lexical_recall(rel, "relative paths")]}
        payloads = mcp_payloads({c: e.builder for c, e in engines.items()}, fixtures)
    metrics += M.m3_lexical_recall(lexical["result"], lexical["source"])
    metrics += M.m5_clarification(interps, fixtures, payloads,
                                  oracle + (" + MCP qualified_name_matches payloads" if payloads is not None else ""))
    metrics.append(M.m13_reproducible(pairs))
    metrics += M.unmeasured_in_p0()
    metrics = [M.oracle_self_check(m) if m["id"] in ORACLE_SELF_CHECK_IDS else m for m in metrics]
    result = {"problems": problems, "metrics": metrics, "lexical": lexical,
              "mcp_payloads": payloads}
    out.mkdir(parents=True, exist_ok=True)
    (out / "p0_metrics.json").write_text(_dump(result))
    (out / "baseline_records.json").write_text(_dump({m: {q: {"plan": p, "proof_report": r} for q, (p, r) in qs.items()}
                                                      for m, qs in recs.items()}))
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="P0 NLQ evaluation substrate run")
    ap.add_argument("--out", required=True)
    ap.add_argument("--index", action="store_true", help="build real indexes and re-measure lexical search")
    args = ap.parse_args(argv)
    out = Path(args.out).resolve()
    if any(out == p or p in out.parents for p in PROTECTED):
        print(f"refusing to write into a canonical location: {out}", file=sys.stderr)
        return 2
    res = run(out, args.index)
    for m in res["metrics"]:
        val = f"{m['numerator']}/{m['denominator']}" if m["status"] == "measured" else f"NOT MEASURED ({m['reason']})"
        print(f"{m['id']:<28} {m['method']:<13} {val}" + ("  [ORACLE SELF-CHECK]" if m.get("oracle_self_check") else ""))
    bad = {k: v for k, v in res["problems"]["fixture_schema"].items() if v}
    print("fixture schema problems:", bad or "none", "| record schema problems:", len(res["problems"]["record_schema"]),
          "| dataset problems:", res["problems"]["dataset"] or "none")
    if res["lexical"]["diff_against_recorded"] is not None:
        print("lexical diffs vs recorded:", len(res["lexical"]["diff_against_recorded"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
