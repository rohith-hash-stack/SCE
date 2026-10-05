"""TypeScript/JavaScript splitter (M4): definitions and their names, method
wrapping, JSDoc attachment, oversized splits and callback descent, routing
by extension, and real Express and tRPC files (every chunk parses with
tree-sitter-typescript, braces balance, every non-blank line covered)."""
import os

import pytest

from harness.ast_splitter import (
    CodeSplitter, PythonSplitter, TypeScriptSplitter, corpus_language, iter_source_files, language_of, module_name,
)

QWEN = os.environ.get("HARNESS_TOKENIZER_PATH", "/home/user/models/qwen2-tokenizer")
CORPORA = "/home/user/SCE/.benchmarks/corpora"
EXPRESS = f"{CORPORA}/express"
TRPC = f"{CORPORA}/trpc/packages/server/src"


class Words:
    name = "words"
    def count(self, t): return len(t.split())


def _names(chunks):
    return [(c.kind, c.qualified_name) for c in chunks]


def _balanced(text: str) -> bool:
    """Braces, brackets and parentheses balance outside strings and comments
    (a cheap check independent of tree-sitter)."""
    pairs, stack, i, n = {")": "(", "]": "[", "}": "{"}, [], 0, len(text)
    while i < n:
        ch = text[i]
        if text.startswith("//", i):
            i = text.find("\n", i) if "\n" in text[i:] else n
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch in "'\"`":
            j = i + 1
            while j < n and text[j] != ch:
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack or stack.pop() != pairs[ch]:
                return False
        i += 1
    return not stack


TS = b'''import { a } from "./a";

/** Builds a thing. */
export function build(x: number): number {
  return x + 1;
}

export const arrow = (y: string) => {
  return y.length;
};

// the main class
export class Store<T> extends Base implements IStore {
  private items: T[] = [];
  static count = 0;

  /** Adds one. */
  add(item: T): void {
    this.items.push(item);
  }

  handler = () => {
    return this.items.length;
  };
}

export type Shape = { a: string };
'''

JS = b'''var app = exports = module.exports = {};

app.init = function init() {
  this.cache = {};
  this.defaultConfiguration();
};

exports.query = function query(options) {
  return function query(req, res, next) {
    next();
  };
};

Route.prototype.dispatch = function dispatch(req, res, done) {
  var idx = 0;
  next();
  function next(err) {
    idx++;
  }
};

var proto = module.exports = function (options) {
  return router;
};
'''


def test_typescript_definitions_and_names():
    ch = TypeScriptSplitter(Words(), 800).split_source(TS, "core/store.ts")
    names = _names(ch)
    assert ("function", "core.store.build") in names
    assert ("function", "core.store.arrow") in names
    assert ("class_preamble", "core.store.Store") in names
    assert ("method", "core.store.Store.add") in names
    assert ("method", "core.store.Store.handler") in names          # arrow-function field
    assert names[0] == ("module_block", "core.store") and names[-1] == ("module_block", "core.store")
    assert all(c.language == "typescript" for c in ch)
    build = next(c for c in ch if c.qualified_name == "core.store.build")
    assert build.content.startswith("/** Builds a thing. */")         # JSDoc directly above belongs to it
    add = next(c for c in ch if c.qualified_name == "core.store.Store.add")
    # the class header (not the class's own comment, as in Python) wraps the method
    assert add.content.startswith("export class Store<T> extends Base implements IStore {\n  /** Adds one. */")
    assert add.content.rstrip().endswith("}")
    pre = next(c for c in ch if c.kind == "class_preamble")
    assert pre.content.startswith("// the main class") and "private items" in pre.content
    assert "add(item" not in pre.content and "Adds one" not in pre.content   # the method's JSDoc is the method's
    sp = TypeScriptSplitter(Words(), 800)
    assert all(sp.parses(c.content, "core/store.ts") for c in ch)


