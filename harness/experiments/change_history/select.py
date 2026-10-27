"""Select and freeze a small set of change-propagation tasks from `mine.py`'s candidates.

    python -m harness.experiments.change_history.select CANDIDATES.json OUT_DIR [--per-tier 4] [--seed 1301]

A caller counts as changed *because of the anchor* only when its own changed lines mention the anchor's
name (the call itself changed), or the anchor's parameter list changed. Callers changed for other
reasons are moved to `unchanged_callers`. The tier is recomputed after that filter.

From the remaining tasks: one per commit (the anchor with the most changed callers), at most two per
repository per tier, drawn in a seeded order, `--per-tier` per tier. Writes `tasks.json` with
`"status": "frozen"` and the sha256 of its task list.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import random
import re
import sys
from pathlib import Path

from harness.experiments.change_history.mine import tier

TIERS = ("easy", "medium", "hard")


def _changed_lines(old: str, new: str) -> list[str]:
    return [line[1:] for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0)
            if line[:1] in "+-" and not line.startswith(("+++", "---"))]


def _params(code: str) -> str:
    m = re.search(r"\(([^)]*)\)", code)
    return re.sub(r"\s+", "", m.group(1)) if m else ""


def because_of_anchor(task: dict, caller: dict) -> bool:
    leaf = task["anchor"].rsplit(".", 1)[-1].split("#", 1)[0]
    if _params(task["anchor_old"]) != _params(task["anchor_new"]):
        return True
    return any(re.search(rf"\b{re.escape(leaf)}\b", line) for line in _changed_lines(caller["old"], caller["new"]))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("candidates")
    p.add_argument("out")
    p.add_argument("--per-tier", type=int, default=4)
    p.add_argument("--seed", type=int, default=1301)
    args = p.parse_args(argv)
    tasks = json.loads(Path(args.candidates).read_text())
    kept = []
    for t in tasks:
        real = [c for c in t["changed_callers"] if because_of_anchor(t, c)]
        if not real:
            continue
        moved = [c["qname"] for c in t["changed_callers"] if c not in real]
        t = {**t, "changed_callers": real, "unchanged_callers": sorted(set(t["unchanged_callers"]) | set(moved))}
        t["tier"] = tier(t["anchor_file"], real)
        kept.append(t)
    best: dict[str, dict] = {}
    for t in kept:  # one task per commit
        cur = best.get(t["commit"])
        if cur is None or len(t["changed_callers"]) > len(cur["changed_callers"]):
            best[t["commit"]] = t
    pool = sorted(best.values(), key=lambda t: (t["repo"], t["commit"]))
    rng = random.Random(args.seed)
    rng.shuffle(pool)
    chosen, per_repo_tier = [], {}
    for tr in TIERS:
        for t in pool:
            if sum(1 for c in chosen if c["tier"] == tr) >= args.per_tier:
                break
            key = (t["repo"], tr)
            if t["tier"] == tr and per_repo_tier.get(key, 0) < 2:
                chosen.append(t)
                per_repo_tier[key] = per_repo_tier.get(key, 0) + 1
    for i, t in enumerate(chosen, 1):
        t["id"] = f"ch{i:02d}"
    body = json.dumps(chosen, sort_keys=True)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "tasks.json").write_text(json.dumps({
        "version": 1, "status": "frozen", "seed": args.seed, "per_tier": args.per_tier,
        "tasks_sha256": hashlib.sha256(body.encode()).hexdigest(), "tasks": chosen}, indent=1) + "\n")
    counts = {tr: sum(1 for t in chosen if t["tier"] == tr) for tr in TIERS}
    print(f"{len(kept)} tasks pass the filter ({len(best)} commits); chose {counts}")
    for t in chosen:
        print(f"  {t['id']} {t['tier']:6} {t['repo']:7} {t['commit'][:10]} {t['anchor']} -> "
              f"{[c['qname'] for c in t['changed_callers']]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
