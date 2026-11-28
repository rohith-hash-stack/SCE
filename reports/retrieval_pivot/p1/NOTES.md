# P1 outcome (offline, no model)

`P1.md` (this directory, the run's own report) and `p1.json` (per case, arm and budget).
Pre-registered plan: `harness/experiments/retrieval_pivot/P1.md`.

**A1 passes P1** - on all cells and on the feasible cells only. Feasible cells are the 71 of 87
case x budget cells where every arm fits the budget; the other 16 have a seed alone, or the
packer's mandatory additions, larger than the budget, identical in every arm.

| feasible cells | A0 gold present / tokens | A1 | summary share of A1 |
|---|---|---|---|
| 2k (83 gold) | 36 / 1,709 | 42 / 1,469 | 6.5% |
| 4k (148 gold) | 63 / 3,119 | 65 / 2,338 | 6.2% |
| 8k (204 gold) | 102 / 5,316 | 106 / 3,745 | 4.8% |

## What the numbers say

1. **The gain is compression, not the summary.** A1n (outer nodes as signature + contract, no
   summary) retains as much gold as A1 on every feasible budget (42 / 65 / 108 vs 42 / 65 / 106),
   in slightly fewer tokens. Offline, the summary line is pure cost (about 5% of the payload).
   Whether it helps a model read the payload is P3's question.
2. **Compression trades fidelity.** A1 delivers gold that A0 sent as code only as signature +
   contract + summary: gold at full body 99 -> 78 at 8k, 62 -> 51 at 4k, for +4 / +2 present.
3. **Compressing the immediate helpers (A2)** gives the most gold present (+14 at 4k on feasible
   cells), but no gold at full body at any budget - the "lack of concrete syntax" risk, in full.
4. **Enriching helpers with summaries (A3)** adds 50-80 tokens over A1 and no gold (equal or one
   less): noise, unless P3 shows the text helps.
5. **The bigger limit is reach, not tokens.** Only 126 of the 204 gold nodes are inside the
   packer's candidate pool at all (its hop limits). At 8k, A1 stops at 3.7k tokens on average, A0
   at 5.3k: budget is left over while 78 gold nodes are never considered. Widening the walk when
   budget remains is a separate, likely larger, lever.
6. **Noise share is 54-70% in every arm** (tokens on nodes neither seed nor gold). Gold lists are
   not exhaustive, so this is an upper bound on noise.

## Caveats

- Summaries: the locked v2 pipeline covers search-pool functions. 77 of Django's 466 candidate
  functions (test-suite code under `tests/` and `django.test`) have no summary and render as
  signature + contract in every summary arm (7 of the 103 Django gold nodes are among them).
- A0 is P1's own baseline (it prices rendered text). Its selection overlaps the production packer's
  at a mean Jaccard of 0.76 on feasible cells (min 0.30).
- Offline P1 measures what is delivered, not whether a model understands it or hallucinates.

## Next

- **P3 (needs a model).** Payloads A0, A1n and A1 at 4k and 8k for the same cases. A1 vs A1n
  isolates the summary text; A1n vs A0 isolates the fidelity cost of compression. Grading: Layer
  4 execution and Q2E grounding, under a rule fixed before it runs.
- **Separately: packer reach.** Admit candidates beyond the hop limits while budget remains, and
  measure gold present against the 126 / 204 ceiling. Offline, cheap, no summaries needed.
