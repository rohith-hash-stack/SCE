# The breadth study's tasks: 32 admitted, not yet frozen (2026-11-28)

**Status:**
- Step 2 of `DESIGN.md` (mine and admit) is done. All 32 tasks passed every admission check.
- **Not frozen.** The list, its sha256 and the analysis go into `AMENDMENTS.md` only with the user's approval (step 4).
  Nothing in this file has been run by a model.

| | |
|---|---|
| File | `tasks.json`, 32 tasks, `tasks_sha256` = `d8f8c5b4a212f7824a4462bda8853521fc3fa9ea4ca40c2eccd4c27afdd2e881` |
| Answer keys | `gold/<id>.patch`: never given to a run; used only by the dry run |
| Admission record | `admission/dry_runs.json`: every dry-run outcome, per task, arm and script |
| Repositories | 14: aiohttp, arrow, cattrs, fastmcp, instructor, marshmallow, packaging, pendulum, pydantic, pydantic-ai, rich, scrapy, sqlglot, werkzeug |

## 1. The tasks

| Id | Family | Repo | Commit | Target | Files in the reference change |
|---|---|---|---|---|---|
| p3-hidden-05 | rename | fastmcp | 7d40b1dd4c | `Transform` (class) | 10 |
| p3-hidden-07 | rename | scrapy | 60b1badacd | `Rule` (class) | 4 |
| p3-hidden-08 | rename | instructor | 6969bb6849 | `process_response` | 3 |
| p3-hidden-09 | rename | pydantic-ai | 5f5a9eac51 | `AbstractAgent` (class) | 7 |
| p3-hidden-10 | rename | werkzeug | c993048614 | `Data` (class) | 3 |
| p3-hidden-11 | rename | aiohttp | 3c608498c6 | `RouteTableDef` (class) | 4 |
| p3-hidden-12 | rename | aiohttp | 3c608498c6 | `ClientPayloadError` (class) | 5 |
| p3-hidden-14 | rename | pydantic-ai | 5f5a9eac51 | `format_as_xml` | 5 |
| bd-signature-01 | signature | aiohttp | f903060fd4 | `MultipartWriter.append_payload` | 2 |
| bd-signature-02 | signature | arrow | 2224255c4a | `get_locale` | 4 |
| bd-signature-03 | signature | arrow | 2224255c4a | `Arrow.astimezone` | 1 |
| bd-signature-04 | signature | cattrs | d043dbdf0d | `fields` | 3 |
| bd-signature-05 | signature | cattrs | d043dbdf0d | `is_generic` | 7 |
| bd-signature-06 | signature | marshmallow | b762ea2d59 | `is_collection` | 3 |
| bd-signature-07 | signature | packaging | ae4946b30f | `_ErrorCollector.error` | 4 |
| bd-signature-08 | signature | packaging | ae4946b30f | `_ErrorCollector.finalize` | 3 |
| bd-move-01 | move | cattrs | d043dbdf0d | `raise_error` | 4 |
| bd-move-02 | move | cattrs | d043dbdf0d | `is_namedtuple` | 6 |
| bd-move-03 | move | pendulum | e06dacedc4 | `week_day` | 3 |
| bd-move-04 | move | pendulum | e06dacedc4 | `local_time` | 3 |
| bd-move-05 | move | rich | 9d8f9a372c | `blend_rgb` | 6 |
| bd-move-06 | move | rich | 9d8f9a372c | `pick_bool` | 5 |
| bd-move-07 | move | sqlglot | 6de2df31b0 | `name_sequence` | 8 |
| bd-move-08 | move | sqlglot | 6de2df31b0 | `remove_unique_constraints` | 6 |
| bd-delete-01 | delete | pydantic | f002759018 | `get_type_ref` | 5 |
| bd-delete-02 | delete | instructor | 7ca007384e | `AnthropicHandlerBase._finalize_parsed_result` | 5 |
| bd-delete-03 | delete | fastmcp | b672468d4f | `Context.request` | 3 |
| bd-delete-04 | delete | marshmallow | 50b2849cca | `validate_unknown_parameter_value` | 4 |
| bd-delete-05 | delete | packaging | 664f049980 | `_coerce_version` | 2 |
| bd-delete-06 | delete | werkzeug | dc1993492e | `get_path_info` | 1 |
| bd-delete-07 | delete | sqlglot | 6fc77ce335 | `MySQL.Parser._parse_partition_bound_values` | 3 |
| bd-delete-08 | delete | werkzeug | 51926a7a88 | `_get_args_for_reloading` | 6 |

**Repositories per task:** cattrs 4; aiohttp, packaging, sqlglot, werkzeug 3 each; the other nine 1–2 each. Within a
family, no repository has more than 2.

**Held out:** no model has run any of these in any phase.
- The renames are Phase 3's never-run candidates.
- Signature and move are new derivations, on repositories Phase 2 never used.
- Deletes are new commits. The 7 commits of earlier delete tasks in this pool were removed before gating; the other 3
  are in JavaScript repositories outside it.

## 2. Admission (`DESIGN.md` section 5): every check passed

