# Change propagation from git history (sanity run)

Does PRISM's change context (`prism impact`, `prism.change_context`) help an AI find and correctly update
the code that must change when a function changes? The answer key is real history: commits in which a
developer changed a function (the anchor) and, in the same commit, the functions that call it.

This README, `mine.py`, `select.py` and `run.py` were committed **before any model ran on these tasks**.
It is a small **sanity run**, not a benchmark: up to 4 tasks per tier, results described, not tested for
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
   | easy | one or two callers changed, all in the anchor's own file |
   | medium | one or two callers changed, at least one in another file |
   | hard | three or more callers changed, or changed callers in two or more other files |

## Arms (same model, same instructions, same anchor change)

| arm | the model also sees |
|---|---|
| `FILE` | the anchor's file as an editor shows it: a window of at most 300 lines around the anchor, before the change |
| `PRISM` | `FILE` + PRISM's change context for the anchor, built at the parent commit (budget 6,000 tokens, production callers only) |

The model is asked to name every other function that must change, and to write its complete new code
(JSON). Model: `qwen2.5-coder:14b-instruct-q8_0` (the locked project model), temperature 0.2, seed 42,
context 20,480 tokens. One call per task and arm.

## Scores (per task and arm, against the commit)

| score | meaning |
|---|---|
| callers found | changed callers the model named (by name, class checked when given) |
| tasks with every caller found | tasks where nothing was missed |
| extra functions named | functions named that the commit did not change |
| added lines kept | for each found caller, the share of the lines the developer added that appear in the model's code |
| removed lines gone | the share of the lines the developer removed that no longer appear |
| new code parses | the model's code for the caller parses (Python `ast`, tree-sitter otherwise) |

Reported per tier and overall. No arm "wins" by rule: with 11 tasks this is a sanity check of whether the
package helps, hurts or does nothing. It decides whether a larger, pre-registered run is worth doing.

## Frozen tasks

`tasks.json` (sha256 of the task list `05244c3b0df47adc…`, in the file): 11 tasks, 4 easy, 4 medium and 3 hard.
Only 3 hard tasks remain after the filter in the mined pool (89 candidates, 50 after the filter, 28 commits).
Two hard tasks (ch09, ch10) share an anchor, `httpx._api.request`, from two different commits; the rules
allow one task per commit, so both stay.

Corrections made after selection and before any model ran:
- the easy rule above now states what `mine.tier` always did: one or two callers changed, all in the
  anchor's own file (the text said "one caller");
- the context was raised from 16,384 to 20,480 tokens, because the largest PRISM prompt (ch08, about 12,700
  tokens) plus a 4,096-token answer did not fit. It applies to both arms.

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

---

# Run 2: forced changes, three versions (pre-registered before selection)

The sanity run above showed that some "easy" commits changed callers for reasons the anchor's change did not
force (the developer's wider intent). Run 2 keeps only **forced** changes and adds a third version: the
map of the repository and the second ring of related functions.

## Tasks

- `mine.py --forced --scan 4000 --max-candidates 200` over six repositories: flask, click, httpx, express
  (as above) and `pallets/jinja`, `psf/requests` (never used for tuning).
- `select.py --forced --seed 2601 --per-tier 4` writes `forced/tasks.json`. A task is kept only when the
  anchor's change would break callers that were not updated (`select.forced_reasons`):
  - a parameter removed or renamed;
  - a new parameter without a default;
  - a changed set of raised exceptions;
  - returning nothing added or removed.

  A caller counts only when its own changed lines touch the call (`select.touches_call`). The same tiers,
  one task per commit, at most two per repository per tier. Tasks from the run-1 commits are allowed;
  run 1's results are not used for anything here.
- `plan.py` then adds each task's map and second-ring plan (`prism.impact.overview.plan_overview`, no
  model), after checking the rebuilt change context equals the frozen one.

## Versions (same model and settings as run 1)

| version | the model also sees |
|---|---|
| `FILE` | the anchor's file window (300 lines), as in run 1 |
| `PRISM` | `FILE` + the change context (first version, budget 6,000, production callers), as in run 1 |
| `PRISM_MAP` | `PRISM` + the map (two sentences for the repository, one per main folder, one per file near the change) + the second ring (one line each for the callers of the callers and for similar functions) |

The map and second-ring sentences are written once per task by the same model, before any version is
answered (`run.py overview`):
- files from their outline;
- folders from their files' sentences;
- the repository from its README and folder sentences;
- second-ring functions from their v2 summary cards with the shipped summarizer prompt.

## Selected and planned (before any model ran)

`forced/tasks.json`: 214 of 319 candidates pass the forced filter (139 commits). Twelve were chosen,
4 easy, 4 medium and 4 hard, from all six repositories. Task-list sha256: `36aa60fd…` (full value in the file).

Changes made while planning, before any model saw a task:
- **Stable convention order.** Conventions tied on count (for example two helpers each "called by 3 of
  28") were listed in Python's per-process set order, so a rebuild could list them in another order
  (ch08). Ties are now broken by name. All 12 frozen contexts rebuild byte for byte.
- **Real repository name.** The map named the temporary worktree. It now uses the task's repository name.
- **"Similar" needs a specific word.** A shared generic name word alone (`get`, `set`, `make`, … —
  `overview.GENERIC_WORDS`) no longer makes a function similar. Sharing callees, or a specific name word
  such as `size` or `terminal`, still does. This removed noise like `get_app_dir` for `get_terminal_size`.

Prompt sizes, measured with a fake model server (same tokenizer as run 1; the map and ring sentences at
20 words each):

| | FILE | PRISM | PRISM_MAP |
|---|---|---|---|
| mean tokens | 4,204 | 7,074 | 7,674 |
| largest (ch02) | 6,002 | 10,144 | 10,992 |

The largest prompt is well below the 16,384 left after the 4,096 output tokens.

## Scores and reading

The same scores as run 1, per tier and overall, described with no significance test. The questions are:
- does `PRISM_MAP` find callers that `PRISM` misses;
- does it name more wrong functions;
- does it cost more tokens and time.

A larger pre-registered run follows only if `PRISM_MAP` is at least as good as `PRISM`.

```
python -m harness.experiments.change_history.mine OUT --forced --scan 4000 --max-candidates 200 NAME=CLONE ...
python -m harness.experiments.change_history.select CANDIDATES.json harness/experiments/change_history/forced --forced --seed 2601
python -m harness.experiments.change_history.plan harness/experiments/change_history/forced/tasks.json NAME=CLONE ...
python -m harness.experiments.change_history.run overview harness/experiments/change_history/forced/tasks.json reports/change_history_forced
python -m harness.experiments.change_history.run generate harness/experiments/change_history/forced/tasks.json reports/change_history_forced
python -m harness.experiments.change_history.run score harness/experiments/change_history/forced/tasks.json reports/change_history_forced
```
