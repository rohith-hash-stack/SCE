"""A raw JSON-RPC (LSP) client for `pyright-langserver --stdio`. Not
multilspy, which defaults to jedi for Python.

Known traps, each guarded and tested:
1. Content-Length is never hard-coded: it is `len(body)` of the UTF-8
   encoded body, so non-ASCII content is framed correctly.
2. The server runs with `bufsize=0` and without `text=True`. Frames are read
   from the raw byte stream and decoded by hand (`FrameDecoder`), which
   handles frames split across reads and several frames in one read.
3. A file must be `didOpen`ed before hover / definition / documentSymbol
   requests on it; the client refuses otherwise (`NotOpenError`).

Handshake (strict order, `handshake()`):
  initialize (rootUri and rootPath) -> initialized ->
  workspace/didChangeConfiguration (typeCheckingMode "basic") ->
  readiness -> workspace/symbol probe verification.

Readiness. The M2 charter waits for `experimental/serverStatus` with
`quiescent=true`. Pyright does not send that notification (it is
rust-analyzer's), nor `$/progress` for its initial analysis, so its
readiness signal is used instead: the `window/logMessage` "Found N source
files" (the project is loaded), then `workspace/symbol` probes repeated until
two consecutive rounds return identical counts. If every probe returns
nothing, the server is not usable for this repository and the arm aborts
(`NotReadyError`).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CMD = ["pyright-langserver", "--stdio"]
READY_TIMEOUT_S = 120.0
REQUEST_TIMEOUT_S = 60.0
_FOUND_FILES = re.compile(r"Found (\d+) source files?")


class LspError(RuntimeError):
    pass


class NotReadyError(LspError):
    pass


class NotOpenError(LspError):
    pass


def encode_message(msg: dict) -> bytes:
    """One LSP frame. Content-Length counts the UTF-8 bytes of the body,
    never characters and never a constant."""
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


class FrameDecoder:
    """Incremental decoder over a raw byte stream: feed any chunk sizes,
    get complete messages out."""

    def __init__(self) -> None:
        self._buf = b""

    def feed(self, data: bytes) -> list[dict]:
        self._buf += data
        out: list[dict] = []
        while True:
            sep = self._buf.find(b"\r\n\r\n")
            if sep < 0:
                return out
            header = self._buf[:sep].decode("ascii", errors="replace")
            length = None
            for line in header.split("\r\n"):
                name, _, value = line.partition(":")
                if name.strip().lower() == "content-length":
                    length = int(value.strip())
            if length is None:
                raise LspError(f"frame without Content-Length: {header!r}")
            start = sep + 4
            if len(self._buf) - start < length:
                return out
            body = self._buf[start:start + length]
            self._buf = self._buf[start + length:]
            out.append(json.loads(body.decode("utf-8")))


def path_to_uri(path: str) -> str:
    return Path(path).resolve().as_uri()


def uri_to_path(uri: str) -> str:
    from urllib.parse import unquote, urlparse
    return unquote(urlparse(uri).path)


@dataclass
class ReadyReport:
    seconds: float
    source_files: int | None
    probes: dict[str, int] = field(default_factory=dict)
    rounds: int = 0

    @property
    def total_probe_results(self) -> int:
        return sum(self.probes.values())


class PyrightClient:
    def __init__(self, root: str, cmd: list[str] | None = None, settings: dict | None = None,
                 request_timeout: float = REQUEST_TIMEOUT_S) -> None:
        self.root = str(Path(root).resolve())
        self.cmd = list(cmd or DEFAULT_CMD)
        self.settings = settings or {"python": {"analysis": {"typeCheckingMode": "basic"}}}
        self.request_timeout = request_timeout
        self.proc: subprocess.Popen | None = None
        self._next_id = 0
        self._responses: dict[int, dict] = {}
        self._cv = threading.Condition()
        self._write_lock = threading.Lock()
        self.notifications: list[dict] = []
        #: every message this client sent, in order: (method, params)
        self.sent: list[tuple[str, Any]] = []
        self.opened: set[str] = set()
        self.stderr_tail: list[str] = []
        self._closed = False

    # ------------------------------------------------------------ process
    def start(self) -> None:
        # bufsize=0 and no text=True: raw bytes, framing decoded by hand
        self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, bufsize=0, cwd=self.root)
        threading.Thread(target=self._read_loop, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        for raw in iter(self.proc.stderr.readline, b""):
            self.stderr_tail = (self.stderr_tail + [raw.decode("utf-8", errors="replace").rstrip()])[-50:]

    def _read_loop(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        decoder = FrameDecoder()
        while True:
            chunk = self.proc.stdout.read(65536)
            if not chunk:
                break
            for msg in decoder.feed(chunk):
                self._dispatch(msg)
        with self._cv:
            self._closed = True
            self._cv.notify_all()

    def _dispatch(self, msg: dict) -> None:
        if "method" in msg and "id" in msg:            # server -> client request
            self._answer_server_request(msg)
        elif "method" in msg:                          # notification
            with self._cv:
                self.notifications.append(msg)
                self._cv.notify_all()
        else:                                          # response
            with self._cv:
                self._responses[msg["id"]] = msg
                self._cv.notify_all()

    def _answer_server_request(self, msg: dict) -> None:
        method, params = msg["method"], msg.get("params") or {}
        result: Any = None
        if method == "workspace/configuration":
            result = [self._config_section(item.get("section", "")) for item in params.get("items", [])]
        self._write({"jsonrpc": "2.0", "id": msg["id"], "result": result})

    def _config_section(self, section: str) -> Any:
        node: Any = self.settings
        for part in [p for p in section.split(".") if p]:
            node = node.get(part, {}) if isinstance(node, dict) else {}
        return node

    def _write(self, msg: dict) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise LspError("server not started")
        with self._write_lock:
            try:
                self.proc.stdin.write(encode_message(msg))
                self.proc.stdin.flush()
            except OSError as exc:   # BrokenPipeError: the server has exited
                raise LspError(f"server exited ({exc}): {' | '.join(self.stderr_tail[-3:])}") from exc

    # ----------------------------------------------------------- messaging
    def notify(self, method: str, params: Any) -> None:
        self.sent.append((method, params))
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: Any, timeout: float | None = None) -> Any:
        with self._cv:
            self._next_id += 1
            rid = self._next_id
        self.sent.append((method, params))
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.monotonic() + (timeout or self.request_timeout)
        with self._cv:
            while rid not in self._responses:
                if self._closed:
                    raise LspError(f"server exited during {method}: {' | '.join(self.stderr_tail[-3:])}")
                left = deadline - time.monotonic()
                if left <= 0:
                    raise LspError(f"{method} timed out")
                self._cv.wait(left)
            resp = self._responses.pop(rid)
        if "error" in resp:
            raise LspError(f"{method}: {resp['error']}")
        return resp.get("result")

    # ------------------------------------------------------------ handshake
    def handshake(self, probes: list[str], ready_timeout: float = READY_TIMEOUT_S) -> ReadyReport:
        uri = path_to_uri(self.root)
        self.request("initialize", {
            "processId": os.getpid(), "rootUri": uri, "rootPath": self.root,
            "workspaceFolders": [{"uri": uri, "name": Path(self.root).name}],
            "capabilities": {
                "workspace": {"configuration": True, "symbol": {}, "workspaceFolders": True},
                "window": {"workDoneProgress": True},
                "textDocument": {"hover": {"contentFormat": ["markdown", "plaintext"]},
                                 "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                                 "definition": {"linkSupport": False}},
            },
        })
        self.notify("initialized", {})
        self.notify("workspace/didChangeConfiguration", {"settings": self.settings})
        return self.wait_ready(probes, ready_timeout)

    def wait_ready(self, probes: list[str], timeout: float = READY_TIMEOUT_S) -> ReadyReport:
        if len(probes) < 5 or not all(probes):
            raise ValueError("at least 5 non-empty probe queries are required")
        t0 = time.monotonic()
        deadline = t0 + timeout
        source_files = None
        with self._cv:   # 1. the project is loaded
            while source_files is None:
                for n in self.notifications:
                    m = _FOUND_FILES.search(str((n.get("params") or {}).get("message", "")))
                    if n.get("method") == "window/logMessage" and m:
                        source_files = int(m.group(1))
                if source_files is not None:
                    break
                if self._closed:
                    raise NotReadyError(f"server exited before loading: {' | '.join(self.stderr_tail[-3:])}")
                left = deadline - time.monotonic()
                if left <= 0:
                    raise NotReadyError(f"no 'Found N source files' within {timeout:.0f}s")
                self._cv.wait(min(left, 1.0))
        # 2. probe until two consecutive rounds agree
        previous: dict[str, int] | None = None
        rounds = 0
        while True:
            counts = {q: len(self.request("workspace/symbol", {"query": q}) or []) for q in probes}
            rounds += 1
            if counts == previous:
                break
            previous = counts
            if time.monotonic() > deadline:
                break
            time.sleep(0.5)
        report = ReadyReport(seconds=time.monotonic() - t0, source_files=source_files, probes=counts, rounds=rounds)
        if report.total_probe_results == 0:
            raise NotReadyError(f"all {len(probes)} workspace/symbol probes returned nothing: {counts}")
        return report

    # ------------------------------------------------------------- queries
    def did_open(self, path: str) -> None:
        path = str(Path(path).resolve())
        if path in self.opened:
            return
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        self.notify("textDocument/didOpen", {"textDocument": {
            "uri": path_to_uri(path), "languageId": "python", "version": 1, "text": text}})
        self.opened.add(path)

    def _require_open(self, path: str) -> str:
        path = str(Path(path).resolve())
        if path not in self.opened:
            raise NotOpenError(f"didOpen {path} before querying it")
        return path

    def _pos(self, path: str, line: int, character: int) -> dict:
        return {"textDocument": {"uri": path_to_uri(self._require_open(path))},
                "position": {"line": line, "character": character}}

    def hover(self, path: str, line: int, character: int) -> str:
        res = self.request("textDocument/hover", self._pos(path, line, character))
        if not res:
            return ""
        contents = res.get("contents")
        if isinstance(contents, dict):
            return str(contents.get("value", ""))
        if isinstance(contents, list):
            return "\n".join(c.get("value", "") if isinstance(c, dict) else str(c) for c in contents)
        return str(contents or "")

    def definition(self, path: str, line: int, character: int) -> list[dict]:
        res = self.request("textDocument/definition", self._pos(path, line, character))
        if not res:
            return []
        locs = res if isinstance(res, list) else [res]
        out = []
        for loc in locs:
            uri = loc.get("uri") or loc.get("targetUri")
            rng = loc.get("range") or loc.get("targetSelectionRange") or loc.get("targetRange")
            if uri and rng:
                out.append({"path": uri_to_path(uri), "line": rng["start"]["line"], "character": rng["start"]["character"]})
        return out

    def document_symbols(self, path: str) -> list[dict]:
        res = self.request("textDocument/documentSymbol",
                           {"textDocument": {"uri": path_to_uri(self._require_open(path))}})
        return list(res or [])

    def workspace_symbol(self, query: str) -> list[dict]:
        return list(self.request("workspace/symbol", {"query": query}) or [])

    def shutdown(self) -> None:
        if self.proc is None:
            return
        try:
            if not self._closed:
                self.request("shutdown", None, timeout=10)
                self.notify("exit", None)
        except (LspError, OSError):
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
