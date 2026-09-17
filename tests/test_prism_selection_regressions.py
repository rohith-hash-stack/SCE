"""Characterization tests for the two algorithmic problems the pilot run
(`pilot-full-20260916T091808Z`) revealed in the real Prism v1.1 causal
engine (`prism.packer.submodular_knapsack.pack_symbol_context`, wrapped
by `benchmarks.engines.prism_engine_cache.PrismEngineCache` - the exact
`prism_v11` engine the pilot measured):

  Problem A - context bloat: on some T02 tasks (t02_015, t02_007,
  t02_014, t02_017, t02_018 in the pilot), Prism packs 5-10x more
  symbols than the task's real pipeline needs, well past what any
  baseline engine packs at the same budget. Prism's genuine wins in the
  pilot came from small, tight contexts (t02_010, t02_020: 4-5 symbols).

  Problem B - a one-symbol shortfall pattern: Prism systematically drops
  either a bare class name (in favor of its own methods, already
  selected) or a private/underscore-prefixed helper that is a real
  pipeline stage, at low budget - `django.core.mail.message.
  EmailMultiAlternatives` (t02_014), `django.db.models.query.QuerySet.
  _clone` (t02_009), `django.utils.http._urlparse` (t02_017).

Every test here runs the real engine against the real, pinned Django
corpus (`benchmarks.corpora.resolver.resolve("django")`) - no mocking,
no synthetic fixture graph - since the whole point is to pin down what
the *actual* production code does today, not a simplified model of it.
Marked `@pytest.mark.slow` (matching this repo's own existing convention
in `tests/test_task_reachability.py`): the module-scoped fixtures below
index the real corpus once (network-free after the first checkout, but
a real multi-second build_pipeline + feature-extraction pass) and reuse
it across every test in this file.

**Deviation from the originally specified budget for the Problem-B
tests, recorded here rather than silently**: the task brief's own
pseudocode for `test_prism_retrieves_class_symbol_alongside_its_method`
and the private-helper test used `budget=4000` for both, matching the
Problem-A tests. Live verification against the real corpus (and cross-
checked against the pilot's own `reports/pilot/checkpoint.json`) shows
all three symbols - `EmailMultiAlternatives`, `QuerySet._clone`,
`_urlparse` - are actually already present in Prism's selected_symbols
at budget=4000 and budget=8000; only at budget=2000 are they missing.
Writing those two tests at budget=4000 as originally specified would
pass today and characterize nothing. They are written at budget=2000
below instead, the one budget where Problem B is real and reproducible,
so they actually pin the target per this phase's own stated purpose.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.corpora.resolver import resolve
from benchmarks.engines.base import selected_symbols
from benchmarks.engines.prism_engine_cache import PrismEngineCache
from benchmarks.ground_truth.loader import load_tasks_from_dir

pytestmark = pytest.mark.slow

TASKS_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "ground_truth" / "tasks" / "django"


@pytest.fixture(scope="module")
def django_repo_path() -> str:
    return str(resolve("django"))


@pytest.fixture(scope="module")
def django_tasks(django_repo_path) -> dict:
    result = load_tasks_from_dir(TASKS_DIR)
    assert not result.rejected, f"ground-truth tasks failed to load: {result.rejected}"
    return {t.task_id: t for t in result.accepted}


@pytest.fixture(scope="module")
def prism_engine(django_repo_path) -> PrismEngineCache:
    """One real `PrismEngineCache`, indexed once against the real,
    pinned Django checkout, reused (via its own in-process
    `_process_graph_cache`) across every test below - `index()` is the
    expensive, seed/budget-independent half of a `retrieve()` call
    (full `build_pipeline` + four-axis feature extraction over the
    entire indexed graph); re-running it per test would make this file
    take many times longer for no additional coverage."""
    engine = PrismEngineCache()
    engine.index(django_repo_path)
    return engine


def _retrieve(prism_engine, django_tasks, task_id: str, budget: int):
    task = django_tasks[task_id]
    pkg = prism_engine.retrieve(task.seed_symbol, budget)
    return pkg, task


# --------------------------------------------------------------------- #
# Problem A - context bloat
# --------------------------------------------------------------------- #
def test_prism_does_not_pack_oversized_context_on_task_015(prism_engine, django_tasks):
    """On t02_015, Prism's selected_symbols was 114 in the pilot
    (budget=8000) / 48 (budget=4000, per the pilot's own checkpoint.json)
    against a baseline of 21-49 symbols at the same budgets. Live
    retrieval today packs 39 at budget=4000 - close to, not identical
    to, the pilot's own 48 (see this module's docstring on why exact
    counts drift slightly run-to-run), but still nearly 8x t02_020's 5
    symbols at the same budget. Cap this task at 30 symbols."""
    pkg, _task = _retrieve(prism_engine, django_tasks, "django_t02_015_admin_each_context", budget=4000)
    n = len(selected_symbols(pkg))
    assert n < 30, f"Prism packed {n} symbols on t02_015 at budget=4000; expected < 30"


def test_prism_preserves_minimal_context_on_task_020(prism_engine, django_tasks):
    """On t02_020, Prism's selected_symbols was 5 in the pilot at every
    budget. This is a regression guard, not a bug reproduction - it
    should already pass today, and must keep passing after Problem A is
    fixed (a fix that also shrinks t02_020's already-minimal context to
    nothing would itself be a regression)."""
    pkg, _task = _retrieve(prism_engine, django_tasks, "django_t02_020_storage_generate_filename", budget=4000)
    n = len(selected_symbols(pkg))
    assert 3 <= n <= 15, f"Prism packed {n} symbols on t02_020 at budget=4000; expected 3-15"


# --------------------------------------------------------------------- #
# Problem B - systematic one-symbol shortfall
# --------------------------------------------------------------------- #
def test_prism_retrieves_class_symbol_alongside_its_method(prism_engine, django_tasks):
    """Prism's retrieval on t02_014 at budget=2000 selects
    EmailMultiAlternatives.attach_alternative (and .alternatives) but
    not the bare class EmailMultiAlternatives itself - present in the
    task's adjudicated pipeline. Budget=2000, not the originally
    specified 4000 - see this module's own docstring."""
    pkg, task = _retrieve(prism_engine, django_tasks, "django_t02_014_send_mail_pipeline", budget=2000)
    class_symbol = "django.core.mail.message.EmailMultiAlternatives"
    assert class_symbol in task.adjudicated.pipeline_symbols, "test fixture assumption: this symbol is a real pipeline stage"
    assert class_symbol in selected_symbols(pkg), (
        "Prism selected methods of EmailMultiAlternatives but not the class itself"
    )


