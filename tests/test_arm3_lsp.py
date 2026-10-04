"""Arm 3 (Copilot-style Pyright LSP): the raw JSON-RPC client's three bug
traps, its error paths (against a fake LSP server), the strict handshake
and the two-hop retrieval (against the real pyright-langserver)."""
import json
import os
import shutil
import subprocess
import sys

import pytest

from harness import config as C
from harness import pyright_client as pc
from harness.arms.arm3_lsp import Arm3PyrightLSP, EditableInstallError, PyrightNotFound, module_origin, \
    reference_positions, utf16_col
from harness.pyright_client import FrameDecoder, LspError, NotOpenError, NotReadyError, PyrightClient, encode_message
from harness.scoring.adapters import adapt
from harness.scoring.canonical import DeliveredContext, DeliveredItem
from harness.scoring.fairness import verify_ranking

FAKE = os.path.join(os.path.dirname(__file__), "fixtures", "fake_lsp_server.py")
FASTAPI = "/home/user/SCE/.benchmarks/corpora/fastapi"
PROBES = ["a", "b", "c", "d", "e"]
HAVE_PYRIGHT = shutil.which("pyright-langserver") is not None
real = pytest.mark.skipif(not (HAVE_PYRIGHT and os.path.isdir(FASTAPI)),
                          reason="needs pyright-langserver and the FastAPI checkout")


class Words:
    name = "words"
    def count(self, t): return len(t.split())


def fake_client(tmp_path, mode="ok", **kw):
    log = tmp_path / "wire.jsonl"
    client = PyrightClient(str(tmp_path), cmd=[sys.executable, FAKE, mode, str(log)], **kw)
    client.start()
    return client, log


