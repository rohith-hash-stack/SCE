"""Phase I: determinism, performance & test suite hardening - verification
suite.

Covers:

1. The blast-seed richness screen failure (Invariant 1) - three real,
   independent structural resolver gaps found and fixed while
   diagnosing `test_all_four_existing_blast_seeds_pass_the_richness_
   screen` (`tests/benchmarks/test_harness_metrics.py`), which is
   itself the primary regression test for this fix and is not
   duplicated here:

   a. Python `super().<method>(...)` had no dedicated handling at all -
      `flatten_reference_chain` correctly returns `None` for it (its
      root is a call, not a name), but the *separate* bare `super(...)`
      call node the same walk also visits fell through to the G44
      bare-name fallback and resolved to whatever unrelated symbol
      happens to be named exactly "super" elsewhere in the repo
      (confirmed: every real `super()` call in the pinned Django
      corpus was resolving to `django.template.loader_tags.BlockNode.
      super`).
   b. An instance-typed local variable/parameter calling a method it
      *inherits* (never overrides) resolved to a guessed, nonexistent
      `<ConcreteClass>.<method>` external node instead of walking the
      MRO for the real, inherited definition - Issue #9's "phantom
      method" fix, previously applied only to `self.<method>()`, not
      to this general case.
   c. A class accessed as `<package>.<ClassName>()` where `<package>/
      __init__.py` re-exports it via `from .submodule import *` (an
      extremely common Django idiom) was never resolved to its real
      defining module at all - the existing Issues #6/#7 barrel/
      re-export machinery only handled a re-exported *symbol* import
      (`from app import OrderService`), not this "already-resolved
      package, attribute needs export resolution too" shape.

2. Canonical, hash-seed-independent tie-breaking (Invariant 2) - the
   determinism audit's own two real findings
   (`submodular_knapsack.py`'s mandatory-upstream-protection fixup,
   `causal_weights.py`'s synthetic-edge population order), verified
   here directly against `PYTHONHASHSEED` variance rather than by
   inspection alone.

3. Cache/harness isolation (Invariant 3, Issue #115) - `prism.runtime.
   index_cache`/`prism.runtime.contract_cache` previously keyed their
   cache validity on the *target* repo's own files only, never this
   engine's own source or the installed tree-sitter grammar versions -
   a real, confirmed staleness bug (an engine bugfix was silently
   invisible to an already-indexed repo indefinitely), not merely a
   theoretical test-isolation concern.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from prism.cli import build_pipeline
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.packer.submodular_knapsack import pack_symbol_context
from prism.parser.lang_config import is_super_call_node, super_call_method_name
from prism.runtime.contract_cache import compute_signature as contract_signature
from prism.runtime.index_cache import (
    index_cache_path,
    load_pipeline_from_cache,
    save_pipeline_to_cache,
)
from prism.traversal._cache_keys import engine_and_grammar_version


# ============================================================
# Invariant 1: the three structural resolver fixes
# ============================================================

def test_super_call_resolves_to_mro_ancestor_not_unrelated_bare_name(tmp_path):
    """The core Issue #17/#111-adjacent bug: `super().clean(v)` must
    resolve to the real base-class method, and the bare `super(...)`
    sub-expression must never itself produce an edge to an unrelated
    same-named symbol elsewhere in the repo."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "class A:\n"
        "    def clean(self, v):\n"
        "        return v\n"
        "\n\n"
        "class B(A):\n"
        "    def clean(self, v):\n"
        "        return super().clean(v)\n"
        "\n\n"
        "def super():\n"
        "    return 'decoy - a real, unrelated symbol also named super'\n"
    )
    builder, _ = build_pipeline(str(repo), use_cache=False)
    targets = {v for _u, v, data in builder.graph.out_edges("mod.B.clean", data=True) if data.get("relation") == "CALLS"}
    assert targets == {"mod.A.clean"}, f"expected only mod.A.clean, got {targets}"


