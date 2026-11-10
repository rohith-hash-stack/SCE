# R4 sanity: static checks and samples (2026-11-10)

**The run** (`ROADMAP_R4_R6.md` §4a′): both arms on the same sample, seed 42, R3a's protocol and turn cap. The
standalone Sanity 2 is retired.
- **Families:** delete and feature. **Family B is not sampled:** 4 tasks pass G1 + G2 + scope, unchanged after K4
  (`FAMILY_B_REGATE.md`), below the floor of 5. It stays directional.
- **Size:** 10 + 10 tasks × 2 arms = **40 runs**, one Kaggle session.

## Layer 1: scope over the gated pool (static, against the developer's commit)

Script: `scope_family.py` (scratch; method as `SCOPE_INVESTIGATION.md`). Classes: upstream-broken (an unresolved call or
a certain arity mismatch in the commit's new or changed functions) > cascading (a changed caller beyond 2 hops) >
bounded+clean.

| Family | Gated tasks checked | Bounded+clean | Upstream-broken | Cascading | Share bounded+clean |
|---|---|---|---|---|---|
| delete | 70 | 55 | 13 | 2 | 79% |
| feature | 23 | 18 | 5 | 0 | 78% |

**The K4-affected repositories** (fastmcp, pydantic-ai, textual, poetry, marshmallow) apart:

| Family | Affected repos: clean / broken | Other repos: clean / broken / cascading |
|---|---|---|
| delete | 15 / 7 | 40 / 6 / 2 |
| feature | 9 / 5 | 9 / 0 / 0 |

**Every one of the 18 upstream-broken records is an arity flag; none is an unresolved name.** Spot-checked 5, all false
positives of two kinds:
- **constructor calls to classes without an explicit `__init__`:** pydantic `BaseModel` (`StdioMCPServer`, fastmcp;
  `OpenAPITool`, a `Tool` subclass), `@dataclass` (`ToolCallPart`, `RunUsage`, pydantic-ai). The binder reads a
  signature that is not the constructor's, so keyword fields read as "no longer a parameter";
- **module functions taking `self` first** (sqlglot's `strposition_sql(self, expression, ...)`, used as generator
  transforms): the binder drops `self`, so a positional argument reads as "gets two values".

The other 13 were not checked. So the 78–79% shares are a **lower bound**; Layer 1's ≥80% bar is probably met, not
shown. **This matters beyond the sanity run:** the same binder feeds the break facts `prism_check_changes` will report.
Both classes must be fixed or suppressed before it ships (recorded for the V1/V2 work, `PRISM_REQUIRED_BEHAVIOR.md`).

## Samples

Drawn only from bounded+clean tasks, so every sampled task passes Layer 1. Rule (`sample.py`, scratch): repositories in
a seed-42 order, one task per repository, then a second, up to 10. Backups follow the same order if a task fails its
scoring check when built (`sanity_tasks.py`: the developer's change must resolve, the unchanged start must not).

**Feature screen (by hand, before sampling):** tasks whose production change is only CI or release tooling are not
product features, and are left out: typer `90f087ac` (`scripts/prepare_release.py`), pydantic-ai `a0022cd0`
(`.github/scripts/check_api_compatibility.py`), fastmcp `f2b965bf` (`scripts/release_contributor_credit.py`). Short
commit messages are **not** screened out: the failing tests are visible, so a title plus tests is the task as a
developer saw it.

@@SAMPLES@@

## Harness

| Piece | File | State |
|---|---|---|
| task builder | `r4/sanity_tasks.py` | done; local test passed (werkzeug delete, typer feature: gold resolves, start does not) |
| delete scoring (`must_be_absent`) | `r3/runner.py` | done; R3's tests pass |
| Kaggle cell | `kaggle/r4_sanity_cell.template.py` | written; renders after the tasks are committed |
| report | `r4/sanity_report.py` | done; tested on synthetic runs |
| local dry run (1–2 tasks per family, both arms, no model) | — | next |

**The PRISM arm** runs R3a's tool layer with the fixes merged at the cell's pinned commit (today: K4).
`prism_check_changes` is part of the arm only if it is built and wired before the session; the report states which.
