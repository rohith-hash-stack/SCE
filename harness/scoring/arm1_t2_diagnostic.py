"""One-off M1 diagnostic: is Arm 1's T2 failure a retrieval failure or a
symbol-mapping bug?

For each Arm 1 T2 cell it records the gold symbols and the top-5 delivered
chunks ({chunk_source_id, chunk_symbols, chunk_text}), and counts, over the
top 5 and over everything delivered, how many gold symbols are
- mentioned: the bare name occurs as a word in the chunk text;
- defined: the text contains `def <name>` / `class <name>`;
- in symbols: the gold FQN is in a chunk's `symbols` field.
A chunk that defines a gold symbol without carrying it in `symbols` would
be a mapping bug; low mention/define counts mean a real retrieval failure.

The record is built by the runner from the delivered context, never inside
the arm: the gold must not reach an arm. Off by default
(`config.ARM1_T2_DIAGNOSTIC`, env `HARNESS_ARM1_T2_DIAGNOSTIC=1`); it is
meant to run once, for M1.

    python -m harness.scoring.arm1_t2_diagnostic --bundles <dir with bundles/>
analyses already-pushed bundles offline (they hold the same data).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re

TOP_K = 5


def _counts(items: list[dict], gold: list[str]) -> dict:
    text = "\n".join(i["content"] for i in items)
    syms = {s for i in items for s in i["symbols"]}
    bare = [g.rsplit(".", 1)[-1] for g in gold]
    return {
        "mentioned": sum(1 for b in bare if re.search(rf"\b{re.escape(b)}\b", text)),
        "defined": sum(1 for b in bare if re.search(rf"\b(?:def|class)\s+{re.escape(b)}\b", text)),
        "in_symbols": sum(1 for g in gold if g in syms),
    }


def record(task, items: list[dict]) -> dict:
    """`items`: the delivered items as dicts (DeliveredItem fields)."""
    gold = list(task.ground_truth.pipeline_symbols)
    ranked = sorted(items, key=lambda i: i["rank"])
    top = ranked[:TOP_K]
    return {
        "task_id": task.task_id, "gold_symbols": gold,
        "retrieved_top5": [{"chunk_source_id": i["source_id"], "chunk_symbols": i["symbols"],
                            "chunk_text": i["content"]} for i in top],
        "top5": _counts(top, gold), "all_delivered": {"n_items": len(ranked), **_counts(ranked, gold)},
        "gold_delivered_at_rank": {g: [i["rank"] for i in ranked if g in i["symbols"]] for g in gold},
    }


def verdict(records: list[dict]) -> str:
    defined = sum(r["all_delivered"]["defined"] for r in records)
    mapped = sum(r["all_delivered"]["in_symbols"] for r in records)
    top_mention = sum(r["top5"]["mentioned"] for r in records)
    n_gold = sum(len(r["gold_symbols"]) for r in records)
    if defined > mapped:
        return f"MAPPING BUG: {defined} gold definitions delivered but only {mapped} carried in symbols"
    return (f"RETRIEVAL FAILURE (not a mapping bug): every delivered gold definition is in symbols "
            f"({mapped}/{defined}); top-5 mentions {top_mention}/{n_gold} gold names")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundles", required=True, help="directory containing bundles/arm1_*.json")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    from benchmarks.corpora.resolver import resolve
    from harness.tasks.loaders import load_tasks
    tasks = {t.task_id: t for t in load_tasks("fastapi", repo_root=str(resolve("fastapi")))}
    records = []
    for f in sorted(glob.glob(os.path.join(args.bundles, "bundles", "arm1_*.json"))):
        bundle = json.load(open(f))["bundle"]
        task = tasks.get(bundle["task_id"])
        if task is not None and task.task_type == "T2_localization":
            records.append(record(task, bundle["items"]))
    for r in records:
        print(f"{r['task_id']}: gold {len(r['gold_symbols'])} | top5 {r['top5']} | all {r['all_delivered']}")
    print(verdict(records) if records else "no Arm 1 T2 bundles found")
    if args.out:
        with open(args.out, "w") as fh:
            json.dump({"records": records, "verdict": verdict(records) if records else None}, fh, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
