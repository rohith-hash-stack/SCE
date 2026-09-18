"""Phase H: Engine Extensibility Layer - verification suite.

Covers all four invariants from the Phase H directive:

1. Import alias resolution (Issue #46) - `import X as Y` / `from M
   import A as B` resolve call sites directly to their canonical
   target. **Empirically confirmed already fully solved** by the
   existing `LocalImportMap`/`_resolve_reference_chain` mechanism
   before any code in this phase was written (`ConcreteGraphBuilder.
   _resolve_reference_chain` consults `import_map.resolve(root)`
   first, ahead of same-module lookup, wildcard imports, and the G44
   ambiguous-call fallback - see `prism.graph.symbol_table.
   LocalImportMap.resolve`'s own Phase H comment). The two tests below
   are regression/characterization tests proving this, including
   against a same-simple-name decoy elsewhere in the repo (an alias
   always wins outright, not merely "usually scores highest") - no
   production change was needed in `symbol_table.py`/`concrete_
   builder.py` beyond documenting the connection to Issue #46.

2. Deterministic content-hash AST cache (Issue #45) - genuinely new
   (`prism.parser.cache`), confirmed via investigation to be an
   actual gap: `ConcreteGraphBuilder._parsed_files` is in-memory,
   per-instance only, and neither `prism.runtime.index_cache` nor
   `prism.runtime.contract_cache` cache parsed ASTs (both documented,
   deliberate scope exclusions - a tree-sitter `Tree`/`Node` isn't
   picklable).

3. Payload enrichment (Issue #35) - `NodeSignature.docstring`, a new
   field carrying a full, `inspect.cleandoc`-normalized docstring
   (`prism.graph.contracts.BehavioralContract.docstring`), distinct
   from the pre-existing one-line `doc_summary`. Params/POSIX paths
   were already correct (`_node_signature`'s params come from a
   real structured `Parameter` list, never a raw text span;
   `_relative_path` already POSIX-normalizes, Phase F/Issue #30) -
   verified here rather than re-implemented.

4. Engine hooks (Issue #36) - `prism.engine.PrismEngine`, a new,
   real production facade (no `PrismEngine` was reachable from `prism.
   cli`/`prism.mcp.server` before this phase - `benchmarks.engines.
   prism_engine.PrismEngine` is benchmark-harness-only and still is).
"""
from __future__ import annotations

import os

import pytest

from prism.cli import build_pipeline
from prism.engine import PrismEngine, QueryContext
from prism.graph.contracts import ContractExtractor
from prism.parser import cache as ast_cache
from prism.parser.tree_sitter_loader import parse_source
from prism.runtime.contract_cache import compute_or_load_contracts
from prism.surface.build import build_context_package
from prism.surface.models import ContextPackage


# ============================================================
# Invariant 1: import alias resolution (Issue #46)
# ============================================================

def test_import_as_alias_resolves_directly(tmp_path):
    """`import module as mod; mod.run()` resolves directly to the
    canonical `module.run` - and keeps doing so even with a same-
    simple-name decoy (`decoy.run`) elsewhere in the repo, proving the
    alias binding wins outright rather than merely scoring well against
    G44's polysemy heuristics."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "module.py").write_text("def run():\n    return 1\n")
    (repo / "decoy.py").write_text("def run():\n    return 999\n")
    (repo / "caller.py").write_text(
        "import module as mod\n\n\ndef entry():\n    return mod.run()\n"
    )

    builder, _ = build_pipeline(str(repo))
    targets = {v for _u, v, data in builder.graph.out_edges("caller.entry", data=True) if data.get("relation") == "CALLS"}
    assert targets == {"module.run"}


def test_from_import_alias_resolves_directly(tmp_path):
    """`from tools import helper as h; h()` resolves directly to the
    canonical `tools.helper` - again against a same-simple-name decoy,
    and additionally covering the aliased-class-import + instantiation
    + method-call shape (`from classes import Widget as W; W().render()`),
    since Issue #46 names "aliased calls" broadly, not just bare
    function calls."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tools.py").write_text("def helper():\n    return 2\n")
    (repo / "decoy.py").write_text("def helper():\n    return 999\n")
    (repo / "caller.py").write_text(
        "from tools import helper as h\n\n\ndef entry():\n    return h()\n"
    )
    builder, _ = build_pipeline(str(repo))
    targets = {v for _u, v, data in builder.graph.out_edges("caller.entry", data=True) if data.get("relation") == "CALLS"}
    assert targets == {"tools.helper"}

    repo2 = tmp_path / "repo2"
    repo2.mkdir()
    (repo2 / "classes.py").write_text("class Widget:\n    def render(self):\n        return 'w'\n")
    (repo2 / "caller.py").write_text(
        "from classes import Widget as W\n\n\ndef make():\n    w = W()\n    return w.render()\n"
    )
    builder2, _ = build_pipeline(str(repo2))
    edges2 = {(v, data.get("relation")) for _u, v, data in builder2.graph.out_edges("caller.make", data=True)}
    assert ("classes.Widget", "INSTANTIATES") in edges2
    assert ("classes.Widget.render", "CALLS") in edges2


