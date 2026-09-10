"""Tests for Items 5 & 7 (second post-implementation audit): Go struct
embedding (`EMBEDS` edges, `ConcreteGraphBuilder._link_go_embeds`) and
method promotion via BFS shadowing rules (`_go_promoted_method`).
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.slicer.distance import RELATION_STRUCTURAL_WEIGHT, RELATION_TENTATIVE_CALL_WEIGHT


def _write_repo(tmp_path, source: str):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.go").write_text(source)
    return repo


# --------------------------------------------------------------------- #
# Item 5: EMBEDS edges
# --------------------------------------------------------------------- #
def test_anonymous_field_creates_embeds_edge(tmp_path) -> None:
    repo = _write_repo(
        tmp_path,
        """package main

type RouterGroup struct {
    basePath string
}

type Engine struct {
    RouterGroup
    trees int
}
""",
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("main.Engine", "main.RouterGroup")
    assert builder.graph.get_edge_data("main.Engine", "main.RouterGroup")["relation"] == "EMBEDS"


def test_pointer_embedded_field_creates_embeds_edge(tmp_path) -> None:
    repo = _write_repo(
        tmp_path,
        """package main

type Pool struct{}

type Engine struct {
    *Pool
}
""",
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("main.Engine", "main.Pool")


def test_named_field_does_not_create_embeds_edge(tmp_path) -> None:
    repo = _write_repo(
        tmp_path,
        """package main

type Widget struct{}

type Container struct {
    widget Widget
}
""",
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert not builder.graph.has_edge("main.Container", "main.Widget")


def test_embeds_relation_weighted_like_extends() -> None:
    assert RELATION_STRUCTURAL_WEIGHT["EMBEDS"] == RELATION_STRUCTURAL_WEIGHT["EXTENDS"] == 0.85


# --------------------------------------------------------------------- #
# Item 7: promoted-method resolution and shadowing
# --------------------------------------------------------------------- #
def test_promoted_method_resolves_through_embedding(tmp_path) -> None:
    repo = _write_repo(
        tmp_path,
        """package main

type RouterGroup struct{}

func (g *RouterGroup) Use(middleware string) {
}

type Engine struct {
    RouterGroup
}

func setup() {
    e := &Engine{}
    e.Use("logger")
}
""",
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("main.setup", "main.RouterGroup.Use")


def test_direct_method_shadows_promoted_one(tmp_path) -> None:
    """A method declared directly on the outer type wins over a
    same-named promoted one from an embedded type - real Go shadowing."""
    repo = _write_repo(
        tmp_path,
        """package main

type RouterGroup struct{}

func (g *RouterGroup) Use(middleware string) {
}

type Engine struct {
    RouterGroup
}

func (e *Engine) Use(middleware string) {
}

func setup() {
    e := &Engine{}
    e.Use("logger")
}
""",
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("main.setup", "main.Engine.Use")
    assert not builder.graph.has_edge("main.setup", "main.RouterGroup.Use")


def test_nearest_depth_wins_over_deeper_embedded_method(tmp_path) -> None:
    repo = _write_repo(
        tmp_path,
        """package main

type Base struct{}

func (b *Base) M() {
}

type Middle struct {
    Base
}

func (m *Middle) M() {
}

type Outer struct {
    Middle
}

func setup() {
    o := &Outer{}
    o.M()
}
""",
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("main.setup", "main.Middle.M")
    assert not builder.graph.has_edge("main.setup", "main.Base.M")


def test_same_depth_collision_is_ambiguous_and_not_guessed(tmp_path) -> None:
    repo = _write_repo(
        tmp_path,
        """package main

type A struct{}
func (a *A) M() {}

type B struct{}
func (b *B) M() {}

type Outer struct {
    A
    B
}

func setup() {
    o := &Outer{}
    o.M()
}
""",
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert not builder.graph.has_edge("main.setup", "main.A.M")
    assert not builder.graph.has_edge("main.setup", "main.B.M")
    assert builder._go_promoted_method("main.Outer", "M") is None
    assert builder._last_go_embedded_collision == "M"


def test_promoted_method_helper_returns_none_for_no_match(tmp_path) -> None:
    repo = _write_repo(
        tmp_path,
        """package main

type Widget struct{}
""",
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder._go_promoted_method("main.Widget", "NoSuchMethod") is None
    assert builder._last_go_embedded_collision is None


def test_cross_file_embedding_resolves_via_repo_wide_type_lookup(tmp_path) -> None:
    """Reproduces the real gin-gonic/gin shape directly: Go's per-file
    module derivation means a struct embedded from a *different* file
    doesn't resolve via the naive same-module lookup alone -
    _resolve_go_type_name's repo-wide fallback is what makes this work."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "routergroup.go").write_text(
        "package main\n\ntype RouterGroup struct{}\n\nfunc (g *RouterGroup) Use(m string) {}\n"
    )
    (repo / "engine.go").write_text(
        'package main\n\ntype Engine struct {\n    RouterGroup\n}\n\nfunc setup() {\n    e := &Engine{}\n    e.Use("x")\n}\n'
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("engine.Engine", "routergroup.RouterGroup")
    assert builder.graph.has_edge("engine.setup", "routergroup.RouterGroup.Use")


def test_tentative_call_weight_is_between_extends_and_calls() -> None:
    assert RELATION_TENTATIVE_CALL_WEIGHT < RELATION_STRUCTURAL_WEIGHT["CALLS"]
