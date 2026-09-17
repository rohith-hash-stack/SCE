# The `<prism_context>` envelope

The XML document `prism.surface.renderer.render` produces and
`prism.surface.parser.parse_context` losslessly reconstructs. This is the
external, versioned contract a model or tool consuming Prism's output
should rely on; `prism.surface.renderer`'s own module docstring is the
implementation-level detail (exact rendering mechanics, CDATA handling,
token accounting) behind it.

## Document shape (schema_version 2)

```xml
<prism_context generated_at="..." run_id="..." schema_version="2">
  <metadata>
    <engine commit="..." name="..." version="..."/>
    <seed file="..." line="1" symbol="..."/>
    <budget exact="true" tokenizer="cl100k_base" tokens="4000"/>
    <language files="3" primary="python" tier="1"/>
    <options><option key="..." value="..."/></options>
  </metadata>
  <causal_path seed="..." direction="forward">
    <stage order="1" symbol="..." distance="0.0" role="entry"/>
    <stage order="2" symbol="..." distance="1.0" role="transform"/>
    <stage order="3" symbol="..." distance="1.85" role="sink"/>
  </causal_path>
  <manifest ...><compression .../>...<distance_metric .../></manifest>
  <coverage ...><feature .../>...<gap .../>...</coverage>
  <warnings><warning code="..." severity="..."><message>...</message>
    <detail key="..." value="..."/></warning></warnings>
  <nodes><node ...><signature>...</signature><features .../>
    <contract .../><body><![CDATA[...]]></body></node></nodes>
  <edges><edge .../></edges>
  <trailer edge_count="..." node_count="..." sha256="..." token_count="..."/>
</prism_context>
```

Every top-level section is optional-in-principle except `<metadata>`,
`<manifest>`, `<coverage>`, `<nodes>`, `<edges>`, and `<trailer>`, which
are always present (empty containers self-close, e.g. `<edges/>`, rather
than being omitted). `<warnings>` and `<causal_path>` are the two
sections that can be genuinely absent from the document.

## `<causal_path>` (added in schema_version 2)

Zero or one block, placed immediately after `<metadata>` and before
`<manifest>`. Present for a single-seed, forward-chain retrieval (a
T02-style "explain this seed's own behavior" query); **absent** for a
blast-radius (upstream-caller) retrieval or an overview/multi-concern
one - never rendered as an empty element.

A forward chain from the seed to whichever reachable **sink** explains
why the seed matters, computed over the envelope's own already-packed
`<nodes>`/`<edges>` (never a symbol the envelope doesn't otherwise
describe):

- **Sink**: a packed symbol (other than the seed) whose feature mask
  carries a substance sink bit (`SINK_NETWORK_IO`, `SINK_DATABASE_IO`,
  `SINK_FILESYSTEM_IO`, or `SINK_PROCESS_IO` - `SINK_TIME_IO`,
  `SINK_RANDOMNESS`, and `SINK_PURE_COMPUTE` do not count), or whose
  output category is `Command` with no further outgoing
  `CALLS`/`INSTANTIATES` edge of its own.
- If one or more sinks are reachable from the seed: the shortest path
  (fewest hops, via the same `CALLS`/`INSTANTIATES` edges the distance
  metric uses) to a sink. Multiple equally-short candidates are broken
  first by the sink's own distance from the seed (closest first), then
  by qualified name, so the choice is fully deterministic.
- If no sink is reachable: the seed's own longest forward chain (the
  most-hops-away reachable packed symbol), same tie-break.
- Capped at 6 stages including the seed; a longer path is truncated to
  the first 6 and the block gets `truncated="true"` (the attribute is
  omitted entirely when not truncated).

Each `<stage>`:

| attribute  | meaning                                                          |
|------------|-------------------------------------------------------------------|
| `order`    | 1-indexed position in the path                                    |
| `symbol`   | fully-qualified name (matches a `<node id="...">` in `<nodes>`)   |
| `distance` | `dist_w` from the seed, rounded to 2 decimals (`0.0` for the seed) |
| `role`     | `entry` (stage 1), `sink`/`return` (the last stage, depending on whether it satisfies the sink definition above), `transform` (everything else) |

## `schema_version` and forward compatibility

`schema_version` is a plain integer attribute on the `<prism_context>`
root, defaulting to `2` (`prism.surface.renderer.RenderOptions.
schema_version`); a document that predates `<causal_path>` reads back as
`schema_version="1"`. The two versions differ by exactly one optional
element:

- A `schema_version 1` consumer parsing a `schema_version 2` document
  simply never looks for `<causal_path>` and is otherwise unaffected -
  every other element, attribute, and ordering rule is unchanged.
- A `schema_version 2` consumer parsing an older `schema_version 1`
  document (or any document with no `<causal_path>` at all) gets
  `ContextPackage.causal_path is None`, the same value it would get for
  a deliberately causal-path-less blast/overview retrieval - there is no
  separate "version too old" error path.

Bumping `schema_version` again in the future should follow the same
shape: one additive, optional element or attribute a consumer that
doesn't know about it can simply ignore, never a change to an existing
element's meaning.
