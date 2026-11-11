"""Sanity samples: 10 delete + 10 feature from the bounded+clean gated tasks, 1-2 per repository, seed 42.
Round-robin over repositories in a seeded order: one task per repository first, then a second, until 10."""
import collections, json, random, re, sys
# production change only in CI/release tooling (.github/, scripts/): not a product feature (checked by hand, 2026-11-10)
TOOLING = {"90f087ac", "a0022cd0", "f2b965bf"}
S = "/tmp/claude-0/-home-user-SCE/7b6039cf-c7d6-5676-a565-1dea0566e0f3/scratchpad/scope"
N, PER_REPO, SEED = 10, 2, 42
rows = [json.loads(l) for l in open(f"{S}/family_scope.jsonl")]
latest = {}
for r in rows:
    latest[(r["family"], r["commit"])] = r
pool, out, layer1 = {}, {}, {}
for fam in ("delete", "feature"):
    fr = [r for (f, _), r in latest.items() if f == fam]
    c = collections.Counter(r.get("layer1") or "error" for r in fr)
    pool[fam] = {"gated_checked": len(fr), **c}
    clean = [r for r in fr if r.get("layer1") == "bounded+clean" and r["commit"][:8] not in TOOLING
             and not re.match(r"(\.github|scripts)/", r.get("anchor") or "")]
    pool[fam]["excluded_tooling_only"] = sum(r["commit"][:8] in TOOLING for r in fr)
    by = collections.defaultdict(list)
    for r in sorted(clean, key=lambda r: r["commit"]):
        by[r["repo"]].append(r)
    rng = random.Random(SEED)
    repos = sorted(by); rng.shuffle(repos)
    for v in by.values():
        rng.shuffle(v)
    pick = []
    for k in range(PER_REPO):
        for repo in repos:
            if len(pick) < N and len(by[repo]) > k:
                pick.append(by[repo][k])
    out[fam] = [r["commit"] for r in pick]
    order = []                                          # the full seeded order (backups after the first N)
    for k in range(PER_REPO):
        for repo in repos:
            if len(by[repo]) > k:
                order.append(by[repo][k])
    out[fam + "_order"] = [r["commit"] for r in order]
    for r in order:
        layer1[r["commit"]] = r["layer1"]
    for r in pick:
        layer1[r["commit"]] = r["layer1"]
    print(fam, dict(c), "->", [(r["repo"], r["commit"][:8]) for r in pick])
json.dump({**out, "layer1": layer1}, open(f"{S}/samples.json", "w"), indent=1)
json.dump(pool, open(f"{S}/layer1_pool.json", "w"), indent=1)
