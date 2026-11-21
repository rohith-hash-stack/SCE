"""A fake LSP server over stdio, for the client's framing and error-path
tests (the real pyright-langserver cannot be made to misbehave on demand).

    python fake_lsp_server.py <mode> [log_path]

Modes:
  ok      answer every request; log "Found 3 source files"; probes return 1
          symbol; ask the client for workspace/configuration once.
  empty   as ok, but every workspace/symbol probe returns nothing.
  nolog   as ok, but never log "Found N source files".
  die     exit right after answering initialize.
  split   as ok, but write every frame one byte at a time, and the log and
          the initialize response merged into one write.
Every frame it reads must carry an exact Content-Length (it reads exactly
that many bytes and parses them); each method received is appended to
`log_path` (one JSON line), so tests can check the order on the wire.
`test/echo` returns its params unchanged.
"""
import json
import os
import sys
import time

MODE = sys.argv[1] if len(sys.argv) > 1 else "ok"
LOG = sys.argv[2] if len(sys.argv) > 2 else None
stdin, stdout = sys.stdin.buffer, sys.stdout.buffer


def read_frame():
    length = None
    while True:
        line = stdin.readline()
        if not line:
            return None
        line = line.rstrip(b"\r\n")
        if not line:
            break
        name, _, value = line.decode("ascii").partition(":")
        if name.strip().lower() == "content-length":
            length = int(value.strip())
    body = stdin.read(length)
    return json.loads(body.decode("utf-8"))      # fails loudly on a wrong Content-Length


def frame(msg):
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    return b"Content-Length: %d\r\n\r\n" % len(body) + body


def write(data):
    if MODE == "split":
        for i in range(len(data)):
            stdout.write(data[i:i + 1])
            stdout.flush()
    else:
        stdout.write(data)
        stdout.flush()


def log(msg):
    if LOG:
        with open(LOG, "a") as fh:
            fh.write(json.dumps({"method": msg.get("method"), "id": msg.get("id"), "params": msg.get("params")}) + "\n")


def main():
    while True:
        msg = read_frame()
        if msg is None:
            return
        log(msg)
        method, rid = msg.get("method"), msg.get("id")
        if method is None:                        # a response to our own request
            continue
        if method == "initialize":
            resp = frame({"jsonrpc": "2.0", "id": rid, "result": {"capabilities": {"hoverProvider": True}}})
            if MODE == "die":
                write(resp)
                os._exit(3)
            write(resp)
        elif method == "initialized":
            write(frame({"jsonrpc": "2.0", "id": 900, "method": "workspace/configuration",
                         "params": {"items": [{"section": "python.analysis"}]}}))
        elif method == "workspace/didChangeConfiguration":
            if MODE != "nolog":
                time.sleep(0.05)
                write(frame({"jsonrpc": "2.0", "method": "window/logMessage",
                             "params": {"type": 3, "message": "Found 3 source files"}}))
        elif method == "workspace/symbol":
            result = [] if MODE == "empty" else [{"name": params_query(msg), "kind": 12,
                                                  "location": {"uri": "file:///x.py", "range": rng(0)}}]
            write(frame({"jsonrpc": "2.0", "id": rid, "result": result}))
        elif method == "textDocument/hover":
            write(frame({"jsonrpc": "2.0", "id": rid,
                         "result": {"contents": {"kind": "markdown", "value": "def café() -> 'naïve ✓'"}}}))
        elif method == "test/echo":
            write(frame({"jsonrpc": "2.0", "id": rid, "result": msg.get("params")}))
        elif method == "shutdown":
            write(frame({"jsonrpc": "2.0", "id": rid, "result": None}))
        elif method == "exit":
            return
        elif rid is not None:
            write(frame({"jsonrpc": "2.0", "id": rid, "result": None}))


def params_query(msg):
    return (msg.get("params") or {}).get("query", "")


def rng(line):
    return {"start": {"line": line, "character": 0}, "end": {"line": line, "character": 1}}


if __name__ == "__main__":
    main()
