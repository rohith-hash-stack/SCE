"""Fix #2 (Rendered-Metadata Metering): verifies the real invariant a
consumer of `build_context_package` actually depends on -
`count_tokens(render(pkg)) <= target_budget` (within a small tolerance)
- not just `sum(node.cost) <= target_budget`, which `prism.packer.
submodular_knapsack`'s own pre-existing tests (`test_selection_
invariants.py`) already cover and continue to hold unchanged.

**Design history - three real regressions found and fixed, in order,
each against `tests/test_prism_selection_regressions.py`'s protected
22/22 suite (which this file must never touch or weaken):**

1. First attempt made `pack_symbol_context`'s own selection metadata-
   aware (passing `contracts` through, shrinking its effective budget).
   Selection itself changed, and several real ground-truth pipeline
   symbols stopped being packed at all (e.g. `django.http.request.
   validate_host` missing even at budget=8000, `HttpRequest`'s own
   class-promotion fixup starved of headroom). Reverted: `build_context_
   package` never passes `contracts` into `pack_symbol_context`; full
   selection recall is preserved unconditionally.
2. Second attempt kept selection untouched but enforced the invariant by
   *evicting* whole nodes post-hoc (largest `distance` first), with
   increasingly careful protected-tier exceptions (fixup-admitted
   symbols, then `causal_path` stages). Each fix closed one real,
   observed case and broke another - because a task's own deepest
   causal-chain stage is, by construction, the *first* thing a
   distance-based eviction rule removes, and no production-only signal
   reliably tells "real pipeline symbol" apart from "opportunistic extra
   context" in general.
3. **Final design: never remove a node at all.** `_enforce_render_
   budget` only ever downgrades a packed node's own body from `L0_full`
   to a signature-only `L2_skeleton` stub (`_downgrade_to_stub`) -
   `benchmarks.engines.base.selected_symbols` (what the protected suite
   checks) reads only `{n.id for n in pkg.nodes}`, never `compression`
   or `body`, so this is invariant under every assertion that suite
   makes by construction, not by any heuristic that could later be
   found to miss a case.

**A real, disclosed structural finding, not a bug**: `L2_skeleton` still
renders a `<signature>`/`<features>` block per node, plus the package's
own fixed `<metadata>`/`<manifest>`/`<coverage>`/`<warnings>`/`<trailer>`
sections - so a budget tight enough relative to how many real symbols
got selected can still legitimately stay over tolerance even with every
downgrade-eligible node already fully stubbed. `test_render_budget_
respected_at_feasible_budgets` proves the invariant at budgets where
it's achievable; `test_below_floor_budget_degrades_safely_not_silently`
proves the sub-floor case degrades honestly (every symbol still
present, no crash) rather than silently claiming compliance it can't
deliver.

**Design history, continued - Track 2 (`_PROTECTED_DOWNGRADE_ROLES`,
`src/prism/surface/build.py`, commit 584ea6a)**: "downgrades a packed
node's own body" above is no longer the complete picture. A real
scoring failure during the noise-reduction spike (`django_t02_009` @
budget=2000 - every node, including the seed itself, ended up
downgraded, stripping a call site the model needed to answer
correctly) led to a fourth refinement: the seed and its direct (1-hop)
causal neighbors (`ROLE_SEED`/`ROLE_CALLEE`/`ROLE_CALLER`) are now
permanently excluded from `_enforce_render_budget`'s own downgrade
candidates - only `ROLE_TRANSITIVE` nodes remain eligible. This is a
real, further narrowing of *which* nodes downgrading protects, not a
change to "never remove a node" - still true, still the whole point of
this file. `test_below_floor_budget_degrades_safely_not_silently`,
`test_causal_path_stages_stay_present_and_untruncated_when_downgraded`,
and `test_seed_is_never_downgraded` (renamed from `test_seed_is_
downgraded_last`, whose own name described exactly the behavior this
removed) were updated accordingly - each now asserts protected-role
nodes stay `L0_full` and transitive-role nodes remain the only ones
actually downgraded, verified directly against each fixture's own real
graph rather than assumed.
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.graph.contracts import compute_contracts
from prism.slicer.tokenizer import count_tokens
from prism.surface.build import _RENDER_BUDGET_TOLERANCE, build_context_package
from prism.surface.renderer import RenderOptions
from prism.surface.renderer import render as render_envelope

_SOURCE = (
    "def leaf_a():\n"
    "    '''A short leaf docstring.'''\n"
    "    return 1\n\n\n"
    "def leaf_b():\n"
    "    '''Another short leaf docstring.'''\n"
    "    return 2\n\n\n"
    "def mid_a(x, y=1):\n"
    "    '''Combines both leaves.'''\n"
    "    return leaf_a() + leaf_b() + x + y\n\n\n"
    "def mid_b():\n"
    "    return leaf_b()\n\n\n"
    "def seed():\n"
    "    '''Entry point calling both mid-tier helpers.'''\n"
    "    return mid_a(1, 2) + mid_b()\n"
)


def _build(tmp_path, subdir="repo"):
    repo = tmp_path / subdir
    repo.mkdir()
    (repo / "svc.py").write_text(_SOURCE)
    builder, _tags = build_pipeline(str(repo))
    contracts = compute_contracts(builder)
    return builder, str(repo), contracts


def _rendered_tokens(builder, repo, contracts, budget):
    pkg = build_context_package(builder, "svc.seed", repo, budget, contracts=contracts, task_type="debug")
    xml = render_envelope(pkg, RenderOptions(include_timestamp=False, include_run_id=False))
    return pkg, count_tokens(xml)


def test_render_budget_respected_at_feasible_budgets(tmp_path):
    """The hard invariant: at a budget where it's achievable, the
    actually-rendered package - what a consumer really receives - never
    exceeds `budget * (1 + _RENDER_BUDGET_TOLERANCE)`, and every symbol
    the knapsack selected is still present (never removed to get there)."""
    builder, repo, contracts = _build(tmp_path)
    baseline_ids = None
    for budget in (2000, 4000):
        pkg, actual = _rendered_tokens(builder, repo, contracts, budget)
        limit = budget * (1 + _RENDER_BUDGET_TOLERANCE)
        assert actual <= limit, (budget, actual, limit)
        ids = {n.id for n in pkg.nodes}
        if baseline_ids is None:
            baseline_ids = ids
        else:
            assert ids == baseline_ids, "symbol set must never shrink or grow due to render-budget enforcement alone"


def test_never_removes_a_selected_symbol_at_any_budget(tmp_path):
    """The core safety property of the final design: for a given budget,
    `_enforce_render_budget` must never change *which* symbols the
    knapsack itself selected for that same budget - only how much detail
    (`compression`/`body`) each one carries. Compared directly against
    `pack_symbol_context`'s own independent selection per budget, not
    against a different budget's selection (a tighter budget legitimately
    admits fewer symbols at the knapsack level - a real, separate,
    already-tested property of `pack_symbol_context` itself, not
    something this fix's own trim step is responsible for)."""
    from prism.packer.submodular_knapsack import pack_symbol_context

    builder, repo, contracts = _build(tmp_path)
    for budget in (100, 300, 500, 1000, 2000, 4000):
        pkg, _actual = _rendered_tokens(builder, repo, contracts, budget)
        selected = pack_symbol_context(builder, "svc.seed", budget)
        assert {n.id for n in pkg.nodes} == set(selected.selected), budget


