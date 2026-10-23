# Set 5 (Hono): diagnostics after the single scoring run

Not part of the pre-registered rules (`DECISION.md` holds the binding result);
read after scoring, never used to edit the set or the rules.

**Results.** Rule 1: keep the test-file exclusion (B vs A 5 better / 4 worse /
21 ties, recall@5 +0.033). Rule 2: not confirmed on Hono (C vs B 6 / 6 / 18,
sign p = 1.0), although recall@5 rose 0.683 -> 0.767 and MRR +0.036.

**Ceiling.** 12 of 30 questions have their gold at rank 1 in every arm: Hono's
function names are descriptive (`bodyLimit`, `csrf`, `getPath`, `writeSSE`),
so BM25 over the code already answers them. 18 of 30 C-vs-B comparisons tie.

**Wins are large, losses mostly small.** C helped hn29 `escapeToBuffer`
28 -> 2, hn19 `timingSafeEqual` unranked -> 41, hn11 6 -> 3, hn23 5 -> 3,
hn10 3 -> 2, hn26 2 -> 1. It hurt hn06 2 -> 3, hn16 2 -> 3, hn04 12 -> 14,
hn21 20 -> 25, and two deep ones: hn14 `timeout` 36 -> 52 and hn22
`SmartRouter.match` 40 -> 61. The sign test counts each question once,
whatever the distance.

**Losses come from summaries that restate the name.** hn22's gold:
"Matches a method and path to a result."; hn21's: "Sorts items by quality and
index."; hn14's: "Creates a timeout middleware for Hono with a specified
duration and exception." None states the behaviour the question asks about.
Across Hono's 555 summaries, 88 (16%) add at most three content words beyond
the function's own name and 148 (27%) are under the prompt's 8-word minimum.
The prompt forbids restating the name; the lint does not check it, so repair
never sees these.

**Candidate for the next summarizer version (needs its own fresh
validation):** a lint flag for name restatement and under-length summaries,
feeding the existing repair pass.
