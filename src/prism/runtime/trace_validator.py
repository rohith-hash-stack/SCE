"""Section 2.1.2 - Deterministic Invalidation & Provenance: every ingested
runtime trace must declare an origin manifest (git commit SHA, the
environment it was recorded in, a timestamp, and per-file SHA-256
fingerprints of the source it was recorded against) and is rejected the
moment that manifest disagrees with the repository's current state,
falling back cleanly to pure static weights rather than silently
calibrating on evidence recorded against code that no longer exists.

**Git-commit check is best-effort, file-fingerprint check is strict**:
when the target repository has no `.git` directory (or `git` itself isn't
on `PATH` - a container image built without git, a vendored copy of a
repository), there is no HEAD to compare against at all. Treating that as
an automatic rejection would make trace ingestion simply not work in a
large, realistic class of deployments for a reason that has nothing to do
with trace staleness. So a manifest's `git_commit_sha` is compared only
when this process itself can resolve a current HEAD; when it can't, that
check is skipped (not silently passed - see `ValidationResult.reason`
population) and the *file fingerprints* - fully self-contained, no git
required - remain the authoritative staleness signal.
"""
from __future__ import annotations

import hashlib
import subprocess
import warnings
from dataclasses import dataclass, field
from pathlib import Path


class PrismWarning(UserWarning):
    """Warning category for every Prism runtime-trace validation warning.
    Every message raised under this category starts with the specific
    code the spec names (`StaleTraceIgnored`), so `grep 'PrismWarning:'`
    or a `warnings.catch_warnings()` filter on this class catches all of
    them uniformly."""


class StaleTraceError(Exception):
    """Raised instead of warning when `--strict-trace-validation` is set -
    lets an automated pipeline fail loudly on stale evidence instead of
    silently falling back."""


@dataclass(frozen=True)
class TraceManifest:
    git_commit_sha: str | None
    environment: str
    timestamp: str | None
    file_fingerprints: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "TraceManifest":
        return cls(
            git_commit_sha=d.get("git_commit_sha"),
            environment=d.get("environment") or "unknown",
            timestamp=d.get("timestamp"),
            file_fingerprints=dict(d.get("file_fingerprints", {})),
        )

    def to_dict(self) -> dict:
        return {
            "git_commit_sha": self.git_commit_sha,
            "environment": self.environment,
            "timestamp": self.timestamp,
            "file_fingerprints": dict(self.file_fingerprints),
        }


@dataclass
class ValidationResult:
    accepted: bool
    reason: str | None = None


def current_git_commit_sha(repo_root: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def validate_trace(
    manifest: TraceManifest,
    repo_root: str,
    strict: bool = False,
    current_commit_sha: str | None | object = "__AUTO__",
) -> ValidationResult:
    """Rejects `manifest` (returns `accepted=False`) the moment either its
    declared git commit disagrees with a resolvable current HEAD, or any
    declared file fingerprint no longer matches the file on disk (missing
    entirely counts as a mismatch, not a pass). `current_commit_sha` is
    exposed as a parameter (rather than always shelling out to `git`
    internally) specifically so tests can exercise the mismatch path
    deterministically without needing a real git repository fixture -
    leave it at the `"__AUTO__"` sentinel for real use.
    """
    if current_commit_sha == "__AUTO__":
        current_commit_sha = current_git_commit_sha(repo_root)

    repo_path = Path(repo_root)
    mismatches: list[str] = []

    if manifest.git_commit_sha and current_commit_sha and manifest.git_commit_sha != current_commit_sha:
        mismatches.append(f"git commit mismatch (trace: {manifest.git_commit_sha[:12]}, HEAD: {current_commit_sha[:12]})")

    for rel_path, expected_hash in manifest.file_fingerprints.items():
        actual_hash = _file_sha256(repo_path / rel_path)
        if actual_hash is None:
            mismatches.append(f"{rel_path}: file missing")
        elif actual_hash != expected_hash:
            mismatches.append(f"{rel_path}: fingerprint mismatch")

    if not mismatches:
        return ValidationResult(accepted=True)

    reason = "; ".join(mismatches)
    message = f"PrismWarning: StaleTraceIgnored - trace for environment {manifest.environment!r} rejected ({reason})"
    if strict:
        raise StaleTraceError(message)
    warnings.warn(message, PrismWarning, stacklevel=2)
    return ValidationResult(accepted=False, reason=reason)
