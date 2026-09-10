# Offline `cl100k_base` fixture cache (Issue A2)

`prism.slicer.tokenizer` prefers `tiktoken`'s real `cl100k_base` BPE
encoding and only degrades to the fail-closed regex fallback
(`src/prism/slicer/tokenizer.py`, Issue A1) when that encoding's
merge-rank table can't be loaded. In a network-restricted CI runner or
sandbox, `tiktoken.get_encoding("cl100k_base")` tries to fetch that table
from `https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken`
on first use and fails - which means `tests/test_bpe_vs_fallback.py`'s
real-vs-fallback comparison test has nothing to compare against and is
skipped rather than silently passing on a fabricated number.

This directory exists so a maintainer *with* network access can populate
a one-time, offline-reusable cache that removes that dependency for
everyone else (subsequent CI runs, other sandboxes, this repository's own
verification runs).

## How `tiktoken` finds its cache

`tiktoken.load.read_file_cached` (see the installed `tiktoken` package)
keys its on-disk cache by `sha1(blob_url).hexdigest()`, checked under
`$TIKTOKEN_CACHE_DIR` *before* ever attempting a network fetch:

```python
import hashlib
blobpath = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
cache_key = hashlib.sha1(blobpath.encode()).hexdigest()
# -> 9b5ad71b2ce5302211f9c61530b329a4922fc6a4
```

## Populating the cache (one-time, from a machine with network access)

```bash
mkdir -p tests/fixtures/tokens/cl100k_cache
TIKTOKEN_CACHE_DIR=tests/fixtures/tokens/cl100k_cache python -c \
  "import tiktoken; tiktoken.get_encoding('cl100k_base')"
```

That downloads the real vocabulary once (verified against tiktoken's own
`expected_hash`) into `tests/fixtures/tokens/cl100k_cache/9b5ad71b2ce...`.
Commit that directory, or point `TIKTOKEN_CACHE_DIR` at it in CI/sandbox
setup - either way, every subsequent `tiktoken.get_encoding("cl100k_base")`
call anywhere that reads the same env var loads instantly from disk with
zero network calls, satisfying Prism's own "100% offline" contract for
the tokenizer path too, not just the extraction pipeline.

## Why this repository doesn't ship a populated cache itself

The sandbox this branch's own work was verified in has
`openaipublic.blob.core.windows.net` explicitly blocked at the outbound
proxy level (a 403 on the CONNECT tunnel, not a timeout) - confirmed
directly, and with no alternate offline source available either (no PyPI
package bundles this vocab; `pip download` for a `tiktoken-cache`-shaped
package finds nothing). So the vocab file itself could not be fetched
from *this* environment to commit here. `tests/test_bpe_vs_fallback.py`
is written to use a real encoding the moment one is available - either
via a populated `TIKTOKEN_CACHE_DIR` as above, or genuine network access
- and to skip (with an explicit, visible reason) rather than fabricate a
result when neither is available, exactly as it does in this sandbox
today.