def test_javascript_commonjs_and_prototype_assignments():
    ch = TypeScriptSplitter(Words(), 800).split_source(JS, "lib/router/route.js")
    names = dict((q, k) for k, q in _names(ch))
    assert names["lib.router.route.init"] == "function"              # app.init = function init
    assert names["lib.router.route.query"] == "function"             # exports.query = function query
    assert names["lib.router.route.dispatch"] == "function"          # Route.prototype.dispatch = ...
    assert names["lib.router.route.proto"] == "function"             # var proto = module.exports = function
    disp = next(c for c in ch if c.qualified_name == "lib.router.route.dispatch")
    assert disp.inner_symbols == ["lib.router.route.dispatch.next"]   # nested function named
    q = next(c for c in ch if c.qualified_name == "lib.router.route.query")
    assert q.inner_symbols == []      # an unnamed-binding function expression is not a named symbol


def test_signature_stub_is_shorter_and_parses():
    sp = TypeScriptSplitter(Words(), 800)
    ch = sp.split_source(TS, "core/store.ts")
    build = next(c for c in ch if c.qualified_name == "core.store.build")
    assert build.signature == "/** Builds a thing. */\nexport function build(x: number): number {\n  // ...\n}"
    add = next(c for c in ch if c.qualified_name == "core.store.Store.add")
    assert "// ..." in add.signature and "push" not in add.signature and len(add.signature) < len(add.content)
    for c in ch:
        if c.signature:
            assert sp.parses(c.signature, "core/store.ts"), c.signature


def test_oversized_function_splits_at_first_level_statements_and_parts_parse():
    body = "".join(f"  const v{i} = compute({i}, {i + 1});\n" for i in range(60))
    src = f"export function big(a: number) {{\n{body}  if (a) {{\n    return 1;\n  }}\n  return 0;\n}}\n".encode()
    sp = TypeScriptSplitter(Words(), 60)
    ch = sp.split_source(src, "big.ts")
    assert len(ch) > 1 and all(c.kind == "function_part" for c in ch)
    assert all(c.content.startswith("export function big(a: number) {") and c.content.endswith("}") for c in ch)
    assert all(sp.parses(c.content, "big.ts") for c in ch)
    assert ch[0].n_parts == len(ch) and [c.part for c in ch] == list(range(1, len(ch) + 1))


def test_oversized_test_callback_descends_into_describe():
    its = "".join(f"  it('case {i}', function () {{\n    assert.equal(f({i}), {i});\n  }});\n\n" for i in range(30))
    src = f"var assert = require('assert');\n\ndescribe('f', function () {{\n{its}}});\n".encode()
    sp = TypeScriptSplitter(Words(), 80)
    ch = sp.split_source(src, "test/f.js")
    blocks = [c for c in ch if "it('case" in c.content]
    assert len(blocks) > 1                                             # split inside the describe callback
    assert all(c.content.startswith("describe('f', function () {") and c.content.endswith("});") for c in blocks)
    assert all(sp.parses(c.content, "test/f.js") for c in ch)
    assert all(c.token_count <= 80 or c.oversized for c in ch)


def test_routing_by_extension_and_module_names():
    sp = CodeSplitter(Words(), 800)
    assert sp.for_path("a/b.py") is sp.python and isinstance(sp.python, PythonSplitter)
    for ext in (".ts", ".tsx", ".js", ".jsx"):
        assert sp.for_path(f"a/b{ext}") is sp.typescript and language_of(f"a/b{ext}") == "typescript"
    assert sp.for_path("a/b.md") is None and sp.split_source(b"# hi", "a/b.md") == []
    assert sp.split_source(b"def f():\n    return 1\n", "m.py")[0].language == "python"
    assert sp.split_source(b"function f() {\n  return 1;\n}\n", "m.ts")[0].language == "typescript"
    assert module_name("lib/router/index.js") == "lib.router"
    assert module_name("core/initTRPC.ts") == "core.initTRPC"
    assert module_name("fastapi/__init__.py") == "fastapi"
    assert module_name("pkg/index.py") == "pkg.index"                 # `index` is a package only in TS/JS


