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
import enum
import json
import time
from pathlib import Path

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.guard import (
    GuardReconciliation,
    build_fingerprints,
    heal_qualified_names,
    load_fingerprints,
    reconcile as guard_reconcile,
    save_fingerprints,
)
from prism.graph.symbol_table import path_to_module
from prism.runtime.tracer import TraceRecord

# --------------------------------------------------------------------- #
# Orphan Event Classification Pipeline (Issue #15): every runtime event
# that fails to match a static symbol gets an explicit reason instead of
# collapsing into one undifferentiated `unresolved_events` bucket - see
# `classify_orphan`.
# --------------------------------------------------------------------- #
class OrphanReason(str, enum.Enum):
    #: No symbol identity at all to work with - the event's `caller` (and,
    #: for a sink event, therefore its whole classification anchor) never
    #: resolved to anything, e.g. an OTel span missing the
    #: `code.namespace`/`code.function` semantic-convention attributes.
    NO_STATIC_MATCH = "NO_STATIC_MATCH"
    #: A real, named symbol Prism's static indexer never saw at all - the
    #: closest available signal to "this callee was constructed/injected
    #: at runtime" (`eval`/`exec`, a metaclass-synthesized method,
    #: `getattr`-obtained dispatch) rather than merely mis-tracked.
    DYNAMIC_DISPATCH = "DYNAMIC_DISPATCH"
    #: The callee's bare simple name *does* match one or more real,
    #: differently-qualified static symbols (see
    #: `GlobalSymbolTable.candidates_for_simple_name`) - most consistent
    #: with file-version drift or a decorator (`functools.wraps`, a class
    #: decorator) shifting the runtime frame's qualname away from what
    #: Pass 1 registered, not a genuinely novel symbol.
    AMBIGUOUS_SIGNATURE = "AMBIGUOUS_SIGNATURE"
    #: An OTel-sourced event naming a real-looking symbol with no
    #: corresponding Python source Prism indexed - this project's own
    #: `sys.settrace`-based tracer never even produces a frame for a C
    #: extension/native call in the first place (no Python frame exists to
    #: trace), so this reason is reachable only via externally-instrumented
    #: (`source == "otel"`) events.
    NATIVE_OR_C_EXTENSION = "NATIVE_OR_C_EXTENSION"
    #: An unnamed lambda/comprehension frame - this project's own tracer
    #: filters these before ever emitting a `TraceRecord` (see
    #: `tracer._SKIP_CO_NAMES`), so this is reachable only from an
    #: externally-supplied trace source that doesn't apply the same filter.
    ANONYMOUS_CLOSURE = "ANONYMOUS_CLOSURE"


#: Frame/symbol name fragments tree-sitter's own definitions query would
#: never capture as a real function/method - mirrors `tracer._SKIP_CO_NAMES`
#: (kept as a separate copy rather than importing it, since this module
#: must also classify OTel-sourced names `tracer.py` never touches).
_ANONYMOUS_NAME_MARKERS = ("<lambda>", "<listcomp>", "<setcomp>", "<dictcomp>", "<genexpr>")


def _looks_anonymous(qualified_name: str) -> bool:
    return any(marker in qualified_name for marker in _ANONYMOUS_NAME_MARKERS)


def classify_orphan(event: TraceRecord, builder: ConcreteGraphBuilder) -> OrphanReason:
    """Deterministic, best-effort classification of one event that
    `GraphReconciler` couldn't attach to a real static symbol - built
    entirely from the signal a `TraceRecord` actually carries (no line
    numbers or source snippets are available), so this is necessarily a
    heuristic rather than a proof, the same "AST-only, no type inference"
    honesty this codebase's other heuristic classifiers already carry.
    """
    if event.callee is None:
        # A pure sink event (`_reconcile_sink`) reaches this path only when
        # its own `caller` didn't resolve - there's no other symbol
        # identity on a sink-only record to classify from.
        return OrphanReason.NO_STATIC_MATCH

    if _looks_anonymous(event.callee) or (event.caller is not None and _looks_anonymous(event.caller)):
        return OrphanReason.ANONYMOUS_CLOSURE

    if event.caller is None:
        return OrphanReason.NO_STATIC_MATCH

    simple_name = event.callee.rsplit(".", 1)[-1]
    if builder.symbol_table.candidates_for_simple_name(simple_name):
        return OrphanReason.AMBIGUOUS_SIGNATURE

    if event.source == "otel":
        return OrphanReason.NATIVE_OR_C_EXTENSION

    return OrphanReason.DYNAMIC_DISPATCH


