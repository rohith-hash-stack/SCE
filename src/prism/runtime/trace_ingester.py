"""Ingests dynamic execution telemetry - Prism's own JSON trace-export
format, or a raw OpenTelemetry export - into a per-environment execution
overlay `prism.analysis.hybrid_engine` calibrates the Markov chain with,
after validating each one via `prism.runtime.trace_validator` (Section
2.1.2's staleness guard runs here, unconditionally, before a single
number from a rejected trace can influence anything downstream).

**Prism trace-export format** (the native shape, produced by a future
`prism trace --export-json` or hand-assembled by an external harness):

    {
      "manifest": {
        "git_commit_sha": "a1b2c3d4...",
        "environment": "staging",
        "timestamp": "2026-09-08T12:00:00Z",
        "file_fingerprints": {"src/auth/login.py": "sha256_hash..."}
      },
      "edges": [
        {"caller": "src.auth.login", "callee": "src.db.user.get", "count": 1420, "errors": 0}
      ]
    }

A raw OpenTelemetry export (`resourceSpans -> scopeSpans -> spans`, the
same shape `prism.runtime.reconciler.parse_otel_export` already reads) has
no Prism-specific manifest embedded in it at all - neither a git commit
nor an "environment" label are standard OTLP concepts - so ingesting one
requires the caller to supply that provenance out of band via
`environment_override` (and, optionally, an explicit manifest for the
staleness check; an OTel export ingested with no manifest information at
all skips file/commit validation entirely, since there is nothing to
validate against - documented here rather than silently pretending it was
checked).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from prism.runtime.reconciler import parse_otel_export
from prism.runtime.trace_validator import TraceManifest, ValidationResult, validate_trace

EdgeKey = tuple[str, str]


@dataclass
class EnvExecutionTrace:
    environment: str
    manifest: TraceManifest
    edge_counts: dict[EdgeKey, int] = field(default_factory=dict)
    edge_errors: dict[EdgeKey, int] = field(default_factory=dict)


@dataclass
class AggregatedTrace:
    """Section 2.1.3 - Environment Tagging & Aggregation: an additive
    execution envelope across environments. Counts are kept *separately*
    per environment (never flattened into one undifferentiated total) so
    `hybrid_engine.weighted_execution_count` can apply a distinct
    per-environment weight, and so the serializer can render an honest
    provenance breakdown instead of one opaque merged number."""

    per_env_edge_counts: dict[str, dict[EdgeKey, int]] = field(default_factory=dict)
    per_env_edge_errors: dict[str, dict[EdgeKey, int]] = field(default_factory=dict)
    per_env_run_counts: dict[str, int] = field(default_factory=dict)
    #: Human-readable rejection reasons for every trace `ingest_trace_file`
    #: rejected - visibility into what was *not* incorporated, since a
    #: silently-empty overlay and an overlay that rejected 5 stale traces
    #: look identical otherwise.
    rejected: list[str] = field(default_factory=list)

    def total_hits(self, caller: str, callee: str) -> int:
        return sum(counts.get((caller, callee), 0) for counts in self.per_env_edge_counts.values())

    def total_errors(self, caller: str, callee: str) -> int:
        return sum(counts.get((caller, callee), 0) for counts in self.per_env_edge_errors.values())

    def breakdown(self, caller: str, callee: str) -> dict[str, int]:
        edge = (caller, callee)
        return {env: counts[edge] for env, counts in self.per_env_edge_counts.items() if edge in counts}

    def is_observed(self, caller: str, callee: str) -> bool:
        return self.total_hits(caller, callee) > 0

    def provenance_summary(self) -> str:
        """`"merged (e2e=34 runs, prod=1200 runs)"` - the exact header
        format Section 2.3's serializer spec asks for."""
        if not self.per_env_run_counts:
            return "static-only (no runtime trace ingested)"
        parts = ", ".join(f"{env}={runs} runs" for env, runs in sorted(self.per_env_run_counts.items()))
        return f"merged ({parts})"


