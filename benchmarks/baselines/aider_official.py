"""`baseline_aider_official`: Aider's own repository map, unmodified.

Runs `aider.repomap.RepoMap` from the installed `aider-chat` package (via
`_aider_worker.py`, in the interpreter named by `$AIDER_PYTHON`) exactly
as Aider does for a session with no files in chat:

- `other_files`: every git-tracked file under the corpus root, Aider's
  own default file set.
- `mentioned_idents` / `mentioned_fnames`: from the task prompt, using
  Aider's own `Coder.get_ident_mentions`, `get_file_mentions` and
  `get_ident_filename_matches`.
- `map_tokens`: 4,000 by default (Aider's own maximum; its default is
  1,024). Counted by Aider with the main model's tokenizer.
- `RepoMap.max_context_window` is left unset, as in Aider's constructor
  default, so the no-files multiplier (`map_mul_no_files`) does not apply.

The model sees Aider's rendered map text verbatim. For scoring only, the
definition tags Aider placed in the final map are mapped onto the PRISM
symbol table (same file, same definition line), so pipeline coverage and
cleanliness are computed on the same symbol ids as every other arm.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from prism.graph.concrete_builder import ConcreteGraphBuilder

WORKER = Path(__file__).with_name("_aider_worker.py")
DEFAULT_MAP_TOKENS = 4000
DEFAULT_AIDER_PYTHON = "/home/user/venvs/aider/bin/python"
TOKENIZER_MODEL = "gpt-4o-mini"
#: Aider's map depends on Python set iteration order (identifier and file
#: sets feed PageRank and its tie-breaks), which string-hash randomization
#: changes per process: the same query gives a different map in different
#: runs. Pinning the hash seed makes a cell reproducible; it is recorded.
DEFAULT_HASH_SEED = "0"


@dataclass
class AiderMap:
    text: str
    aider_tokens: int
    tags: list[tuple[str, int, str]]
    symbols: list[str]
    unmatched_tags: list[tuple[str, int, str]]
    mentioned_idents: list[str]
    mentioned_fnames: list[str]
    aider_version: str
    n_files: int
    extra: dict = field(default_factory=dict)


def aider_python() -> str:
    """`$AIDER_PYTHON`, else this interpreter if it can import `aider`,
    else the conventional isolated venv."""
    env = os.environ.get("AIDER_PYTHON")
    if env:
        return env
    try:
        import aider.repomap  # noqa: F401
        return sys.executable
    except ImportError:
        return DEFAULT_AIDER_PYTHON


def tracked_files(root: str) -> list[str]:
    """Git-tracked files under `root` (absolute), as Aider enumerates them;
    falls back to a filesystem walk when `root` isn't in a git work tree."""
    try:
        out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout
        files = [os.path.join(root, f) for f in out.splitlines() if f]
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = [os.path.join(d, f) for d, _, fs in os.walk(root) if "/.git" not in d for f in fs]
    return sorted(f for f in files if os.path.isfile(f))


def map_tags_to_symbols(
    builder: ConcreteGraphBuilder, root: str, tags: list[tuple[str, int, str]],
) -> tuple[list[str], list[tuple[str, int, str]]]:
    """Each Aider def tag `(rel_fname, 0-based line, name)` -> the PRISM
    symbol in that file whose bare name matches and whose definition span
    contains the line (innermost if nested). Unmatched tags are returned,
    not guessed."""
    by_file: dict[str, list] = {}
    for qname in builder.symbol_table._symbols:
        info = builder.symbol_table.get(qname)
        if info is not None:
            by_file.setdefault(os.path.realpath(info.file), []).append((qname, info))
    symbols, unmatched = [], []
    for rel_fname, line, name in tags:
        cands = [
            (q, i) for q, i in by_file.get(os.path.realpath(os.path.join(root, rel_fname)), [])
            if q.rsplit(".", 1)[-1] == name and i.line_range[0] - 1 <= line <= i.line_range[1] - 1
        ]
        if not cands:
            unmatched.append((rel_fname, line, name))
            continue
        q, _ = min(cands, key=lambda qi: qi[1].line_range[1] - qi[1].line_range[0])
        if q not in symbols:
            symbols.append(q)
    return symbols, unmatched


def build_aider_map(
    builder: ConcreteGraphBuilder,
    root: str,
    prompt: str,
    map_tokens: int = DEFAULT_MAP_TOKENS,
    python: str | None = None,
    hash_seed: str = DEFAULT_HASH_SEED,
) -> AiderMap:
    python = python or aider_python()
    if not shutil.which(python) and not os.path.exists(python):
        raise RuntimeError(f"no aider-chat interpreter at {python!r}; set $AIDER_PYTHON")
    files = tracked_files(root)
    req = {"root": root, "files": files, "prompt": prompt, "map_tokens": map_tokens, "model": TOKENIZER_MODEL}
    env = dict(os.environ, PYTHONHASHSEED=hash_seed)
    proc = subprocess.run([python, str(WORKER)], input=json.dumps(req), capture_output=True, text=True, timeout=1800, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"aider worker failed: {proc.stderr[-2000:]}")
    out = json.loads(proc.stdout)
    tags = [tuple(t) for t in out["tags"]]
    symbols, unmatched = map_tags_to_symbols(builder, root, tags)
    return AiderMap(
        text=out["map"], aider_tokens=out["aider_tokens"], tags=tags, symbols=symbols, unmatched_tags=unmatched,
        mentioned_idents=out["mentioned_idents"], mentioned_fnames=out["mentioned_fnames"],
        aider_version=out["aider_version"], n_files=len(files), extra={"pythonhashseed": hash_seed},
    )
