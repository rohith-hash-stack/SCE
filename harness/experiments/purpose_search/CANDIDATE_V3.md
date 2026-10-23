# Summarizer candidate v3: a stricter lint for name-restating summaries

**Status: implemented, unvalidated. v2 stays locked and shipped** until the
plan below says otherwise. A v3 index is used by `prism.ask` at weight 0.

**Plan approved** (Stage 1 gates, set 6 repositories and the set 6 rule below,
as written) before any Stage 1-3 result existed.

**Outcome: both candidates failed Stage 1 and are dropped; v2 stays locked.**
`reports/summary_candidates_stage1/STAGE1.md`: after repair v3-name leaves
restates_name at 10.3% (gate 10%), v3 at 12.3% with too_short at 20.7% (gate
15%); nothing lost, v2 flags clean. Repair rephrases more than it adds
(`reports/summary_candidates_stage1/NOTES.md`). Stages 2 and 3 do not run
for these candidates; set 6 is not written.

## Why

Set 5 (Hono) did not confirm the summary field (C vs B 6 better / 6 worse,
`reports/heldout_set5/NOTES.md`). Its two deep losses were on summaries that
restate the function's name ("Matches a method and path to a result." for
`SmartRouter.match`). The v2 prompt forbids that, but the v2 lint does not
check it, so repair never sees those summaries.

## The candidates

All three share the v2 summarizer exactly: model, card, `SUMMARY_PROMPT`,
`REPAIR_PROMPT`, two repair attempts. Only the lint that decides what goes to
repair differs (`prism.summaries.lint`, `prism.summaries.pipeline.CANDIDATES`):

| candidate | extra lint checks | meaning |
|---|---|---|
| **v2** (locked) | none | as shipped |
| **v3** | `restates_name`, `too_short` | at most 2 content words beyond the function's (and its class's) name and generic filler; or under the prompt's 8-word floor |
| **v3-name** | `restates_name` | ablation: the name rule without the length rule |

Because the summary prompt is unchanged, every v2 summary keeps its card key:
a v3 index is built from a v2 one by re-linting and repairing only (`prism
summarize --candidate v3`), never by re-summarizing. A v3 -> v2 run starts
over, so a locked v2 index never holds text a candidate's repair wrote.

## Stage 0 - offline (done)

- `tests/test_summary_lint_v3.py`: the flags, the repair messages, v2 -> v3
  conversion repairs only flagged summaries and is ignored by search, v3 -> v2
  starts over, harness caches installed as v3 are re-linted.
- v2 is unchanged: lint and repair messages identical to the previous code on
  1,057/1,057 real summaries (click, Hono); both live indexes still match the
  locked pipeline.
- Repair budget on the summaries we have (first pass, before repair):

  | repo | summaries | v3 flags | | repo | summaries | v3 flags |
  |---|---|---|---|---|---|---|
  | fastapi_template | 71 | 41% | | httpie | 615 | 52% |
  | styleguide | 203 | 52% | | fastify | 800 | 59% |
  | express | 270 | 43% | | zod | 274 | 44% |
  | trpc | 1,099 | 53% | | click | 512 | 41% |
  | flask | 366 | 37% | | hono | 555 | 37% |

  4,765 summaries, 2,294 flagged by v3 (48%), at most 4,588 repair calls;
  `restates_name` alone flags 17-37% per repo. Most of v3's extra flags are
  the length rule - 25-53% of summaries per repo are under 8 words, including
  the summaries that won set 4. That is why the v3-name ablation exists.

## Stage 1 - conversion on Kaggle (a gate, not a decision)

Convert all ten indexes (the eight sets 2-4 caches via `--from-cache`, click
and Hono from their live indexes) to v3 and to v3-name with the production
command, `prism summarize --candidate <c>` - `stage1.py run`, on Kaggle with
`kaggle/summary_candidates_cell.py`. On the current engine the sets 2-4 caches
install 38-97% of their entries (their cards were built with dependency
summaries, and the const-binding fix changed JS/TS call edges), so the v2 step
first summarizes the rest (~512 functions) with the locked v2. Approved gates
(`stage1.py gate`), per candidate, pooled over the ten repos after repair:

- its new flags: `restates_name` <= 10%, `too_short` <= 15% (repair must fix
  most of what it flags - some functions are genuinely trivial);
- the v2 flags stay <= 5% (repair must not introduce identifiers or filler);
- no summary lost; every entry `checked`.

A candidate failing a gate is dropped (its repair cannot do what the lint
asks), and the report shows before/after samples.

## Stage 2 - sets 2-5 (diagnostic only)

Score v2, v3 and v3-name at w = 0.5 against BASE on sets 2-5, through the
shipped `NLQIndex(summaries=..., summary_weight=0.5)`. These sets have all
been scored before, so this decides nothing on its own. Stop rule: a
candidate with more questions worse than better than v2, pooled over sets
2-5, is dropped before stage 3 (it would only spend the fresh set).

## Stage 3 - fresh set 6 (the decision)

A new held-out set, written and frozen before any engine, search, ranking or
summarizer runs on its repositories (set 5's protocol and `check.py`):

- 60 questions over 4-5 repositories none of sets 1-5 used, Python and
  TypeScript mixed, each with >= 300 library functions; chosen by these
  criteria before any is indexed (proposal: `encode/httpx`,
  `Textualize/rich`, `koajs/koa`, `sindresorhus/ky` - approved). click
  is excluded: the first live run printed searches on it.
- 60 rather than 48: candidates differ from v2 only on repaired functions, so
  most questions will tie (set 5: 18 of 30 tied C vs B).
- Summaries: v2 summarized fresh with the locked pipeline, then converted to
  v3 / v3-name by repair only - the arms differ in exactly the lint.

Arms, at w = 0.5 except BASE: **BASE**, **V2**, **V3**, **V3N**.

> **Approved rule (fixed before set 6 is written).** A candidate X
> (v3 or v3-name) qualifies if (1) X beats V2: more questions better than
> worse with a two-sided sign test p < 0.025 (0.05 split over the two
> candidates), and recall@5 at most 0.02 below V2; and (2) X beats BASE
> under set 4's rule (p < 0.05, recall@5 within 0.02). If both qualify, ship
> v3-name (the smaller change) unless V3 beats V3N with p < 0.05. If neither
> qualifies, v2 stays locked.

Shipping a candidate means: `locked_pipeline()` records its lint, the index
weight rule accepts it, and existing v2 indexes are upgraded by `prism
summarize --candidate <c>` (repair only).
