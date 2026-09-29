# Spike: `GlobalSymbolTable` in-module duplicate-identifier collisions

**Status: spike complete, root cause and reproduction confirmed live, three strategies proposed — decision pending before implementation.** Scoped by `epic/engine-hardening-and-consolidation` ahead of tRPC (a real TypeScript monorepo where factory patterns and shadowed helpers make this collision shape far more likely than it was on Express).

## 1. What surfaced it

The Express pilot's own Task 013 (`express_t02_013_param_registration.yaml`) found that `lib.router.param` — the qualified name for the router's real, public `proto.param(name, fn)` registration API — actually resolves in `builder.symbol_table` to an *entirely different, unrelated* function: a private closure named `param(err)` nested inside `proto.process_params`, one of `process_params`'s own two locally-scoped helper functions. Both are real, both are named `param`, and the indexer keeps exactly one qualified name per (module, simple name) pair — whichever gets registered last silently wins.

## 2. Root cause, confirmed directly in `concrete_builder.py`

`ConcreteGraphBuilder._register_definition` (`src/prism/graph/concrete_builder.py:511-560`) walks up from a function's AST node looking for an enclosing *class* to build a qualified name:

```python
cursor = node.parent
while cursor is not None:
    if cursor.type in class_types:
        cls_name_node = cursor.child_by_field_name("name")
        if cls_name_node is not None:
            enclosing_class = f"{module}.{node_text(cls_name_node, parsed.source)}"
        break
    cursor = cursor.parent

if enclosing_class is not None:
    qualified_name = f"{enclosing_class}.{name}"
else:
    qualified_name = f"{module}.{name}"   # <-- every non-class-nested function lands here
```

