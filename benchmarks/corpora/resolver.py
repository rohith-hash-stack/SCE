"""v1.1+ Empirical Benchmarking Harness: resolves and validates a local
checkout of each real-world corpus (Django, Gin, tRPC, Express) at an
exact, pinned commit SHA - reproducibility requires every engine and
every run to see byte-identical source, not "whatever HEAD happened to
be" at benchmark time.

Pins live in `pinned_commits.json` (sibling file, not inline Python
constants), each entry real and verified directly against the live
repository's own tag refs (`git ls-remote --tags <url>`) at the time this
module was written - the commit each named release tag dereferences to,
not a fabricated/guessed SHA:

    django  -> tag 4.2.30    (the 4.2 LTS line)
    gin     -> tag v1.9.1
    trpc    -> tag v10.45.4
    express -> tag 4.21.0
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CACHE_DIR = PROJECT_ROOT / ".benchmarks" / "corpora"
FETCH_TIMEOUT_SECONDS = 300
PINNED_COMMITS_PATH = Path(__file__).resolve().parent / "pinned_commits.json"


@dataclass(frozen=True)
class CorpusSpec:
    name: str
    url: str
    pinned_commit: str
    #: A monorepo's own real indexing root, relative to the cloned repo's
    #: top-level directory (e.g. tRPC's `"packages/server/src"` -
    #: `docs/architecture_boundaries.md` Category 10's own real-world
    #: motivation: `PrismEngine.from_repo`/`build_pipeline` given the
    #: bare monorepo root would index every workspace package at once,
    #: producing qualified names prefixed by each package's own full
    #: relative path (`packages.server.src.core.router....` instead of
    #: the real, ground-truth-authored `core.router....`) - the wrong
    #: indexing root for a corpus whose ground truth was authored
    #: against one specific package. `None` (the default) is a complete
    #: no-op - a single-package repo (Django/Express/FastAPI/Gin) is
    #: indexed at its own real clone root exactly as before this field
    #: existed.
    subdir: str | None = None


def _load_corpora(path: Path = PINNED_COMMITS_PATH) -> dict[str, CorpusSpec]:
    data = json.loads(path.read_text())
    return {
        name: CorpusSpec(name=name, url=entry["url"], pinned_commit=entry["pinned_commit"], subdir=entry.get("subdir"))
        for name, entry in data.items()
    }


#: Loaded once at import time from `pinned_commits.json` - see that
#: file's own entries (each carries its own `tag` field documenting
#: which release the `pinned_commit` SHA corresponds to) for the
#: authoritative version-to-commit mapping.
CORPORA: dict[str, CorpusSpec] = _load_corpora()


class CorpusResolutionError(Exception):
    """Raised for any failure resolving a corpus to its pinned commit -
    `git` missing, network/clone failure, or (fatal, never silently
    tolerated) a checked-out HEAD that doesn't match the pinned SHA."""


def _run_git(args: list[str], cwd: Path | None = None, timeout: float = FETCH_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise CorpusResolutionError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise CorpusResolutionError(f"`git {' '.join(args)}` timed out after {timeout}s") from exc


def _current_head_sha(repo_dir: Path) -> str | None:
    result = _run_git(["rev-parse", "HEAD"], cwd=repo_dir)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def resolve_corpus(spec: CorpusSpec, cache_dir: Path = DEFAULT_CACHE_DIR, force: bool = False) -> Path:
    """Ensures a local checkout of `spec.url` at exactly `spec.
    pinned_commit` exists under `cache_dir/<spec.name>`, returning its
    path. Reuses an already-correct checkout; a checkout present but at
    the *wrong* commit is never silently used - it's re-checked-out (or,
    failing that, re-cloned from scratch) rather than trusted as-is.

    Prefers `git fetch --depth 1 origin <sha>` (a shallow fetch of
    exactly the pinned commit, no history) - supported by GitHub and
    most modern git servers (`uploadpack.allowReachableSHA1InWant`) - and
    falls back to a full fetch only if the server rejects fetch-by-SHA.
    """
    dest = cache_dir / spec.name

    if dest.exists():
        if force:
            shutil.rmtree(dest)
        else:
            if _current_head_sha(dest) == spec.pinned_commit:
                return dest
            checkout = _run_git(["checkout", spec.pinned_commit], cwd=dest)
            if checkout.returncode == 0 and _current_head_sha(dest) == spec.pinned_commit:
                return dest
            shutil.rmtree(dest)

    cache_dir.mkdir(parents=True, exist_ok=True)
    dest.mkdir(parents=True)

    init = _run_git(["init"], cwd=dest)
    if init.returncode != 0:
        shutil.rmtree(dest)
        raise CorpusResolutionError(f"`git init` failed for {spec.name}:\n{init.stderr}")
    _run_git(["remote", "add", "origin", spec.url], cwd=dest)

    fetch = _run_git(["fetch", "--depth", "1", "origin", spec.pinned_commit], cwd=dest)
    if fetch.returncode == 0:
        checkout = _run_git(["checkout", "FETCH_HEAD"], cwd=dest)
    else:
        full_fetch = _run_git(["fetch", "origin"], cwd=dest)
        if full_fetch.returncode != 0:
            shutil.rmtree(dest)
            raise CorpusResolutionError(
                f"could not fetch {spec.name} from {spec.url} "
                f"(shallow fetch-by-SHA and full fetch both failed):\n"
                f"shallow: {fetch.stderr}\nfull: {full_fetch.stderr}"
            )
        checkout = _run_git(["checkout", spec.pinned_commit], cwd=dest)

    if checkout.returncode != 0:
        shutil.rmtree(dest)
        raise CorpusResolutionError(f"could not check out {spec.pinned_commit} for {spec.name}:\n{checkout.stderr}")

    resolved = _current_head_sha(dest)
    if resolved != spec.pinned_commit:
        shutil.rmtree(dest)
        raise CorpusResolutionError(
            f"{spec.name}: checked-out HEAD {resolved!r} does not match pinned commit {spec.pinned_commit!r} - "
            "refusing to hand back a mismatched checkout"
        )
    return dest


def resolve(name: str, cache_dir: Path = DEFAULT_CACHE_DIR, force: bool = False) -> Path:
    """`resolve_corpus` keyed by one of `CORPORA`'s own registered
    names ("django", "gin", "trpc", "express"), joined with the spec's
    own `subdir` when it has one (a monorepo indexed at one real
    package's own root, never the bare clone root - see `CorpusSpec.
    subdir`'s own docstring). Raises `CorpusResolutionError` - never
    silently falls back to the bare clone root - if a declared `subdir`
    doesn't actually exist in the real checkout, since every ground-truth
    task authored against it would otherwise index against the wrong
    root without any visible failure at all.
    """
    spec = CORPORA.get(name)
    if spec is None:
        raise CorpusResolutionError(f"unknown corpus {name!r} - registered corpora: {sorted(CORPORA)}")
    dest = resolve_corpus(spec, cache_dir=cache_dir, force=force)
    if spec.subdir is None:
        return dest
    subdir_path = dest / spec.subdir
    if not subdir_path.is_dir():
        raise CorpusResolutionError(
            f"{name}: declared subdir {spec.subdir!r} does not exist in the real checkout at {dest}"
        )
    return subdir_path