# ============================================================
# Invariant 2: deterministic content-hash AST cache (Issue #45)
# ============================================================

@pytest.fixture(autouse=True)
def _clear_ast_cache():
    ast_cache.clear_cache()
    yield
    ast_cache.clear_cache()


def test_ast_cache_hit_on_identical_content(tmp_path, monkeypatch):
    monkeypatch.delenv("PRISM_DISABLE_CACHE", raising=False)
    path = tmp_path / "mod.py"
    path.write_text("def f():\n    return 1\n")

    first = ast_cache.parse_file_cached(str(path))
    assert ast_cache.cache_size() == 1
    second = ast_cache.parse_file_cached(str(path))

    # A real cache hit returns the SAME ParsedFile object - a fresh
    # tree-sitter parse would produce an equal-but-distinct object, so
    # identity (not just equal content) is what proves the parse was
    # actually skipped the second time, not merely produced the same
    # result independently.
    assert first is second
    assert ast_cache.cache_size() == 1


def test_ast_cache_invalidates_on_content_change(tmp_path, monkeypatch):
    monkeypatch.delenv("PRISM_DISABLE_CACHE", raising=False)
    path = tmp_path / "mod.py"
    path.write_text("def f():\n    return 1\n")

    first = ast_cache.parse_file_cached(str(path))
    key_before = ast_cache.cache_key(str(path), path.read_bytes())

    path.write_text("def f():\n    return 2\n")
    second = ast_cache.parse_file_cached(str(path))
    key_after = ast_cache.cache_key(str(path), path.read_bytes())

    assert key_before != key_after
    assert first is not second
    # Both the old and new content-hash entries are real, independent
    # cache rows - a content change is a real miss producing a new
    # entry, not an in-place eviction/overwrite of the old one.
    assert ast_cache.cache_size() == 2


def test_ast_cache_disabled_by_env_var(tmp_path, monkeypatch):
    path = tmp_path / "mod.py"
    path.write_text("def f():\n    return 1\n")

    monkeypatch.setenv("PRISM_DISABLE_CACHE", "1")
    first = ast_cache.parse_file_cached(str(path))
    second = ast_cache.parse_file_cached(str(path))

    assert first is not second, "PRISM_DISABLE_CACHE=1 must bypass the cache - every call is a fresh parse"
    assert ast_cache.cache_size() == 0, "a bypassed call must never populate the cache either"

    monkeypatch.delenv("PRISM_DISABLE_CACHE")
    third = ast_cache.parse_file_cached(str(path))
    fourth = ast_cache.parse_file_cached(str(path))
    assert third is fourth, "clearing the env var must restore normal caching immediately"


