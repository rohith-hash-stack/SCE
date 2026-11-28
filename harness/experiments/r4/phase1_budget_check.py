"""Phase 1 step 1d: counterfactual budget accounting from two replays of the same Phase 0 run.

    python -B -m harness.experiments.r4.phase1_budget_check PLAIN_DIR COMPACT_DIR [--out report.json]

Both directories come from `replay run ... --count-prompts`, the second with `--compact --hygiene`. The replies are the
recorded ones, so the trajectory is the same; only the prompts differ. Reported per turn and in total:
- the prompt size in each replay (tokenizer counts, the same tokenizer on both sides);
- cumulative tokens used (prompt + recorded answer), and the turn at which the 300,000 budget would have ended each;
- what compaction did (superseded, stale, repeated, summarised) and how many outputs the window rule still stubbed.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

BUDGET = 300_000


def _turns(d: str) -> list[dict]:
    log = next(p for p in glob.glob(os.path.join(d, "*.jsonl")))
    return [json.loads(ln) for ln in open(log)]


def check(plain: str, compact: str) -> dict:
    a, b = _turns(plain), _turns(compact)
    rows, ua, ub, end_a, end_b = [], 0, 0, None, None
    for x, y in zip(a, b):
        ua += x["prompt_tokens"] + x["answer_tokens"]
        ub += y["prompt_tokens"] + y["answer_tokens"]
        end_a = end_a or (x["turn"] if ua > BUDGET else None)
        end_b = end_b or (y["turn"] if ub > BUDGET else None)
        rows.append({"turn": x["turn"], "tool": x.get("tool"), "prompt_plain": x["prompt_tokens"],
                     "prompt_compact": y["prompt_tokens"], "stubbed_plain": x.get("stubbed", 0),
                     "stubbed_compact": y.get("stubbed", 0), "compaction": y.get("compaction")})
    n = len(rows)
    return {"turns_compared": n, "used_plain": ua, "used_compact": ub,
            "saving_pct": round(100 * (ua - ub) / ua, 1) if ua else None,
            "mean_prompt_plain": round(sum(r["prompt_plain"] for r in rows) / n) if n else None,
            "mean_prompt_compact": round(sum(r["prompt_compact"] for r in rows) / n) if n else None,
            "late_prompt_plain": max((r["prompt_plain"] for r in rows), default=0),
            "late_prompt_compact": max((r["prompt_compact"] for r in rows), default=0),
            "turns_with_stubbing_plain": sum(1 for r in rows if r["stubbed_plain"]),
            "turns_with_stubbing_compact": sum(1 for r in rows if r["stubbed_compact"]),
            "budget_turn_plain": end_a, "budget_turn_compact": end_b,
            "turns_affordable_compact": (BUDGET - ub) // max(1, rows[-1]["prompt_compact"] + 200) if rows else None,
            "per_turn": rows}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("plain")
    p.add_argument("compact")
    p.add_argument("--out")
    a = p.parse_args(argv)
    rep = check(a.plain, a.compact)
    if a.out:
        json.dump(rep, open(a.out, "w"), indent=1)
    print(json.dumps({k: v for k, v in rep.items() if k != "per_turn"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
