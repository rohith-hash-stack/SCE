"""Check a `.prism/summaries.json` written by a live `prism summarize` run.

Reports what production will do with it and whether it meets the bar the
locked candidate was validated at:

- coverage, stale entries, locked pipeline, and the summary weight
  `prism.ask` will apply (`prism.summaries.index`);
- lint rates over every summary against its card (`prism.summaries.lint`),
  against the gate (any flag <= GATE pooled) - the index's own lint (v2, or a
  candidate's stricter one), plus every extra check's rate for reference;
- idempotency: a dry run must find nothing left to summarize;
- one `prism ask` per question given, with the route and weight it reports.

Usage: python -m harness.experiments.purpose_search.live_check REPO_ROOT OUT_JSON [QUESTION ...]
Exit status 1 when coverage is under 95%, the weight is not the expected one (0.5 for the
locked v2, 0 for an unvalidated candidate), the gate fails, or work is left.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from prism.cli import build_pipeline
from prism.graph.entry_points import find_entry_points
from prism.query.nlq import NLQIndex
from prism.summaries.cards import build_card
from prism.summaries.index import MIN_COVERAGE, SUMMARY_WEIGHT, index_path, read_index, state_for
from prism.summaries.lint import EXTRA_FLAGS, rates
from prism.summaries.pipeline import CANDIDATES, build_index

GATE = 0.05


def check(root: str, questions: list[str]) -> dict:
    builder = build_pipeline(root)[0]
    state = state_for(builder, root)
    data = read_index(index_path(root)) or {}
    entries = data.get("functions") or {}
    entry_points = find_entry_points(builder)
    pairs = [(e["summary"], c["card"]) for q, e in entries.items() if q in state.summaries
             and (c := build_card(builder, {}, entry_points, q, axes=False))]
    candidate = (data.get("pipeline") or {}).get("lint", "v2")
    lint = rates(pairs, CANDIDATES[candidate]["lint_flags"])
    strict_lint = rates(pairs, EXTRA_FLAGS)
    left = build_index(builder, root, dry_run=True, candidate=candidate)["to_summarize"]
    nlq = NLQIndex(builder, root, summaries="auto")
    asks = []
    for question in questions:
        res = nlq.search(question, top_k=3)
        asks.append({"question": question, "route": res["intent"]["route"],
                     "summary_weight": res["intent"]["summary_weight"], "top3": [h["qname"] for h in res["hits"]]})
    gate_ok = all(v <= GATE for k, v in lint.items() if k not in ("any", "n"))
    # A candidate's index is unvalidated: search must ignore it (weight 0) until it is locked.
    expected_weight = SUMMARY_WEIGHT if candidate == "v2" else 0.0
    return {"candidate": candidate, "status": state.status(),
            "unchecked": sum(1 for e in entries.values() if not e.get("checked")),
            "lint": lint, "strict_lint": strict_lint, "gate": GATE, "gate_ok": gate_ok, "left_to_summarize": left,
            "asks": asks, "samples": dict(list((q, e["summary"]) for q, e in sorted(entries.items()))[:12]),
            "ok": state.coverage >= MIN_COVERAGE and state.weight == expected_weight and gate_ok and left == 0}


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    root, out, questions = args[0], Path(args[1]), args[2:]
    result = check(root, questions)
    out.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({k: result[k] for k in ("candidate", "status", "lint", "gate_ok", "left_to_summarize", "unchecked", "ok")},
                     indent=1))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