def test_tsx_parses_jsx():
    src = b"export function View(p: Props) {\n  return <div className={p.c}>{p.t}</div>;\n}\n"
    sp = TypeScriptSplitter(Words(), 800)
    ch = sp.split_source(src, "ui/view.tsx")
    assert _names(ch) == [("function", "ui.view.View")] and sp.parses(ch[0].content, "ui/view.tsx")


def test_iter_source_files_and_corpus_language(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    for name in ("b.ts", "c.js", "d.tsx", "e.jsx"):
        (tmp_path / name).write_text("export const x = 1;\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "f.js").write_text("x\n")
    assert [os.path.basename(f) for f in iter_source_files(str(tmp_path), "python")] == ["a.py"]
    assert [os.path.basename(f) for f in iter_source_files(str(tmp_path), "typescript")] == \
        ["b.ts", "c.js", "d.tsx", "e.jsx"]
    assert corpus_language(str(tmp_path)) == "typescript"                     # 4 TS files vs 1 .py
    assert corpus_language(str(tmp_path), {"repo_id": "fastapi"}) == "python"  # config wins
    assert corpus_language(str(tmp_path), {"repo_id": "express"}) == "typescript"
    assert corpus_language(str(tmp_path), {"language": "python"}) == "python"


@pytest.mark.skipif(not os.path.exists(QWEN), reason="Qwen tokenizer missing")
@pytest.mark.parametrize("root,rel,expect", [
    (EXPRESS, "lib/application.js", ["lib.application.init", "lib.application.use", "lib.application.handle"]),
    (EXPRESS, "lib/router/index.js", ["lib.router.handle", "lib.router.process_params"]),
    (TRPC, "core/internals/procedureBuilder.ts", ["core.internals.procedureBuilder.createBuilder",
                                                  "core.internals.procedureBuilder.createResolver"]),
    (TRPC, "error/TRPCError.ts", ["error.TRPCError.TRPCError", "error.TRPCError.TRPCError.constructor",
                                  "error.TRPCError.getTRPCErrorFromUnknown"]),
])
def test_real_express_and_trpc_files_chunk_parse_and_cover(root, rel, expect):
    if not os.path.isdir(root):
        pytest.skip("corpus checkout missing")
    from harness.tokenizer import HuggingFaceTokenizer
    sp = TypeScriptSplitter(HuggingFaceTokenizer(QWEN), 800)
    path = os.path.join(root, rel)
    chunks = sp.split_file(path, root)
    lines = open(path, encoding="utf-8").read().split("\n")
    covered = set().union(*(c.source_rows for c in chunks))
    assert [i for i, ln in enumerate(lines) if ln.strip() and i not in covered] == []
    assert [c.source_id for c in chunks if not sp.parses(c.content, rel)] == []
    assert [c.source_id for c in chunks if not _balanced(c.content)] == []
    assert all(c.token_count <= 800 or c.oversized for c in chunks)
    names = {c.qualified_name for c in chunks}
    assert set(expect) <= names, set(expect) - names
    # tRPC's createBuilder returns an object literal whose methods the T2 gold names
    if rel.endswith("procedureBuilder.ts"):
        inner = {s for c in chunks for s in c.inner_symbols}
        assert {"core.internals.procedureBuilder.createBuilder.input",
                "core.internals.procedureBuilder.createBuilder.use"} <= inner


@pytest.mark.skipif(not os.path.exists(QWEN), reason="Qwen tokenizer missing")
@pytest.mark.parametrize("root", [EXPRESS, TRPC])
def test_whole_ts_corpus_chunks_parse(root):
    if not os.path.isdir(root):
        pytest.skip("corpus checkout missing")
    from harness.tokenizer import HuggingFaceTokenizer
    sp = CodeSplitter(HuggingFaceTokenizer(QWEN), 800)
    files = iter_source_files(root, "typescript")
    chunks = [c for f in files for c in sp.split_file(f, root)]
    assert len(files) > 50 and len(chunks) > 200
    assert [c.source_id for c in chunks if not sp.typescript.parses(c.content, c.file)] == []
    assert all(c.token_count <= 800 or c.oversized for c in chunks)
