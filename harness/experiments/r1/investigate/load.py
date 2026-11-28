"""Shared loaders for the post-hoc R1 investigation (blast and newfn answers, tasks)."""
# Post-hoc R1 investigation scripts (reports/r1/REPORT.md, "Post-hoc investigation"). They read the scratch clones
# under the session scratchpad; outputs are copied to reports/r1/investigation/.
import json, sys
sys.path[:0] = ["/home/user/SCE", "/home/user/SCE/src"]
from harness.experiments.r1.run import tasks_of, repos_of

def answers(kind):
    out = {}
    for repo in repos_of(kind):
        for path, src in ((f"/home/user/SCE/reports/suite/{repo}/{kind}/answers.json", "suite"),
                          (f"/home/user/SCE/reports/r1/{repo}/{kind}/answers.json", "r1")):
            try: a = json.load(open(path))
            except OSError: continue
            for k, v in a.items():
                t, rest = k.rsplit("/", 1)
                if src == "suite":
                    rest += "@42"
                ver, seed = rest.split("@", 1)
                out.setdefault((t, ver, seed), v)
    return out

def items(raw):
    try:
        return [x for x in json.loads(raw).get("affected", []) if isinstance(x, dict)]
    except (ValueError, TypeError, AttributeError):
        return []

def tasks(kind):
    return {t["id"]: t for r in repos_of(kind) for t in tasks_of(kind, r)}
