# Change propagation from git history (sanity run)

Does PRISM's change context (`prism impact`, `prism.change_context`) help an AI find and correctly update
the code that must change when a function changes? The answer key is real history: commits in which a
developer changed a function (the anchor) and, in the same commit, the functions that call it.

This README, `mine.py`, `select.py` and `run.py` were committed **before any model ran on these tasks**.
It is a small **sanity run**, not a benchmark: 12 tasks, 4 per tier, results described, not tested for
significance.

## Tasks

1. `mine.py` scans the newest 1,500 non-merge commits of four repositories never used for tuning PRISM's
   change context: `pallets/flask`, `pallets/click`, `encode/httpx` (Python) and `expressjs/express`
   (JavaScript). It keeps commits that touch 1-6 non-test source files and at most 200 changed lines,
   where a changed function's old code calls another changed function. It then indexes the parent revision
   with PRISM and keeps the anchor when at least one of its production callers in the index changed.
2. `select.py` keeps a changed caller only when its own changed lines mention the anchor's name, or the
   anchor's parameter list changed (so the caller changed *because of* the anchor). It then picks one task
   per commit and at most two per repository per tier, in a seeded order (seed 1301), 4 per tier. It
   freezes `tasks.json` with the sha256 of the task list.
3. Tiers:

   | tier | rule |
   |---|---|
   | easy | one caller changed, in the anchor's own file |
   | medium | one or two callers changed, at least one in another file |
   | hard | three or more callers changed, or changed callers in two or more other files |

## Arms (same model, same instructions, same anchor change)

| arm | the model also sees |
|---|---|
| `FILE` | the anchor's file as an editor shows it: a window of at most 300 lines around the anchor, before the change |
| `PRISM` | `FILE` + PRISM's change context for the anchor, built at the parent commit (budget 6,000 tokens, production callers only) |

The model is asked to name every other function that must change, and to write its complete new code
(JSON). Model: `qwen2.5-coder:14b-instruct-q8_0` (the locked project model), temperature 0.2, seed 42,
context 16,384 tokens. One call per task and arm, 24 calls in all.

## Scores (per task and arm, against the commit)

| score | meaning |
|---|---|
| callers found | changed callers the model named (by name, class checked when given) |
| tasks with every caller found | tasks where nothing was missed |
| extra functions named | functions named that the commit did not change |
| added lines kept | for each found caller, the share of the lines the developer added that appear in the model's code |
| removed lines gone | the share of the lines the developer removed that no longer appear |
| new code parses | the model's code for the caller parses (Python `ast`, tree-sitter otherwise) |

Reported per tier and overall. No arm "wins" by rule: with 12 tasks this is a sanity check of whether the
package helps, hurts or does nothing. It decides whether a larger, pre-registered run is worth doing.

## Run

```
python -m harness.experiments.change_history.mine OUT flask=CLONE click=CLONE httpx=CLONE express=CLONE
python -m harness.experiments.change_history.select OUT/candidates.json harness/experiments/change_history
python -m harness.experiments.change_history.run prompts harness/experiments/change_history/tasks.json OUT
python -m harness.experiments.change_history.run generate harness/experiments/change_history/tasks.json reports/change_history
python -m harness.experiments.change_history.run score harness/experiments/change_history/tasks.json reports/change_history
```

`kaggle/change_history_cell.py` runs `generate` and `score` on Kaggle (the tasks and prompts are frozen in
`tasks.json`; Kaggle needs no repository checkouts).
