"""Graph reconciler & edge synthesizer: merges recorded runtime evidence
(a `.prism/traces/run_*.jsonl` file written by `tracer.py`, or an ingested
OpenTelemetry export) into the static Concrete Graph (`G_C`) and tag
matrix (`M`) an already-built `ConcreteGraphBuilder` holds.

Three things happen per event, purely additive - nothing here ever
removes or downgrades something the static two-pass resolver already
found:

  - **Edge promotion**: a traced `caller -> callee` call that the static
    resolver *also* found gets `confidence="CONFIRMED_RUNTIME"` and an
    invocation count on its existing `G_C` edge.
  - **Dynamic edge discovery**: a traced call the static resolver missed
    (an interface resolving to a runtime-injected concrete
    implementation, reflection, dynamic dispatch) becomes a brand new
    `G_C` edge tagged `provenance="RUNTIME_DISCOVERED"`.
  - **External sink attachment**: a runtime-observed DB/HTTP/messaging
    operation with a resolvable calling symbol adds the matching
    `#external_io`/`#db_write`/`#db_read`/`#event_producer`/
    `#event_consumer` tag to that symbol in `M` - the same tag the static
    `TaggingEngine` would have assigned had the call site been
    statically recognizable at all.

Every outcome that *can't* be attached to a real static symbol (an
unresolvable caller, a callee the indexer never saw) is kept, not
dropped - as an `unresolved_events` entry - so `prism status` can report
"N runtime events observed with no static counterpart" honestly rather
than silently discarding evidence that doesn't fit the graph.
"""
from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.runtime.tracer import TraceRecord

# --------------------------------------------------------------------- #
# Trace file I/O
# --------------------------------------------------------------------- #
def traces_dir(repo_root: str) -> Path:
    return Path(repo_root) / ".prism" / "traces"


def new_trace_path(repo_root: str) -> Path:
    """A fresh, timestamped path under `.prism/traces/` - nanosecond
    resolution (not just second-resolution) specifically so two `prism
    trace` invocations issued in quick succession (a scripted loop, back-
    to-back CLI calls in a test) still land in genuinely distinct files;
    at whole-second resolution alone this collided in practice (confirmed
    by two CliRunner-driven `prism trace` calls in the same test producing
    only one trace file, since `Tracer.start` opens its output in append
    mode and silently merged the second run's events into the first's
    file instead of starting a new one).
    """
    ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    nanos = time.time_ns() % 1_000_000_000
    return traces_dir(repo_root) / f"run_{ts}.{nanos:09d}Z.jsonl"


def load_trace_file(path: str | Path) -> list[TraceRecord]:
    """Read a `.prism/traces/run_*.jsonl` file back into `TraceRecord`s -
    the inverse of `TraceRecord.to_json_line`, and also what
    `ingest_otel_file` below normalizes an OTel export down to, so
    `GraphReconciler.reconcile` never needs to know which source produced
    a given event.
    """
    records: list[TraceRecord] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(TraceRecord(**json.loads(line)))
    return records


