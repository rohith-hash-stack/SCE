# Eight-arm SLM harness — Milestone 4 build items (pre-run)

M4 scope (Option D): task types T1, T2, T5; corpora FastAPI, Django,
Express, tRPC; all seven arms; seeds 42, 43, 44. T3 and T4 are deferred.
Three build items come before the M4 run: TypeScript support for Arms 1
and 2, TypeScript support for Arm 3, and the T1 judge. T5 task expansion
(item 4) waits for confirmation. The gate runner (`harness.kaggle_m1`) still
indexes FastAPI only; the multi-corpus run loop is not part of these items.

## 1. TypeScript chunking for Arms 1 and 2 (`harness/ast_splitter.py`)

- `TypeScriptSplitter` (tree-sitter-typescript; the TSX grammar for
  `.tsx/.js/.jsx`) produces the same chunk kinds as the Python splitter:
  `function`, `method`, `function_part`, `class_preamble`, `module_block`,
  under the same 800-token cap.
  - Definitions: `function_declaration`, `class_declaration` (a preamble,
    then one chunk per `method_definition` or arrow-function field),
    `const f = () => {...}` / `var f = function () {...}`, and CommonJS or
    prototype assignments (`app.init = function init`,
    `Route.prototype.dispatch = function dispatch`), named by the assigned
    property. All of them also inside `export`.
  - Names follow the T2 gold: `lib/application.js`'s `app.init` is
    `lib.application.init`; `index.ts/.js` is its package, as `__init__.py` is.
  - Named functions nested in a chunk (Express's inner `next`, tRPC's
    object-literal methods `createBuilder.input`) are listed in
    `Chunk.inner_symbols` and join the item's `symbols` in Arms 1 and 2.
  - A comment directly above a definition belongs to it. A method chunk is
    wrapped in its class header and closing row, so it parses on its own.
  - Oversized functions split at first-level statements, each part wrapped
    in the header rows (through the body's `{`) and the closing rows; parts
    that do not parse fall back to the whole function, as in Python.
  - Two TypeScript-only rules, for JavaScript test files (one top-level
    `describe(...)` call each, up to 8,578 tokens on Express): an oversized
    top-level statement that carries a callback with a block body is split
    inside that callback, recursively; and every multi-row statement is its
    own packing unit.
  - Each chunk carries its signature stub (`Chunk.signature`), which Arm 2
    uses as the Priompt `<first>` fallback.
- `CodeSplitter` routes by extension: `.py` goes to `PythonSplitter`, and
  `.ts/.tsx/.js/.jsx` to `TypeScriptSplitter`.
- `iter_source_files(root, language)` lists the corpus's own language only,
  and `config.CORPUS_LANGUAGE` maps each corpus to its language. Arms 1 and 2
  read it through the index config's `repo_id`.
- Python is unchanged: on FastAPI (5,398 chunks) and Django (34,870), every
  chunk is byte-identical to the pre-M4 splitter.
- Arm 2 finds a TypeScript seed that is a nested function (Express's
  `lib.router.next`) through `inner_symbols`, and falls back to its module.

## 2. TypeScript LSP for Arm 3 (`harness/ts_lsp_client.py`, `harness/arms/arm3_lsp.py`)

- `TypeScriptLspClient` subclasses the Pyright client: same framing, same
  requests, same handshake order (initialize → initialized →
  didChangeConfiguration → readiness → probes). It changes the didOpen
  languageId and readiness.
- Readiness: tsserver loads no project until a file is open ("No Project"),
  and sends neither "Found N source files" nor `$/progress`. The client:
  1. opens the corpus entry point;
  2. waits on tsserver's `projectInfo`, which returns once the project is
     built and is the file-count signal;
  3. repeats the five `workspace/symbol` probes every 0.5 s until their
     counts have held for 2 s. On Express the counts still moved about 1 s
     after `projectInfo` returned.

  Probes are five top-level definitions from files in the loaded project.
- Arm 3 routes by corpus: Pyright for Python, typescript-language-server for
  TypeScript, with the same two hops and items. References in the seed's
  body are found with tree-sitter (`ts_reference_positions`: calls, `new`,
  type references, `extends`/`implements`). A nested seed is found by name
  in its file's outline. There is no editable-install check for TypeScript.
- Kaggle: `npm install -g typescript typescript-language-server`, added to
  the `kaggle/m1_smoke.ipynb` install cell. The M4 script needs the same line.
- New CPU smoke checks: `ts_chunk_parse` and `arm3_ts_lsp_ready` (BLOCKED
  when the server is absent).

## 3. T1 judge (`harness/scoring/judge.py`)

- Protocol: `judge(task, answer, context) -> {"faithfulness",
  "answer_relevancy"}`. `_score_t1` takes the minimum as TSR.
- `OpenAICompatibleJudge` covers DeepSeek and Gemini through their
  OpenAI-compatible `/chat/completions`. It runs at temperature 0, asks for a
  JSON object, and retries HTTP 429/5xx and network errors (2, 4, 8, 16 s).
  It refuses a Qwen model name.
- Config: `T1_JUDGE_PROVIDER` ("deepseek" | "gemini"),
  `T1_JUDGE_API_KEY_ENV` (default `DEEPSEEK_API_KEY` / `GEMINI_API_KEY`) and
  `T1_JUDGE_MODEL` (default `deepseek-chat` / `gemini-2.5-flash`). Each can
  be overridden by `HARNESS_`-prefixed environment variables.
  - Without the key, T1 is NaN with judge_status "unavailable", as before.
  - A failed call is NaN for that cell with judge_status "error: ...".
- The judge never sees the mechanism. It gets the question, the answer
  text, and the delivered items' contents as numbered passages, minus each
  item's header line. It never sees the arm id, metadata, item kinds,
  provenance, gold or the reference answer.
- Faithfulness counts claims supported by the context. With no context
  (Arm 0), nothing is supported.
