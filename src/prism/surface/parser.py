"""v1.1+ Agent Surface: `parse_context` - the lossless inverse of
`prism.surface.renderer.render`.

    parse_context(render(pkg)) == pkg

(for a `pkg` rendered with `RenderOptions(include_timestamp=True,
include_run_id=True)` and a `budget.tokens` large enough that
`render` never has cause to auto-append its own `BUDGET_OVERFLOW`
warning - see `renderer.py`'s own docstring: both of those are real,
intentional, one-way transformations the renderer applies by default,
not roundtrip bugs).

Parses via `defusedxml.ElementTree` (blocks XML entity-expansion attacks -
"billion laughs", external entity resolution - unlike the stdlib
`xml.etree.ElementTree` this codebase already uses elsewhere for
attribute-order-preserving *building*, which was never meant to parse
untrusted input) rather than hand-rolled parsing, plus this module's own
depth/element-count guard on top: a `<prism_context>` document may in
principle arrive from an untrusted MCP client or a fetched artifact, the
same threat model `prism.mcp.security` already documents for tool
arguments.
"""
from __future__ import annotations

from xml.etree.ElementTree import Element

from defusedxml import ElementTree as DefusedET

from prism.surface.models import (
    BudgetRef,
    ContextPackage,
    CoverageGap,
    CoverageSummary,
    EdgeEntry,
    EngineRef,
    EnvelopeWarning,
    FeatureCoverage,
    LanguageRef,
    Manifest,
    ManifestCompression,
    ManifestDistanceMetric,
    NodeContract,
    NodeEntry,
    NodeFeatures,
    NodeSignature,
    NodeSignatureParam,
    NodeSignatureReturn,
    SeedRef,
)

#: Mirrors the spec's own stated limits - a document nested or fanned out
#: beyond any real `<prism_context>` this codebase's own renderer would
#: ever produce is treated as malformed/hostile input, not parsed on a
#: best-effort basis.
MAX_DEPTH = 100
MAX_ELEMENT_COUNT = 100_000


class SurfaceParseError(Exception):
    """Raised for any `<prism_context>` document this module refuses to
    parse - malformed XML, a depth/element-count limit exceeded, or a
    required element/attribute missing. Never a bare `KeyError`/
    `ValueError`/`AttributeError` escaping from inside this module."""


def _check_size(root: Element) -> None:
    count = 0
    stack: list[tuple[Element, int]] = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        count += 1
        if depth > MAX_DEPTH:
            raise SurfaceParseError(f"document exceeds maximum nesting depth {MAX_DEPTH}")
        if count > MAX_ELEMENT_COUNT:
            raise SurfaceParseError(f"document exceeds maximum element count {MAX_ELEMENT_COUNT}")
        for child in node:
            stack.append((child, depth + 1))


def _get_bool(elem: Element, key: str, default: bool | None = None) -> bool | None:
    raw = elem.get(key)
    if raw is None:
        return default
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise SurfaceParseError(f"attribute {key!r} on <{elem.tag}> is not a valid boolean: {raw!r}")


def _get_int(elem: Element, key: str, default: int | None = None) -> int | None:
    raw = elem.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SurfaceParseError(f"attribute {key!r} on <{elem.tag}> is not a valid integer: {raw!r}") from exc


def _get_float(elem: Element, key: str, default: float | None = None) -> float | None:
    raw = elem.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise SurfaceParseError(f"attribute {key!r} on <{elem.tag}> is not a valid float: {raw!r}") from exc


def _get_str(elem: Element, key: str, default: str | None = None, required: bool = False) -> str | None:
    raw = elem.get(key)
    if raw is None:
        if required:
            raise SurfaceParseError(f"<{elem.tag}> is missing required attribute {key!r}")
        return default
    return raw


def _require_child(elem: Element, tag: str) -> Element:
    child = elem.find(tag)
    if child is None:
        raise SurfaceParseError(f"<{elem.tag}> is missing required child <{tag}>")
    return child


def _text_of(elem: Element) -> str:
    return elem.text or ""


def _parse_engine(elem: Element) -> EngineRef:
    return EngineRef(
        name=_get_str(elem, "name", required=True),
        version=_get_str(elem, "version", required=True),
        commit=_get_str(elem, "commit", required=True),
    )


