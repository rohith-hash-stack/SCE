"""The agent's run_tests at the start state (investigation only)."""
import json, os, shutil, subprocess, sys, tempfile, time
sys.path.insert(0, "/home/user/SCE")
from harness.experiments.r3.runner import apply_patch, TOKENIZER
from harness.experiments.r3.shared_tools import SharedTools, make_env
B = "/tmp/claude-0/-home-user-SCE/7b6039cf-c7d6-5676-a565-1dea0566e0f3/scratchpad"
clones = dict(s.split("=", 1) for s in open(f"{B}/pilotgate.repos").read().split())
clones.update(dict(s.split("=", 1) for s in open(f"{B}/verify/repos").read().split()))
tid = sys.argv[1]; mode = sys.argv[2] if len(sys.argv) > 2 else "start"
t = json.load(open(f"/home/user/SCE/reports/r3/r3a/tasks/{tid}.json"))
cache = f"{B}/jsb/cache" if t["language"] == "js" else f"{B}/pyb/cache"
clone = clones[t["repo"]]
scratch = tempfile.mkdtemp(prefix="inv3s_", dir=f"{B}/inv3"); work = os.path.join(scratch, "work")
subprocess.run(["git", "-C", clone, "worktree", "add", "-q", "--detach", work, t["parent"]], check=True)
try:
    apply_patch(work, t.get("start_patch"))
    env = make_env(clone, t["parent"], work, cache); env.setup()
    tok = TOKENIZER if os.path.exists(TOKENIZER) else None
    st = SharedTools(work, env, t.get("visible_tests", []), tok)
    print(f"##### {tid} {t['repo']} visible={t['visible_tests']} tokenizer_cap={'yes' if tok else 'no'}")
    print(st.call("run_tests"))
    if mode == "hidden":   # the same tool after the hidden tests are applied: what f2p failures look like
        apply_patch(work, t["hidden_patch"])
        print("----- with the hidden tests applied (never shown to agents) -----")
        print(st.call("run_tests", files=sorted(t["fail_to_pass"])))
finally:
    subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", work], capture_output=True)
    shutil.rmtree(scratch, ignore_errors=True)