def test_larger_budget_never_needs_more_compression_than_a_smaller_one(tmp_path):
    """Monotonicity control: the number of stubbed (`L2_skeleton`) nodes
    must never increase as budget grows - a larger budget should only
    ever restore detail, never remove it."""
    builder, repo, contracts = _build(tmp_path)
    prev_stubbed = None
    for budget in (500, 1000, 2000, 4000):
        pkg, _actual = _rendered_tokens(builder, repo, contracts, budget)
        stubbed = sum(1 for n in pkg.nodes if n.compression != "L0_full")
        if prev_stubbed is not None:
            assert stubbed <= prev_stubbed, (budget, stubbed, prev_stubbed)
        prev_stubbed = stubbed


def test_below_floor_budget_degrades_safely_not_silently(tmp_path):
    """A budget tight enough relative to this fixture's own five real
    symbols cannot satisfy the strict invariant no matter what. This
    proves the honest degraded behavior: every symbol the knapsack
    selected is still present, the function terminates rather than
    looping, it does NOT silently claim to be within budget when it
    structurally cannot be, and it does NOT drop a symbol to get there
    either.

    Updated for Track 2's `_PROTECTED_DOWNGRADE_ROLES`
    (`src/prism/surface/build.py`, commit 584ea6a): `_enforce_render_
    budget` no longer downgrades every node to its smallest
    representation - the seed and its direct (1-hop) causal neighbors
    (`ROLE_SEED`/`ROLE_CALLEE`/`ROLE_CALLER`) are now permanently
    excluded from downgrade, only `ROLE_TRANSITIVE` nodes remain
    eligible. In this fixture, `svc.seed` (seed) and `svc.mid_a`/
    `svc.mid_b` (its own direct callees) are protected and verified
    directly against the real graph below - confirmed empirically, not
    assumed - while `svc.leaf_a`/`svc.leaf_b` (2 hops out, transitive)
    are still downgraded exactly as before. The degradation is
    therefore *less* complete than the pre-Track-2 baseline (3 of 5
    nodes can never shrink at all now), so the budget stays exceeded
    here just as honestly as it always did - the "not silently"
    guarantee this test's own name is about is unaffected by the
    protection change."""
    builder, repo, contracts = _build(tmp_path)
    pkg, actual = _rendered_tokens(builder, repo, contracts, 500)
    assert len(pkg.nodes) == 5

    protected = {n.id: n for n in pkg.nodes if n.role in ("seed", "callee", "caller")}
    transitive = {n.id: n for n in pkg.nodes if n.role == "transitive"}
    assert set(protected) == {"svc.seed", "svc.mid_a", "svc.mid_b"}
    assert set(transitive) == {"svc.leaf_a", "svc.leaf_b"}
    assert all(n.compression == "L0_full" for n in protected.values()), "seed/1-hop neighbors must never be downgraded"
    assert all(n.compression == "L2_skeleton" for n in transitive.values()), "transitive nodes remain eligible for downgrade"

    assert actual > 500 * (1 + _RENDER_BUDGET_TOLERANCE)


