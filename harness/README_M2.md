# Eight-arm SLM harness — Milestone 2 (in progress)

Scope and decisions are in `docs/m2_charter.md`. M2 replaces the M1 stubs
for Arm 2 and Arm 3. **Both are implemented and tested locally; neither has run on Kaggle.**

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

### Arm 2 fidelity

Arm 2 is **Cursor-style, not Cursor-exact**. It reproduces the published
mechanism, the Priompt packing algorithm (priority ordering, binary-searched
cutoff, `<first>` fallback), around inputs that Cursor does not publish. It is
not a reverse-engineering of Cursor's production system. Three deviations:

- **Retrieval is BM25.** Cursor's actual retriever is not public.
- **Seed-file priority decays with chunk distance** from the seed symbol.
  Cursor's actual seed priority is based on cursor position, which a
  benchmark task does not have.
- **The stub bonus is +500,** a value measured on the FastAPI tasks (below),
  not a public constant.

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

   **That is a corpus property, not an Arm 2 bug, and a potential systematic
   weakness of the Cursor-style stub mechanism.** On repositories whose
   signatures carry heavy decorator or annotation metadata (FastAPI's
   `routing.py` puts `Annotated[..., Doc(...)]` documentation in every
   parameter), signature stubs stop being cheap, so the fallback that is
   meant to keep more of the file visible cannot.
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

## Arm 3 — Copilot-style Pyright LSP (fidelity MEDIUM)

`harness/arms/arm3_lsp.py`, over the raw JSON-RPC client
`harness/pyright_client.py` (not multilspy).

- **Index:**
  1. editable-install check;
  2. launch `pyright-langserver --stdio`;
  3. initialize (rootUri and rootPath) -> initialized ->
     didChangeConfiguration (`typeCheckingMode` "basic") -> readiness;
  4. five `workspace/symbol` probes: the module name, `__init__`, and the
     first three public top-level definitions of the package (never task
     data). All-zero probes abort the arm for that repository.
- **Hop 1 (seed file):**
  - didOpen, then documentSymbol: the file outline, top-level symbols plus
    class members;
  - hover on the seed symbol;
  - definition at each reference in the seed symbol's body: call targets,
    annotations and base classes, at most 40 distinct names in source
    order, located with Python's `ast` and converted to UTF-16 columns;
  - a definition in the seed file itself gets a hover.
- **Hop 2:** for each other repository file a definition lands in: didOpen,
  a hover on the defined symbol, and the top-level outline. Then stop: a
  definition is never requested from a hop-2 file. Definitions outside the
  repository (typeshed, site-packages) are not followed.
- **Items, in rank order:**
  1. the seed hover;
  2. the seed outline;
  3. same-file hovers;
  4. per hop-2 file, its hovers and then its outline.

  `lsp_hover` names the symbol it describes; `lsp_symbol` names every
  symbol its outline lists. Items are added greedily while they fit the
  13,000 budget; under-filling it is expected (587 to 3,029 Qwen tokens on
  the 5 FastAPI T2 tasks).
- **FQN mapping:** module path from the file, plus the innermost outline
  symbol whose range contains the line.

### Arm 3 deviations from the charter (declared in `build_meta`)

