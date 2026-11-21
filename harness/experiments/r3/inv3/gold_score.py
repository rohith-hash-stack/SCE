"""Step 1: score the developer's fix through R3a's own runner.score() (investigation only; no repo changes)."""
import json, os, shutil, subprocess, sys, tempfile, time, glob
sys.path.insert(0, "/home/user/SCE")
from harness.experiments.r3.runner import apply_patch, score, snapshot_tree
from harness.experiments.r3.shared_tools import make_env
B = "/tmp/claude-0/-home-user-SCE/7b6039cf-c7d6-5676-a565-1dea0566e0f3/scratchpad"
clones = dict(s.split("=", 1) for s in open(f"{B}/pilotgate.repos").read().split())
clones.update(dict(s.split("=", 1) for s in open(f"{B}/verify/repos").read().split()))
tid = sys.argv[1]
t = json.load(open(f"/home/user/SCE/reports/r3/r3a/tasks/{tid}.json"))
cache = f"{B}/jsb/cache" if t["language"] == "js" else f"{B}/pyb/cache"
clone = clones[t["repo"]]
scratch = tempfile.mkdtemp(prefix="inv3_", dir=f"{B}/inv3")
work = os.path.join(scratch, "work")
t0 = time.time()
subprocess.run(["git", "-C", clone, "worktree", "add", "-q", "--detach", work, t["parent"]], check=True)
rec = {"task": tid, "repo": t["repo"]}
try:
    apply_patch(work, t.get("start_patch"))
    env = make_env(clone, t["parent"], work, cache); env.setup()
    start_tree = snapshot_tree(work)
    # the developer's fix: every gold (non-test, beyond the start state) file set to the commit's version
    for f in t["gold_files"]:
        r = subprocess.run(["git", "-C", work, "show", f"{t['commit']}:{f}"], capture_output=True)
        p = os.path.join(work, f)
        if r.returncode == 0:
            os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(r.stdout)
        elif os.path.exists(p):
            os.remove(p)
    s = score(t, work, env, start_tree)
    bad = {k: v for k, v in (s.get("tests") or {}).items() if v != "passed"}
    rec.update(resolved=s.get("resolved"), hidden_applied=s.get("hidden_patch_applied"), reason=s.get("reason"),
               n_f2p=sum(map(len, t["fail_to_pass"].values())), f2p_passed=s.get("fail_to_pass_passed"),
               p2p=sum(map(len, t["pass_to_pass"].values())), p2p_broken=s.get("pass_to_pass_broken"),
               file_f1=s.get("file_f1"), function_f1=s.get("function_f1"), not_passed=list(bad.items())[:8],
               n_not_passed=len(bad))
except Exception as exc:
    rec.update(error=f"{type(exc).__name__}: {str(exc)[:600]}")
finally:
    rec["seconds"] = round(time.time() - t0)
    subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", work], capture_output=True)
    shutil.rmtree(scratch, ignore_errors=True)
print(json.dumps(rec))