def wire(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


# ------------------------------------------------- trap 1: Content-Length
def test_content_length_counts_utf8_bytes_not_characters():
    msg = {"jsonrpc": "2.0", "method": "x", "params": {"text": "café ✓ 日本"}}
    raw = encode_message(msg)
    header, body = raw.split(b"\r\n\r\n", 1)
    n = int(header.split(b":")[1])
    assert n == len(body) == len(json.dumps(msg, ensure_ascii=False).encode("utf-8"))
    assert n > len(json.dumps(msg, ensure_ascii=False))          # bytes, not characters
    assert FrameDecoder().feed(raw) == [msg]


def test_non_ascii_round_trip_through_a_server(tmp_path):
    """The fake server reads exactly Content-Length bytes and parses them: a
    character count would cut the body short and the echo would fail."""
    client, log = fake_client(tmp_path)
    try:
        params = {"text": "naïve café ✓ 日本語 " * 50}
        assert client.request("test/echo", params, timeout=10) == params
    finally:
        client.shutdown()


# ------------------------------------ trap 2: bufsize=0, no text=True, framing
def test_server_runs_unbuffered_in_binary_mode(monkeypatch, tmp_path):
    seen = {}
    real_popen = subprocess.Popen

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(pc.subprocess, "Popen", spy)
    client, _ = fake_client(tmp_path)
    try:
        assert seen["bufsize"] == 0
        assert not seen.get("text") and not seen.get("universal_newlines") and "encoding" not in seen
    finally:
        client.shutdown()


def test_frame_decoder_handles_split_and_merged_frames():
    msgs = [{"id": i, "result": "ü" * i} for i in range(1, 6)]
    stream = b"".join(encode_message(m) for m in msgs)
    dec, out = FrameDecoder(), []
    for i in range(len(stream)):                 # one byte at a time
        out += dec.feed(stream[i:i + 1])
    assert out == msgs
    assert FrameDecoder().feed(stream) == msgs   # all in one read
    dec = FrameDecoder()                         # a body split mid-UTF-8 sequence
    raw = encode_message({"v": "日本"})
    cut = raw.index("日".encode()) + 1
    assert dec.feed(raw[:cut]) == [] and dec.feed(raw[cut:]) == [{"v": "日本"}]


def test_frame_without_content_length_is_an_error():
    with pytest.raises(LspError, match="Content-Length"):
        FrameDecoder().feed(b"Content-Type: x\r\n\r\n{}")


def test_byte_at_a_time_server_still_handshakes(tmp_path):
    client, _ = fake_client(tmp_path, mode="split")
    try:
        rep = client.handshake(PROBES, ready_timeout=20)
        assert rep.source_files == 3 and rep.total_probe_results == 5
    finally:
        client.shutdown()


# ---------------------------------------------- trap 3: didOpen before queries
def test_queries_on_an_unopened_file_are_refused(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("def f():\n    return 1\n")
    client, log = fake_client(tmp_path)
    try:
        client.handshake(PROBES, ready_timeout=20)
        for call in (lambda: client.hover(str(f), 0, 4), lambda: client.definition(str(f), 0, 4),
                     lambda: client.document_symbols(str(f))):
            with pytest.raises(NotOpenError):
                call()
        assert not any(m["method"] == "textDocument/hover" for m in wire(log))   # nothing reached the server
        client.did_open(str(f))
        assert "café" in client.hover(str(f), 0, 4)
        methods = [m["method"] for m in wire(log)]
        assert methods.index("textDocument/didOpen") < methods.index("textDocument/hover")
    finally:
        client.shutdown()


# --------------------------------------------------- handshake & error paths
def test_handshake_order_on_the_wire_and_configuration_answered(tmp_path):
    client, log = fake_client(tmp_path)
    try:
        rep = client.handshake(PROBES, ready_timeout=20)
    finally:
        client.shutdown()
    methods = [m["method"] for m in wire(log)]
    assert methods[:3] == ["initialize", "initialized", "workspace/didChangeConfiguration"]
    init = wire(log)[0]["params"]
    assert init["rootUri"].startswith("file://") and init["rootPath"] == str(tmp_path.resolve())
    conf = next(m for m in wire(log) if m["method"] == "workspace/didChangeConfiguration")
    assert conf["params"]["settings"]["python"]["analysis"]["typeCheckingMode"] == "basic"
    answer = next(m for m in wire(log) if m["method"] is None and m["id"] == 900)   # workspace/configuration
    assert answer is not None
    assert methods.count("workspace/symbol") >= 5 and rep.rounds >= 2
    first_probe = methods.index("workspace/symbol")
    assert all(m == "workspace/symbol" for m in methods[first_probe:first_probe + 5])


def test_all_empty_probes_abort(tmp_path):
    client, _ = fake_client(tmp_path, mode="empty")
    try:
        with pytest.raises(NotReadyError, match="returned nothing"):
            client.handshake(PROBES, ready_timeout=20)
    finally:
        client.shutdown()


def test_no_readiness_signal_times_out(tmp_path):
    client, _ = fake_client(tmp_path, mode="nolog")
    try:
        with pytest.raises(NotReadyError, match="Found N source files"):
            client.handshake(PROBES, ready_timeout=1.5)
    finally:
        client.shutdown()


def test_server_exit_is_reported_not_hung(tmp_path):
    client, _ = fake_client(tmp_path, mode="die")
    try:
        with pytest.raises(LspError, match="exited"):
            client.handshake(PROBES, ready_timeout=20)
    finally:
        client.shutdown()


def test_fewer_than_five_probes_rejected(tmp_path):
    client, _ = fake_client(tmp_path)
    try:
        with pytest.raises(ValueError, match="5"):
            client.wait_ready(["a", "b"], timeout=1)
    finally:
        client.shutdown()


def test_arm_aborts_on_empty_probes_and_on_missing_server(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text("def f():\n    return 1\n")
    arm = Arm3PyrightLSP(tokenizer=Words(), cmd=[sys.executable, FAKE, "empty"], require_editable=False)
    with pytest.raises(NotReadyError):
        arm.index(str(tmp_path), {"probes": PROBES, "ready_timeout": 20})
    arm.close()
    arm = Arm3PyrightLSP(tokenizer=Words(), cmd=["no-such-pyright-langserver", "--stdio"], require_editable=False)
    with pytest.raises(PyrightNotFound, match="pyright-langserver not found"):
        arm.index(str(tmp_path), {})


def test_editable_install_check(tmp_path):
    arm = Arm3PyrightLSP(tokenizer=Words(), cmd=[sys.executable, FAKE, "ok"], require_editable=True)
    (tmp_path / "zz_not_installed_pkg").mkdir()
    with pytest.raises(EditableInstallError, match="pip install -e"):
        arm.index(str(tmp_path / "zz_not_installed_pkg"), {"probes": PROBES})
    assert arm.client is None                     # checked before the server starts
    assert module_origin("json", sys.executable).endswith("json")


# ------------------------------------------------------------ pure helpers
def test_utf16_columns_and_reference_positions():
    import ast
    line = "x = 'é😀'; foo()"
    byte_col = line.encode().index(b"foo")
    assert utf16_col(line, byte_col) == line.index("foo") + 1      # 😀 is two UTF-16 units
    src = ("class A(Base):\n    def m(self, r: Annotated[Req, Doc('x')]) -> Out:\n"
           "        return self.helper.run(make(r))\n")
    refs = [(n, ln) for n, ln, _c in reference_positions(ast.parse(src))]
    assert refs == [("Base", 0), ("Annotated", 1), ("Req", 1), ("Doc", 1), ("Out", 1), ("run", 2), ("make", 2)]


# ------------------------------------------------------------- adapter
def _raw(items, meta):
    ctx = DeliveredContext("arm3", "t1", items, sum(i.token_count for i in items), 13_000, meta)
    return {"bundle": ctx.to_dict(), "completion": {"text": '{"symbols": ["a.b"]}', "generation_tokens": 3,
                                                    "latency_seconds": 0.1}}


def test_adapter_accepts_lsp_items_and_rejects_others():
    from types import SimpleNamespace
    task = SimpleNamespace(task_id="t1", task_type="T2_localization")
    meta = {"hop1_definitions": 1, "hop2_files": 1, "ready": {}}
    ok = [DeliveredItem("f.py:1", "h", 1, 1, "lsp_hover", ["m.f"], {"hop": 1, "lsp_method": "textDocument/hover"}),
          DeliveredItem("g.py#outline", "o", 1, 2, "lsp_symbol", ["m.g"],
                        {"hop": 2, "lsp_method": "textDocument/documentSymbol"})]
    ctx, ans = adapt("arm3", _raw(ok, meta), task)
    assert ans.answer_symbols == ["a.b"] and len(ctx.items) == 2
    with pytest.raises(ValueError, match="kinds"):
        adapt("arm3", _raw([DeliveredItem("x", "c", 1, 1, "code_chunk", [])], meta), task)
    with pytest.raises(ValueError, match="hop 2"):
        adapt("arm3", _raw([DeliveredItem("x", "c", 1, 1, "lsp_hover", [], {"hop": 3, "lsp_method": "textDocument/hover"})],
                           meta), task)
    with pytest.raises(ValueError, match="ready"):
        adapt("arm3", _raw(ok, {"hop1_definitions": 1, "hop2_files": 1}), task)


# ------------------------------------------------- against the real Pyright
@pytest.fixture(scope="module")
def fastapi_arm():
    arm = Arm3PyrightLSP(tokenizer=Words(), require_editable=False)
    arm.index(FASTAPI, {})
    yield arm
    arm.close()


SEED = {"task_id": "t", "task_type": "T2_localization", "seed_symbol": "fastapi.dependencies.utils.get_dependant"}


@real
def test_real_handshake_order_and_readiness(fastapi_arm):
    sent = [m for m, _p in fastapi_arm.client.sent]
    assert sent[:3] == ["initialize", "initialized", "workspace/didChangeConfiguration"]
    init = fastapi_arm.client.sent[0][1]
    assert init["rootUri"].startswith("file://") and init["rootPath"] == FASTAPI
    rep = fastapi_arm.ready
    assert rep.seconds < C.ARM3_READY_TIMEOUT_S and rep.source_files and rep.source_files > 100
    assert len(rep.probes) >= 5 and {"fastapi", "__init__"} <= set(rep.probes) and rep.total_probe_results > 0


@real
def test_real_two_hop_retrieval(fastapi_arm):
    client = fastapi_arm.client
    start = len(client.sent)
    ctx = fastapi_arm.retrieve("how is the dependant tree built?", SEED)
    calls = client.sent[start:]
    verify_ranking(ctx.items)
    assert 0 < ctx.total_tokens <= C.RETRIEVAL_BUDGET
    assert {it.kind for it in ctx.items} <= {"lsp_hover", "lsp_symbol"}
    assert {int(it.provenance["hop"]) for it in ctx.items} <= {1, 2}
    assert {it.provenance["lsp_method"] for it in ctx.items} == {"textDocument/hover", "textDocument/documentSymbol"}
    seed_uri = pc.path_to_uri(os.path.join(FASTAPI, "fastapi/dependencies/utils.py"))
    # hop 1: the seed hover first, naming the seed; then the seed outline
    assert ctx.items[0].kind == "lsp_hover" and ctx.items[0].symbols == [SEED["seed_symbol"]]
    assert "def get_dependant" in ctx.items[0].content
    assert ctx.items[1].kind == "lsp_symbol" and SEED["seed_symbol"] in ctx.items[1].symbols
    # definitions are only ever requested from the seed file: no third hop
    defs = [p for m, p in calls if m == "textDocument/definition"]
    assert defs and all(p["textDocument"]["uri"] == seed_uri for p in defs)
    assert len(defs) <= C.ARM3_MAX_DEFINITIONS
    # trap 3 on the real path: every query's file was didOpen'ed before it
    order: list[str] = []
    for m, p in client.sent:
        if m == "textDocument/didOpen":
            order.append(p["textDocument"]["uri"])
        elif m in ("textDocument/hover", "textDocument/definition", "textDocument/documentSymbol"):
            assert p["textDocument"]["uri"] in order, f"{m} before didOpen"
    # hop-2 items come from files other than the seed's and carry FQNs
    hop2 = [it for it in ctx.items if it.provenance["hop"] == 2]
    assert hop2 and all(not it.source_id.startswith("fastapi/dependencies/utils.py") for it in hop2)
    assert any(s == "fastapi.dependencies.models.Dependant" for it in hop2 for s in it.symbols)
    assert ctx.build_meta["hop2_files"] >= 1 and ctx.build_meta["ready"]["probes"]


@real
def test_real_budget_is_never_exceeded_and_no_seed_is_empty(fastapi_arm):
    small = Arm3PyrightLSP.__new__(Arm3PyrightLSP)
    small.__dict__.update(fastapi_arm.__dict__)
    small.budget = 60
    ctx = small.retrieve("q", SEED)
    assert ctx.total_tokens <= 60 and all(it.token_count <= 60 for it in ctx.items)
    empty = fastapi_arm.retrieve("q", {"task_id": "t", "task_type": "T2_localization", "seed_symbol": None})
    assert empty.items == [] and empty.total_tokens == 0


@real
def test_real_method_seed_and_fqn_mapping(fastapi_arm):
    ctx = fastapi_arm.retrieve("q", {**SEED, "seed_symbol": "fastapi.routing.APIRoute.__init__"})
    assert ctx.items[0].symbols == ["fastapi.routing.APIRoute.__init__"]
    path = os.path.join(FASTAPI, "fastapi/dependencies/models.py")
    fq, _sym = fastapi_arm.fqn_at(path, 20, {})
    assert fq.startswith("fastapi.dependencies.models.Dependant")