#: `RuntimeTrust` thresholds (Issue #15.3) - below LOW, static analysis is
#: the primary ground truth and the trace should be treated as
#: supplementary evidence only; at/above HIGH, runtime-confirmed edges earn
#: an extra distance discount (see `prism.slicer.distance.DistanceConfig`).
RUNTIME_TRUST_LOW_THRESHOLD = 0.5
RUNTIME_TRUST_HIGH_THRESHOLD = 0.9

# Item 9 (second post-implementation audit): Bayesian Confidence
# Degradation for Static False Positives. A `caller` this reconciliation
# saw issue at least this many traced call/sink events is trusted to have
# genuinely executed - not just "the tracer happened to catch it once" -
# so a *static* CALLS edge out of it that never once appeared among the
# traced events is real evidence the static resolver over-linked
# (an overload branch never taken, a dead-code path, a mis-resolved
# reference-chain guess), not merely "the trace didn't cover this yet".
# Below this count, a caller's silence about an edge is far more likely to
# just mean "this trace didn't happen to exercise that caller much at
# all", so no penalty is applied - the same asymmetry `RuntimeTrust`
# itself already encodes (a handful of events can't safely indict
# anything).
NON_OBSERVATION_EXECUTION_THRESHOLD = 10

# Item 10 (second post-implementation audit): Fuzzy Anchor Matching. A
# `pytest_tracer`-sourced event whose exact `callee` qualified name misses
# the static symbol table (`DYNAMIC_DISPATCH`-shaped: a decorator-wrapped
# or metaclass-synthesized callable whose `co_qualname` drifted from what
# Pass 1 indexed) still carries the real `(file, line)` its frame actually
# ran at - a static symbol defined within this many source lines of that
# location, in the *same* file, is treated as the real target. 25 lines
# comfortably covers a decorator stack, a multi-line signature, or a
# docstring/blank-line gap between the recorded frame line and the
# textual `def`, without being so wide it starts matching an unrelated
# sibling function three screens away.
FUZZY_ANCHOR_LINE_WINDOW = 25