1. **Readiness.** The charter waits for `experimental/serverStatus` with
   `quiescent=true`. Pyright 1.1.408 sends neither that notification
   (rust-analyzer's) nor `$/progress` for its initial analysis (observed).
   The substitute:
   - wait for Pyright's `window/logMessage` "Found N source files";
   - then repeat the five probes until two consecutive rounds return
     identical counts.

   The 120 s limit and the all-zero abort are kept. Pyright >= 1.1.400 does
   not emit serverStatus quiescent=true; the arm falls back to waiting for
   the 'Found N source files' log line, then requires two consecutive probe
   rounds to agree before retrieval starts. The 120 s timeout and
   abort-on-empty-probes guards are preserved. On FastAPI: ready in
   6–7 s, 1,129 files, 2 probe rounds, probe counts
   `{fastapi: 9, __init__: 108, ...}`.
2. **Editable install.** Charter step 1 is enforced as a check, not
   performed by the arm. Before launching Pyright, the arm checks, in a
   fresh interpreter outside the checkout, that the package imports from
   the checkout itself. If it does not, the arm raises
   `EditableInstallError` naming the `pip install -e` command. The check is
   on by default (`ARM3_REQUIRE_EDITABLE_INSTALL`). Local development, where
   FastAPI is deliberately not installed (pdm-backend would write
   `.pdm-build` into the corpus checkout), opts out with
   `HARNESS_ARM3_REQUIRE_EDITABLE=0`. Pyright still resolves `fastapi.*`
   from the workspace root there. Approved as checked-not-performed: the harness never
   modifies the environment as a side effect of indexing; the install is
   the Kaggle script's job, and `HARNESS_ARM3_REQUIRE_EDITABLE=0` is the
   local escape hatch only.
3. **Outline items name every listed symbol.** This is what the outline
   delivers to the model, but it means `delivered_symbols` (CPI,
   cleanliness, retrieval diagnostics) counts 18 to 153 outline names per
   task. On the 5 FastAPI T2 tasks every gold symbol is among the delivered
   symbols, but only 18 of 20 are named by a hover.

   **Accounting fix (approved).** Every delivered item now carries
   `symbol_provenance`, set by its arm's adapter:

   | Arm | `symbol_provenance` |
   |---|---|
   | Arm 1 | `body` |
   | Arm 2 | `body` (full chunk) or `signature_stub` |
   | Arm 3 | the LSP method whose result is the item: `hover` or `documentSymbol` (`definition` is reserved; every definition target is delivered as the hover at that target) |
   | Arm 4 (M3) | `tool_result` |
   | Arm 5 | `body` or `signature_stub` |
   | Oracle | `oracle` |

   Two per-cell metrics, in the registry, the Parquet schema and the
   summary table (mean and CI), sit beside CPI:
   - `delivered_symbols_resolved`: unique symbols delivered with provenance
     `hover`, `body`, `signature_stub` or `oracle`;
   - `delivered_symbols_named_only`: unique symbols delivered only with
     `documentSymbol` or `definition`, never also resolved by another
     item.

   CPI, cleanliness, `uniform_cpi` and every other primary metric are
   unchanged and do not read the field. Arm 3's mechanism is unchanged; it
   only records `lsp_method` in each item's provenance.

### Arm 3 verification (local)

- `tests/test_arm3_lsp.py` (20 tests).
  - The three bug traps, each confirmed to fail when its guard is removed:
    - Content-Length as UTF-8 bytes, including a non-ASCII round trip
      through a server that reads exactly that many bytes;
    - `bufsize=0` with no `text`/`encoding`, a decoder fed one byte at a
      time and split mid-UTF-8, and a server that writes one byte at a
      time;
    - `NotOpenError` before didOpen, and didOpen preceding every query on
      the wire.
  - Fake server (`tests/fixtures/fake_lsp_server.py`): the handshake
    order on the wire, the `workspace/configuration` answer, the all-zero
    probe abort, a server that never signals readiness, a server that
    exits, a missing `pyright-langserver`, the editable-install check, and
    the adapter.
  - Real pyright-langserver on FastAPI: the handshake order and readiness,
    two-hop retrieval (definitions requested only from the seed file, hop
    ≤ 2, `Dependant` mapped across files), the budget, the no-seed case,
    and a method seed.
- CPU smoke check `arm3_lsp_ready`:
  - PASS locally with `HARNESS_ARM3_REQUIRE_EDITABLE=0`: ready in 6.0 s;
  - FAIL with the editable-install check on, since FastAPI is not
    installed here;
  - BLOCKED without `pyright-langserver`.
- `harness.kaggle_m1 --dry-run --fake-encoders` with
  `HARNESS_ARM3_REQUIRE_EDITABLE=0`: 36/36 cells PASS (Arms 0, 1, 2, 3, 5
  and the Oracle).

Not run on Kaggle yet. The Kaggle script changes for M2 are bundled for
when both arms are ready, per the charter: a Node/npm check plus
`npm install -g pyright`, and `pip install -e` of the FastAPI checkout.
