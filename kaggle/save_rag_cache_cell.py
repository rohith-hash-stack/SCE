# ============================================================
# One-off Kaggle cell: save the hybrid-RAG embedding cache
#
# Run this in the SAME session, right after run_validation_cell.py stops
# (it stops itself at MAX_RUNTIME_HOURS, leaving ~40 min of session time).
# The first arm1_hybrid_rag cell on a corpus embeds the whole corpus with
# jina-code on the CPU - about 3 h for Django - and caches the vectors in
# SCE/.benchmarks/cache/hybrid_rag/*.npy, which a new session does not have.
# This pushes those files (split into <50 MB parts) to CACHE_BRANCH, an
# orphan branch holding nothing else; run_validation_cell.py restores them
# at startup, so the next session skips the rebuild.
#
# The vectors are a pure cache: the file name carries a digest of the
# chunk texts and the encoder, so a restored file is used only when it
# matches exactly what would be computed.
# ============================================================
import glob
import os
import shutil
import subprocess

SCE_DIR = "/kaggle/working/SCE"
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
CACHE_BRANCH = "eval/two-stage-rag-cache"
PART_MB = 45

token = os.environ.get("GITHUB_TOKEN")
if not token:
    from kaggle_secrets import UserSecretsClient
    token = UserSecretsClient().get_secret("GITHUB_TOKEN")
files = sorted(glob.glob(f"{SCE_DIR}/.benchmarks/cache/hybrid_rag/*.npy"))
if not files:
    raise SystemExit("no cached vectors under SCE/.benchmarks/cache/hybrid_rag - nothing to save")

stage = "/kaggle/working/rag_cache_push"
shutil.rmtree(stage, ignore_errors=True)
os.makedirs(stage)
for f in files:
    name = os.path.basename(f)
    with open(f, "rb") as src:
        i = 0
        while chunk := src.read(PART_MB * 2**20):
            with open(f"{stage}/{name}.part{i:03d}", "wb") as out:
                out.write(chunk)
            i += 1
    print(f"{name}: {os.path.getsize(f) / 2**20:.0f} MB in {i} part(s)", flush=True)

env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
url = REPO_URL.replace("https://", f"https://x-access-token:{token}@")
for cmd in (["git", "init", "-q", "-b", "cache"], ["git", "add", "."],
            ["git", "-c", "user.name=Kaggle", "-c", "user.email=kaggle@local", "commit", "-q", "-m",
             "two-stage validation: hybrid-RAG embedding cache (not results)"],
            ["git", "push", "-q", "--force", url, f"HEAD:refs/heads/{CACHE_BRANCH}"]):
    r = subprocess.run(cmd, cwd=stage, env=env, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"{cmd[1]} failed: {(r.stderr or r.stdout).replace(token, '***')}")
print(f"[ok] pushed {len(files)} cache file(s) to {CACHE_BRANCH}", flush=True)
