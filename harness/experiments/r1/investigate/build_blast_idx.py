"""Every production function of each blast repository at its task commit (for classifying named functions)."""
import json, subprocess, tempfile, sys
sys.path[:0] = ["/home/user/SCE", "/home/user/SCE/src"]
from load import tasks
from prism.cli import build_pipeline
T = tasks("blast"); out = {}
for repo in sorted({t["repo"] for t in T.values()}):
    commit = next(t["commit"] for t in T.values() if t["repo"] == repo)
    clone = f"/tmp/claude-0/-home-user-SCE/7b6039cf-c7d6-5676-a565-1dea0566e0f3/scratchpad/repos/{repo}"
    wt = tempfile.mkdtemp(prefix="bi_")
    subprocess.run(["git", "-C", clone, "worktree", "add", "-q", "--detach", wt, commit], check=True)
    try:
        b = build_pipeline(wt, use_cache=False)[0]
        out[repo] = sorted(q for q in b._def_nodes if (s := b.symbol_table.get(q)) is not None and s.kind in ("function", "method", "class"))
        print(repo, len(out[repo]), flush=True)
    finally:
        subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", wt])
json.dump(out, open("blast_functions.json", "w"))
