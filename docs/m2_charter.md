# M2 charter: Arms 2 and 3

**Branch:** `harness/eight-arm-slm`. **Starts after** M1's final Kaggle run is
reviewed. **Not started.**

M2 replaces the M1 stubs for Arm 2 (Cursor-style, Priompt packing) and Arm 3
(Copilot-style, Pyright LSP) with real implementations. Both run through the
same `RetrievalArm` interface, `finalize_context` budget enforcement,
adapters, unified `score()`, bootstrap and reporting that M1 built and
validated; nothing in that shared path changes. Both arms are Python-only, so
they run on FastAPI and Django and the results table discloses the asymmetry.

## Arm 2: Cursor-style (Priompt packing). Fidelity MEDIUM

- **Chunking: reuse `harness/ast_splitter.py` unchanged.** It already
  implements the spec's rules:
  - first-level splits only;
  - class-header prefixes;
  - decorators and docstrings kept on their definitions;
  - `Outer.Inner` naming;
  - `class_preamble` chunks.

  M1 validated it on 40,265 FastAPI and Django chunks with 0 syntax errors.
- **Priorities:** chunks in the seed symbol's file 1000; each retrieved chunk
  `500 − 10·rank`.
- **`<first>` fallback:** a component below the cutoff is demoted from its
  full body to a signature stub (`kind="signature_stub"`; def/class header
  plus the first docstring line).
- **Packing:** binary-search the priority cutoff so the packed total is at
  most 13,000 harness tokens. M1 parity with the serving model was 0.0%, so
  the HF tokenizer is the counter.
- **Latency sub-components:** `L_ast_parse`, `L_priority_sort`,
  `L_binary_search_tokenize`.
- **Declared simplification:** Cursor's codebase retrieval is not public, so
  the ranking that feeds `500 − 10·rank` is inferred (decision 1 below).

## Arm 3: Copilot-style (Pyright LSP). Fidelity MEDIUM

**`harness/pyright_client.py`** is a raw JSON-RPC client over
`pyright-langserver --stdio`. It does not use multilspy, which defaults to
jedi. The handshake runs in strict order:
1. `pip install -e .` of the target repository has finished;
2. launch the server;
3. `initialize`, with both `rootUri` and `rootPath`;
4. `initialized`;
5. `workspace/didChangeConfiguration` with `typeCheckingMode="basic"`;
6. wait for `experimental/serverStatus` with `quiescent=true`;
7. send at least 5 `workspace/symbol` probes, including the module name and
   `__init__`. If all of them return 0 results, the arm aborts for that
   repository.

**Retrieval: seed-anchored, bounded to two hops.**
- **Hop 1:** `didOpen`, then `hover` and `documentSymbol` on the seed file.
- **Hop 2:** if a definition lands in another file, `didOpen` that file,
  fetch its top-level hover and symbol outline, then stop.

Items are `lsp_hover` and `lsp_symbol`. Each LSP location is mapped to a
symbol-table name by file and line. Under-filling the 13,000 budget is
expected and legitimate.

**Known-bug guards, each covered by a test:**
- Content-Length is always `len(body.encode("utf-8"))`;
- the process runs with `bufsize=0` and no `text=True`, and frames are
  decoded manually;
- every file gets `didOpen` before hover or definition requests on it.

**Latency sub-components:** `L_lsp_hover`, `L_lsp_definition`,
`L_lsp_documentSymbol`, `L_didOpen`.

## New dependency and the Kaggle script

Arm 3 needs `pyright-langserver`, installed with `npm install -g pyright`.
It also needs FastAPI and Django installed editable (`pip install -e`)
before Pyright launches, so imports resolve.

The Kaggle script will therefore need two additions in M2: a Node/npm check
plus `npm install -g pyright`, and the per-corpus `pip install -e`.
**The script is not modified now.**

The dev container already has Node 22 and `pyright-langserver`, so Arm 3 can
be tested here against the real server, not only a fake.

## Acceptance criteria (mirroring M1)

1. **Unit tests for every new module, plus the full suite green:**
   - the Priompt binary search: monotone cutoff, never over budget, stubs
     only below the cutoff;
   - signature-stub generation;
   - the JSON-RPC framing, against a fake LSP server over pipes;
   - the handshake order and the quiescence abort;
   - the two-hop bound;
   - the `adapt_arm2` and `adapt_arm3` adapters, which replace the stubs.
2. **CPU smoke additions:**
   - Arm 2 packs a real FastAPI task to at most 13,000 tokens, and every
     full chunk and stub compiles;
   - Arm 3 reaches `quiescent=true` on FastAPI in under 120 s, and at least
     5 probes return at least 1 symbol in total.

   Any PASS, FAIL or BLOCKED result is reported as such.
3. **Kaggle gate:**
   - Gate A: 5 synthetic tasks × Arms 0, 1, 2, 3, 5 and the Oracle;
   - Gate B: 5 FastAPI T2 tasks × the same arms;
   - all rows PASS and exit code 0;
   - tokenizer parity at most 2%;
   - no truncation-guard trips;
   - every FAIL, if any, carries a failure record;
   - `summary.parquet` written.
4. **Documentation:** `harness/README_M2.md`, with fidelity disclosures and
   every simplification declared in each arm's `build_meta`.

## Decisions needed before implementation

1. **Arm 2's ranking source.** Which retriever orders the chunks that get
   `500 − 10·rank`?
   - Option A (recommended): BM25 over the same AST chunks, a lexical,
     editor-style signal that is distinct from Arm 1.
   - Option B: Arm 1's fused BM25 and dense ranking without the reranker.
2. **Corpora for the M2 gate:** FastAPI only, as in M1, or also the 4 Django
   T5 tasks, so T5 is exercised on real tasks by every Python arm.
3. **The `ARM1_EXCLUDE_DIRS` finding** stays an Arm 1 lever, off by default.
   Arms 2 and 3 index the whole checkout, like every other arm.

## Out of scope for M2

- Arm 4 (the agent loop) and TypeScript chunking for Express and tRPC: M3.
- The noise sweep (gated off) and the T1 judge, T3/T4 test runner, and
  hypothesis sign-off: M4.
- Fix D: no streaming early-stop. Revisit only if repetition loops become
  more frequent than M1's 4 cells.
