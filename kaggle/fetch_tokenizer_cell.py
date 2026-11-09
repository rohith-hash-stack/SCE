# ============================================================
# Fetch tiktoken's cl100k_base encoding and commit it to PRISM (tokenizer option (b), 2026-11-09).
#
# Downloads cl100k_base.tiktoken, checks its SHA-256 against tiktoken's own pinned hash, and commits it to
# src/prism/slicer/assets/cl100k_base.tiktoken on BRANCH. PRISM then loads it with no network call, and only if the
# hash matches (src/prism/slicer/tokenizer.py, get_offline_bpe_encoding).
#
# Independent of R3a: no GPU, no model, no R3a file touched. Settings: Internet ON, secret GITHUB_TOKEN. About 1 min.
# ============================================================
import hashlib
import os
import subprocess
import time
import urllib.request

BRANCH = "feature/option3-contract-axis"
REPO_URL = "https://github.com/rohith-hash-stack/SCE.git"
URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"   # tiktoken_ext.openai_public
DEST = "src/prism/slicer/assets/cl100k_base.tiktoken"
WORK = "/kaggle/working/sce_tokenizer"
ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")

token = os.environ.get("GITHUB_TOKEN")
if not token:
    from kaggle_secrets import UserSecretsClient
    token = UserSecretsClient().get_secret("GITHUB_TOKEN")


def git(*args, check=True):
    r = subprocess.run(["git", *args], cwd=WORK, env=ENV, capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(f"git {args[0]} failed: {(r.stderr or r.stdout).replace(token, '***')[-500:]}")
    return r


data = urllib.request.urlopen(URL, timeout=120).read()
digest = hashlib.sha256(data).hexdigest()
if digest != SHA256:
    raise SystemExit(f"checksum mismatch: {digest} != {SHA256}; nothing committed")
print(f"[ok] downloaded {len(data)} bytes, sha256 {digest}")

url = REPO_URL.replace("https://", f"https://x-access-token:{token}@")
subprocess.run(["rm", "-rf", WORK])
r = subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", BRANCH, url, WORK], env=ENV,
                   capture_output=True, text=True)
if r.returncode:
    raise SystemExit("clone failed: " + r.stderr.replace(token, "***")[-500:])
os.makedirs(os.path.dirname(os.path.join(WORK, DEST)), exist_ok=True)
open(os.path.join(WORK, DEST), "wb").write(data)
git("add", DEST)
if git("diff", "--cached", "--quiet", check=False).returncode == 0:
    print("[ok] the asset is already committed and identical")
    raise SystemExit(0)
git("-c", "user.email=kaggle@local", "-c", "user.name=Kaggle", "commit", "-q", "-m",
    f"PRISM: vendor tiktoken's cl100k_base encoding (sha256 {SHA256}), fetched on Kaggle")
for attempt in range(5):
    if git("push", "origin", f"HEAD:{BRANCH}", check=False).returncode == 0:
        print(f"[ok] pushed {DEST} to {BRANCH}")
        print("=== ALL DONE ===")
        break
    git("pull", "-q", "--rebase", "--depth", "50", "origin", BRANCH, check=False)   # an R3a checkpoint landed first
    time.sleep(2 ** (attempt + 1))
else:
    raise SystemExit("push failed after 5 attempts")
