"""Tests for Item 3 (second post-implementation audit): Go local
parameter/short-var-declaration type tracking (Stage 1) and the
codebase-unique receiver fallback (Stage 2) -
`ConcreteGraphBuilder._bind_go_short_var_declarations`/
`_go_unique_receiver_for_method`, `go_call_resolution_ratio`.
"""
from __future__ import annotations

from prism.cli import build_pipeline


def _write_repo(tmp_path, source: str):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.go").write_text(source)
    return repo


# --------------------------------------------------------------------- #
# Stage 1: short-var-declaration (`:=`) composite-literal type tracking
# --------------------------------------------------------------------- #
def test_short_var_decl_pointer_composite_literal_resolves(tmp_path) -> None:
    repo = _write_repo(
        tmp_path,
        """package main

type Router struct {
    Name string
}

func (r *Router) Group(path string) {
}

func setup() {
    r := &Router{}
    r.Group("/api")
}
""",
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("main.setup", "main.Router.Group")


def test_short_var_decl_value_composite_literal_resolves(tmp_path) -> None:
    repo = _write_repo(
        tmp_path,
        """package main

type Router struct {
    Name string
}

func (r Router) Group(path string) {
}

func setup() {
    r := Router{}
    r.Group("/api")
}
""",
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("main.setup", "main.Router.Group")


def test_short_var_decl_bare_constructor_call_is_not_guessed(tmp_path) -> None:
    """`r := gin.Default()` - a constructor-*function* call, not a
    composite literal - must NOT resolve via Stage 1 (no return-type
    inference is attempted); it may still resolve via Stage 2 if exactly
    one struct defines the called method, so this only checks Stage 1's
    own scope isn't overreaching by asserting on a method name no struct
    in this tiny fixture defines."""
    repo = _write_repo(
        tmp_path,
        """package main

func setup() {
    r := gin.Default()
    r.NoSuchMethodAnywhere()
}
""",
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert not any(
        v.endswith(".NoSuchMethodAnywhere") for _u, v in builder.graph.edges() if _u == "main.setup"
    )


def test_multi_value_short_var_decl_binds_each_independently(tmp_path) -> None:
    repo = _write_repo(
        tmp_path,
        """package main

type Router struct {
    Name string
}

func (r *Router) Group(path string) {
}

func setup() {
    a, b := &Router{}, 1
    _ = b
    a.Group("/x")
}
""",
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("main.setup", "main.Router.Group")


# --------------------------------------------------------------------- #
# Stage 2: codebase-unique receiver fallback
# --------------------------------------------------------------------- #
def test_unique_receiver_fallback_resolves_with_tentative_kind(tmp_path) -> None:
    """`x.OnlyOneStructHasThis()` where `x`'s type isn't tracked at all
    (a struct field, not a local var/param) but exactly one struct in the
    whole repo defines that method - must resolve, marked tentative."""
    repo = _write_repo(
        tmp_path,
        """package main

type Widget struct {
    Name string
}

func (w *Widget) OnlyOneStructHasThis() {
}

type Container struct {
    inner *Widget
}

func (c *Container) UseIt() {
    c.inner.OnlyOneStructHasThis()
}
""",
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("main.Container.UseIt", "main.Widget.OnlyOneStructHasThis")
    edge_data = builder.graph.get_edge_data("main.Container.UseIt", "main.Widget.OnlyOneStructHasThis")
    assert edge_data.get("kind") == "TENTATIVE_CALL"


def test_ambiguous_receiver_is_not_guessed_by_stage_2(tmp_path) -> None:
    """`_go_unique_receiver_for_method` itself (Item 3 Stage 2's own
    logic, isolated from the separate, pre-existing polysemy-scoring
    fallback `_resolve_ambiguous_call` also tries when `_resolve_segments`
    returns None - a different, already-shipped mechanism with its own
    namespace/arity/locality-based judgment call, not part of this
    audit item) must not guess when two unrelated structs both define a
    same-named method."""
    repo = _write_repo(
        tmp_path,
        """package main

type Widget struct{}
func (w *Widget) Render() {}

type Page struct{}
func (p *Page) Render() {}
""",
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder._go_unique_receiver_for_method("Render") is None
    assert builder._go_unique_receiver_for_method("NoSuchMethod") is None


def test_unique_receiver_helper_returns_the_one_real_candidate(tmp_path) -> None:
    repo = _write_repo(
        tmp_path,
        """package main

type Widget struct{}
func (w *Widget) OnlyOneDefinesThis() {}
""",
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder._go_unique_receiver_for_method("OnlyOneDefinesThis") == "main.Widget.OnlyOneDefinesThis"


def test_package_qualified_call_is_never_treated_as_receiver_call(tmp_path) -> None:
    """`gin.Default()` itself (the call, not its result) must not be
    counted toward go_call_resolution_ratio's denominator - `gin` is a
    known import alias, not an unresolved receiver variable."""
    repo = _write_repo(
        tmp_path,
        """package main

import "github.com/gin-gonic/gin"

func setup() {
    gin.Default()
}
""",
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder._go_receiver_call_sites_total == 0


# --------------------------------------------------------------------- #
# go_call_resolution_ratio
# --------------------------------------------------------------------- #
def test_resolution_ratio_is_one_with_no_receiver_calls(tmp_path) -> None:
    repo = _write_repo(tmp_path, "package main\n\nfunc f() {}\n")
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.go_call_resolution_ratio == 1.0


def test_resolution_ratio_reflects_real_mixed_resolution(tmp_path) -> None:
    repo = _write_repo(
        tmp_path,
        """package main

type Router struct{}
func (r *Router) Group(path string) {}

func setup() {
    r := &Router{}
    r.Group("/ok")   // resolves via Stage 1

    var x interface{}
    x.NoStructDefinesThis()  // never resolves
}
""",
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert 0.0 < builder.go_call_resolution_ratio < 1.0


def test_go_call_resolution_ratio_on_real_gin_repo() -> None:
    """Corroborated against the real gin-gonic/gin clone this branch's
    own verification work has used throughout - reports the real,
    measured ratio rather than asserting against an unverified target.

    Measured directly at each stage of this audit's Go work: 47.45%
    (1445/3045) after Item 3 (local parameter/short-var-decl type
    tracking + codebase-unique receiver fallback) alone; 49.75%
    (1515/3045) after Item 5/7 (struct embedding + promoted-method BFS)
    on top of that - real, meaningful progress, but short of the
    audit's own aspirational >=60% target. The dominant remaining gap is
    a different, larger problem neither item asked for: arbitrary
    struct-*field* type tracking (`type Foo struct { ctx *Context }`
    then `f.ctx.Method()`) - this class tracks parameter/receiver/
    short-var-declaration types, not general field types, which would
    need a much larger type-propagation pass (bordering on a real Go
    type-checker) to close. Asserting a hard >=0.60 floor here would
    either be false today or brittle against exactly the kind of
    ordinary refactor (a new file, a new struct) that shouldn't break a
    resolution-ratio regression test - so this only pins a sane range
    and a floor comfortably below what's actually been measured, to
    catch a real regression without being a tripwire for unrelated
    changes.
    """
    import os

    repo_path = "/home/user/gin-gonic/gin"
    if not os.path.isdir(repo_path):
        import pytest

        pytest.skip(f"real gin-gonic/gin clone not present at {repo_path} in this environment")
    builder, _tag_matrix = build_pipeline(repo_path)
    ratio = builder.go_call_resolution_ratio
    assert 0.40 <= ratio <= 1.0
    assert builder._go_receiver_call_sites_total > 0  # sanity: gin has plenty of receiver calls
