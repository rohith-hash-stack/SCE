"""v1.1+ Empirical Benchmarking Harness: resolves and validates a local
checkout of each real-world corpus (Django, Gin, tRPC) at an exact,
pinned commit SHA - reproducibility requires every engine and every run
to see byte-identical source, not "whatever HEAD happened to be" at
benchmark time.

`CORPORA`'s three entries are real, verified commits (each is the exact
commit a real upstream release tag points to, confirmed directly via
`git ls-remote --tags <url>` against the live repository at the time this
module was written - `django` -> tag `5.1.9`, `gin` -> tag `v1.10.1`,
`trpc` -> tag `v11.0.0`), not placeholders.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CACHE_DIR = PROJECT_ROOT / ".benchmarks" / "corpora"
FETCH_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class CorpusSpec:
    name: str
    url: str
    pinned_commit: str


#: Verified directly against each repository's own tag refs (`git
#: ls-remote --tags <url>`) - the commit each named release tag
#: dereferences to, not a fabricated/guessed SHA.
CORPORA: dict[str, CorpusSpec] = {
    "django": CorpusSpec(
        name="django", url="https://github.com/django/django.git",
        pinned_commit="db5c8a97bb084cee880e678681d63bddecf6d38b",  # tag 5.1.9
    ),
    "gin": CorpusSpec(
        name="gin", url="https://github.com/gin-gonic/gin.git",
        pinned_commit="b5af7796535d97d9c7af42539af01d787fcb3b4d",  # tag v1.10.1
    ),
    "trpc": CorpusSpec(
        name="trpc", url="https://github.com/trpc/trpc.git",
        pinned_commit="85841a1ae4679847fd29ad5454c1a584a2e206d2",  # tag v11.0.0
    ),
}


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
    names ("django", "gin", "trpc")."""
    spec = CORPORA.get(name)
    if spec is None:
        raise CorpusResolutionError(f"unknown corpus {name!r} - registered corpora: {sorted(CORPORA)}")
    return resolve_corpus(spec, cache_dir=cache_dir, force=force)