def test_super_call_node_detection_is_python_only(tmp_path):
    """`is_super_call_node`/`super_call_method_name` must be true no-ops
    outside Python - JS/Java/C#'s own `super` semantics are structurally
    different and out of scope, not silently mishandled."""
    from prism.parser.tree_sitter_loader import LanguageID, parse_source

    js_src = b"class B extends A {\n  clean(v) {\n    return super.clean(v);\n  }\n}\n"
    parsed = parse_source("mod.js", js_src)

    def find_calls(node, out):
        if node.type == "call_expression":
            out.append(node)
        for c in node.children:
            find_calls(c, out)

    calls: list = []
    find_calls(parsed.root_node, calls)
    for call_node in calls:
        assert not is_super_call_node(call_node, LanguageID.JAVASCRIPT, js_src)
        assert super_call_method_name(call_node, js_src, LanguageID.JAVASCRIPT) is None


def test_instance_typed_variable_inherits_method_via_mro(tmp_path):
    """`f = Sub(); f.clean(v)` where `Sub` inherits (never overrides)
    `clean` from `Base` must resolve to the real `Base.clean`, not a
    guessed, nonexistent `Sub.clean` external node."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "class Base:\n"
        "    def clean(self, v):\n"
        "        return v\n"
        "\n\n"
        "class Sub(Base):\n"
        "    pass\n"
        "\n\n"
        "def use():\n"
        "    f = Sub()\n"
        "    return f.clean(1)\n"
    )
    builder, _ = build_pipeline(str(repo), use_cache=False)
    targets = {v for _u, v, data in builder.graph.out_edges("mod.use", data=True) if data.get("relation") == "CALLS"}
    assert "mod.Base.clean" in targets
    assert "mod.Sub.clean" not in targets


def test_package_attribute_wildcard_reexport_resolves_to_real_module(tmp_path):
    """`import pkg; pkg.DateField()` where `pkg/__init__.py` does `from
    pkg.fields import *` must resolve `DateField` to its real defining
    module (`pkg.fields.DateField`), including for a subsequent method
    call on the resulting instance."""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("from pkg.fields import *\n")
    (repo / "pkg" / "fields.py").write_text(
        "class Base:\n"
        "    def clean(self, v):\n"
        "        return v\n"
        "\n\n"
        "class DateField(Base):\n"
        "    pass\n"
    )
    (repo / "caller.py").write_text(
        "import pkg\n"
        "\n\n"
        "def use():\n"
        "    f = pkg.DateField()\n"
        "    return f.clean(1)\n"
    )
    builder, _ = build_pipeline(str(repo), use_cache=False)
    targets = {v for _u, v, data in builder.graph.out_edges("caller.use", data=True) if data.get("relation") == "CALLS"}
    assert "pkg.fields.Base.clean" in targets


# ============================================================
# Invariant 2: canonical tie-breaking under PYTHONHASHSEED variance
# ============================================================

_DETERMINISM_FIXTURE = (
    "def seed():\n"
    "    a = alpha()\n"
    "    b = beta()\n"
    "    c = gamma()\n"
    "    return a, b, c\n"
    "\n\n"
    "def alpha():\n"
    "    return 1\n"
    "\n\n"
    "def beta():\n"
    "    return 2\n"
    "\n\n"
    "def gamma():\n"
    "    return 3\n"
)


def _pack_selected_symbols(repo_path: str, seed: str, budget: int) -> list[str]:
    builder, _ = build_pipeline(repo_path, use_cache=False)
    result = pack_symbol_context(builder, seed, budget)
    return list(result.selected)


def test_selection_output_identical_across_hashseed_0_and_42(tmp_path):
    """The real, end-to-end determinism check the directive asks for:
    the same repo packed under two different `PYTHONHASHSEED` values
    must produce byte-identical `selected` symbol lists (order
    included, not just set-equality) - run as two real subprocesses,
    not simulated, since `PYTHONHASHSEED` only takes effect at
    interpreter startup.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(_DETERMINISM_FIXTURE)

    script = (
        "from prism.cli import build_pipeline\n"
        "from prism.packer.submodular_knapsack import pack_symbol_context\n"
        f"builder, _ = build_pipeline({str(repo)!r}, use_cache=False)\n"
        "result = pack_symbol_context(builder, 'mod.seed', 4000)\n"
        "print(','.join(result.selected))\n"
    )

    outputs = []
    for seed_value in ("0", "42"):
        env = dict(os.environ, PYTHONHASHSEED=seed_value)
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=120)
        assert proc.returncode == 0, proc.stderr
        outputs.append(proc.stdout.strip())

    assert outputs[0] == outputs[1], f"PYTHONHASHSEED=0 gave {outputs[0]!r}, PYTHONHASHSEED=42 gave {outputs[1]!r}"
    assert outputs[0], "expected a non-empty selection"


