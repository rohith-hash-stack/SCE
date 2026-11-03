# R2 held-out mining check (2026-11-03; no model calls)

## Method

- **Pipeline:** R1's forward pipeline, unchanged:
  - `change_history.mine --forced --scan 10000 --max-candidates 100000`, over each repository's full history at the
    HEAD below;
  - then `suite.forced`'s eligibility filter: a forced change, a caller whose changed lines touch the call, and one
    task per commit.
- **"Qualifying"** means eligible after that filter.
- **Rule (approved):** a repository needs at least 5 qualifying tasks. A short one is replaced by the next reserve of
  the same language, in order.
- **Faults:** every log was checked. No commit was skipped for an error, so the zeros are real.

## Result

| repository | language | source | HEAD | qualifying (easy/medium/hard) | result | replaced by |
|---|---|---|---|---|---|---|
| Delgan/loguru | Python | held-out | d776b4233280 | 23 (14/5/4) | pass | |
| more-itertools/more-itertools | Python | held-out | 08dca284ba54 | 13 (11/2/0) | pass | |
| sdispater/pendulum | Python | held-out | e06dacedc4cd | 10 (7/0/3) | pass | |
| python-poetry/tomlkit | Python | held-out | 85d391e1ba90 | 8 (5/3/0) | pass | |
| pytest-dev/pluggy | Python | held-out | 30b8ae208f59 | 9 (8/1/0) | pass | |
| python-attrs/cattrs | Python | held-out | e9239b708220 | 6 (2/3/1) | pass | |
| lark-parser/lark | Python | held-out | 9a4fb9c7458e | 61 (42/13/6) | pass | |
| mahmoud/boltons | Python | held-out | d31d821d56c8 | 20 (20/0/0) | pass | |
| hynek/structlog | Python | held-out | 81779f0abe59 | 3 (1/1/1) | **short** | funcy |
| arrow-py/arrow | Python | held-out | 2224255c4acc | 1 (1/0/0) | **short** | **none left** |
| pyparsing/pyparsing | Python | reserve 1 | 810199f89bf9 | 4 (1/2/1) | short | |
| Suor/funcy | Python | reserve 2 | a876c7e1cf11 | 10 (10/0/0) | pass | replaces structlog |
| pallets/itsdangerous | Python | reserve 3 | 672971d66a2e | 2 | short | |
| jazzband/tablib | Python | reserve 4 | b6a75dcbe01c | 4 | short | |
| gvergnaud/ts-pattern | TS | held-out | f11be73acb18 | 2 | **short** | none |
| supermacro/neverthrow | TS | held-out | 5ef3a018bda7 | 0 | **short** | none |
| unjs/ofetch | TS | held-out | 1dbc37fd1cea | 4 | **short** | none |
| unjs/c12 | TS | held-out | f7a7664cca4f | 3 | **short** | none |
| sindresorhus/p-queue | TS | held-out | 7f272ab5ff22 | 2 | **short** | none |
| unjs/ufo | TS | held-out | f06c800d0c59 | 2 | **short** | none |
| unjs/defu | TS | reserve 1 | 82632b66f591 | 1 | short | |
| unjs/pathe | TS | reserve 2 | bc7477a01f0b | 0 | short | |
| sindresorhus/p-map | TS | reserve 3 | d61b6a33cc1d | 2 | short | |
| lukeed/kleur | TS | reserve 4 | fa3454483899 | 1 | short | |
| chalk/chalk | TS | reserve 5 | 72110019e147 | 0 | short | |

## Outcome: **the mining check fails under the approved rules**

| language | target | repositories reaching 5 | tasks available at 5 per repository |
|---|---|---|---|
| Python | 50 (10 × 5) | 9 | **45**: 5 short, one repository missing |
| TypeScript | 30 (6 × 5) | **0** | **0**: 17 qualifying tasks in all, over 11 repositories, none reaching 5 |

- **Why TypeScript is short:** these are small single-purpose libraries, with 106-971 commits each. Few commits in
  them change a function and, in the same commit, the callers it forces to change.
- The Python repositories are larger, or have longer histories.

## Options for the team [TBD — team; nothing below has been done]

1. **More TypeScript repositories** (recommended), plus one Python repository.
   - Larger TypeScript libraries, with long histories and internal call chains. None of these appears anywhere in
     this repository (exact `owner/repo` check) or in R3's pool: `mobxjs/mobx`, `immerjs/immer`, `ReactiveX/rxjs`,
     `pmndrs/zustand`, `sinclairzx81/typebox`, `effect-ts/effect`.
   - Each would go through this same check, with no model calls, before it is admitted, under the same
     5-per-repository rule.
   - For Python: the next candidates by the same rule [to be named on approval].
   - This keeps every approved rule. Only the repository list grows.
2. **Relax the 5-per-repository cap.**
   - Python: 45 at 5 per repository; 50 is reachable at 6 per repository from the 9 that pass.
   - It does not rescue TypeScript: 17 tasks exist in all.
3. **Change the language mix** to Python-only (80 Python tasks). This reverses the approved 50/30 and drops A6's
   "must include TypeScript".
   - Not recommended: it confounds a section's effect with language, which the 50/30 mix was chosen to prevent.
4. **Run with what exists:** 45 Python tasks and up to 17 TypeScript tasks without a cap, 62 in all. This breaks the
   locked 80 per version.
   - Not recommended.
