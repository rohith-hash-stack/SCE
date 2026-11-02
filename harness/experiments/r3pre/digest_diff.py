"""A2 (before R3): the digest of each changed function before and after the developer's commit, on R1 forward tasks.

    python -m harness.experiments.r3pre.digest_diff FULL_CLONES_DIR OUT.json [N=20]

For N forward tasks (drawn with seed 5501 from repositories with a full clone), index the parent and the commit,
and diff the digest of the anchor and of every caller the developer changed. A fact is (kind, text, conditions);
line numbers are ignored. No model calls.
"""
from __future__ import annotations

import json
import random
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

CONTRACT = ("raises", "returns", "catches", "state", "dynamic", "entry", "decorator")


def facts(digest) -> Counter:
    return Counter((f.kind, " ".join(f.text.split()), tuple(f.when)) for f in digest.facts)


def at(clone: str, rev: str, qnames: list[str]) -> dict:
    from prism.cli import build_pipeline
    from prism.summaries.digest import DigestBuilder
    wt = tempfile.mkdtemp(prefix="dd_")
    subprocess.run(["git", "-C", clone, "worktree", "add", "-q", "--detach", wt, rev], check=True, capture_output=True)
    try:
        b = build_pipeline(wt, use_cache=False)[0]
        db = DigestBuilder(b)
        out = {}
        for q in qnames:
            if q in b._def_nodes:
                d = db.digest(q)
                out[q] = {"facts": facts(d), "render": d.render(header=False)}
        return out
    finally:
        subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", wt], capture_output=True)


def delta(before: Counter, after: Counter) -> dict:
    added, removed = after - before, before - after
    kinds = Counter(k for (k, _t, _w) in list(added.elements()) + list(removed.elements()))
    return {"added": [list(x) for x in added.elements()], "removed": [list(x) for x in removed.elements()],
            "kinds_changed": dict(kinds), "conditions_changed": sum(1 for x in added.elements() if x[2]) +
            sum(1 for x in removed.elements() if x[2]), "contract": sum(kinds[k] for k in CONTRACT)}


def main(argv=None) -> int:
    root, out = (argv or sys.argv[1:])[:2]
    n = int((argv or sys.argv[1:])[2]) if len(argv or sys.argv[1:]) > 2 else 20
    repos = sorted(p.name for p in Path(root).iterdir() if (p / ".git").exists())
    pool = [t for r in repos for t in json.loads(Path(f"harness/experiments/suite/repos/{r}/forward.json").read_text())["tasks"]
            if Path(f"harness/experiments/suite/repos/{r}/forward.json").exists()]
    random.Random(5501).shuffle(pool)
    path = Path(out)
    done = json.loads(path.read_text()) if path.exists() else {}
    for t in pool:
        if len(done) >= n:
            break
        if t["id"] in done:
            continue
        clone = str(Path(root) / t["repo"])
        names = [t["anchor"]] + [c["qname"] for c in t["changed_callers"]]
        try:
            old, new = at(clone, t["parent"], names), at(clone, t["commit"], names)
        except subprocess.CalledProcessError:
            continue
        if t["anchor"] not in old or t["anchor"] not in new:
            continue
        rec = {"repo": t["repo"], "tier": t["tier"], "anchor": t["anchor"], "anchor_old": t["anchor_old"],
               "anchor_new": t["anchor_new"], "anchor_delta": delta(old[t["anchor"]]["facts"], new[t["anchor"]]["facts"]),
               "anchor_before": old[t["anchor"]]["render"], "anchor_after": new[t["anchor"]]["render"], "callers": {}}
        for c in t["changed_callers"]:
            q = c["qname"]
            if q in old and q in new:
                rec["callers"][q] = {"delta": delta(old[q]["facts"], new[q]["facts"]), "old": c["old"], "new": c["new"]}
        done[t["id"]] = rec
        path.write_text(json.dumps(done, indent=1, default=list) + "\n")
        print(f"  {t['id']}: anchor kinds changed {rec['anchor_delta']['kinds_changed']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