def test_ast_cache_wired_into_real_indexing_pipeline(tmp_path, monkeypatch):
    """Not just a unit test of the cache module in isolation - confirms
    `ConcreteGraphBuilder.pass1_collect_definitions` (the real indexing
    pipeline `build_pipeline` drives) actually calls through
    `parse_file_cached`, so a second `build_pipeline` call against an
    unchanged repo in the same process gets real cache hits for every
    file, not just a demonstration against a standalone helper."""
    monkeypatch.delenv("PRISM_DISABLE_CACHE", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n")

    ast_cache.clear_cache()
    build_pipeline(str(repo))
    size_after_first = ast_cache.cache_size()
    assert size_after_first >= 1

    build_pipeline(str(repo))
    size_after_second = ast_cache.cache_size()
    assert size_after_second == size_after_first, "re-indexing unchanged content must not grow the cache"


# ============================================================
# Invariant 3: payload enrichment (Issue #35)
# ============================================================

def test_payload_enrichment_signature_and_docstring(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "def widget(x, y=1):\n"
        '    """Compute a widget value.\n'
        "\n"
        "    Has an indented example below:\n"
        "\n"
        "        widget(1, 2)\n"
        "\n"
        "    Args:\n"
        "        x: the base value\n"
        "        y: an offset\n"
        '    """\n'
        "    return x + y\n"
    )
    builder, _ = build_pipeline(str(repo))
    contracts = compute_or_load_contracts(builder, str(repo))
    pkg = build_context_package(builder, "mod.widget", str(repo), 4000, contracts=contracts)
    node = next(n for n in pkg.nodes if n.id == "mod.widget")

    # Signature: clean, structured params - never raw text/body spans.
    assert [p.name for p in node.signature.params] == ["x", "y"]
    assert node.signature.params[1].optional is True

    # Docstring: full text, normalized, with the code block's own
    # relative indentation preserved (not flattened to one line the
    # way the pre-existing doc_summary field deliberately is).
    assert node.signature.docstring is not None
    assert node.signature.docstring.startswith("Compute a widget value.")
    assert "    widget(1, 2)" in node.signature.docstring
    assert "\n\n" in node.signature.docstring  # paragraph structure survived

    # doc_summary (pre-existing) stays a short, single-line summary -
    # this phase adds a field, it does not repurpose the old one.
    assert contracts["mod.widget"].doc_summary == "Compute a widget value."

    # POSIX-normalized path (already true pre-Phase-H - verified, not
    # re-implemented).
    assert "\\" not in node.file
    assert node.file == "mod.py"


def test_docstring_round_trips_through_xml_render_and_parse(tmp_path):
    from prism.surface.parser import parse_context
    from prism.surface.renderer import render

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text('def f():\n    """A real docstring."""\n    return 1\n')
    builder, _ = build_pipeline(str(repo))
    contracts = compute_or_load_contracts(builder, str(repo))
    pkg = build_context_package(builder, "mod.f", str(repo), 4000, contracts=contracts)

    xml = render(pkg)
    assert "<docstring>" in xml
    roundtripped = parse_context(xml)
    node = next(n for n in roundtripped.nodes if n.id == "mod.f")
    original = next(n for n in pkg.nodes if n.id == "mod.f")
    assert node.signature.docstring == original.signature.docstring == "A real docstring."


# ============================================================
# Invariant 4: engine extension hooks (Issue #36)
# ============================================================

def test_engine_hooks_execution(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n\n\ndef entry():\n    return f()\n")

    engine = PrismEngine.from_repo(str(repo))

    events: list[str] = []
    pre_contexts: list[QueryContext] = []
    post_packages: list[ContextPackage] = []

    def pre_a(ctx: QueryContext) -> None:
        events.append("pre_a")
        pre_contexts.append(ctx)

    def pre_b(ctx: QueryContext) -> None:
        events.append("pre_b")

    def post_a(pkg: ContextPackage) -> None:
        events.append("post_a")
        post_packages.append(pkg)
        # A hook mutating what it received must never reach the real
        # engine/return value - see the identity/mutation assertions
        # below.
        pkg.nodes.append("should-not-leak")  # type: ignore[arg-type]

    def post_b(pkg: ContextPackage) -> None:
        events.append("post_b")

    engine.register_pre_traversal_hook(pre_a)
    engine.register_pre_traversal_hook(pre_b)
    engine.register_post_packing_hook(post_a)
    engine.register_post_packing_hook(post_b)

    result = engine.retrieve("mod.entry", 4000)

    # Registration-order execution, pre-traversal hooks strictly before
    # post-packing hooks.
    assert events == ["pre_a", "pre_b", "post_a", "post_b"]

    # The hook received a real, correct snapshot of the query.
    assert pre_contexts[0].seed_id == "mod.entry"
    assert pre_contexts[0].budget_tokens == 4000

    # post_a's in-place mutation of the package it received never
    # reached the real return value - proof the engine hands hooks a
    # copy, not the live object.
    assert "should-not-leak" not in result.nodes
    assert post_packages[0] is not result
    assert [n.id for n in post_packages[0].nodes[: len(result.nodes)]] == [n.id for n in result.nodes]

    # A second retrieve() call re-fires every hook, still in order -
    # hooks are a standing registration, not a one-shot subscription.
    events.clear()
    engine.retrieve("mod.entry", 4000)
    assert events == ["pre_a", "pre_b", "post_a", "post_b"]


def test_engine_hooks_do_not_affect_engine_internal_state(tmp_path):
    """A hook is purely an observer: registering one, or one raising/
    mutating its input, must never change what a later `retrieve()`
    call (with no hooks involved at all) returns."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n")

    baseline_engine = PrismEngine.from_repo(str(repo))
    baseline = baseline_engine.retrieve("mod.f", 4000)

    hooked_engine = PrismEngine.from_repo(str(repo))

    def mutating_pre_hook(ctx: QueryContext) -> None:
        with pytest.raises(Exception):
            ctx.seed_id = "tampered"  # frozen dataclass - must raise

    def mutating_post_hook(pkg: ContextPackage) -> None:
        pkg.nodes.clear()  # mutate the copy into an empty list

    hooked_engine.register_pre_traversal_hook(mutating_pre_hook)
    hooked_engine.register_post_packing_hook(mutating_post_hook)

    result = hooked_engine.retrieve("mod.f", 4000)
    assert [n.id for n in result.nodes] == [n.id for n in baseline.nodes]

    # A second, hook-free retrieve from the SAME engine is also
    # unaffected by the earlier hooked call.
    result2 = hooked_engine.retrieve("mod.f", 4000)
    assert [n.id for n in result2.nodes] == [n.id for n in baseline.nodes]