def test_causal_path_stages_stay_present_and_untruncated_when_downgraded(tmp_path):
    """Regression control for the design history above: a causal chain
    under real render-budget pressure must keep every stage present in
    both `nodes` and `causal_path.stages` - `truncated` stays `False`,
    since nothing was ever removed (unlike the discarded eviction-based
    design, downgrading never needs to mark the chain as truncated).

    Updated for Track 2's `_PROTECTED_DOWNGRADE_ROLES` (see the sibling
    `test_below_floor_budget_degrades_safely_not_silently`'s own
    docstring for the full explanation): the tight budget no longer
    forces *every* node to `L2_skeleton` - `chain.seed` (seed) and
    `chain.a` (its own direct callee) are protected and stay `L0_full`;
    only `chain.b`/`chain.c` (2-3 hops out, transitive) are still
    downgraded. Confirmed directly against the real graph below, not
    assumed. The real regression this test guards against - a stage
    disappearing from `nodes`/`causal_path.stages`, or `truncated`
    flipping `True` - is unaffected either way."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "chain.py").write_text(
        "def a():\n    '''Stage a docstring.'''\n    return b()\n\n\n"
        "def b():\n    '''Stage b docstring.'''\n    return c()\n\n\n"
        "def c():\n    '''Stage c docstring.'''\n    return 1\n\n\n"
        "def seed():\n    '''Entry point.'''\n    return a()\n"
    )
    builder, _tags = build_pipeline(str(repo))
    contracts = compute_contracts(builder)

    pkg = build_context_package(builder, "chain.seed", str(repo), 900, contracts=contracts, task_type="debug")
    assert {n.id for n in pkg.nodes} == {"chain.seed", "chain.a", "chain.b", "chain.c"}

    protected = {n.id: n for n in pkg.nodes if n.role in ("seed", "callee", "caller")}
    transitive = {n.id: n for n in pkg.nodes if n.role == "transitive"}
    assert set(protected) == {"chain.seed", "chain.a"}
    assert set(transitive) == {"chain.b", "chain.c"}
    assert all(n.compression == "L0_full" for n in protected.values()), "seed/1-hop neighbors must never be downgraded"
    assert all(n.compression == "L2_skeleton" for n in transitive.values()), "transitive stages remain eligible for downgrade"

    assert pkg.causal_path is not None
    assert {s.symbol for s in pkg.causal_path.stages} == {"chain.seed", "chain.a", "chain.b", "chain.c"}
    assert pkg.causal_path.truncated is False


def test_seed_is_never_downgraded(tmp_path):
    """Renamed from `test_seed_is_downgraded_last` (Track 2,
    `_PROTECTED_DOWNGRADE_ROLES`, commit 584ea6a) - the old name and
    the old assertion ("seed must be stubbed once nothing else is left
    to shrink") described exactly the behavior this fix deliberately
    removed. The seed (and its direct causal neighbors) is now
    permanently excluded from `_enforce_render_budget`'s own downgrade
    candidates, full stop - not "downgraded last", never downgraded at
    all, regardless of budget.

    A deliberately very long docstring (matching the real `django.db.
    models.base.Model.save` task this whole mechanism was built
    against) reproduces a single-node graph where the seed alone,
    un-downgradable, is *more* real content than a tight budget can
    hold - the render-budget invariant is honestly, disclosedly not
    guaranteed here (see `_enforce_render_budget`'s own docstring), and
    this test confirms that's exactly what happens: the seed stays
    `L0_full` even though the package stays over budget, rather than
    ever being silently stubbed to hide the gap. Verified directly:
    real measured tokens at budget=800 are 1350 against a 840 limit -
    genuinely, honestly over, not a borderline artifact."""
    repo = tmp_path / "repo"
    repo.mkdir()
    long_doc = " ".join(["This is a long docstring sentence explaining behavior in detail."] * 15)
    (repo / "mod.py").write_text(f'def seed():\n    """{long_doc}"""\n    return 1\n')
    builder, _tags = build_pipeline(str(repo))
    contracts = compute_contracts(builder)

    pkg = build_context_package(builder, "mod.seed", str(repo), 800, contracts=contracts, task_type="debug")
    rendered = render_envelope(pkg, RenderOptions(include_timestamp=False, include_run_id=False))
    actual = count_tokens(rendered)
    assert len(pkg.nodes) == 1
    assert pkg.nodes[0].id == "mod.seed"
    assert pkg.nodes[0].role == "seed"
    assert pkg.nodes[0].compression == "L0_full", "the seed must never be downgraded, even when the package stays over budget as a result"
    assert actual > 800 * (1 + _RENDER_BUDGET_TOLERANCE), "this budget is a genuine, honest over-budget case, not a borderline pass"

    # A comfortable budget: the seed was always L0_full regardless (it's
    # protected), but this confirms the render-budget invariant is met
    # here too, same as any other achievable-budget case in this file.
    pkg_ok = build_context_package(builder, "mod.seed", str(repo), 1400, contracts=contracts, task_type="debug")
    assert pkg_ok.nodes[0].compression == "L0_full"
    rendered_ok = render_envelope(pkg_ok, RenderOptions(include_timestamp=False, include_run_id=False))
    assert count_tokens(rendered_ok) <= 1400 * (1 + _RENDER_BUDGET_TOLERANCE)


def test_metadata_aware_pricing_is_opt_in_pack_symbol_context_unaffected(tmp_path):
    """`pack_symbol_context` itself (no `contracts`) must be byte-for-
    byte unaffected by Fix #2 - every pre-existing caller/test
    (`test_selection_invariants.py` et al.) depends on this, and
    `build_context_package` itself deliberately never passes its own
    `contracts` through to it (see this file's own module docstring,
    finding #1)."""
    from prism.packer.submodular_knapsack import pack_symbol_context

    builder, _repo, _contracts = _build(tmp_path)
    result = pack_symbol_context(builder, "svc.seed", 2000)
    assert result.total_cost <= 2000
    for item in result.items:
        info = builder.symbol_table.get(item.symbol)
        assert info is not None
        parsed = builder.parsed_file(info.file)
        source = parsed.source.decode("utf-8")
        start, end = info.line_range
        snippet = "\n".join(source.splitlines()[max(start - 1, 0):end])
        assert item.cost == max(count_tokens(snippet), 1) or item.compression == "L2_skeleton"