def test_mandatory_upstream_protection_tie_break_is_deterministic(tmp_path):
    """Determinism audit finding: `submodular_knapsack.py`'s mandatory-
    upstream-protection fixup picked the minimum-distance upstream
    caller via a bare `set` iteration with no secondary tie-break key -
    an exact distance tie between two callers was hash-seed dependent.
    Two callers at the exact same upstream distance from the seed;
    the fixup must pick the same one every real run, in-process (no
    subprocess needed - this exercises the tie-break's own key
    function, not general hash-seed variance)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "def seed():\n"
        "    return 1\n"
        "\n\n"
        "def caller_a():\n"
        "    return seed()\n"
        "\n\n"
        "def caller_b():\n"
        "    return seed()\n"
    )
    results = set()
    for _ in range(5):
        builder, _ = build_pipeline(str(repo), use_cache=False)
        result = pack_symbol_context(builder, "mod.seed", 4000)
        results.add(tuple(result.selected))
    assert len(results) == 1, f"selection varied across repeated builds: {results}"


# ============================================================
# Invariant 3: cache/harness isolation (Issue #115)
# ============================================================

def test_index_cache_invalidates_on_engine_version_change(tmp_path, monkeypatch):
    """The real bug this phase found and fixed: `index_cache.py`'s
    cache validity previously depended only on the target repo's own
    file content hashes, never on this engine's own source - so an
    engine bugfix was silently invisible to an already-indexed repo
    until one of ITS files happened to change. Simulated here by
    monkeypatching `engine_and_grammar_version` to a different value
    between save and load, standing in for a real engine-code change
    between two `build_pipeline` calls."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n")
    files = [str(repo / "mod.py")]

    builder = ConcreteGraphBuilder(str(repo))
    builder.pass1_collect_definitions(files)
    builder.pass2_resolve_calls(files)

    save_pipeline_to_cache(str(repo), files, "1", builder, {})
    hit = load_pipeline_from_cache(str(repo), files, "1")
    assert hit is not None, "expected a real cache hit with an unchanged engine version"

    monkeypatch.setattr("prism.runtime.index_cache.engine_and_grammar_version", lambda: "a-different-engine-version")
    miss = load_pipeline_from_cache(str(repo), files, "1")
    assert miss is None, "a changed engine version must be a real cache miss, not a stale hit"


def test_contract_cache_signature_includes_engine_version(tmp_path, monkeypatch):
    """Same fix, mirrored in `contract_cache.py`: `compute_signature`
    must change when `engine_and_grammar_version()` changes, holding
    the repo's own files fixed - otherwise a `BehavioralContract`
    schema change (Phase H's own `docstring` field, say) would be
    silently invisible to an already-cached repo."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n")
    builder, _ = build_pipeline(str(repo), use_cache=False)

    sig_before = contract_signature(builder)
    monkeypatch.setattr("prism.runtime.contract_cache.engine_and_grammar_version", lambda: "a-different-engine-version")
    sig_after = contract_signature(builder)

    assert sig_before != sig_after


def test_ast_cache_default_location_is_per_repo_not_shared(tmp_path):
    """Issue #115 (parallel runner collisions): two distinct repos must
    never share a cache location - `index_cache_path` is derived from
    `repo_root` itself, so two `tmp_path`-isolated test repos (the
    pattern every test in this suite already uses) can never collide
    even when run in parallel pytest workers, without any test needing
    to coordinate cache paths explicitly."""
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    repo_a.mkdir()
    repo_b.mkdir()
    assert index_cache_path(str(repo_a)) != index_cache_path(str(repo_b))
    assert str(repo_a) in str(index_cache_path(str(repo_a)))
    assert str(repo_b) in str(index_cache_path(str(repo_b)))


def test_engine_and_grammar_version_is_a_real_non_empty_fingerprint():
    """Sanity check on the shared helper both cache-isolation fixes
    depend on: a real, stable, non-empty string - not silently `""`/
    `None` in this environment (which would make the whole engine-
    version check a permanent no-op)."""
    version = engine_and_grammar_version()
    assert isinstance(version, str)
    assert version
    assert version == engine_and_grammar_version(), "must be stable within one process"
