"""Phase J: Multi-Language Parity Hardening (TypeScript & Go) - verification
suite.

Investigated before implementing anything (this project's own established
discipline): TS heritage clauses (`extends`/`implements`) already emitted
real `EXTENDS`/`IMPLEMENTS` edges, `_mro_ancestors` already covered TS, and
`export {x} from './y'` / `export * from './y'` re-exports already resolved
end-to-end - none of that needed fixing. Go struct embedding, promoted-
method MRO resolution, and both pointer/value receiver styles were also
already fully correct. The real gaps, found by direct empirical repro
against synthetic fixtures rather than assumed from the directive's own
text:

1. TS/JS local variables were **never type-tracked for method-call
   resolution at all** - `const c = new Circle(); c.area()` produced no
   CALLS edge even for a method declared directly on `Circle` with no
   inheritance involved. Two independent causes: (a) JS/TS were absent
   from `instance_binding_langs` entirely, so `_build_function_instance_
   map`/`_build_class_instance_map` never ran for these languages; (b)
   `_constructor_call_segments` had no case for JS/TS's own `new_
   expression` grammar node at all (only a bare call, Python's idiom, or
   Java/C#'s `object_creation_expression`). Fixed in `concrete_builder.py`.
2. A Go `go f()` goroutine call resolved its `CALLS` edge correctly but
   was never distinguished from an ordinary synchronous call in
   `call_kind` metadata - `_call_kind`'s fire-and-forget check only
   recognized `expression_statement`, never `go_statement`. Fixed in
   `call_site.py`.
3. Go channel send (`ch <- value`) / receive (`<-ch`) had zero
   recognition anywhere - not even partial scaffolding. Added narrow,
   same-function-body-only tracking to `_data_flow_common.py`
   (deliberately not cross-function/cross-goroutine - see that module's
   own updated docstring for why).
4. `typeorm` was the one real gap among the 8 named substance-sink
   patterns; the other 7 were already fully covered in `canonical_
   sinks.py`. Added.
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.graph.call_site import compute_call_site_context
from prism.semantics.bitmask import FeatureBit
from prism.semantics.extractor import compute_feature_masks
from prism.traversal.continuous_dijkstra import compute_topological_distances
from prism.traversal.data_flow_go import compute_go_data_flow_edges


# ============================================================
# Invariant 1: TypeScript heritage & instance binding
# ============================================================

def test_ts_interface_implementation_generates_extends_edge(tmp_path):
    """Already-working behavior (Issue #17-adjacent, pre-Phase-J) -
    characterized here as a regression guard, not a new fix: a TS class
    `implements` an interface, and the resulting edge is real."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "base.ts").write_text("export interface Shape {\n    area(): number;\n}\n")
    (repo / "impl.ts").write_text(
        "import { Shape } from './base';\n\n"
        "export class Circle implements Shape {\n"
        "    area(): number {\n        return 3.14;\n    }\n"
        "}\n"
    )
    builder, _ = build_pipeline(str(repo), use_cache=False)
    edges = {(v, data.get("relation")) for _u, v, data in builder.graph.out_edges("impl.Circle", data=True)}
    assert ("base.Shape", "IMPLEMENTS") in edges


def test_ts_reexport_resolves_canonical_symbol(tmp_path):
    """Already-working behavior, characterized here: `export { foo } from
    './bar'` resolves a caller importing through the barrel to the real
    definition, not the barrel file itself."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "real.ts").write_text("export function realFunc(): number {\n    return 1;\n}\n")
    (repo / "barrel.ts").write_text("export { realFunc } from './real';\n")
    (repo / "caller.ts").write_text(
        "import { realFunc } from './barrel';\n\n"
        "export function caller(): number {\n    return realFunc();\n}\n"
    )
    builder, _ = build_pipeline(str(repo), use_cache=False)
    targets = {v for _u, v, data in builder.graph.out_edges("caller.caller", data=True) if data.get("relation") == "CALLS"}
    assert targets == {"real.realFunc"}


def test_ts_local_variable_instance_binding_direct_method(tmp_path):
    """The real gap this phase fixed: a method call through a `const`/
    `let`-declared local variable, constructed via `new Foo()`, resolved
    to nothing at all before this fix - not even for a method declared
    directly on the constructed class."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.ts").write_text(
        "export class Circle {\n"
        "    area(): number {\n        return 3.14;\n    }\n"
        "}\n\n"
        "export function direct(): number {\n"
        "    const c = new Circle();\n"
        "    return c.area();\n"
        "}\n"
    )
    builder, _ = build_pipeline(str(repo), use_cache=False)
    targets = {v for _u, v, data in builder.graph.out_edges("mod.direct", data=True) if data.get("relation") == "CALLS"}
    assert targets == {"mod.Circle.area"}