The walk only ever checks `cursor.type in class_types` — it never checks for an enclosing *function*. A function nested inside another function (JS's `process_params`'s own `param`/`paramCallback` closures; a Python closure; any language's local helper) falls straight through to the bare `module.name` form, identically to a real top-level, module-scope function. Two functions with the same simple name in the same module — one top-level, one nested three closures deep, or both nested in different places — collide on the exact same dict key in `GlobalSymbolTable._symbols`, and `add()`'s plain `self._symbols[qualified_name] = symbol` assignment means the later registration silently overwrites the earlier one. No error, no warning — a genuinely wrong `SymbolInfo` (wrong line range, wrong real function) returned from every future `.get()` call for that name, forever.

**This is not a new discovery in isolation** — `tests/test_fuzzy_anchor_matching.py`'s own `test_end_to_end_tracer_and_reconciler_resolve_a_decorator_wrapped_dispatch` docstring already names the underlying behavior explicitly: *"Pass 1's own definitions query registers nested closures like `wrapper` as flat top-level symbols (`svc.wrapper`, not scoped under `svc.deco`...) - a genuine, pre-existing static-indexing gap... not something Item 10 is scoped to fix."* What's new here is the concrete *consequence*: when two such flat registrations happen to share a name, the collision is silent and produces a wrong answer, not just an imprecisely-scoped one.

## 3. Confirmed cross-language, not JS-specific

Reproduced live with a minimal synthetic Python fixture (`/tmp/spike_test/mod.py`):

```python
def real_api(name, fn):
    """The real, public top-level function."""
    return dispatch(name, fn)

def dispatch(name, fn):
    return fn(name)

def process(items):
    def real_api(err):
        """An unrelated nested closure that happens to share a name."""
        return err
    return real_api(items[0])
```

`builder.symbol_table.get("mod.real_api").line_range` returns `(11, 13)` — the nested closure — never `(1, 3)`, the real top-level function. Same root cause (`_register_definition`'s ancestor walk), same silent-wrong-answer failure mode, confirmed independent of language. This will recur on any repo, not just Express or JS/TS ones — it's a real property of `ConcreteGraphBuilder` itself.

## 4. Real blast radius, honestly bounded

This does **not** mean every nested function is wrong today — only a nested function whose simple name happens to collide with another definition (top-level or nested) in the *same module* is affected. Most nested helpers have distinctive names and never collide. The risk is structural and silent, not currently large in measured terms (Express's own pilot found exactly one real instance across a 20-task, 20-symbol-pipeline suite) — but tRPC's own real surface (heavy generics, factory-function patterns, a monorepo where the same short helper name like `resolver`, `middleware`, or `next` recurs across many files *and* within nested closures in each) is exactly the shape most likely to multiply this, per the epic's own stated motivation for running this spike before tRPC's pilot begins.

## 5. Three disambiguation strategies, with trade-offs

**Option A — Symmetric scope-qualification (the "textbook correct" fix).** Extend the existing ancestor walk to also detect an enclosing *function*, not just a class, and qualify a nested function's name the same way a method's is already qualified (`module.EnclosingClass.method` → generalized to `module.enclosing_fn.nested_fn`, recursively for multiply-nested closures).
- *Pro*: fully general, eliminates the collision class entirely, no exceptions.
- *Con*: **reshapes the qualified name of every currently-nested function in every already-indexed corpus** — not just colliding ones. Any already-authored ground-truth task, cached index, or external reference to a nested closure's current flat name (e.g. `svc.wrapper` in the fuzzy-anchor test above) breaks. Blast radius is proportional to "how many nested functions exist anywhere," not "how many actually collide" — large and mostly unnecessary, since ~99% of nested functions never collide with anything.

**Option B — Detect-and-flag only (zero behavior change).** Add a real collision check to `GlobalSymbolTable.add()`: when a qualified name is about to be overwritten by a *different* `def_node` (not a legitimate re-registration of the same symbol, e.g. from a cache rehydration), record it in a real, queryable `collisions: dict[str, list[SymbolInfo]]` structure and optionally log it. Resolution behavior (last-write-wins) is completely unchanged.
- *Pro*: zero blast radius, zero risk to existing tasks/caches/tests. Makes the problem *visible* (a task author or an automated audit can now query `builder.symbol_table.collisions` directly instead of discovering it by hand, as Task 013 did) without touching resolution semantics at all.
- *Con*: doesn't actually fix anything — a real call site still silently resolves to the wrong function. Purely diagnostic.

**Option C — Suffix-on-collision only (targeted fix, minimal blast radius).** Keep every non-colliding qualified name exactly as it is today (zero change for ~99% of the corpus). Only when `add()` detects a genuine second, different definition for an already-registered qualified name, give the *later* registration a stable, deterministic disambiguating suffix (e.g. `module.name#2`, or better, `module.name` qualified by its own real enclosing-function chain the same way Option A would, but applied *only* to the specific colliding pair, not universally).
- *Pro*: fixes the actual bug (both real functions become independently resolvable) with a blast radius bounded to cases that are *already silently wrong today* — there is no currently-correct resolution this could regress, since a collision means one of the two symbols is already being returned incorrectly for every lookup.
- *Con*: the disambiguated name (`module.name#2` or similar) isn't necessarily the name a human or an LLM would naturally guess or request — a call-site resolver would still need `candidates_for_simple_name`-style fallback logic to find it, and the *first*-registered symbol keeps the plain, guessable name while the second doesn't (an arbitrary, registration-order-dependent asymmetry, though a deterministic one).

## 6. Recommendation

**Option C**, as the pragmatic middle ground: it is the only option that fixes the real, confirmed bug (a wrong `SymbolInfo` silently returned) while bounding its blast radius to exactly the cases already broken today, leaving the other ~99% of the corpus — including every existing ground-truth task, cache entry, and test — completely untouched. Option A is more architecturally "correct" in the abstract but reshapes far more of the corpus than the bug it fixes justifies, for a real risk of invalidating already-authored ground truth (checked: no existing Express/Django/FastAPI ground-truth task currently references a nested-closure qualified name directly, but this hasn't been checked against every corpus, and a rename that broad deserves its own dedicated audit, not a side effect of a collision fix). Option B is a reasonable *first* landing if the team wants visibility before committing to any resolution-changing behavior — worth doing regardless of which of A/C is chosen next, since it's free and immediately useful for auditing tRPC once its own pilot begins.

**This report stops at the recommendation.** Implementing Option C (or B first, then C) is real engine-code work with its own test-suite blast-radius check, intentionally not started in this same session pass — the spike's own deliverable, per the epic's scoping, is the confirmed root cause plus a deterministic, decision-ready strategy, not a shipped change to core indexing behavior without an explicit go-ahead.
