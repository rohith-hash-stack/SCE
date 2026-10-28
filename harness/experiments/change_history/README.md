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

# Run 3: the break check (pre-registered before selection)

Run 2 showed that the model misses callers it can see: in 6 tasks it answered "nothing to change" next to a
call that the change breaks. Run 3 tests `prism.impact.breaks`. Given the anchor's new code, it checks
every call site the way the language binds arguments and states which calls fail or change meaning. The
check was designed after reading run 2's misses, so run 3 uses **new commits only**.

## Tasks

- `select.py --forced --seed 3301 --per-tier 8 --exclude tasks.json forced/tasks.json` writes
  `breaks/tasks.json`. It uses the same mined candidates and rules as run 2, minus the commits of runs 1
  and 2. The pool has only four hard commits, so the hard tier will be small.
- `plan.py --breaks` adds `breaks_context` to each task: the frozen change context with the break
  section at the top. It is computed at the parent revision from the anchor's new code (no model). It
  checks that the context without the section equals the frozen `prism_context`.

## Versions (same model and settings as runs 1 and 2)

| version | the model also sees |
|---|---|
| `FILE` | the anchor's file window |
| `PRISM` | `FILE` + the change context, as in runs 1 and 2 |
| `PRISM_BREAKS` | `FILE` + the change context with the break section ("Calls this change breaks") |

## The check without a model

On the run-3 tasks:
- **recall:** changed callers with at least one `fails` or `changes meaning` statement;
- **false alarms:** unchanged production callers with such a statement.

The same two numbers are reported over all the other forced candidates as development data. There they
are only descriptive: the check's rules are frozen with this commit and are not changed after these numbers
are seen.

## Scores and reading

The same scores as runs 1 and 2, per tier and overall, with no significance test. The questions are:
- does `PRISM_BREAKS` find callers that `PRISM` misses;
- does it name more wrong functions, especially callers the check flagged wrongly.