def write_trace_file(path: str | Path, records: list[TraceRecord]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(record.to_json_line() + "\n")


# --------------------------------------------------------------------- #
# OpenTelemetry export ingestion
# --------------------------------------------------------------------- #
# OTLP's span-kind enum (https://opentelemetry.io/docs/specs/otlp/) -
# handled both as this integer form and as the string form some
# exporters/converters emit instead ("SPAN_KIND_CLIENT", or just "CLIENT").
_KIND_INT_NAMES = {0: "UNSPECIFIED", 1: "INTERNAL", 2: "SERVER", 3: "CLIENT", 4: "PRODUCER", 5: "CONSUMER"}

# The `db.statement` leading verb (or `db.operation`, when the statement
# text itself isn't present) that indicates a write vs. a read - anything
# else defaults to a read, deliberately: overclassifying an unrecognized
# operation as a write risks a false `#db_write` (which the metamodel
# treats as REQUIRES_BEFORE `#auth_guard` - a stronger claim than
# warranted from an unrecognized verb alone).
_DB_WRITE_VERBS = frozenset({"INSERT", "UPDATE", "DELETE", "REPLACE", "MERGE", "UPSERT"})


def _normalize_kind(kind: object) -> str:
    if isinstance(kind, int):
        return _KIND_INT_NAMES.get(kind, "UNSPECIFIED")
    if isinstance(kind, str):
        s = kind.upper()
        return s[len("SPAN_KIND_"):] if s.startswith("SPAN_KIND_") else s
    return "UNSPECIFIED"


def _attr_scalar(value: dict) -> object:
    for key in ("stringValue", "intValue", "boolValue", "doubleValue"):
        if key in value:
            return value[key]
    return None


def _attributes_dict(attribute_list: list[dict] | None) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in attribute_list or []:
        key = item.get("key")
        if key is not None:
            result[key] = _attr_scalar(item.get("value") or {})
    return result


def _symbol_for_attrs(attrs: dict[str, object]) -> str | None:
    """The `code.namespace`/`code.function` OTel semantic-convention
    attributes (https://opentelemetry.io/docs/specs/semconv/attributes-registry/code/)
    are the one standard way a span identifies *which source symbol*
    emitted it - when an instrumentation library populates them, they
    already land in the exact `module.Class.method`-shaped form Prism's own
    static indexer uses (a well-behaved auto-instrumenter derives
    `code.namespace`/`code.function` from the same qualified-name
    machinery this project's own `co_qualname`-based tracer uses). A span
    with neither attribute contributes no call edge and can only ever be
    a sink event attached to its *parent's* symbol instead.
    """
    namespace = attrs.get("code.namespace")
    function = attrs.get("code.function")
    if namespace and function:
        return f"{namespace}.{function}"
    return str(function) if function else None


def _infer_sink(attrs: dict[str, object], kind: object) -> tuple[str | None, str | None]:
    """`(tag, detail)` for a span that itself represents an outbound
    external operation - `(None, None)` for a span that doesn't (most
    spans; an ordinary internal function call has none of these
    attributes at all).
    """
    if "db.statement" in attrs or "db.system" in attrs or "db.operation" in attrs:
        statement = str(attrs.get("db.statement") or "")
        verb = (statement.strip().split(None, 1)[0] if statement.strip() else str(attrs.get("db.operation") or "")).upper()
        tag = "#db_write" if verb in _DB_WRITE_VERBS else "#db_read"
        detail = statement or str(attrs.get("db.operation") or attrs.get("db.system") or "")
        return tag, detail or None

    if "messaging.system" in attrs or "messaging.destination" in attrs:
        kind_name = _normalize_kind(kind)
        destination = str(attrs.get("messaging.destination") or attrs.get("messaging.system") or "")
        if kind_name == "PRODUCER":
            return "#event_producer", destination or None
        if kind_name == "CONSUMER":
            return "#event_consumer", destination or None
        return None, None

    if _normalize_kind(kind) == "CLIENT" and any(k in attrs for k in ("http.method", "http.url", "rpc.method", "rpc.system", "net.peer.name")):
        detail = attrs.get("http.url") or attrs.get("rpc.method") or attrs.get("net.peer.name")
        return "#external_io", str(detail) if detail else None

    return None, None


def parse_otel_export(data: dict) -> list[TraceRecord]:
    """Normalize a standard OTLP JSON export (`resourceSpans ->
    scopeSpans -> spans`) into the same `TraceRecord` shape `tracer.py`
    itself produces, via each span's parent-child nesting (`parentSpanId`)
    plus the `code.namespace`/`code.function` semantic-convention
    attributes. A span can contribute up to two records: a call edge (if
    both it and its parent carry a resolvable symbol) and a sink-tag event
    (if the span's own attributes identify an external DB/HTTP/messaging
    operation - attached to the *parent's* symbol, since that's the
    function whose body actually issued the call the same way the static
    tagger's own `CALL_SINK_RULES` tag the caller, not the callee).
    """
    spans: list[dict] = []
    for resource_span in data.get("resourceSpans", []):
        for scope_span in resource_span.get("scopeSpans", []):
            for span in scope_span.get("spans", []):
                spans.append(
                    {
                        "span_id": span.get("spanId"),
                        "parent_span_id": span.get("parentSpanId") or None,
                        "attributes": _attributes_dict(span.get("attributes")),
                        "kind": span.get("kind"),
                    }
                )

    by_id = {s["span_id"]: s for s in spans if s["span_id"]}

    records: list[TraceRecord] = []
    for span in spans:
        own_symbol = _symbol_for_attrs(span["attributes"])
        parent = by_id.get(span["parent_span_id"]) if span["parent_span_id"] else None
        parent_symbol = _symbol_for_attrs(parent["attributes"]) if parent else None

        if parent_symbol and own_symbol:
            records.append(TraceRecord(caller=parent_symbol, callee=own_symbol, source="otel"))

        sink_tag, detail = _infer_sink(span["attributes"], span["kind"])
        if sink_tag:
            sink_source = parent_symbol or own_symbol
            records.append(TraceRecord(caller=sink_source, callee=None, source="otel", sink_tag=sink_tag, detail=detail))

    return records


def ingest_otel_file(path: str | Path) -> list[TraceRecord]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return parse_otel_export(data)


# --------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------- #
@dataclasses.dataclass
class ReconciliationResult:
    confirmed_edges: list[tuple[str, str]]
    discovered_edges: list[tuple[str, str]]
    sink_symbols: dict[str, set[str]]
    unresolved_events: list[TraceRecord]
    invocation_counts: dict[tuple[str, str], int]


class GraphReconciler:
    """Merges a list of `TraceRecord`s into an already-built
    `ConcreteGraphBuilder`'s `graph`/`symbol_table` and the pipeline's
    `tag_matrix` - mutating both in place, the same way `TaggingEngine`
    already does when it mirrors a tag onto a graph node's own attributes.
    """

    def __init__(self, builder: ConcreteGraphBuilder, tag_matrix: dict[str, set[str]]) -> None:
        self.builder = builder
        self.tag_matrix = tag_matrix

    def reconcile(self, events: list[TraceRecord]) -> ReconciliationResult:
        confirmed: set[tuple[str, str]] = set()
        discovered: set[tuple[str, str]] = set()
        sink_symbols: dict[str, set[str]] = {}
        unresolved: list[TraceRecord] = []
        counts: dict[tuple[str, str], int] = {}

        for event in events:
            if event.callee is not None:
                self._reconcile_call(event, confirmed, discovered, unresolved, counts)
            if event.sink_tag is not None:
                self._reconcile_sink(event, sink_symbols, unresolved)

        for edge in confirmed:
            self.builder.graph.edges[edge]["confidence"] = "CONFIRMED_RUNTIME"
            self.builder.graph.edges[edge]["runtime_invocation_count"] = counts.get(edge, 0)

        for edge in discovered:
            self.builder.graph.add_edge(
                edge[0], edge[1],
                relation="CALLS",
                provenance="RUNTIME_DISCOVERED",
                confidence="CONFIRMED_RUNTIME",
                runtime_invocation_count=counts.get(edge, 0),
            )

        for symbol, tags in sink_symbols.items():
            merged = self.tag_matrix.setdefault(symbol, set())
            merged.update(tags)
            if symbol in self.builder.graph:
                self.builder.graph.nodes[symbol]["tags"] = merged

        return ReconciliationResult(
            confirmed_edges=sorted(confirmed),
            discovered_edges=sorted(discovered),
            sink_symbols={k: set(v) for k, v in sink_symbols.items()},
            unresolved_events=unresolved,
            invocation_counts=counts,
        )

    def _reconcile_call(
        self,
        event: TraceRecord,
        confirmed: set[tuple[str, str]],
        discovered: set[tuple[str, str]],
        unresolved: list[TraceRecord],
        counts: dict[tuple[str, str], int],
    ) -> None:
        caller, callee = event.caller, event.callee
        if callee not in self.builder.symbol_table or caller is None or caller not in self.builder.symbol_table:
            # Either side isn't a symbol the static indexer actually
            # knows about - most commonly the immediate caller being
            # outside the repo (a test runner, a framework dispatch loop)
            # or the traced frame belonging to a symbol kind Pass 1 never
            # registers (module-level code, a lambda). Kept for
            # visibility, not silently dropped - `prism status` surfaces
            # this count - but there's no real `G_C` node pair to draw an
            # edge between.
            unresolved.append(event)
            return
        edge = (caller, callee)
        counts[edge] = counts.get(edge, 0) + 1
        if self.builder.graph.has_edge(caller, callee):
            confirmed.add(edge)
        else:
            discovered.add(edge)

    def _reconcile_sink(
        self, event: TraceRecord, sink_symbols: dict[str, set[str]], unresolved: list[TraceRecord]
    ) -> None:
        target = event.caller if event.caller and event.caller in self.builder.symbol_table else None
        if target is None:
            unresolved.append(event)
            return
        sink_symbols.setdefault(target, set()).add(event.sink_tag)


# --------------------------------------------------------------------- #
# Persisted runtime state - what `prism status` reads back, across separate
# CLI invocations, without needing to keep a live process (or the whole
# networkx graph) around between a `prism trace` run and a later `prism
# status` call.
# --------------------------------------------------------------------- #
def runtime_state_path(repo_root: str) -> Path:
    return Path(repo_root) / ".prism" / "runtime_state.json"


def _empty_state() -> dict:
    return {
        "trace_files": [],
        "confirmed_edges": [],
        "discovered_edges": [],
        "sink_symbols": {},
        # [caller, callee, count] triples, not a {"caller|callee": count}
        # dict - a JSON object needs string keys, and joining the pair into
        # one string risks colliding with a real qualified name that
        # happens to contain the same separator (unlikely, but a triple
        # sidesteps the question entirely rather than picking a separator
        # and hoping).
        "edge_invocation_counts": [],
        "unresolved_event_count": 0,
        "last_updated": None,
    }


def load_runtime_state(repo_root: str) -> dict:
    path = runtime_state_path(repo_root)
    if not path.exists():
        return _empty_state()
    return json.loads(path.read_text(encoding="utf-8"))


def save_runtime_state(repo_root: str, state: dict) -> None:
    path = runtime_state_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def merge_result_into_state(state: dict, result: ReconciliationResult, trace_file_name: str) -> dict:
    """A pure function (returns a new dict) that folds one reconciliation
    run's results into a persisted state dict - accumulating across
    however many `prism trace` runs a caller has done, so the graph's
    runtime-confirmed picture only ever grows richer over time, the same
    way real test coverage accumulates across separate runs.
    """
    merged = json.loads(json.dumps(state))  # cheap deep copy, state is plain JSON-shaped data

    trace_files = list(merged.get("trace_files", []))
    if trace_file_name not in trace_files:
        trace_files.append(trace_file_name)
    merged["trace_files"] = trace_files

    confirmed = {tuple(e) for e in merged.get("confirmed_edges", [])}
    confirmed.update(result.confirmed_edges)

    discovered = {tuple(e) for e in merged.get("discovered_edges", [])}
    discovered.update(result.discovered_edges)
    # An edge already promoted to CONFIRMED (in this run or an earlier
    # one) shouldn't also linger as merely "discovered" - discovered
    # means "the static pass run alongside this reconciliation didn't
    # have it", which stays true independently per-run, but the
    # persisted state should report each edge under its single best
    # (most-confirmed) classification.
    discovered -= confirmed
    # Stored as lists, not tuples - JSON has no tuple type, and this dict
    # must stay JSON-shaped (list-of-2-lists) consistently whether or not
    # it actually round-tripped through `save_runtime_state`/
    # `load_runtime_state` in between two `merge_result_into_state` calls
    # (chaining calls purely in-memory, as a caller reconciling several
    # trace files in one process would, is equally valid).
    merged["confirmed_edges"] = [list(edge) for edge in sorted(confirmed)]
    merged["discovered_edges"] = [list(edge) for edge in sorted(discovered)]

    sink_symbols = {k: set(v) for k, v in merged.get("sink_symbols", {}).items()}
    for symbol, tags in result.sink_symbols.items():
        sink_symbols.setdefault(symbol, set()).update(tags)
    merged["sink_symbols"] = {k: sorted(v) for k, v in sink_symbols.items()}

    counts: dict[tuple[str, str], int] = {
        (caller, callee): count for caller, callee, count in merged.get("edge_invocation_counts", [])
    }
    for edge, count in result.invocation_counts.items():
        counts[edge] = counts.get(edge, 0) + count
    merged["edge_invocation_counts"] = [[caller, callee, count] for (caller, callee), count in sorted(counts.items())]

    merged["unresolved_event_count"] = merged.get("unresolved_event_count", 0) + len(result.unresolved_events)
    merged["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return merged


def apply_runtime_state(builder: ConcreteGraphBuilder, tag_matrix: dict[str, set[str]], state: dict) -> None:
    """Rehydrate a freshly-built (purely static) `ConcreteGraphBuilder`/
    `tag_matrix` with a *persisted* `.prism/runtime_state.json` - the
    counterpart to `GraphReconciler.reconcile()` for a caller (the MCP
    server's `GraphCache`, primarily) that has the summarized state but
    not the original trace events. Mutates both in place, the same way
    `reconcile()` itself does.

    Every edge is re-validated against the fresh static pass before being
    touched - the codebase may have changed since the trace was recorded,
    so a `discovered_edges` entry whose caller/callee no longer exist (or
    that the static resolver now finds on its own) is skipped rather than
    blindly re-applied.
    """
    for caller, callee in state.get("confirmed_edges", []):
        if builder.graph.has_edge(caller, callee):
            builder.graph.edges[caller, callee]["confidence"] = "CONFIRMED_RUNTIME"

    for caller, callee in state.get("discovered_edges", []):
        if builder.graph.has_edge(caller, callee):
            # The static resolver now finds this edge on its own (the
            # source changed since the trace was recorded) - promote it
            # instead of re-declaring it "discovered".
            builder.graph.edges[caller, callee]["confidence"] = "CONFIRMED_RUNTIME"
            continue
        if caller in builder.symbol_table and callee in builder.symbol_table:
            builder.graph.add_edge(
                caller, callee, relation="CALLS", provenance="RUNTIME_DISCOVERED", confidence="CONFIRMED_RUNTIME"
            )

    for caller, callee, count in state.get("edge_invocation_counts", []):
        if builder.graph.has_edge(caller, callee):
            builder.graph.edges[caller, callee]["runtime_invocation_count"] = count

    for symbol, tags in state.get("sink_symbols", {}).items():
        if symbol not in builder.symbol_table:
            continue
        merged_tags = tag_matrix.setdefault(symbol, set())
        merged_tags.update(tags)
        if symbol in builder.graph:
            builder.graph.nodes[symbol]["tags"] = merged_tags