def _parse_seed(elem: Element) -> SeedRef:
    return SeedRef(
        symbol=_get_str(elem, "symbol", required=True),
        file=_get_str(elem, "file", required=True),
        line=_get_int(elem, "line", 0),
    )


def _parse_budget(elem: Element) -> BudgetRef:
    return BudgetRef(
        tokens=_get_int(elem, "tokens", 0),
        tokenizer=_get_str(elem, "tokenizer", required=True),
        exact=_get_bool(elem, "exact", False),
    )


def _parse_language(elem: Element) -> LanguageRef:
    tier = _get_str(elem, "tier", required=True)
    if tier not in ("1", "2", "3"):
        raise SurfaceParseError(f"<language> tier must be '1', '2', or '3', got {tier!r}")
    return LanguageRef(tier=tier, primary=_get_str(elem, "primary", required=True), files=_get_int(elem, "files", 0))


def _parse_metadata(root: Element) -> tuple[EngineRef, SeedRef, BudgetRef, LanguageRef, dict[str, str]]:
    metadata = _require_child(root, "metadata")
    engine = _parse_engine(_require_child(metadata, "engine"))
    seed = _parse_seed(_require_child(metadata, "seed"))
    budget = _parse_budget(_require_child(metadata, "budget"))
    language = _parse_language(_require_child(metadata, "language"))
    options: dict[str, str] = {}
    options_elem = metadata.find("options")
    if options_elem is not None:
        for opt in options_elem.findall("option"):
            key = _get_str(opt, "key", required=True)
            options[key] = _get_str(opt, "value", "")
    return engine, seed, budget, language, options


def _parse_manifest(root: Element) -> Manifest:
    elem = _require_child(root, "manifest")
    compression = [
        ManifestCompression(level=_get_str(c, "level", required=True), count=_get_int(c, "count", 0))
        for c in elem.findall("compression")
    ]
    dm_elem = _require_child(elem, "distance_metric")
    distance_metric = ManifestDistanceMetric(
        name=_get_str(dm_elem, "name", required=True),
        lambda_data_flow=_get_float(dm_elem, "lambda_data_flow", 0.0),
        lambda_guard=_get_float(dm_elem, "lambda_guard", 0.0),
        dist_max=_get_float(dm_elem, "dist_max", 0.0),
    )
    return Manifest(
        packed_nodes=_get_int(elem, "packed_nodes", 0),
        considered_nodes=_get_int(elem, "considered_nodes", 0),
        reachable_nodes=_get_int(elem, "reachable_nodes", 0),
        compression=compression,
        distance_metric=distance_metric,
    )


def _parse_coverage(root: Element) -> CoverageSummary:
    elem = _require_child(root, "coverage")
    features = [
        FeatureCoverage(id=_get_str(f, "id", required=True), present=_get_bool(f, "present", False), count=_get_int(f, "count", 0))
        for f in elem.findall("feature")
    ]
    gaps = [
        CoverageGap(feature=_get_str(g, "feature", required=True), reason=_get_str(g, "reason", required=True))
        for g in elem.findall("gap")
    ]
    return CoverageSummary(
        total_features=_get_int(elem, "total_features", 0),
        covered_features=_get_int(elem, "covered_features", 0),
        omitted_features=_get_int(elem, "omitted_features", 0),
        features=features,
        gaps=gaps,
    )


def _parse_warnings(root: Element) -> list[EnvelopeWarning]:
    elem = root.find("warnings")
    if elem is None:
        return []
    warnings = []
    for w in elem.findall("warning"):
        message_elem = w.find("message")
        message = _text_of(message_elem) if message_elem is not None else ""
        details: dict[str, str | int | float] = {}
        for detail in w.findall("detail"):
            key = _get_str(detail, "key", required=True)
            raw_value = _get_str(detail, "value", "")
            type_name = _get_str(detail, "type", "str")
            if type_name == "int":
                details[key] = _get_int(detail, "value", 0)
            elif type_name == "float":
                details[key] = _get_float(detail, "value", 0.0)
            else:
                details[key] = raw_value
        warnings.append(
            EnvelopeWarning(code=_get_str(w, "code", required=True), severity=_get_str(w, "severity", required=True), message=message, details=details)
        )
    return warnings


