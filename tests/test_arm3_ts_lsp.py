"""Arm 3 on TypeScript corpora (M4): the typescript-language-server client
against the real server on Express and tRPC (handshake order, quiescence
probe, non-empty hover on a known symbol, routing by corpus), plus unit
tests for the tree-sitter reference locator and the languageId mapping.
The real-server tests skip when the server or the checkout is missing."""
import os
import shutil

import pytest

from harness.ast_splitter import TS_LANGUAGE, TSX_LANGUAGE
from harness.arms.arm3_lsp import Arm3PyrightLSP, ts_reference_positions
from harness.ts_lsp_client import TypeScriptLspClient

CORPORA = "/home/user/SCE/.benchmarks/corpora"
EXPRESS = f"{CORPORA}/express"
TRPC = f"{CORPORA}/trpc/packages/server/src"
HAVE_SERVER = shutil.which("typescript-language-server") is not None
real = pytest.mark.skipif(not HAVE_SERVER, reason="typescript-language-server not installed")


class Words:
    name = "words"
    def count(self, t): return len(t.split())


def _parse(src: bytes, lang=TS_LANGUAGE):
    from tree_sitter import Parser
    return Parser(lang).parse(src).root_node


# ------------------------------------------------------------------ units
def test_ts_reference_positions_calls_new_types_and_heritage():
    src = (b"class A extends Base implements I {\n"
           b"  m(x: Opts): Result<Item> {\n"
           b"    const r = helper(x);\n"
           b"    this.store.save(r);\n"
           b"    return new Wrapper(r);\n"
           b"  }\n"
           b"}\n")
    refs = ts_reference_positions(_parse(src))
    names = [n for n, _, _ in refs]
    for expected in ("Base", "I", "Opts", "Result", "Item", "helper", "save", "Wrapper"):
        assert expected in names, expected
    assert "store" not in names and "this" not in names       # a member call is located at its method
    save = next(r for r in refs if r[0] == "save")
    assert save[1] == 3 and src.split(b"\n")[3][save[2]:save[2] + 4] == b"save"
    assert refs == sorted(refs, key=lambda f: (f[1], f[2], f[0]))


def test_ts_reference_positions_commonjs():
    src = b"app.init = function init() {\n  this.cache = {};\n  this.defaultConfiguration();\n  merge(a, b);\n};\n"
    names = [n for n, _, _ in ts_reference_positions(_parse(src, TSX_LANGUAGE))]
    assert names == ["defaultConfiguration", "merge"]


def test_language_ids():
    c = TypeScriptLspClient("/tmp", anchor=None)
    assert [c.language_id(f"a{e}") for e in (".ts", ".tsx", ".js", ".jsx")] == \
        ["typescript", "typescriptreact", "javascript", "javascriptreact"]


def test_missing_server_is_reported(tmp_path):
    (tmp_path / "index.ts").write_text("export function f() {\n  return 1;\n}\n")
    arm = Arm3PyrightLSP(tokenizer=Words(), cmd=["definitely-not-a-tsserver", "--stdio"])
    with pytest.raises(Exception, match="typescript-language-server not found"):
        arm.index(str(tmp_path), {"language": "typescript"})


# ------------------------------------------------------------ real server
@pytest.fixture(scope="module", params=["express", "trpc"])
def ts_arm(request):
    root = {"express": EXPRESS, "trpc": TRPC}[request.param]
    if not HAVE_SERVER or not os.path.isdir(root):
        pytest.skip("typescript-language-server or checkout missing")
    arm = Arm3PyrightLSP(tokenizer=Words())
    arm.index(root, {"repo_id": request.param})
    yield request.param, arm
    arm.close()


@real
def test_routing_by_corpus_and_handshake_order(ts_arm):
    corpus, arm = ts_arm
    assert arm.language == "typescript" and isinstance(arm.client, TypeScriptLspClient)
    sent = [m for m, _ in arm.client.sent]
    i = sent.index("initialize")
    assert sent[i:i + 3] == ["initialize", "initialized", "workspace/didChangeConfiguration"]
    # readiness: anchor didOpen, then projectInfo, then the workspace/symbol probes
    j = sent.index("textDocument/didOpen")
    assert j > i + 2 and sent[j + 1] == "workspace/executeCommand" and sent[j + 2] == "workspace/symbol"
    anchor = arm.client.sent[j][1]["textDocument"]
    assert anchor["uri"].endswith("/index.js" if corpus == "express" else "/index.ts")
    assert anchor["languageId"] == ("javascript" if corpus == "express" else "typescript")


@real
def test_quiescence_probe(ts_arm):
    _, arm = ts_arm
    r = arm.ready
    assert r is not None and len(r.probes) == 5 and all(v >= 1 for v in r.probes.values())
    assert r.source_files and r.source_files > 50                    # projectInfo's file list
    assert r.rounds >= 1 + int(arm.client.quiescence_s / 0.5)        # counts held for the quiescence window
    assert r.seconds >= arm.client.quiescence_s


@real
def test_non_empty_hover_on_known_symbol(ts_arm):
    corpus, arm = ts_arm
    seed = "lib.application.init" if corpus == "express" else "core.initTRPC.createTRPCInner"
    ctx = arm.retrieve("q", {"task_id": "t", "seed_symbol": seed, "task_type": "T2_localization"})
    hover = next(i for i in ctx.items if i.provenance["origin"] == "seed")
    assert hover.kind == "lsp_hover" and hover.symbols == [seed]
    body = hover.content.split("\n", 1)[1]
    assert seed.rsplit(".", 1)[-1] in body and len(body.strip()) > 10
    outline = next(i for i in ctx.items if i.provenance["origin"] == "seed_outline")
    assert seed in outline.symbols and len(outline.symbols) >= 5
    assert ctx.build_meta["language_server"] == "typescript-language-server"


@real
def test_nested_seed_and_hop2_on_real_tasks(ts_arm):
    corpus, arm = ts_arm
    seed = "lib.router.next" if corpus == "express" else "core.router.createRouterInner.createCaller"
    ctx = arm.retrieve("q", {"task_id": "t", "seed_symbol": seed, "task_type": "T2_localization"})
    assert any(i.provenance["origin"] == "seed" for i in ctx.items)   # nested function found in the outline
    assert ctx.build_meta["hop1_definitions"] > 0
    assert all(i.token_count > 0 for i in ctx.items) and ctx.total_tokens <= ctx.budget_tokens