def test_ts_local_variable_instance_binding_inherited_method(tmp_path):
    """Same fix, the inherited-method case: `b.render()` where `Button`
    extends `BaseWidget` and never overrides `render` must resolve to
    the real base method via the existing MRO walk, not a guessed,
    nonexistent `Button.render`."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "base.ts").write_text(
        "export class BaseWidget {\n"
        "    render(): string {\n        return 'base';\n    }\n"
        "}\n"
    )
    (repo / "impl.ts").write_text(
        "import { BaseWidget } from './base';\n\n"
        "export class Button extends BaseWidget {\n"
        "    click(): void {}\n"
        "}\n"
    )
    (repo / "caller.ts").write_text(
        "import { Button } from './impl';\n\n"
        "export function inherited(): string {\n"
        "    const b = new Button();\n"
        "    return b.render();\n"
        "}\n"
    )
    builder, _ = build_pipeline(str(repo), use_cache=False)
    targets = {v for _u, v, data in builder.graph.out_edges("caller.inherited", data=True) if data.get("relation") == "CALLS"}
    assert "base.BaseWidget.render" in targets
    assert "impl.Button.render" not in targets


def test_ts_qualified_new_expression_resolves(tmp_path):
    """`new pkg.Foo()` (a member-expression constructor target, not a
    bare identifier) must also bind - `_constructor_call_segments`'s new
    `new_expression` case reuses `flatten_reference_chain`, which
    already handles the multi-segment case generically."""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "index.ts").write_text(
        "export class Widget {\n"
        "    render(): string {\n        return 'w';\n    }\n"
        "}\n"
    )
    (repo / "caller.ts").write_text(
        "import * as pkg from './pkg/index';\n\n"
        "export function use(): string {\n"
        "    const w = new pkg.Widget();\n"
        "    return w.render();\n"
        "}\n"
    )
    builder, _ = build_pipeline(str(repo), use_cache=False)
    targets = {v for _u, v, data in builder.graph.out_edges("caller.use", data=True) if data.get("relation") == "CALLS"}
    assert "pkg.index.Widget.render" in targets


# ============================================================
# Invariant 2: Go struct embedding & pointer receivers
# ============================================================

def test_go_embedded_struct_method_resolves_via_mro(tmp_path):
    """Already-working behavior (verified, not fixed), characterized
    here: a call to a method defined only on an embedded struct resolves
    directly via `_go_promoted_method`'s BFS over `EMBEDS` edges."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.go").write_text(
        "package main\n\n"
        "type BaseServer struct{}\n\n"
        "func (b *BaseServer) Start() {}\n\n"
        "type Server struct {\n\tBaseServer\n\tPort int\n}\n\n"
        "func Run(s *Server) {\n\ts.Start()\n}\n"
    )
    builder, _ = build_pipeline(str(repo), use_cache=False)
    edges = {(v, data.get("relation")) for _u, v, data in builder.graph.out_edges("main.Server", data=True)}
    assert ("main.BaseServer", "EMBEDS") in edges
    targets = {v for _u, v, data in builder.graph.out_edges("main.Run", data=True) if data.get("relation") == "CALLS"}
    assert "main.BaseServer.Start" in targets


