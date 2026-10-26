# Summary grounding: do summaries written from the function's own code rank better?

Written and committed **before any run**. The rule below decides the reading of the result; it is not tuned after.

## Question

The shipped summarizer (v2, `prism.summaries.pipeline`) writes each function's one-sentence summary from a
profile card that does **not** contain the function body: name, scope, decorators, signature, caller and
callee names, raises, docstring. An audit of 60 set-6 functions found v2 summaries covered 9 of 73
important facts; the missed ones were conditions (31/34), behaviour (15/16), exceptions (14/14) and state
changes (3/3) - all inside the body. Does writing the summary from the body improve `prism.ask` ranking?

## Arms (all through the shipped `NLQIndex`, test files excluded, summary weight 0.5)

| arm | summary text | source |
|---|---|---|
| `BASE` | none | - |
| `V2` | the locked v2 summaries | `reports/summary_candidate_v5/set6/<repo>/v2.json` (unchanged, already tracked) |
| `SRC` | one sentence written from the v2 card **plus the function's source** (first `BODY_CAP` = 60 lines, numbered) | generated here |
| `SRCC` | the same input plus the resources the body touches (`prism.summaries.cards.resources`); the model first lists facts about the function's own code, each with its line numbers (condition, exception, state_change, return, external_call, computation), then writes the sentence from those facts only | generated here |
| `SRCC_PLUS` (diagnostic) | `SRCC` sentence + the text of its validated facts | derived from `SRCC`, no extra calls |

Fixed across arms:

- **Generation settings:** model `qwen2.5-coder:14b-instruct-q8_0` (the locked model), the shipped Ollama client and sampling (`prism.summaries.pipeline.Ollama`: temperature 0.4, seed 42, num_ctx 8192).
- **Sentence rules:** the same as v2 (8-14 words, no code identifiers, no callee lists, no internal labels).
- **Lint and repair:** v2's (`prism.summaries.lint`, `pipeline.repair`, at most 2 rewrites).
- **Functions:** every function and method in the NLQ pool of each set-6 repository.

## Data

Set 6 (frozen, 120 questions, httpx / rich / elysia / oclif at the pinned commits; the run refuses an
unfrozen or altered set).

**Caveat:** set 6 has been scored before. V5 was decided on it, and post-hoc analyses have read its per-question ranks. So this run is a **development comparison**, not a held-out claim. A result here justifies a run on a new frozen set; it does not by itself justify shipping.

## Metrics (identical to `harness/experiments/heldout_retrieval/set6/score.py`)

- Per question: the best-ranked gold symbol (unranked = worst).
- Recall@5, recall@20 and MRR over the gold symbols.
- Paired comparison: better / worse / ties on the best gold rank, with a two-sided exact sign test (`set5.score.compare`).

## Pre-registered reading

- **An arm "beats" another arm** iff all three hold: more questions better than worse, sign p < 0.05, and mean recall@5 at most 0.02 lower.
- **Primary contrasts:** `SRC vs V2` and `SRCC vs V2`.
- **Secondary contrasts:** `SRC vs BASE`, `SRCC vs BASE`, `SRCC vs SRC`.
- **Diagnostic only:** `SRCC_PLUS`, per-repository tables, and the split by whether the gold function's own summary changed.
- **Validity check:** `V2 vs BASE` must reproduce the recorded set-6 result (37 better / 20 worse, recall@5 0.758 vs 0.700, `reports/heldout_set6/DECISION.md`). If it does not, the run is reported as invalid.

Also recorded:

- generation cost (calls, failures, repairs, seconds);
- the share of functions whose body was cut at 60 lines;
- for `SRCC`, how many listed facts cited lines outside the function and were dropped;
- the summaries of the 60 functions from the earlier accuracy audit (`accuracy_sample.json`), for fact-coverage scoring afterwards.

## Run

```
python -m harness.experiments.summary_grounding.run generate <apps_dir> <out_dir> --base-urls http://127.0.0.1:11434
python -m harness.experiments.summary_grounding.run score <apps_dir> <out_dir>
```

`<apps_dir>/<repo>` holds the pinned checkout (`kaggle/summary_grounding_cell.py` prepares it). Generation is
resumable: summaries are checkpointed to `<out_dir>/<repo>/<arm>.json`.
