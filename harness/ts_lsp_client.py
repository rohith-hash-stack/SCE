"""A raw JSON-RPC (LSP) client for `typescript-language-server --stdio`, the
TypeScript/JavaScript counterpart of `harness.pyright_client.PyrightClient`
(M4, Arm 3 on Express and tRPC). It reuses that client's framing, request
and query code unchanged; only the languageId and readiness differ.

Handshake (strict order, the same as Pyright's): initialize (rootUri and
rootPath) -> initialized -> workspace/didChangeConfiguration -> readiness
-> workspace/symbol probe verification.

Readiness. tsserver loads projects lazily: until a file is open, every
workspace/symbol request fails with "No Project", and it sends neither
Pyright's "Found N source files" log nor `$/progress` for the load. So:
1. didOpen an anchor file (the corpus entry point: its top-level `index`
   file, else the first source file);
2. tsserver's `projectInfo` request (through `workspace/executeCommand`
   `typescript.tsserverRequest`) returns once the anchor's project is
   built, with the project's file list: the counterpart of Pyright's
   "Found N source files";
3. quiescence probe: the workspace/symbol probes are repeated every 0.5 s
   until their counts have not changed for `quiescence_s` (2 s by default;
   on Express the counts still moved about 1 s after `projectInfo`
   returned). All-zero probes abort (`NotReadyError`).
"""
from __future__ import annotations

import time
from pathlib import Path

from harness.pyright_client import READY_TIMEOUT_S, LspError, NotReadyError, PyrightClient, ReadyReport

DEFAULT_CMD = ["typescript-language-server", "--stdio"]
#: workspace/symbol counts must hold this long before the server counts as ready
QUIESCENCE_S = 2.0
PROBE_INTERVAL_S = 0.5
LANGUAGE_IDS = {".ts": "typescript", ".tsx": "typescriptreact", ".js": "javascript", ".jsx": "javascriptreact"}


class TypeScriptLspClient(PyrightClient):
    def __init__(self, root: str, cmd: list[str] | None = None, settings: dict | None = None,
                 anchor: str | None = None, quiescence_s: float = QUIESCENCE_S, **kw) -> None:
        super().__init__(root, cmd=list(cmd or DEFAULT_CMD), settings=settings or {}, **kw)
        #: the file opened to make tsserver load the project
        self.anchor = anchor
        self.quiescence_s = quiescence_s
        #: the anchor project's file count, from projectInfo (set by wait_ready)
        self.project_files: int | None = None

    def language_id(self, path: str) -> str:
        return LANGUAGE_IDS.get(Path(path).suffix, "typescript")

    def project_info(self, path: str) -> dict:
        """tsserver's projectInfo for an open file: returns once the file's
        project is built. Its body holds `configFileName` and `fileNames`."""
        res = self.request("workspace/executeCommand", {
            "command": "typescript.tsserverRequest",
            "arguments": ["projectInfo", {"file": str(Path(path).resolve()), "needFileNameList": True}]})
        if not isinstance(res, dict) or not res.get("success", True):
            raise LspError(f"projectInfo failed: {res!r}")
        return res.get("body") or {}

    def wait_ready(self, probes, timeout: float = READY_TIMEOUT_S) -> ReadyReport:
        """`probes`: the probe queries, or a callable that picks them from
        the loaded project's file paths (so every probe can be found)."""
        if not self.anchor:
            raise NotReadyError("no anchor file: tsserver loads no project until a file is open")
        t0 = time.monotonic()
        deadline = t0 + timeout
        self.did_open(self.anchor)                                    # 1. load the anchor's project
        info = self.project_info(self.anchor)                         # 2. returns once it is built
        files = list(info.get("fileNames") or [])
        self.project_files = len(files)
        if callable(probes):
            probes = probes(files)
        if len(probes) < 5 or not all(probes):
            raise ValueError(f"at least 5 non-empty probe queries are required: {probes!r}")
        previous: dict[str, int] | None = None                        # 3. quiescence probe
        stable_since = time.monotonic()
        rounds = 0
        while True:
            counts = {q: len(self.request("workspace/symbol", {"query": q}) or []) for q in probes}
            rounds += 1
            now = time.monotonic()
            if counts != previous:
                previous, stable_since = counts, now
            elif now - stable_since >= self.quiescence_s:
                break
            if now > deadline:
                break
            time.sleep(PROBE_INTERVAL_S)
        report = ReadyReport(seconds=time.monotonic() - t0, source_files=self.project_files, probes=counts,
                             rounds=rounds)
        if report.total_probe_results == 0:
            raise NotReadyError(f"all {len(probes)} workspace/symbol probes returned nothing: {counts}")
        return report


__all__ = ["DEFAULT_CMD", "QUIESCENCE_S", "TypeScriptLspClient"]