#: The tag `DYNAMIC_DISPATCH` orphans feed back onto their (statically
#: resolvable) caller - Issue #15.4's "dynamically tag candidate symbols
#: with #dynamic". Distinct from `prism.tagger.rules.DYNAMIC_HAZARD_TAG`
#: (a *static* AST-shape signal): this one is asserted only once a real
#: execution actually dispatched somewhere the static indexer couldn't see.
RUNTIME_DYNAMIC_TAG = "#dynamic"

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
    #: `OrphanReason.value -> count` - every entry in `unresolved_events`
    #: accounted for under exactly one reason (Issue #15's "zero silent
    #: orphans" invariant), keyed by string rather than the enum itself so
    #: this stays trivially JSON-serializable for `merge_result_into_state`.
    orphan_reasons: dict[str, int] = dataclasses.field(default_factory=dict)
    #: RuntimeTrust = Matched Events / Total Events (1.0 when there were no
    #: events at all - vacuously trustworthy, nothing to distrust).
    trust_score: float = 1.0
    #: Non-`None` iff `trust_score < RUNTIME_TRUST_LOW_THRESHOLD` - a
    #: human-readable diagnostic `prism trace`/`prism status` surfaces
    #: directly, per Issue #15.3.
    trust_warning: str | None = None
    #: Callers of a `DYNAMIC_DISPATCH` orphan that got `RUNTIME_DYNAMIC_TAG`
    #: fed back onto them (Issue #15.4).
    dynamic_tagged_symbols: set[str] = dataclasses.field(default_factory=set)
    #: Item 9: static `CALLS` edges `metadata["unobserved_in_traces"]` was
    #: just set on - a frequently-executing caller (per
    #: `NON_OBSERVATION_EXECUTION_THRESHOLD`) that never once traced a call
    #: down this particular statically-resolved edge. Kept, never deleted -
    #: see `GraphReconciler._apply_non_observation_penalty`.
    non_observed_edges: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    #: Item 10: `(caller, matched_symbol)` pairs Fuzzy Anchor Matching
    #: synthesized this run - `matched_symbol` is the nearest static
    #: symbol within `FUZZY_ANCHOR_LINE_WINDOW` lines of the traced
    #: frame's actual source location, not necessarily the exact name the
    #: frame's `co_qualname` reported. See `GraphReconciler._fuzzy_anchor_match`.
    fuzzy_matched_edges: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    #: Item 10: fraction of callee-side orphans (caller resolved, exact
    #: callee name didn't) Fuzzy Anchor Matching rescued this run - 1.0
    #: vacuously when there were no such orphans.
    orphan_resolution_ratio: float = 1.0


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
        fuzzy_matched: set[tuple[str, str]] = set()
        sink_symbols: dict[str, set[str]] = {}
        unresolved: list[TraceRecord] = []
        counts: dict[tuple[str, str], int] = {}
        fuzzy_orphan_events: list[TraceRecord] = []
        fuzzy_resolved_events: list[TraceRecord] = []

        for event in events:
            if event.callee is not None:
                self._reconcile_call(
                    event, confirmed, discovered, fuzzy_matched, unresolved, counts,
                    fuzzy_orphan_events, fuzzy_resolved_events,
                )
            if event.sink_tag is not None:
                self._reconcile_sink(event, sink_symbols, unresolved)

        for edge in confirmed:
            self.builder.graph.edges[edge]["confidence"] = "CONFIRMED_RUNTIME"
            self.builder.graph.edges[edge]["runtime_invocation_count"] = counts.get(edge, 0)
            # Item 9: an edge a *previous* reconcile() call on this same
            # builder flagged unobserved is now proven observed - clear the
            # stale flag rather than leaving a penalized weight on an edge
            # this very run just confirmed.
            self.builder.graph.edges[edge].pop("unobserved_in_traces", None)

        for edge in discovered:
            self.builder.graph.add_edge(
                edge[0], edge[1],
                relation="CALLS",
                provenance="RUNTIME_DISCOVERED",
                confidence="CONFIRMED_RUNTIME",
                runtime_invocation_count=counts.get(edge, 0),
            )

        for edge in fuzzy_matched:
            # A fuzzy match landing on a pair the graph already has an edge
            # for (static, or already discovered/confirmed above) adds no
            # new information - never downgrade an existing edge to a
            # weaker `TENTATIVE_DYNAMIC_CALL`.
            if not self.builder.graph.has_edge(*edge):
                self.builder.graph.add_edge(
                    edge[0], edge[1],
                    relation="CALLS",
                    provenance="RUNTIME_FUZZY_MATCHED",
                    confidence="TENTATIVE_RUNTIME",
                    kind="TENTATIVE_DYNAMIC_CALL",
                    runtime_invocation_count=counts.get(edge, 0),
                )

        for symbol, tags in sink_symbols.items():
            merged = self.tag_matrix.setdefault(symbol, set())
            merged.update(tags)
            if symbol in self.builder.graph:
                self.builder.graph.nodes[symbol]["tags"] = merged

        # Orphan Event Classification Pipeline (Issue #15): every
        # unresolved event gets exactly one OrphanReason - "zero silent
        # orphans" - and a DYNAMIC_DISPATCH orphan whose *caller* did
        # resolve statically feeds RUNTIME_DYNAMIC_TAG back onto it.
        orphan_reasons: dict[str, int] = {}
        dynamic_tagged: set[str] = set()
        for event in unresolved:
            reason = classify_orphan(event, self.builder)
            orphan_reasons[reason.value] = orphan_reasons.get(reason.value, 0) + 1
            if reason is OrphanReason.DYNAMIC_DISPATCH and event.caller in self.builder.symbol_table:
                dynamic_tagged.add(event.caller)

        for symbol in dynamic_tagged:
            merged = self.tag_matrix.setdefault(symbol, set())
            merged.add(RUNTIME_DYNAMIC_TAG)
            if symbol in self.builder.graph:
                self.builder.graph.nodes[symbol]["tags"] = merged

        total_events = len(events)
        matched_events = total_events - len(unresolved)
        trust_score = (matched_events / total_events) if total_events else 1.0
        trust_warning = None
        if total_events and trust_score < RUNTIME_TRUST_LOW_THRESHOLD:
            trust_warning = (
                f"RuntimeTrust is low ({trust_score:.0%} of {total_events} events matched a static symbol) - "
                "treat this trace as supplementary evidence only; static analysis remains the primary ground truth."
            )

        if trust_score >= RUNTIME_TRUST_HIGH_THRESHOLD:
            for edge in confirmed | discovered:
                self.builder.graph.edges[edge]["high_trust_runtime"] = True

        # Item 9: Bayesian Confidence Degradation for Static False
        # Positives - computed from this run's own events only (the same
        # per-run scope `trust_score` already uses, per
        # `merge_result_into_state`'s "most recently observed, not
        # averaged" comment above - a caller's execution volume is a
        # property of *this* trace, not an all-time total this module
        # doesn't otherwise track).
        symbol_execution_count: dict[str, int] = {}
        for event in events:
            if event.caller is not None:
                symbol_execution_count[event.caller] = symbol_execution_count.get(event.caller, 0) + 1
        non_observed = self._apply_non_observation_penalty(symbol_execution_count, confirmed)

        # Item 10: what fraction of callee-side orphans (caller resolved,
        # exact callee name didn't) Fuzzy Anchor Matching actually rescued
        # this run - vacuously 1.0 when there were no such orphans to
        # begin with, the same "nothing to distrust" convention
        # `trust_score` itself already uses for a trace with zero events.
        orphan_resolution_ratio = (
            len(fuzzy_resolved_events) / len(fuzzy_orphan_events) if fuzzy_orphan_events else 1.0
        )

        return ReconciliationResult(
            confirmed_edges=sorted(confirmed),
            discovered_edges=sorted(discovered),
            sink_symbols={k: set(v) for k, v in sink_symbols.items()},
            unresolved_events=unresolved,
            invocation_counts=counts,
            orphan_reasons=orphan_reasons,
            trust_score=round(trust_score, 4),
            trust_warning=trust_warning,
            dynamic_tagged_symbols=dynamic_tagged,
            non_observed_edges=sorted(non_observed),
            fuzzy_matched_edges=sorted(fuzzy_matched),
            orphan_resolution_ratio=round(orphan_resolution_ratio, 4),
        )

    def _apply_non_observation_penalty(
        self, symbol_execution_count: dict[str, int], confirmed: set[tuple[str, str]]
    ) -> set[tuple[str, str]]:
        """Item 9: for every *static* (`provenance` unset -
        `RUNTIME_DISCOVERED` edges are runtime evidence themselves and
        can't be "unobserved") `CALLS` edge `(u, v)` where `u` executed at
        least `NON_OBSERVATION_EXECUTION_THRESHOLD` times in this trace
        but `(u, v)` itself was never among the confirmed edges above,
        marks `metadata["unobserved_in_traces"] = True` on the existing
        graph edge - never deletes it, so a cold error path a test suite
        simply doesn't exercise stays fully visible to `prism query`, just
        priced as less certain (see `prism.slicer.distance`'s
        `GAMMA_UNOBSERVED` multiplier, which is what actually turns this
        flag into a distance/ranking effect).
        """
        non_observed: set[tuple[str, str]] = set()
        for u, v, data in self.builder.graph.edges(data=True):
            if data.get("relation", "CALLS") != "CALLS":
                continue
            if data.get("provenance") in ("RUNTIME_DISCOVERED", "RUNTIME_FUZZY_MATCHED"):
                # Both are runtime evidence themselves - an edge synthesized
                # *from* this run's trace can't simultaneously be "never
                # observed" by it.
                continue
            if symbol_execution_count.get(u, 0) < NON_OBSERVATION_EXECUTION_THRESHOLD:
                continue
            if (u, v) in confirmed:
                continue
            data["unobserved_in_traces"] = True
            non_observed.add((u, v))
        return non_observed

    def _reconcile_call(
        self,
        event: TraceRecord,
        confirmed: set[tuple[str, str]],
        discovered: set[tuple[str, str]],
        fuzzy_matched: set[tuple[str, str]],
        unresolved: list[TraceRecord],
        counts: dict[tuple[str, str], int],
        fuzzy_orphan_events: list[TraceRecord],
        fuzzy_resolved_events: list[TraceRecord],
    ) -> None:
        caller, callee = event.caller, event.callee
        if caller is None or caller not in self.builder.symbol_table:
            # The caller side isn't a symbol the static indexer actually
            # knows about - most commonly it being outside the repo (a test
            # runner, a framework dispatch loop). Kept for visibility, not
            # silently dropped - `prism status` surfaces this count - but
            # there's no real `G_C` source node to draw an edge from, and
            # Item 10's Fuzzy Anchor Matching (below) can't help either: it
            # only ever resolves the *callee* side.
            unresolved.append(event)
            return
        if callee not in self.builder.symbol_table:
            # Item 10: the exact qualified name Pass 1 registered and the
            # one this frame's `co_qualname` produced disagree (a
            # decorator-wrapped or metaclass-synthesized callable) - try
            # matching by source-location proximity before giving up.
            fuzzy_orphan_events.append(event)
            matched = self._fuzzy_anchor_match(event)
            if matched is None:
                unresolved.append(event)
                return
            fuzzy_resolved_events.append(event)
            edge = (caller, matched)
            counts[edge] = counts.get(edge, 0) + 1
            fuzzy_matched.add(edge)
            return
        edge = (caller, callee)
        counts[edge] = counts.get(edge, 0) + 1
        if self.builder.graph.has_edge(caller, callee):
            confirmed.add(edge)
        else:
            discovered.add(edge)

    def _fuzzy_anchor_match(self, event: TraceRecord) -> str | None:
        """Item 10: Fuzzy Anchor Matching. Only ever called once exact
        qualified-name resolution has already failed for `event.callee`.
        Searches the *same file* `event.callee_file` names for a static
        symbol whose `line_range` is within `FUZZY_ANCHOR_LINE_WINDOW`
        lines of `event.callee_line` - the nearest one wins; a genuine tie
        (two equidistant candidates) is real ambiguity, not a coin flip, so
        it is left unresolved rather than guessed, the same "don't guess"
        principle `ConcreteGraphBuilder`'s Go Stage 2 fallback and TS
        heritage-target resolution already apply.

        Candidates are restricted to `function`/`method` symbols - the
        same restriction `GlobalSymbolTable._simple_name_index` already
        applies for the identical reason ("a class ... is never itself the
        target of ... a call"): a traced *call* event's target is always a
        callable, and an enclosing class's `line_range` always contains
        every one of its methods' ranges, which would otherwise manufacture
        a spurious distance-0 tie between a method and its own class on
        every single match attempt. The same containment shape recurs one
        level down for a *nested* function (a closure's `line_range` is
        always inside its enclosing function's) - among candidates that
        directly contain `event.callee_line` (distance 0), the smallest
        (most specific/innermost) span wins rather than tying, so a traced
        closure resolves to itself, not to the outer function textually
        wrapped around it. A genuine tie (equal distance *and* equal span)
        is real ambiguity, not a coin flip, so it is left unresolved rather
        than guessed - the same "don't guess" principle `ConcreteGraphBuilder`'s
        Go Stage 2 fallback and TS heritage-target resolution already apply.
        """
        if event.callee_file is None or event.callee_line is None:
            return None
        module = path_to_module(event.callee_file, self.builder.repo_root)
        best: list[tuple[int, int, str]] = []
        for symbol in self.builder.symbol_table:
            if symbol.module != module or symbol.kind not in ("function", "method"):
                continue
            start, end = symbol.line_range
            if start <= event.callee_line <= end:
                distance = 0
            else:
                distance = min(abs(start - event.callee_line), abs(end - event.callee_line))
            if distance <= FUZZY_ANCHOR_LINE_WINDOW:
                best.append((distance, end - start, symbol.qualified_name))
        if not best:
            return None
        best.sort()
        if len(best) > 1 and best[0][:2] == best[1][:2]:
            return None
        return best[0][2]

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
        "orphan_reasons": {},
        "trust_score": 1.0,
        "dynamic_tagged_symbols": [],
        # Item 9: [caller, callee] pairs cumulatively flagged
        # `unobserved_in_traces` across every `prism trace` run recorded so
        # far, minus any pair a later run did confirm (see
        # `merge_result_into_state`).
        "unobserved_edges": [],
        # Item 10: [caller, callee] pairs cumulatively synthesized by Fuzzy
        # Anchor Matching across every run so far, minus any pair a later
        # exact-match run superseded (see `merge_result_into_state`).
        "fuzzy_matched_edges": [],
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

    orphan_reasons = dict(merged.get("orphan_reasons", {}))
    for reason, count in result.orphan_reasons.items():
        orphan_reasons[reason] = orphan_reasons.get(reason, 0) + count
    merged["orphan_reasons"] = orphan_reasons

    # The most recently observed trust score, not an average across every
    # run ever recorded - a stale, high-trust trace from months ago
    # shouldn't mask a newly-introduced reconciliation problem the latest
    # run just surfaced.
    merged["trust_score"] = result.trust_score

    dynamic_tagged = set(merged.get("dynamic_tagged_symbols", []))
    dynamic_tagged.update(result.dynamic_tagged_symbols)
    merged["dynamic_tagged_symbols"] = sorted(dynamic_tagged)

    # Item 9: accumulate flagged pairs across runs, same as
    # `discovered -= confirmed` above - a pair a *later* run did confirm is
    # real evidence the earlier non-observation was just incomplete trace
    # coverage, not a genuine static false positive, so it comes back off
    # the list rather than staying flagged forever.
    unobserved = {tuple(e) for e in merged.get("unobserved_edges", [])}
    unobserved.update(result.non_observed_edges)
    unobserved -= confirmed
    unobserved -= discovered
    merged["unobserved_edges"] = [list(edge) for edge in sorted(unobserved)]

    # Item 10: accumulate across runs; a pair later resolved exactly
    # (confirmed or discovered) supersedes its earlier fuzzy guess -
    # `apply_runtime_state` applies `confirmed_edges`/`discovered_edges`
    # first, so leaving a superseded pair in here would be harmless either
    # way, but pruning it keeps the persisted state an honest reflection
    # of "still only a fuzzy guess as of the latest evidence".
    fuzzy_matched = {tuple(e) for e in merged.get("fuzzy_matched_edges", [])}
    fuzzy_matched.update(result.fuzzy_matched_edges)
    fuzzy_matched -= confirmed
    fuzzy_matched -= discovered
    merged["fuzzy_matched_edges"] = [list(edge) for edge in sorted(fuzzy_matched)]

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

    for symbol in state.get("dynamic_tagged_symbols", []):
        if symbol not in builder.symbol_table:
            continue
        merged_tags = tag_matrix.setdefault(symbol, set())
        merged_tags.add(RUNTIME_DYNAMIC_TAG)
        if symbol in builder.graph:
            builder.graph.nodes[symbol]["tags"] = merged_tags

    # Item 9: re-validated against the fresh static graph exactly like
    # `discovered_edges` above - a pair the current static pass no longer
    # even has an edge for (the code changed since the trace) has nothing
    # to flag.
    for caller, callee in state.get("unobserved_edges", []):
        if builder.graph.has_edge(caller, callee):
            builder.graph.edges[caller, callee]["unobserved_in_traces"] = True

    # Item 10: re-validated against the fresh static graph exactly like
    # `discovered_edges` above - and, same as there, an edge the static
    # resolver now finds on its own supersedes the earlier fuzzy guess
    # rather than being downgraded back to `TENTATIVE_DYNAMIC_CALL`.
    for caller, callee in state.get("fuzzy_matched_edges", []):
        if builder.graph.has_edge(caller, callee):
            continue
        if caller in builder.symbol_table and callee in builder.symbol_table:
            builder.graph.add_edge(
                caller, callee,
                relation="CALLS",
                provenance="RUNTIME_FUZZY_MATCHED",
                confidence="TENTATIVE_RUNTIME",
                kind="TENTATIVE_DYNAMIC_CALL",
            )

    # RuntimeTrust boost (Issue #15.3), re-derived from the persisted
    # trust_score rather than a separately-persisted edge list - the most
    # recent run's trust level is what should govern every currently-live
    # confirmed/discovered edge, consistent with `merge_result_into_state`
    # only ever keeping the latest trust_score, not an average.
    if state.get("trust_score", 1.0) >= RUNTIME_TRUST_HIGH_THRESHOLD:
        for caller, callee in [*state.get("confirmed_edges", []), *state.get("discovered_edges", [])]:
            if builder.graph.has_edge(caller, callee):
                builder.graph.edges[caller, callee]["high_trust_runtime"] = True


def heal_and_apply_runtime_state(
    builder: ConcreteGraphBuilder, tag_matrix: dict[str, set[str]], state: dict, repo_root: str,
) -> GuardReconciliation:
    """`apply_runtime_state`, but self-healing across symbol renames first
    (`prism.graph.guard`): without this, a rename silently drops every
    runtime-confirmed edge/sink tag that pointed at the old name, because
    `apply_runtime_state` correctly (and by design) skips anything that no
    longer resolves against the fresh static graph. This reconciles the
    persisted fingerprint snapshot from the last index against the current
    one, rewrites `state`'s qualified-name references for anything it can
    match unambiguously, applies the (possibly healed) state as normal, and
    persists the fresh fingerprint snapshot for the next run. Returns the
    `GuardReconciliation` so a caller (`prism status`) can report what was
    healed rather than have it happen silently.
    """
    old_fingerprints = load_fingerprints(repo_root)
    new_fingerprints = build_fingerprints(builder)
    reconciliation = guard_reconcile(old_fingerprints, new_fingerprints)

    healed_state = heal_qualified_names(state, reconciliation.renamed)
    apply_runtime_state(builder, tag_matrix, healed_state)

    save_fingerprints(repo_root, new_fingerprints)
    return reconciliation
