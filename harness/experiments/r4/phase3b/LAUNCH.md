# Phase 3b: launch instructions (prepared 2026-11-26; the 80 runs authorized by the user 2026-11-26)

**Authority:**
- the design: `AMENDMENTS.md`, "Phase 3b: the whitespace root cause …", and its "Phase 3b note 1";
- the cell: `kaggle/r4_phase3b_cell.py`, pinned to code commit **`f77411d4`**.
- **Authorized:** the user gave the go-ahead for the 80 runs on 2026-11-26, on this cell and commit unchanged.

## 1. What is pinned

| Item | Value |
|---|---|
| Code commit | `f77411d46ad66e625dcc32cd70d32b337fa7a0ae` |
| Source trees | `src` `227e7565…`, `harness` `6a957880…`; the cell checks both and stops on a difference |
| Code tested | The same code as `254dfc4f`: only documentation and verification files changed since. The full suite (3562 passed, 0 failed) ran on that code. |
| Task file | `harness/experiments/r4/phase3/phase3b.json`, sha256 `506e4047266c…`: design `phase3b`, 4 arms, 80 runs, 5 shards |
| Model | `devstral-small-2:24b-instruct-2512-q4_K_M`: weights `c580819b…`, template `580f051b…`, params `e0daf17f…` |
| Ollama | 0.34.0; context 20480, flash attention, q8_0 KV cache, 1 parallel, 1 loaded model (POC 2's settings) |
| Flags | `--ledger --phase1 --raw-tools --feedback --guards`, turn cap 60, budget 1,000,000 tokens |
| Tokenizer | Qwen2.5 tokenizer, sha256 `c0382117…` |

## 2. Kaggle settings (every shard)

- **Accelerator:** GPU T4 x2.
- **Internet:** on.
- **Secret:** `GITHUB_TOKEN`, which must belong to `rohith-hash-stack`; the cell checks this.
- **The cell:** paste the whole of `kaggle/r4_phase3b_cell.py` into **one** notebook cell.
- **Edit one line only:** `SHARD_ID = k` at the top. Nothing else in the cell is to be edited.

## 3. Shards and seeds

Each shard runs 16 runs: 4 task-and-seed blocks × 4 arms.

| Shard | p3-hidden-15 (litestar) seeds | p3-hidden-03 (werkzeug) seed | Results folder |
|---|---|---|---|
| 1 | 301, 302, 303 | 301 | `reports/r4/phase3b_shard1` |
| 2 | 304, 305, 306 | 302 | `reports/r4/phase3b_shard2` |
| 3 | 307, 308, 309 | 303 | `reports/r4/phase3b_shard3` |
| 4 | 310, 311, 312 | 304 | `reports/r4/phase3b_shard4` |
| 5 | 313, 314, 315 | 305 | `reports/r4/phase3b_shard5` |

**Totals:**
- p3-hidden-15: 15 seeds × 4 arms = **60**;
- p3-hidden-03: 5 seeds × 4 arms = **20**;
- **80** runs, all distinct.

**Arm order within a block** rotates by one from block to block. Across all 80 runs, each arm holds each position
exactly 5 times. With A = PRISM_GW, C = PRISM_GW_C, E = PRISM_GW_E, B = BASELINE_LSP:

| Shard | Block 1 | Block 2 | Block 3 | Block 4 (p3-hidden-03) |
|---|---|---|---|---|
| 1 | A C E B | C E B A | E B A C | B A C E |
| 2 | C E B A | E B A C | B A C E | A C E B |
| 3 | E B A C | B A C E | A C E B | C E B A |
| 4 | B A C E | A C E B | C E B A | E B A C |
| 5 | A C E B | C E B A | E B A C | B A C E |

The cell prints its shard's blocks before any run; check them against this table.

## 4. What each shard does, in order

1. **Token, code and install:**
   - checks the token's owner;
   - checks out `f77411d4` and checks both source trees;
   - installs prism, jedi 0.19.2, ast-grep 0.39.6 and pyflakes 3.2.0;
   - checks the tokenizer's sha256.
2. **The task file:** checks the sha256, the design `phase3b`, the 4 arms and the 80 runs. Checks that the shard has
   16 distinct runs, and that every block runs each arm once.
3. **Ollama and the model:** installs Ollama 0.34.0 if it is missing, starts it and pulls the model.
4. **Environment check** (amendment section 8, note 1). The cell stops on any difference, pushes the record, and
   starts no run:
   - Ollama server version 0.34.0;
   - the model manifest's three layer digests;
   - 2 × T4.
   - Each session appends a line to `environment.jsonl`: the config sha256, the versions, the digests and the GPUs.
5. **Raw-template pre-flight**, for all 4 arms: the prompt token counts must match Ollama's, or the cell stops.
6. **The runs, block by block:**
   - one `pilot_run` per block, the arms in the block's order;
   - a checkpoint push every 10 minutes;
   - a push after each block.
7. **Health check,** once all 16 runs are done: more than 2 infrastructure deaths, or parse errors at 2% of turns,
   fail the shard. The cell stops with "report back".
8. **Records:** writes `progress.json` and pushes it.
   - **No outcome report is produced:** the results stay blinded until all 5 shards are in.

## 5. Expected duration

POC 2's run times on p3-hidden-15 averaged 10.1 min (PRISM_GW; at most 14.2) and 7.2 min (BASELINE_LSP).

| | Estimate |
|---|---|
| Setup per session (install, a 15 GB model pull, pre-flight) | about 20–30 min |
| One shard's 16 runs | about 2.5–3.5 h, assuming C and E run like PRISM_GW |
| One shard, worst case (every run at the 60-turn cap, about 20 min each) | about 5.5 h |
| All 80 runs | about 13–15 GPU hours |

- p3-hidden-03 has not yet been run at 60 turns, so its time is an estimate.
- A shard fits one Kaggle session (11.5 h; no run starts in its last 30 minutes).
- **Parallel sessions:** shards may run at the same time. Each pushes only its own folder.

## 6. If something stops

| The cell says | Do this |
|---|---|
| `SESSION LIMIT … run the cell again with SHARD_ID = k` | Run it again with the same `SHARD_ID`. Finished runs are restored and never repeated. |
| `the environment differs from the pinned one` | Do not retry on another setup. Report the message and `environment.jsonl`. |
| `the pre-flight found a prompt mismatch` | Report back with `preflight.json`. |
| `failed the health check` | Report back with `health.log`. Do not re-run the shard. |
| `some runs exited with an error` | Run the cell again with the same `SHARD_ID`: only the missing runs run. Report it if it repeats. |
| `[push] FAILED` | The results are in `/kaggle/working/SCE/reports/r4/phase3b_shard<k>`. Report back before the session ends. |

## 7. After all 5 shards

- **Report back,** and I check that all 80 runs and 5 environment records are present.
- **Then the frozen analysis,** once, on all shards merged:

  ```
  python -B -m harness.experiments.r4.phase3b_analysis 'reports/r4/phase3b_shard*' \
      harness/experiments/r4/phase3/phase3b.json --clones DIR --out reports/r4/phase3b_analysis.json
  ```
