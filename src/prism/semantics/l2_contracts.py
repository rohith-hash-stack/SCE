"""Layer 2 callback contracts: static knowledge about what an inherited
runtime/stdlib method calls back on `self` (Operation Groundwire).

A Layer 3 class that subclasses a Layer 2 class (`urllib3.connection.
HTTPConnection(http.client.HTTPConnection)`) overrides hooks the stdlib
base calls internally - `self.endheaders()` runs inside `http.client` and
ends in the subclass's own `connect()`. No source for the stdlib is
indexed, so the offline summarizer (`prism.deps.summarizer`) uses this
table to add the missing edges: inherited method -> the `self` methods it
is documented to call. Like the Layer 2 primitive table, this ships with
Prism and describes the runtime, never a third-party package.
"""
from __future__ import annotations

_HTTP_CLIENT_CONNECTION: dict[str, tuple[str, ...]] = {
    "request": ("putrequest", "putheader", "endheaders", "send"),
    "endheaders": ("send",),
    "send": ("connect",),
}

#: {Layer 2 base class: {inherited method: self-methods it calls back}}.
L2_CALLBACK_CONTRACTS: dict[str, dict[str, tuple[str, ...]]] = {
    "http.client.HTTPConnection": _HTTP_CLIENT_CONNECTION,
    "http.client.HTTPSConnection": _HTTP_CLIENT_CONNECTION,
    "threading.Thread": {"start": ("run",)},
    "multiprocessing.Process": {"start": ("run",)},
    "socketserver.BaseServer": {"serve_forever": ("process_request",), "process_request": ("finish_request",)},
    "socketserver.TCPServer": {"serve_forever": ("process_request",), "process_request": ("finish_request",)},
    "socketserver.BaseRequestHandler": {"__init__": ("setup", "handle", "finish")},
}
