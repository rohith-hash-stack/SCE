# Offline BPE asset (Item 1)

`src/prism/slicer/tokenizer.py`'s `get_offline_bpe_encoding()` checks
this directory for `cl100k_base.tiktoken` *before* ever attempting
`tiktoken.get_encoding`'s network fetch. It is empty in version control
today - see the parent module's own `OFFLINE_BPE_ASSET_PATH` comment for
why (no network path exists in this project's development/CI sandbox to
fetch and verify the real ~1.6MB vocabulary file, and committing a
fabricated placeholder would silently corrupt every token count, which
is strictly worse than the honest regex fallback this module already
has without it).

To populate it for real, from any machine with network access to
`openaipublic.blob.core.windows.net`:

```bash
python -c "
import shutil, tiktoken
from tiktoken.load import load_tiktoken_bpe
enc = tiktoken.get_encoding('cl100k_base')  # downloads + caches once
# tiktoken's own on-disk cache key is sha1(blob_url) - see
# tests/fixtures/tokens/README.md for the exact derivation - copy that
# cached file here under its real name:
"
```
or more directly, since `tiktoken`'s loader accepts a local path:
```bash
curl -o src/prism/slicer/assets/cl100k_base.tiktoken \
  https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken
```
(verify the download's SHA-256 against the `expected_hash` in
`tiktoken_ext.openai_public.cl100k_base()`'s own source before trusting
it in this repository).

`tests/fixtures/tokens/README.md` documents the equivalent
`TIKTOKEN_CACHE_DIR` mechanism, which achieves the same "zero network
calls at runtime" property without committing a binary asset into
version control - prefer that route for CI; this one exists for a fully
self-contained checkout that needs no environment setup at all.
