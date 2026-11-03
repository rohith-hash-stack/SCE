# R2: proposal for its two open items (held-out repositories, language mix). For team approval; not frozen.

R2's pre-registration is Amendment R1-A, A6, in `reports/suite/REPORT.md`. It is locked. Fixed there:
- the five versions (full, −4, −6, −7, −4+7), each padded back to the full context's size;
- seeds 42, 1001 and 2002;
- the ±0.03 equivalence margin, the Holm family and the decision rules;
- **development on R1's forward tasks.**

Open: the confirmation (held-out) repositories, their number and language mix. Nothing here changes a rule.

## A size note to settle first [TBD — team]

A6's development set is R1's **192** forward tasks; only the confirmation set is new. So:

| part | tasks | versions | seeds | calls |
|---|---|---|---|---|
| development (A6, fixed) | 192 | 5 | 3 | 2,880 |
| confirmation (this proposal) | 80 | 5 | 3 | 1,200 |
| **R2 in all** | | | | **4,080** |

"80 tasks per version × 5 × 3 ≈ 1,200 calls" is the confirmation part only. Whoever plans the budget should know
that R2 is about 4,080 calls: roughly 3-4 Kaggle sessions at R1's forward pace of about 35 s per call.

## Language mix

**Proposal: mirror the development mix.** R1's forward tasks are 120 Python and 72 JS/TS, that is 62.5 / 37.5.
The confirmation set is **50 Python and 30 JS/TS**.

- **Why mirror:** A6 calls a section load-bearing only if the development result holds "in the same direction on
  the confirmation tasks". A different language mix would confound a section's effect with language. R1 already
  showed forward behaving differently in JavaScript: wrong functions barely moved.
- **The alternative** [TBD — team]: 40 / 40, for equal power per language. That makes a per-language confirmation
  claim stronger, but the dev-vs-confirmation comparison less clean. Recommendation: mirror.

## Repositories: 16, 5 tasks each (10 Python, 6 JS/TS), with ordered reserves

**Rule:**
- No repository from the suite, R1, A3, earlier benchmarks, **or R3's pool**.
- Tasks are mined with R1 forward's miner over the full history. R2 has no cutoff rule, so any date qualifies.
- At most 5 tasks per repository.
- A repository that yields fewer than 5 is replaced by the next reserve of the same language, in order.

| # | repository | language | source | tasks | why it is available |
|---|---|---|---|---|---|
| 1 | `hynek/structlog` | Python | held-out | 5 | in R3's first pool, short after R3's cutoff; not in R3's final pool |
| 2 | `Delgan/loguru` | Python | held-out | 5 | as above |
| 3 | `more-itertools/more-itertools` | Python | held-out | 5 | as above |
| 4 | `sdispater/pendulum` | Python | held-out | 5 | as above |
| 5 | `arrow-py/arrow` | Python | held-out | 5 | as above |
| 6 | `python-poetry/tomlkit` | Python | held-out | 5 | as above |
| 7 | `pytest-dev/pluggy` | Python | held-out | 5 | as above |
| 8 | `python-attrs/cattrs` | Python | held-out | 5 | scanned for R3, short |
| 9 | `lark-parser/lark` | Python | held-out | 5 | never used |
| 10 | `mahmoud/boltons` | Python | held-out | 5 | never used |
| 11 | `gvergnaud/ts-pattern` | TypeScript | held-out | 5 | in R3's first pool, short |
| 12 | `supermacro/neverthrow` | TypeScript | held-out | 5 | as above |
| 13 | `unjs/ofetch` | TypeScript | held-out | 5 | as above |
| 14 | `unjs/c12` | TypeScript | held-out | 5 | scanned for R3, short |
| 15 | `sindresorhus/p-queue` | TypeScript | held-out | 5 | scanned for R3, short |
| 16 | `unjs/ufo` | TypeScript | held-out | 5 | never used |
| — | R1's forward tasks (10 suite repositories) | 120 Python / 72 JS | original | 192 | A6's development set, fixed |

- **Reserves, in order:**
  - Python: `pyparsing/pyparsing`, `Suor/funcy`, `pallets/itsdangerous`, `jazzband/tablib`;
  - JS/TS: `unjs/defu`, `unjs/pathe`, `sindresorhus/p-map`, `lukeed/kleur`, `chalk/chalk`.
- **"Never used" was checked by `owner/repo` name** against everything in this repository, when this file was written.
- **Yield:** the forward miner's yield on these repositories has not been measured [TBD — the mining check, no model
  calls]. Small libraries (p-queue, ufo) may need their reserves.

**Overlap with R3:** none. Every repository above is outside R3's final pool: the 18 Family B repositories and the 7
relaxed Family N repositories. The R3-short ones are short only under R3's 2024-12-01 cutoff, which R2 does not
have. After R2, they count as used for any later round.