def test_go_pointer_receiver_binding(tmp_path):
    """Already-working behavior, characterized here: both pointer
    (`*T`) and value (`T`) receiver styles resolve uniformly, for both
    a direct call and (combined with the embedding test above) a
    promoted one."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.go").write_text(
        "package main\n\n"
        "type Widget struct{}\n\n"
        "func (w *Widget) PointerMethod() {}\n\n"
        "func (w Widget) ValueMethod() {}\n\n"
        "func UsePointer(w *Widget) {\n\tw.PointerMethod()\n}\n\n"
        "func UseValue(w Widget) {\n\tw.ValueMethod()\n}\n"
    )
    builder, _ = build_pipeline(str(repo), use_cache=False)
    pointer_targets = {v for _u, v, data in builder.graph.out_edges("main.UsePointer", data=True) if data.get("relation") == "CALLS"}
    value_targets = {v for _u, v, data in builder.graph.out_edges("main.UseValue", data=True) if data.get("relation") == "CALLS"}
    assert "main.Widget.PointerMethod" in pointer_targets
    assert "main.Widget.ValueMethod" in value_targets


# ============================================================
# Invariant 3: concurrency & data flow
# ============================================================

def test_go_goroutine_call_kind_is_fire_and_forget(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.go").write_text(
        "package main\n\n"
        "func Launch() {\n\tgo doWork()\n}\n\n"
        "func doWork() {}\n"
    )
    builder, _ = build_pipeline(str(repo), use_cache=False)
    edge_data = None
    for u, v, data in builder.graph.out_edges("main.Launch", data=True):
        if v == "main.doWork":
            edge_data = data
    assert edge_data is not None
    assert edge_data.get("relation") == "CALLS"
    assert edge_data.get("call_kind") == "fire_and_forget"


def test_go_channel_data_flow_within_one_function(tmp_path):
    """The narrow, same-function-body channel tracking this phase adds:
    a value sent on a channel and received back within the same
    function still produces a real, resolved data-flow edge."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.go").write_text(
        "package main\n\n"
        "func produce() int {\n\treturn 1\n}\n\n"
        "func consume(x int) {}\n\n"
        "func run() {\n"
        "\tch := make(chan int)\n"
        "\tch <- produce()\n"
        "\tresult := <-ch\n"
        "\tconsume(result)\n"
        "}\n"
    )
    builder, _ = build_pipeline(str(repo), use_cache=False)
    edges = compute_go_data_flow_edges(builder)
    assert ("main.produce", "main.consume") in edges


def test_go_channel_cross_function_flow_is_not_guessed(tmp_path):
    """Deliberate scope boundary, verified rather than assumed: a
    channel passed to another function is NOT tracked - this module
    refuses to guess at cross-function/cross-goroutine flow rather than
    silently fabricate an edge with no real basis."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.go").write_text(
        "package main\n\n"
        "func produce() int {\n\treturn 1\n}\n\n"
        "func consume(x int) {}\n\n"
        "func worker(ch chan int) {\n"
        "\tresult := <-ch\n"
        "\tconsume(result)\n"
        "}\n\n"
        "func run() {\n"
        "\tch := make(chan int)\n"
        "\tgo worker(ch)\n"
        "\tch <- produce()\n"
        "}\n"
    )
    builder, _ = build_pipeline(str(repo), use_cache=False)
    edges = compute_go_data_flow_edges(builder)
    assert ("main.produce", "main.consume") not in edges


def test_ts_async_await_data_flow_still_correct(tmp_path):
    """Not a new fix - the existing Phase B `_unwrap_await` mechanism
    already covers TS; verified here as a Phase J regression guard
    since this phase touched the same shared module."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.ts").write_text(
        "async function fetchData(): Promise<number> {\n    return 1;\n}\n\n"
        "function useData(x: number): void {}\n\n"
        "async function run(): Promise<void> {\n"
        "    const data = await fetchData();\n"
        "    useData(data);\n"
        "}\n"
    )
    builder, _ = build_pipeline(str(repo), use_cache=False)
    from prism.traversal.data_flow_ts import compute_ts_data_flow_edges

    edges = compute_ts_data_flow_edges(builder)
    assert ("mod.fetchData", "mod.useData") in edges