def _load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parse_prism_trace_export(data: dict) -> tuple[TraceManifest, list[tuple[str, str, int, int]]]:
    manifest = TraceManifest.from_dict(data.get("manifest", {}))
    edges = [
        (e["caller"], e["callee"], int(e.get("count", 0)), int(e.get("errors", 0)))
        for e in data.get("edges", [])
        if e.get("caller") and e.get("callee")
    ]
    return manifest, edges


def _edge_counts_from_otel(data: dict) -> dict[EdgeKey, int]:
    counts: dict[EdgeKey, int] = {}
    for record in parse_otel_export(data):
        if record.caller and record.callee:
            edge = (record.caller, record.callee)
            counts[edge] = counts.get(edge, 0) + 1
    return counts


def ingest_trace_file(
    path: str | Path,
    repo_root: str,
    strict: bool = False,
    environment_override: str | None = None,
) -> tuple[EnvExecutionTrace | None, ValidationResult]:
    """Reads, validates, and normalizes one trace export. Returns
    `(None, validation_result)` for a rejected trace - the caller (see
    `merge_env_traces`) decides what "fall back to pure static weights"
    means for its own pipeline; this function never raises on a stale
    trace unless `strict=True` (see `trace_validator.validate_trace`)."""
    data = _load_json(path)

    if "manifest" in data and "edges" in data:
        manifest, edges = parse_prism_trace_export(data)
        edge_counts: dict[EdgeKey, int] = {}
        edge_errors: dict[EdgeKey, int] = {}
        for caller, callee, count, errors in edges:
            key = (caller, callee)
            edge_counts[key] = edge_counts.get(key, 0) + count
            edge_errors[key] = edge_errors.get(key, 0) + errors
    else:
        if environment_override is None:
            raise ValueError(
                f"{path}: a raw OpenTelemetry export has no embedded Prism manifest - "
                "pass environment_override to ingest it"
            )
        manifest = TraceManifest(git_commit_sha=None, environment=environment_override, timestamp=None, file_fingerprints={})
        edge_counts = _edge_counts_from_otel(data)
        edge_errors = {}

    if environment_override and environment_override != manifest.environment:
        manifest = TraceManifest(
            git_commit_sha=manifest.git_commit_sha, environment=environment_override,
            timestamp=manifest.timestamp, file_fingerprints=manifest.file_fingerprints,
        )

    validation = validate_trace(manifest, repo_root, strict=strict)
    if not validation.accepted:
        return None, validation

    return (
        EnvExecutionTrace(environment=manifest.environment, manifest=manifest, edge_counts=edge_counts, edge_errors=edge_errors),
        validation,
    )


def merge_env_traces(
    paths: list[str | Path], repo_root: str, strict: bool = False, environment_override: str | None = None,
) -> AggregatedTrace:
    """Ingests and additively merges every trace file in `paths` - the
    top-level entry point CLI/MCP callers use. A rejected trace is simply
    absent from the result (its reason recorded in `.rejected`), never
    raising on its own unless `strict=True`."""
    aggregated = AggregatedTrace()
    for path in paths:
        trace, validation = ingest_trace_file(path, repo_root, strict=strict, environment_override=environment_override)
        if trace is None:
            aggregated.rejected.append(f"{path}: {validation.reason}")
            continue
        env_counts = aggregated.per_env_edge_counts.setdefault(trace.environment, {})
        for edge, count in trace.edge_counts.items():
            env_counts[edge] = env_counts.get(edge, 0) + count
        env_errors = aggregated.per_env_edge_errors.setdefault(trace.environment, {})
        for edge, count in trace.edge_errors.items():
            env_errors[edge] = env_errors.get(edge, 0) + count
        aggregated.per_env_run_counts[trace.environment] = aggregated.per_env_run_counts.get(trace.environment, 0) + 1
    return aggregated