| Check | Rename | Signature, move | Delete |
|---|---|---|---|
| 1. Scoring | `derive_scoring_check`, re-run on today's code: reference resolves, empty does not; the done gate accepts the reference, rejects the empty | same, 16 of 16 | R4's own steps: `sanity_tasks` (R3's gate: the developer's change resolves, the start does not) and `sanity_gate_tasks` (the done gate accepts the developer's change, rejects the start) |
| 2. Dry run, both arms, `--guards --edit-fix` | reference resolved, empty stuck: 8 of 8 | 16 of 16 | 8 of 8 (with `--all-families`) |
| 3. Gateway check: no gateway block of a reference edit | 0 blocked, 8 of 8 | 0 blocked; **the gateway is inactive** (section 4) | 0 blocked; **inactive** |
| 4. Turn sentence says 60 turns | 32 of 32 | | |

**The reference replay's turns** (scripted, BASELINE_LSP; PRISM_GW_C needs 3 fewer):
- most tasks take 9–31 of the 60 turns;
- **bd-delete-01 takes 48, and bd-delete-05 39.** These are the tightest: a model making the same edits one by one has
  little spare.

**Attrition:**
- **Rename:** none. The first 8 in the rule's order were admitted; spares p3-hidden-17 to -20 were not needed.
- **Signature, move:** 49 candidates tried by `derive_tasks`. They failed on: the tests failing at the start; the
  reference failing the tests; no rewritable signature; a method of a class with an outside base; pydantic's
  environment failing to install.
- **Delete:** 43 commits tried at the gate, 14 passed. Of the 29 failures, 24 were related tests already failing at the
  commit (15 in pydantic-ai, 6 in scrapy), 3 were installs failing and 2 had no related test file. pydantic-ai accounts for
  18 of the 29. Of the 14 passes, one, fastmcp `3b0660a686`, failed the gate check: the done gate
  rejected the developer's own change. The first 8 others were taken; 5 are spares.

## 3. Departures from the design (decided while mining; for the user to accept or change before the freeze)

| # | Design | What was done | Why |
|---|---|---|---|
| 1 | Repositories from the set-up list and the scouted pool | **Signature and move exclude the six Phase 2 repositories** (werkzeug, litestar, fastmcp, pydantic-ai, instructor, scrapy) | Phase 2 ran signature and move tasks there. Excluding the repositories keeps the targets unseen without per-target exclusion. One scout, on pydantic-ai, instructor and scrapy, was stopped after 2 hours and 9.5 GB. |
| 2 | Order: grep precision lowest first, then dependents, across a family | **Signature and move:** `derive_tasks`' own order, repository by repository (alphabetical), precision order within one | the tool's fixed order; it stops at 8 admitted |
| 3 | Same order for delete | **Delete:** `gate_families`' seeded draw (seed 4401) | the delete miner records no grep precision |
| 4 | — | Derived ids renamed from `p2-…` to `bd-signature-NN`, `bd-move-NN`; deletes are `bd-delete-NN` | the tools reuse Phase 2's and the sanity runs' ids |

No departure looks at any arm's behaviour on a task.

## 4. Findings that affect the claim (for the user's decision before the freeze)

**1. PRISM's gateway is active on the 8 rename tasks only.**
- On signature, move and delete it reports "not a rename task: only the syntax check applies" (`gateway.py`,
  `_start`). This is deliberate: the gateway knows what must change only for a rename.
- So on 24 of 32 tasks, PRISM_GW_C is PRISM's tools plus the syntax check, without the done blocking or the worklist.
- **What the primary estimate then measures:** "PRISM's tools, plus the gateway where it applies". That is narrower
  than `DESIGN.md`'s "PRISM (its tools and its gateway)".
- **Options:**
  - (a) Accept, and word the claim accordingly; report the rename family as the gateway's sub-analysis.
  - (b) Keep all 32 but pre-register family as a reported stratum.
  - (c) Extend the gateway to other families. This is a code change to PRISM, which would need its own validation.

**2. The rename prompts call classes "the method".**
- Six of the 8 rename targets are classes, for example "Rename the method `Transform`".
- The wording comes from Phase 3's task builder, and Phase 3b's p3-hidden-15 (`SessionMiddleware`, a class) had it
  too.
- I have not changed it: the tasks were validated as they are, and the wording is the same for both arms.
- Fixing it (to "the class") before the freeze means rebuilding those prompts and repeating the dry run.

## 5. Reproducing

Scratch paths shortened to `$S`. The clones are pinned by each task's `commit` and `parent`.

```
# rename: the candidates
python -B -m harness.experiments.r4.breadth.rename_candidates $S/breadth/rename.json
# signature, move
python -B -m harness.experiments.r4.derive_scout $S/breadth/scout2.json NAME=CLONE@REV ...      # 18 repositories
python -B -m harness.experiments.r4.derive_tasks SCOUT.json OUT.json --cache C --scratch D \
    --want signature=8 --want move=8 --per-repo 2 --turns 60 --cleanup NAME=CLONE ...         # scout2 + pydantic
# delete
python -B -m harness.experiments.r4.mine_families $S/breadth/mine_delete NAME=CLONE ... --since 2024-11-01
python -B -m harness.experiments.r4.gate_families POOL.json OUT --family delete --want 14 --max-attempts 80 \
    --per-repo 2 --seed 4401 --cache-py C --cache-js CJ NAME=CLONE ...
python -B -m harness.experiments.r4.breadth.delete_tasks POOL.json GATE.jsonl OUT.json GOLD --cache-py C --cache-js CJ NAME=CLONE ...
# admission
python -B -m harness.experiments.r4.derive_scoring_check TASKS.json GOLD --cache C --scratch D NAME=CLONE ...
python -B -m harness.experiments.r4.phase2_dryrun TASKS.json GOLD OUT NAME=CLONE ... --cache C \
    --arms BASELINE_LSP PRISM_GW_C --guards --edit-fix --empty-every-arm [--all-families]
```
