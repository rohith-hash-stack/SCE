"""v1.1+ Agent Surface: the reference `<prism_context>` renderer.

`render(pkg, options)` is a **pure function**: no I/O, no wall-clock reads
unless `options.include_timestamp`/`include_run_id` explicitly ask for
`pkg.generated_at`/`pkg.run_id` to be emitted (both are plain fields on
`pkg` itself, never read from the system clock here), no randomness. The
same `(pkg, options)` pair always produces the exact same byte string -
`tests/surface/test_renderer_properties.py`'s own Determinism property
tests this directly.

**No string templating of untrusted content**: every attribute value goes
through `_esc_attr` (`&`/`<`/`>`/`"` entity-escaped, plus a defensive
strip of any XML 1.0-illegal control character - real source can
theoretically carry a stray control byte, and this must never crash or
silently emit invalid XML over it) and every node body goes through
`_cdata`, never raw string interpolation.

### Document shape

    <prism_context generated_at="..." run_id="..." schema_version="1">
      <metadata>
        <engine commit="..." name="..." version="..."/>
        <seed file="..." line="1" symbol="..."/>
        <budget exact="true" tokenizer="cl100k_base" tokens="4000"/>
        <language files="3" primary="python" tier="1"/>
        <options><option key="..." value="..."/></options>
      </metadata>
      <manifest ...><compression .../>...<distance_metric .../></manifest>
      <coverage ...><feature .../>...<gap .../>...</coverage>
      <warnings><warning code="..." severity="..."><message>...</message>
        <detail key="..." value="..."/></warning></warnings>
      <nodes><node ...><signature>...</signature><features .../>
        <contract .../><body><![CDATA[...]]></body></node></nodes>
      <edges><edge .../></edges>
      <trailer edge_count="..." node_count="..." sha256="..." token_count=".../>
    </prism_context>

This exact nesting is this module's own design (the spec fixes the
top-level element *ordering* and the mechanics below, not a byte-exact
schema) - every sub-model from `prism.surface.models` becomes its own
child element, its own scalar fields as alphabetically-sorted attributes,
consistently, so the shape is predictable without needing to special-case
any one block.

### Mechanics (spec-mandated, not this module's own choice)

  - `generated_at`/`run_id` are omitted from the root element entirely
    unless `options.include_timestamp`/`include_run_id` is set - even
    when `pkg.generated_at`/`pkg.run_id` are populated. This is what
    makes Determinism testable at all: two packages built seconds apart
    for the same underlying pack render identically by default.
  - Attributes are sorted alphabetically by name within every element.
  - Node ordering: seed first, then `(distance ASC, id ASC)`.
  - Edge ordering: `(from_node ASC, to_node ASC, type ASC)`.
  - Warning ordering: `(severity DESC, code ASC)` - "DESC" on a
    `Literal["low","medium","high"]` means high-severity first, via
    `_SEVERITY_RANK`.
  - Line endings normalized to `\n`; the document ends with exactly one
    trailing newline, no more.
  - A body's `]]>` sequence is split as `]]]]><![CDATA[>` - the
    standard, lossless XML technique (closes the CDATA section one
    character early, emits the literal `]]>` as plain escaped-free text
    between two CDATA runs, reopens immediately) - never dropped to
    unescaped text, never a crash.
  - `options.pretty`/`options.indent`: real, not cosmetic no-ops - each
    of the 7 top-level sections, and each repeated item within a
    container (`node`, `edge`, `warning`, `option`, `compression`,
    `feature`, `gap`, `detail`), gets its own indented line
    (`_join_children`, operating strictly *between* already-fully-
    rendered sibling strings - never re-parses or reformats *inside* one,
    so a `<body>`'s `CDATA` content is never touched by indentation).
    Nesting past that (params inside a `<signature>`, for instance) stays
    on one line - a deliberate "readable outline, not maximally deep"
    scope, not an oversight.

### Token accounting & the `BUDGET_OVERFLOW` warning

`actual_tokens` is `prism.slicer.tokenizer.count_tokens` over the
rendered document *up to and including* `<edges>` (metadata through
edges - everything except `<trailer>` itself, which cannot include its
own byte count). Deciding whether to inject a `BUDGET_OVERFLOW` warning
needs that count *before* `<warnings>` is rendered (warnings render
earlier in document order than nodes/edges) - resolved with one extra
pass, not a fixed point: `<metadata><manifest><coverage><nodes><edges>`
are rendered once (without `<warnings>` interleaved) purely to get an
accurate token count, that count decides whether `BUDGET_OVERFLOW` is
appended to the (possibly already non-empty) warnings list, then the
real, final document - `<warnings>` now included at its correct position
- is assembled and hashed. This means the overflow decision is made from
a slight *undercount* relative to the truly final document (missing
`<warnings>`'s and `<trailer>`'s own few dozen bytes) - stated here
plainly rather than pretended away, and immaterial in practice since real
overflow is dominated by node/edge content, not the warnings block.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

from pydantic import BaseModel

from prism.slicer.tokenizer import count_tokens
from prism.surface.models import ContextPackage, EnvelopeWarning

_COMPRESSION_LEVELS = ("L0_full", "L1_pruned", "L2_skeleton", "L3_alias")
_SEVERITY_RANK = {"high": 2, "medium": 1, "low": 0}


class RenderOptions(BaseModel):
    indent: int = 2
    pretty: bool = True
    include_timestamp: bool = False
    include_run_id: bool = False
    include_bodies: bool = True
    max_body_lines: Optional[int] = None
    schema_version: int = 1


# --------------------------------------------------------------------- #
# Low-level XML primitives - the only place this module ever touches a
# raw string that could contain untrusted content.
# --------------------------------------------------------------------- #
#: XML 1.0's `Char` production excludes every C0 control character except
#: tab (`\x09`), LF (`\x0A`), and CR (`\x0D`) - a real source file could
#: in principle carry one of these (a stray `\x00`, a raw `\x1b` escape
#: sequence in a fixture/test-data string), and an XML serializer that
#: doesn't handle this either crashes or emits genuinely invalid XML no
#: parser can read back. Stripped, not escaped - there is no valid XML
#: escape for these code points at all.
_ILLEGAL_XML_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _sanitize(s: str) -> str:
    return _ILLEGAL_XML_CHARS.sub("", s)


def _esc_attr(value: object) -> str:
    """Escapes `value` for use inside a double-quoted XML attribute.
    Beyond the usual `&`/`<`/`>`/`"` entities, a literal tab/LF/CR is
    escaped as a numeric character reference (`&#9;`/`&#10;`/`&#13;`),
    not left as a raw whitespace byte - XML 1.0's attribute-value
    normalization rule requires every conformant parser to collapse a
    *literal* whitespace character inside an attribute value to a plain
    space, silently losing it; a character reference decodes to the
    exact original character instead, immune to that normalization
    (`tests/surface/test_renderer_properties.py`'s own Roundtrip Fidelity
    property is what surfaced this - a `str | int | float` warning
    `detail` value carrying a real newline previously came back as a
    space after a parse round trip).
    """
    if isinstance(value, bool):
        s = "true" if value else "false"
    elif isinstance(value, float):
        s = repr(value)
    elif isinstance(value, int):
        s = str(value)
    else:
        s = _sanitize(str(value))
    escaped = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    return escaped.replace("\t", "&#9;").replace("\n", "&#10;").replace("\r", "&#13;")


def _esc_text(value: str) -> str:
    s = _sanitize(value)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cdata(value: str) -> str:
    """Wraps `value` in `<![CDATA[...]]>`, splitting any embedded `]]>`
    sequence losslessly (`]]]]><![CDATA[>`) rather than ever dropping to
    escaped plain text or raising."""
    s = _sanitize(value)
    s = s.replace("]]>", "]]]]><![CDATA[>")
    return f"<![CDATA[{s}]]>"


def _attrs(fields: dict[str, object]) -> str:
    """Renders `fields` as ` key="value"` pairs, sorted alphabetically by
    key, `None` values omitted entirely (an optional field genuinely
    absent, not merely empty)."""
    parts = [f'{key}="{_esc_attr(fields[key])}"' for key in sorted(fields) if fields[key] is not None]
    return (" " + " ".join(parts)) if parts else ""


def _leaf(tag: str, attrs: dict[str, object] | None = None, text: str | None = None) -> str:
    """A self-closing element, or one wrapping plain (already-escaped)
    text with no further nested structure - used for every element this
    module never needs to pretty-indent internally."""
    attr_str = _attrs(attrs or {})
    if text is None:
        return f"<{tag}{attr_str}/>"
    return f"<{tag}{attr_str}>{text}</{tag}>"


def _join_children(items: list[str], depth: int, options: RenderOptions) -> str:
    """Joins already-fully-rendered sibling element strings as the inner
    content of their shared parent - compact (`"".join(items)`) or, when
    `options.pretty`, one indented line per item, indented `depth` levels
    (the parent's own closing tag then lands back at `depth - 1`). Never
    reformats *inside* any one item, so a `<body>` CDATA block anywhere
    in `items` passes through byte-for-byte.
    """
    if not items:
        return ""
    if not options.pretty:
        return "".join(items)
    pad = " " * (options.indent * depth)
    closing_pad = " " * (options.indent * (depth - 1))
    return "\n" + "\n".join(pad + item for item in items) + "\n" + closing_pad


def _container(tag: str, attrs: dict[str, object], items: list[str], depth: int, options: RenderOptions) -> str:
    attr_str = _attrs(attrs)
    if not items:
        return f"<{tag}{attr_str}/>"
    return f"<{tag}{attr_str}>{_join_children(items, depth, options)}</{tag}>"


# --------------------------------------------------------------------- #
# Section renderers
# --------------------------------------------------------------------- #
def _render_metadata(pkg: ContextPackage, options: RenderOptions) -> str:
    engine = _leaf("engine", {"name": pkg.engine.name, "version": pkg.engine.version, "commit": pkg.engine.commit})
    seed = _leaf("seed", {"symbol": pkg.seed.symbol, "file": pkg.seed.file, "line": pkg.seed.line})
    budget = _leaf("budget", {"tokens": pkg.budget.tokens, "tokenizer": pkg.budget.tokenizer, "exact": pkg.budget.exact})
    language = _leaf("language", {"tier": pkg.language.tier, "primary": pkg.language.primary, "files": pkg.language.files})
    option_items = [_leaf("option", {"key": key, "value": pkg.options[key]}) for key in sorted(pkg.options)]
    options_elem = _container("options", {}, option_items, depth=3, options=options)
    return _container("metadata", {}, [engine, seed, budget, language, options_elem], depth=2, options=options)


def _render_manifest(pkg: ContextPackage, options: RenderOptions) -> str:
    m = pkg.manifest
    by_level = {c.level: c.count for c in m.compression}
    compression_items = [
        _leaf("compression", {"level": level, "count": by_level[level]}) for level in _COMPRESSION_LEVELS if level in by_level
    ]
    dm = m.distance_metric
    distance_metric = _leaf(
        "distance_metric",
        {"name": dm.name, "lambda_data_flow": dm.lambda_data_flow, "lambda_guard": dm.lambda_guard, "dist_max": dm.dist_max},
    )
    return _container(
        "manifest",
        {"packed_nodes": m.packed_nodes, "considered_nodes": m.considered_nodes, "reachable_nodes": m.reachable_nodes},
        [*compression_items, distance_metric],
        depth=2,
        options=options,
    )


def _render_coverage(pkg: ContextPackage, options: RenderOptions) -> str:
    c = pkg.coverage
    feature_items = [_leaf("feature", {"id": f.id, "present": f.present, "count": f.count}) for f in c.features]
    gap_items = [_leaf("gap", {"feature": g.feature, "reason": g.reason}) for g in c.gaps]
    return _container(
        "coverage",
        {"total_features": c.total_features, "covered_features": c.covered_features, "omitted_features": c.omitted_features},
        [*feature_items, *gap_items],
        depth=2,
        options=options,
    )


def _sorted_warnings(warnings: list[EnvelopeWarning]) -> list[EnvelopeWarning]:
    return sorted(warnings, key=lambda w: (-_SEVERITY_RANK.get(w.severity, -1), w.code))


def _detail_type_name(value: object) -> str:
    """Which of `EnvelopeWarning.details`' own `str | int | float` union
    `value` is - preserved as a companion `type` attribute so `prism.
    surface.parser` can reconstruct the original Python type rather than
    always coming back as a string (every XML attribute is textual)."""
    if isinstance(value, bool):
        return "str"  # not part of the union; stringified defensively
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "str"


def _render_one_warning(w: EnvelopeWarning, options: RenderOptions) -> str:
    message = _leaf("message", {}, text=_esc_text(w.message))
    detail_items = [
        _leaf("detail", {"key": key, "value": w.details[key], "type": _detail_type_name(w.details[key])})
        for key in sorted(w.details)
    ]
    return _container("warning", {"code": w.code, "severity": w.severity}, [message, *detail_items], depth=3, options=options)


def _render_warnings(warnings: list[EnvelopeWarning], options: RenderOptions) -> str:
    items = [_render_one_warning(w, options) for w in _sorted_warnings(warnings)]
    return _container("warnings", {}, items, depth=2, options=options)


def _sorted_nodes(pkg: ContextPackage) -> list:
    seed_nodes = [n for n in pkg.nodes if n.role == "seed"]
    rest = sorted((n for n in pkg.nodes if n.role != "seed"), key=lambda n: (n.distance, n.id))
    return seed_nodes + rest


def _truncate_body(body: str, max_lines: int | None) -> str:
    if max_lines is None:
        return body
    lines = body.split("\n")
    if len(lines) <= max_lines:
        return body
    return "\n".join(lines[:max_lines])


def _render_one_node(node, options: RenderOptions) -> str:
    param_items = [_leaf("param", {"name": p.name, "type": p.type, "optional": p.optional}) for p in node.signature.params]
    returns_item = []
    if node.signature.returns is not None:
        returns_item = [_leaf("returns", {"type": node.signature.returns.type, "kind": node.signature.returns.kind})]
    signature = _container("signature", {}, [*param_items, *returns_item], depth=4, options=options)

    features = _leaf(
        "features",
        {"substance": node.features.substance, "form": node.features.form, "output": node.features.output, "role": node.features.role},
    )

    contract_items = []
    if node.contract is not None:
        contract_items = [
            _leaf(
                "contract",
                {
                    "target_id": node.contract.target_id,
                    "call_line": node.contract.call_line,
                    "unpacks": node.contract.unpacks,
                    "passes_args": node.contract.passes_args,
                },
            )
        ]

    body_items = []
    if options.include_bodies:
        body_items = [_leaf("body", {}, text=_cdata(_truncate_body(node.body, options.max_body_lines)))]

    items = [signature, features, *contract_items, *body_items]
    return _container(
        "node",
        {
            "id": node.id,
            "role": node.role,
            "distance": node.distance,
            "compression": node.compression,
            "cost": node.cost,
            "symbol_name": node.symbol_name,
            "symbol_kind": node.symbol_kind,
            "language": node.language,
            "file": node.file,
            "line": node.line,
            "end_line": node.end_line,
        },
        items,
        depth=3,
        options=options,
    )


def _render_nodes(pkg: ContextPackage, options: RenderOptions) -> str:
    items = [_render_one_node(n, options) for n in _sorted_nodes(pkg)]
    return _container("nodes", {}, items, depth=2, options=options)


def _sorted_edges(pkg: ContextPackage) -> list:
    return sorted(pkg.edges, key=lambda e: (e.from_node, e.to_node, e.type))


def _render_one_edge(edge) -> str:
    return _leaf(
        "edge",
        {
            "from": edge.from_node,
            "to": edge.to_node,
            "type": edge.type,
            "weight": edge.weight,
            "data_flow": edge.data_flow,
            "guard": edge.guard,
            "back_edge": edge.back_edge,
        },
    )


def _render_edges(pkg: ContextPackage, options: RenderOptions) -> str:
    items = [_render_one_edge(e) for e in _sorted_edges(pkg)]
    return _container("edges", {}, items, depth=2, options=options)


def _root_open(pkg: ContextPackage, options: RenderOptions) -> str:
    attrs: dict[str, object] = {"schema_version": options.schema_version}
    if options.include_timestamp and pkg.generated_at is not None:
        attrs["generated_at"] = pkg.generated_at
    if options.include_run_id and pkg.run_id is not None:
        attrs["run_id"] = pkg.run_id
    return f"<prism_context{_attrs(attrs)}>"


def render(pkg: ContextPackage, options: RenderOptions = RenderOptions()) -> str:
    """Pure function. Guarantees a byte-identical XML string for
    identical `(pkg, options)`. See this module's own docstring for the
    document shape and every ordering/CDATA/token-accounting rule."""
    metadata = _render_metadata(pkg, options)
    manifest = _render_manifest(pkg, options)
    coverage = _render_coverage(pkg, options)
    nodes = _render_nodes(pkg, options)
    edges = _render_edges(pkg, options)
    root_open = _root_open(pkg, options)

    # First pass: everything except <warnings>/<trailer>, purely to get
    # an accurate token count to decide BUDGET_OVERFLOW against.
    provisional_body = _join_children([metadata, manifest, coverage, nodes, edges], depth=1, options=options)
    provisional_tokens = count_tokens(root_open + provisional_body)

    warnings = list(pkg.warnings)
    already_flagged = any(w.code == "BUDGET_OVERFLOW" for w in warnings)
    if provisional_tokens > pkg.budget.tokens and not already_flagged:
        warnings.append(
            EnvelopeWarning(
                code="BUDGET_OVERFLOW",
                severity="high",
                message=(
                    f"rendered context uses {provisional_tokens} tokens, "
                    f"exceeding the requested budget of {pkg.budget.tokens}"
                ),
                details={"actual_tokens": provisional_tokens, "budget_tokens": pkg.budget.tokens},
            )
        )

    warnings_xml = _render_warnings(warnings, options)
    body_items = [metadata, manifest, coverage, warnings_xml, nodes, edges]
    body_without_trailer = _join_children(body_items, depth=1, options=options)
    actual_tokens = count_tokens(root_open + body_without_trailer)
    sha256 = hashlib.sha256((root_open + body_without_trailer).encode("utf-8")).hexdigest()

    trailer = _leaf(
        "trailer",
        {"node_count": len(pkg.nodes), "edge_count": len(pkg.edges), "sha256": sha256, "token_count": actual_tokens},
    )
    # Rejoin with trailer included as a proper sibling of the other 6
    # sections - a pure re-concatenation of already-fixed strings (never
    # reformats any of them), so this doesn't affect what was hashed
    # above; it only gets `<trailer>` correctly indented alongside them.
    full_body = _join_children([*body_items, trailer], depth=1, options=options)

    document = root_open + full_body + "</prism_context>"
    document = document.replace("\r\n", "\n").replace("\r", "\n")
    return document.rstrip("\n") + "\n"