def _parse_signature(elem: Element | None) -> NodeSignature:
    if elem is None:
        return NodeSignature()
    params = [
        NodeSignatureParam(name=_get_str(p, "name", required=True), type=_get_str(p, "type"), optional=_get_bool(p, "optional", False))
        for p in elem.findall("param")
    ]
    returns_elem = elem.find("returns")
    returns = None
    if returns_elem is not None:
        returns = NodeSignatureReturn(type=_get_str(returns_elem, "type"), kind=_get_str(returns_elem, "kind", required=True))
    return NodeSignature(params=params, returns=returns)


def _parse_features(elem: Element | None) -> NodeFeatures:
    if elem is None:
        return NodeFeatures(substance="", form="", output="", role="")
    return NodeFeatures(
        substance=_get_str(elem, "substance", ""),
        form=_get_str(elem, "form", ""),
        output=_get_str(elem, "output", ""),
        role=_get_str(elem, "role", ""),
    )


def _parse_contract(elem: Element | None) -> NodeContract | None:
    if elem is None:
        return None
    return NodeContract(
        target_id=_get_str(elem, "target_id", required=True),
        call_line=_get_int(elem, "call_line", 0),
        unpacks=_get_str(elem, "unpacks"),
        passes_args=_get_str(elem, "passes_args"),
    )


def _parse_body(elem: Element | None) -> str:
    if elem is None:
        return ""
    return elem.text or ""


def _parse_nodes(root: Element) -> list[NodeEntry]:
    elem = root.find("nodes")
    if elem is None:
        return []
    nodes = []
    for n in elem.findall("node"):
        role = _get_str(n, "role", required=True)
        compression = _get_str(n, "compression", required=True)
        nodes.append(
            NodeEntry(
                id=_get_str(n, "id", required=True),
                role=role,
                distance=_get_float(n, "distance", 0.0),
                compression=compression,
                cost=_get_int(n, "cost", 0),
                symbol_name=_get_str(n, "symbol_name", required=True),
                symbol_kind=_get_str(n, "symbol_kind", required=True),
                language=_get_str(n, "language", required=True),
                file=_get_str(n, "file", required=True),
                line=_get_int(n, "line", 0),
                end_line=_get_int(n, "end_line", 0),
                signature=_parse_signature(n.find("signature")),
                features=_parse_features(n.find("features")),
                contract=_parse_contract(n.find("contract")),
                body=_parse_body(n.find("body")),
            )
        )
    return nodes


def _parse_edges(root: Element) -> list[EdgeEntry]:
    elem = root.find("edges")
    if elem is None:
        return []
    edges = []
    for e in elem.findall("edge"):
        edges.append(
            EdgeEntry(
                from_node=_get_str(e, "from", required=True),
                to_node=_get_str(e, "to", required=True),
                type=_get_str(e, "type", required=True),
                weight=_get_float(e, "weight", 0.0),
                data_flow=_get_bool(e, "data_flow", False),
                guard=_get_bool(e, "guard", False),
                back_edge=_get_bool(e, "back_edge", False),
            )
        )
    return edges


def parse_context(xml_str: str) -> ContextPackage:
    """Reconstructs a `ContextPackage` from a `<prism_context>` document -
    the lossless inverse of `prism.surface.renderer.render`. Raises
    `SurfaceParseError` for malformed XML, a size-limit violation, or a
    missing required element/attribute; never a bare stdlib exception.
    """
    try:
        root = DefusedET.fromstring(xml_str)
    except Exception as exc:  # defusedxml raises its own + ET's ParseError subclasses
        raise SurfaceParseError(f"malformed XML: {exc}") from exc

    if root.tag != "prism_context":
        raise SurfaceParseError(f"root element must be <prism_context>, got <{root.tag}>")

    _check_size(root)

    schema_version = _get_int(root, "schema_version", 1)
    generated_at = _get_str(root, "generated_at")
    run_id = _get_str(root, "run_id")

    engine, seed, budget, language, options = _parse_metadata(root)
    manifest = _parse_manifest(root)
    coverage = _parse_coverage(root)
    warnings = _parse_warnings(root)
    nodes = _parse_nodes(root)
    edges = _parse_edges(root)

    return ContextPackage(
        schema_version=schema_version,
        engine=engine,
        seed=seed,
        budget=budget,
        language=language,
        options=options,
        manifest=manifest,
        coverage=coverage,
        warnings=warnings,
        nodes=nodes,
        edges=edges,
        run_id=run_id,
        generated_at=generated_at,
    )
