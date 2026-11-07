import json, os, sys, collections
from harness.experiments.r3.mine import window_commits
B = sys.argv[1]; D = f"{B}/r3x/mine"; out = f"{B}/r3x/screen"; os.makedirs(out, exist_ok=True)
repos = ["mypy", "sqlalchemy", "rollup", "prettier", "pydantic", "pylint", "eslint", "svelte"]
res = {}
for r in repos:
    kept, counts = window_commits(f"{B}/r3x/repos/{r}", "2024-12-01", "2026-12-31"); kept = set(kept)
    cands, y = [], collections.Counter()
    for q in range(1, 9):
        cands += json.load(open(f"{D}/{r}/q{q}/candidates_B.json"))
        for k, v in json.load(open(f"{D}/{r}/q{q}/yield.json"))[f"{r}:B"].items():
            if isinstance(v, int): y[k] += v
    before = len(cands)
    seen, uniq = set(), []
    for c in cands:
        if c["commit"] in kept and c["commit"] not in seen:
            seen.add(c["commit"]); uniq.append(c)
    buckets = collections.Counter(c["caller_bucket"] for c in uniq)
    deep = sum(1 for c in uniq if len(c["changed_callers"]) >= 2)
    y = dict(y); y.update(counts); y["commits_kept"] = len(kept)
    y["cross_chunk_repeats_dropped"] = before - len(uniq); y["candidates"] = len(uniq)
    res[r] = {"yield": y, "callers": dict(sorted(buckets.items())), "deep": deep, "mined": len(uniq),
              "deep_share": round(deep / len(uniq), 3) if uniq else None,
              "screens_at_40pct": bool(uniq) and deep / len(uniq) >= 0.40}
    json.dump(uniq, open(f"{out}/candidates_B_{r}.json", "w"), indent=1)
json.dump(res, open(f"{out}/screen.json", "w"), indent=1)
for r, v in res.items():
    print(f"{r:11} kept={v['yield']['commits_kept']:5} mined={v['mined']:3} deep={v['deep']:3} share={v['deep_share']} callers={v['callers']} pass={v['screens_at_40pct']}")