# ============================================================
# Invariant 4: substance/sink mask parity
# ============================================================

def test_ts_go_substance_sink_mask_parity(tmp_path):
    """TS `fs.readFile` and Go `os.Open` both trigger SINK_FILESYSTEM_IO
    - already-covered patterns (not a Phase J fix), verified directly
    per the directive's own required test."""
    ts_repo = tmp_path / "ts_repo"
    ts_repo.mkdir()
    (ts_repo / "mod.ts").write_text(
        "import * as fs from 'fs';\n\n"
        "export function readIt(path: string): void {\n"
        "    fs.readFile(path, () => {});\n"
        "}\n"
    )
    go_repo = tmp_path / "go_repo"
    go_repo.mkdir()
    (go_repo / "main.go").write_text(
        "package main\n\n"
        "import \"os\"\n\n"
        "func ReadIt(path string) {\n"
        "\tos.Open(path)\n"
        "}\n"
    )

    ts_builder, _ = build_pipeline(str(ts_repo), use_cache=False)
    ts_masks = compute_feature_masks(ts_builder)
    assert ts_masks.get("mod.readIt", 0) & int(FeatureBit.SINK_FILESYSTEM_IO)

    go_builder, _ = build_pipeline(str(go_repo), use_cache=False)
    go_masks = compute_feature_masks(go_builder)
    assert go_masks.get("main.ReadIt", 0) & int(FeatureBit.SINK_FILESYSTEM_IO)


def test_typeorm_database_sink_recognized(tmp_path):
    """The one genuine gap among the 8 named sink patterns - added in
    canonical_sinks.py."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "db.ts").write_text(
        "import { DataSource } from 'typeorm';\n\n"
        "export async function runQuery(): Promise<unknown> {\n"
        "    const ds = new DataSource({ type: 'postgres' });\n"
        "    return ds.query('SELECT 1');\n"
        "}\n"
    )
    builder, _ = build_pipeline(str(repo), use_cache=False)
    masks = compute_feature_masks(builder)
    assert masks.get("db.runQuery", 0) & int(FeatureBit.SINK_DATABASE_IO)


# ============================================================
# Determinism across repeated builds (matching Phase I's own bar)
# ============================================================

def test_multilang_traversal_determinism(tmp_path):
    """Traversal distances on synthetic TS and Go repos produce
    identical metrics across repeated builds - the same repeated-build
    stability bar Phase I already established for Python, applied here
    to the two languages this phase actually touched."""
    ts_repo = tmp_path / "ts_repo"
    ts_repo.mkdir()
    (ts_repo / "mod.ts").write_text(
        "export class Base {\n"
        "    run(): number {\n        return 1;\n    }\n"
        "}\n\n"
        "export class Sub extends Base {}\n\n"
        "export function use(): number {\n"
        "    const s = new Sub();\n"
        "    return s.run();\n"
        "}\n"
    )
    go_repo = tmp_path / "go_repo"
    go_repo.mkdir()
    (go_repo / "main.go").write_text(
        "package main\n\n"
        "type Base struct{}\n\n"
        "func (b *Base) Run() int { return 1 }\n\n"
        "type Sub struct {\n\tBase\n}\n\n"
        "func Use(s *Sub) int {\n\treturn s.Run()\n}\n"
    )

    for repo_path, seed in ((str(ts_repo), "mod.use"), (str(go_repo), "main.Use")):
        results = set()
        for _ in range(3):
            builder, _ = build_pipeline(repo_path, use_cache=False)
            distances = compute_topological_distances(builder, seed)
            results.add(tuple(sorted(distances.items())))
        assert len(results) == 1, f"{repo_path}: distances varied across repeated builds: {results}"
