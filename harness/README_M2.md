# Eight-arm SLM harness — Milestone 2 (in progress)

Scope and decisions are in `docs/m2_charter.md`. M2 replaces the M1 stubs
for Arm 2 and Arm 3. **Arm 2 is implemented; Arm 3 has not started.**

## Arm 2 — Cursor-style Priompt packing (fidelity MEDIUM)

`harness/arms/arm2_priompt.py`. The variable under test is the Priompt
packing; the retriever is a declared stand-in.

- **Chunks:** `harness/ast_splitter.py`, unchanged (the same 800-token chunks
  as Arm 1).
- **Retrieval:** BM25 (k1 = 1.5, b = 0.75) over the chunks, top 50
  (decision 1 in the charter).
- **Components:** one Priompt `<first>` per chunk: the full body at priority
  p, then its signature stub at p + 500. `<first>` renders the first child
  whose priority is at least the cutoff. This follows Priompt's own renderer
  (`priompt/src/lib.ts`, the `first` case): the preferred long child has the
  lower priority, and the short fallback the higher one.
- **Priorities:**
  - each retrieved chunk gets 500 − 10·rank;
  - the seed symbol's own chunk gets 1000, and the rest of the seed file
    1000 minus the chunk distance from it, floored at 501 so the whole seed
    file stays above every retrieved chunk.
- **Cutoff:** binary search over the distinct priorities for the lowest
  cutoff whose packed total fits the 13,000-token budget, counted with the
  harness tokenizer.
- **Delivery:** full bodies as `code_chunk`, stubs as `signature_stub`, in
  descending priority.
- **Stubs:** enclosing class headers, decorators, the def/class header, the
  first docstring line (as a string literal), then `...`. 0 syntax errors on
  all 40,052 FastAPI and Django chunks. A chunk gets a stub only if the stub
  is strictly shorter than its body; otherwise it has none and drops below
  the cutoff.

### Inferred choices (declared in `build_meta["simplifications"]`)

The spec fixes only the body priorities (1000 for the seed file, 500 − 10·rank
for retrieved chunks). Two choices were needed beyond that; both are measured
on the 5 FastAPI T2 tasks with the Qwen tokenizer.

1. **Seed-file priority falls with distance** (1000 at the seed chunk, then
   −1 per chunk, floor 501). With a flat 1000, the seed file is
   all-or-nothing:
   - on 4 tasks, no full body was packed at all (stubs only);
   - on t02_004, whose seed file is `fastapi/routing.py`, nothing was
     packed: even the file's stubs exceed 13,000 tokens.

   Priompt's README recommends priority falling with distance from the point
   of interest for long files.
2. **Stub bonus +500,** the width of the retrieved band. Gold symbols
   delivered as full bodies, per task (t02_001…005):

   | Stub bonus | Gold as full bodies | Full / stub items |
   |---|---|---|
   | +500 (chosen) | 5/5, 4/4, 3/3, 0/5, 1/3 | 103 full, 50 stubs |
   | +1000 (every stub above every body: the literal reading of "demote below-cutoff bodies to stubs") | 0 on every task | stubs only |

   At +1000 the stubs of everything consume the whole budget. t02_004 stays
   stubs-only under both settings, because its signatures embed long
   `Doc(...)` annotations.
3. **A stub must be strictly shorter than its body.** For tiny chunks the
   stub (header, docstring line, `...`) can cost as much as the body. Such a
   "fallback" is pointless, and it would make the packed total non-monotone
   in the cutoff (Priompt's README, caveat 5). With the rule, the binary
   search is exact; a test checks it against brute force on random component
   sets.

### Verification (local)

- `tests/test_arm2_priompt.py` (11 tests):
  - stub shape, and compilation on every FastAPI stub;
  - `<first>` semantics;
  - a monotone packed total, with the binary search equal to brute force;
  - seed-file and retrieved priorities;
  - never over budget at budgets of 13,000, 120, 40 and 0;
  - delivery order, the adapter, and the no-seed case.
- CPU smoke check `arm2_packing`: the 5 real FastAPI T2 tasks pack within
  13,000 tokens (largest: 12,908), with 103 full chunks and 50 stubs, and 0
  compile errors.
- `harness.kaggle_m1 --dry-run --fake-encoders`: 30/30 table cells (Arms 0,
  1, 2, 5 and the Oracle), exit code 0. Arm 2 is indexed and run by the gate
  runner from `config.ACTIVE_ARMS`.

Not run on Kaggle yet. The Kaggle script changes for M2 are bundled for
when both arms are ready, per the charter.