If `PRISM_BREAKS` finds more callers without more wrong functions, it becomes the default whenever new
code is known (an agent's proposed edit), and the bigger test follows.

## Run 3: selected and planned (before any model ran)

`breaks/tasks.json`: 190 forced candidates in 124 new commits pass the filter. Nineteen were chosen: 8 easy,
8 medium and 3 hard (only four hard commits remained). Task-list sha256: `48841c54…`.

- **ch19:** the anchor `Environment.compile` calls itself, so it is also one of its own changed callers.
  An answer naming it is scored as the anchor, so ch19 can find at most 2 of its 3 callers.
- **ch17:** its frozen context was mined before ties in conventions were broken by name. It was rebuilt with
  the same lines in the stable order (`plan.py`).

The check on the 19 tasks, without a model (rules frozen in this commit):

| | flagged `fails` / `changes meaning` |
|---|---|
| callers that had to change | 23 of 32 |
| production callers that did not change (false alarms) | 0 of 9 |

Prompt sizes (mean / largest tokens): FILE 3,578 / 5,567; PRISM 6,133 / 9,895; PRISM_BREAKS 6,329 / 10,096.

Development data (after the rules were frozen; `breaks/dev_check.json`): the forced candidates of the 124
other commits, with the same rules.

| | flagged |
|---|---|
| callers that had to change | 131 of 221 (59%) |
| production callers that did not change | 14 of 96 (15%) |

- **False alarms:** six of the 14 come from one httpx commit. There, `TextDecoder.decode` calls
  `self.decoder.decode(...)`, which is linked to `GZipDecoder.decode` by name only; the statement says so
  ("possible"). The others are mostly "too many positional arguments" in Flask callers that may have been
  changed in a later commit.
- **Misses:** recall depends on what changed. When the parameter list changed, 131 of 167 changed callers
  are flagged (78%). When only the raised errors or empty returns changed, 0 of 54 are: those get only a
  `check` statement, by design, and are not counted here.

## Note: token counts and the context window (checked after run 2)

The token figures above come from PRISM's tokenizer. On these machines it falls back to a regex
approximation (`prism.slicer.tokenizer.is_exact()` is False), which overcounts. Recounted with the model's
own tokenizer (Qwen2 `tokenizer.json`, the same as Qwen2.5's):

| | mean prompt tokens (real) | approximation said |
|---|---|---|
| run 1 FILE / PRISM | 3,071 / 5,692 (1.85x) | 4,701 / 8,208 (1.75x) |
| run 2 FILE / PRISM / PRISM_MAP | 2,689 / 4,731 / 5,209 | 4,204 / 7,074 / 7,675 |
| run 3 FILE / PRISM / PRISM_BREAKS | 2,307 / 4,275 / 4,428 | 3,578 / 6,133 / 6,329 |

No call came near its window:
- The largest prompt plus answer was 11,136 tokens of the 20,480 window (run 1, ch09).
- The longest answer was 3,083 tokens of the 4,096 cap.
- All 58 answers of runs 1 and 2 are complete JSON.
- Run 3's largest prompt is 6,902 tokens; with a full 4,096-token answer it would still be 10,998.
- The map calls of run 2 (8,192 window) were at most 1,583 tokens, and none came back empty.

# Run 4: the big test (pre-registered before mining)

Run 3: the break check gained 4 callers where it fired and lost 4 where it was silent or partial (the model
read "No call site breaks" as "nothing to change", and a partial list as complete). Run 4 tests the
revised section (**wording 2**, `prism.impact.breaks.render_breaks`):
- when the check finds nothing, the section is left out (the prompt is then exactly PRISM's);
- when it finds breaks, it says the list is not complete.

It is tested on a much larger set from repositories never used before.

## Repositories (none used in runs 1-3, or to design the check)

Python: `pallets/werkzeug`, `encode/starlette`, `fastapi/fastapi`, `python-attrs/attrs`, `Textualize/rich`,
`marshmallow-code/marshmallow`, `tornadoweb/tornado`, `aio-libs/aiohttp`, `scrapy/scrapy`, `httpie/cli`,
`sanic-org/sanic`, `pydantic/pydantic`.

JavaScript / TypeScript: `koajs/koa`, `axios/axios`, `tj/commander.js`, `yargs/yargs`, `mochajs/mocha`,
`fastify/fastify`, `socketio/socket.io`, `hapijs/hapi`, `date-fns/date-fns`, `sindresorhus/got`.

A repository that cannot be cloned or indexed is dropped and listed in the report. None is replaced after
its tasks have been seen.

## Tasks

- **Mining:** `mine.py --forced --scan 10000` over each repository's history, with every pass-1 candidate
  indexed (no candidate cap).
- **Selection:** `select.py --forced --no-self-callers --seed 4401 --per-tier 60 --per-repo-tier 3`. The same filter and
  tiers as runs 2-3, at most 3 tasks per repository per tier, up to 60 per tier (180 tasks).
- **New quality rule:** an anchor never counts as its own caller (a recursive call). Run 3's ch19 could
  not be fully scored because of this; such callers are dropped before selection.
- **Planning:** `plan.py --breaks --no-overview` adds `breaks_context` with wording 2.
- **Frozen before any model runs:** the task list sha256 is pinned in the Kaggle cell.

## Versions (same model and settings as runs 1-3)

| version | the model also sees |
|---|---|
| `FILE` | the anchor's file window |
| `PRISM` | `FILE` + the change context, as in runs 1-3 |
| `PRISM_BREAKS` | `FILE` + the change context with the wording-2 break section (none when nothing breaks) |

Each answer also records Ollama's own prompt and answer token counts and its stop reason, so a cut-off
answer or prompt is visible directly.

## Scores and tests (fixed now)

The same scores as runs 1-3, per tier, per language and overall. With this many tasks, two comparisons
are tested; everything else is descriptive:

1. **Primary: `PRISM_BREAKS` vs `PRISM`**, on changed callers found. McNemar's exact test over the
   callers both versions were scored on (found by one version only), two-sided, α = 0.05. Also a
   bootstrap 95% interval (10,000 resamples over tasks, seed 4401) of the difference in callers found per
   task.
2. **Secondary: `PRISM` vs `FILE`**, with the same tests.

Wrong functions named are compared per task with the Wilcoxon signed-rank test (descriptive if all
differences are zero).

## Decision rule (fixed now)

- `PRISM_BREAKS` becomes the default when new code is known only if both hold:
  - it finds significantly more callers than `PRISM`;
  - its mean number of wrong functions per task is no higher than `PRISM`'s.
- If it finds more callers but not significantly, it stays optional and the report says so.

The run may take several Kaggle sessions: the cell resumes from the answers already pushed.
