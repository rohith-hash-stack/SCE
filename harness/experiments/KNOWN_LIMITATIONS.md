# PRISM: known limitations (recorded, not yet fixed)

Each entry names the behaviour, the requirement it violates (`PRISM_REQUIRED_BEHAVIOR.md`), the evidence, and
where it is tracked: a golden case, a test, a fix plan. An entry is removed only when its fix lands and the linked
golden file or test changes to show it.

## Manager-based calls are not linked, and not reported (recorded 2026-11-09)

**Limitation:** manager-based calls (`Model.objects.get`) are not linked and not reported as "not followed". This
violates the spec's honesty requirement (S4/S5). Fix deferred to R4's correctness work or later.

- **The behaviour:**
  - Django models expose a manager through a class attribute (`objects = Manager()`, a descriptor), and the manager
    proxies `QuerySet` methods. So `Model.objects.get(...)`, `.filter(...)` and the like reach
    `django.db.models.query.QuerySet.get` at runtime.
  - PRISM's graph has no edge for these calls, and no tool says one may be missing.
  - Since `934f83c8` (2026-10-07; builtin method names are no longer guessed onto repo methods), the
    `QuerySet.get` callers PRISM lists are only the few direct ones. 2 direct and 5 transitive on Django 4.2.30, on
    2026-11-09; the 34 listed earlier were `dict.get` and request lookups, not real calls.
- **The requirements violated:**
  - Q2, completeness about gaps;
  - L1, "name what static analysis cannot see";
  - T2 (callers) and V3 (what is not updated), for any change to a manager-proxied method.
- **Consequences:**
  - `benchmarks/ground_truth/tasks/django/django_t13_002_blast_queryset_get.yaml` (a T13 blast task) has a seed
    with only 7 static callers, below the authoring screen's 20 (`benchmarks/ground_truth/schema.py`,
    `validate_blast_seed_richness`). Its BCCR is near-vacuous until this is fixed.
  - It does not affect R3a: none of R3a's repositories is Django.
- **Tracked by:**
  - `tests/benchmarks/test_harness_metrics.py::test_queryset_get_seed_passes_the_richness_screen_once_manager_calls_are_linked`,
    a strict xfail. It fails once the calls are linked, so this entry, the test and the T13 task are revisited
    together.
  - The golden case "manager-proxied calls" (`GOLDEN_TESTS_PLAN.md` §1, `py_repo`). Its expected output today is
    "silent on the manager call".
- **The fix** (when scheduled): link `X.objects.m(...)` to the manager's proxied `QuerySet.m` where the manager
  class is resolvable, and otherwise report the call as "not followed (manager proxy)". A deterministic, graph-only
  change, part of the correctness work (spec Q1/Q2/L1).