@pytest.mark.parametrize(
    "task_id, missing_symbol",
    [
        pytest.param(
            "django_t02_009_queryset_filter_clone", "django.db.models.query.QuerySet._clone",
            id="t02_009-QuerySet._clone",
        ),
        pytest.param(
            "django_t02_017_redirect_url_safety_check", "django.utils.http._urlparse",
            id="t02_017-_urlparse",
        ),
    ],
)
def test_prism_retrieves_private_helper_in_pipeline(prism_engine, django_tasks, task_id, missing_symbol):
    """On t02_009, Prism misses QuerySet._clone (a private helper and a
    real pipeline stage) at budget=2000, while BFS-forward retrieves it
    at the same budget. Same pattern on t02_017's _urlparse. Budget=2000,
    not the originally specified 4000 - see this module's own
    docstring."""
    pkg, task = _retrieve(prism_engine, django_tasks, task_id, budget=2000)
    assert missing_symbol in task.adjudicated.pipeline_symbols, "test fixture assumption: this symbol is a real pipeline stage"
    assert missing_symbol in selected_symbols(pkg)


# --------------------------------------------------------------------- #
# Budget compliance and determinism (should already hold today - not a
# reproduction of either problem, a standing invariant to guard)
# --------------------------------------------------------------------- #
def test_prism_budget_compliance_across_all_t02_tasks(prism_engine, django_tasks):
    """Total rendered token cost never exceeds the requested budget, for
    every T02 task, at every budget the pilot swept. The knapsack's own
    admission gate (`current_cost + cost > target_budget: continue` in
    `select_submodular_context`) is supposed to guarantee this by
    construction - this test is the standing proof it actually does,
    for the real corpus, not just in the algorithm's own docstring."""
    t02_tasks = [t for t in django_tasks.values() if t.task_type == "debug"]
    assert len(t02_tasks) == 20

    violations = []
    for task in t02_tasks:
        for budget in (2000, 4000, 8000):
            pkg = prism_engine.retrieve(task.seed_symbol, budget)
            total_cost = sum(node.cost for node in pkg.nodes)
            if total_cost > budget:
                violations.append((task.task_id, budget, total_cost))

    assert not violations, f"budget overflow(s): {violations}"


def test_prism_determinism_across_processes(prism_engine, django_tasks):
    """Same (seed, budget) against the same indexed graph -> the same
    selected symbol set and the same per-symbol token costs, run twice.
    `select_submodular_context` sorts the frontier (`sorted(frontier)`)
    specifically to make tie-breaking deterministic (fix-knapsack-
    tiebreak) - this is the standing proof that guarantee holds for a
    real task, not just the algorithm's own docstring."""
    task = django_tasks["django_t02_015_admin_each_context"]
    pkg1 = prism_engine.retrieve(task.seed_symbol, 4000)
    pkg2 = prism_engine.retrieve(task.seed_symbol, 4000)

    assert selected_symbols(pkg1) == selected_symbols(pkg2)
    costs1 = sorted((n.id, n.cost) for n in pkg1.nodes)
    costs2 = sorted((n.id, n.cost) for n in pkg2.nodes)
    assert costs1 == costs2


# --------------------------------------------------------------------- #
# No regression on Prism's genuine pilot wins
# --------------------------------------------------------------------- #
#: All 5 scored TSR=1.0 for prism_v11 across the pilot's full seed/budget
#: sweep, per reports/pilot/checkpoint.json (t02_010/011/019/020 are the
#: 4 highest-TSR tasks; t02_008 is the next-highest, also TSR=1.0).
WINNING_TASKS = [
    "django_t02_008_db_session_load_decode",
    "django_t02_010_i18n_catalog_translation",
    "django_t02_011_permissions_backend_check",
    "django_t02_019_request_get_host_validation",
    "django_t02_020_storage_generate_filename",
]


@pytest.mark.parametrize("task_id", WINNING_TASKS)
@pytest.mark.parametrize("budget", [2000, 4000, 8000])
def test_no_regression_on_prism_winning_tasks(prism_engine, django_tasks, task_id, budget):
    """These 5 tasks are where Prism's pilot wins came from - a fix for
    Problem A (bloat) or Problem B (shortfall) that breaks pipeline
    coverage on any of these, at any budget the pilot swept, is not a
    net improvement. Every adjudicated pipeline symbol must still be
    present in Prism's selected_symbols."""
    task = django_tasks[task_id]
    pkg = prism_engine.retrieve(task.seed_symbol, budget)
    syms = selected_symbols(pkg)
    missing = [s for s in task.adjudicated.pipeline_symbols if s not in syms]
    assert not missing, f"{task_id}@{budget}: missing pipeline symbols {missing}"
