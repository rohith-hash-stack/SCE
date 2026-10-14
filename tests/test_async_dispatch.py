"""Step 2: async event loops and background dispatch chains.

Synthetic packages reproduce shapes seen in real async libraries:

- `aiodb`   - `asyncpg`: `loop = asyncio.get_event_loop()` then
  `loop.create_connection(...)` on an untyped loop, plus the top-level
  `asyncio.open_connection` stream helper.
- `aiosrv`  - `aiohttp.web`: `asyncio.start_server` / `loop.create_server`
  (LISTEN), and `loop.getaddrinfo` (DNS).
- `taskpkg` - a function handed on by reference: `loop.run_in_executor(
  None, self._connect)`, `Thread(target=worker)`, `executor.submit(fn)`
  (celery/kombu worker and pool shapes).
"""
from __future__ import annotations

import textwrap

import pytest

from prism.deps.discover import find_distribution
from prism.deps.summarizer import summarize_package
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.symbol_table import GlobalSymbolTable
from prism.semantics.bitmask import FeatureBit
from prism.semantics.primitives import derive_primitive_bits
from prism.semantics.substance import EVENT_LOOP_METHODS, fallback_category

F = FeatureBit
NET = int(F.PRIM_NET_CONNECT)
LISTEN = int(F.PRIM_NET_LISTEN)
DNS = int(F.PRIM_NET_DNS)
SPAWN = int(F.PRIM_PROC_SPAWN)

AIODB = {
    "__init__.py": "from .conn import connect, stream\n",
    "conn.py": """
        import asyncio


        class Protocol(asyncio.Protocol):
            pass


        async def connect(host, port):
            loop = asyncio.get_event_loop()
            return await loop.create_connection(Protocol, host, port)


        async def stream(host, port):
            return await asyncio.open_connection(host, port)


        def create_connection(host):
            return host  # a bare same-named helper is not the event loop's
    """,
}

AIOSRV = {
    "__init__.py": "from .server import serve, serve_loop, resolve, run_tool\n",
    "server.py": """
        import asyncio


        async def handler(reader, writer):
            pass


        async def serve(port):
            return await asyncio.start_server(handler, "0.0.0.0", port)


        class App:
            def __init__(self, loop):
                self._loop = loop

            async def serve_loop(self, port):
                return await self._loop.create_server(asyncio.Protocol, "0.0.0.0", port)


        async def serve_loop(app, port):
            return await app.serve_loop(port)


        async def resolve(host):
            loop = asyncio.get_running_loop()
            return await loop.getaddrinfo(host, 443)


        async def run_tool(cmd):
            return await asyncio.create_subprocess_exec(cmd)
    """,
}

TASKPKG = {
    "__init__.py": "from .worker import Pool, start_thread, submit_job, plain\n",
    "worker.py": """
        import asyncio
        import socket
        import threading
        from concurrent.futures import ThreadPoolExecutor


        def _dial(host):
            return socket.create_connection((host, 80))


        def worker():
            _dial("broker")


        class Pool:
            def _connect(self):
                return _dial("db")

            async def acquire(self):
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, self._connect)


        def start_thread():
            threading.Thread(target=worker).start()


        def submit_job():
            with ThreadPoolExecutor() as executor:
                return executor.submit(worker)


        def plain(values):
            return sorted(values, key=len)
    """,
}


def _install(site, name, files):
    pkg = site / name
    pkg.mkdir(parents=True)
    record = []
    for rel, text in files.items():
        (pkg / rel).write_text(textwrap.dedent(text))
        record.append(f"{name}/{rel},sha256=x,1")
    info = site / f"{name}-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n")
    (info / "top_level.txt").write_text(name + "\n")
    (info / "RECORD").write_text("\n".join(record) + "\n")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    site = tmp_path_factory.mktemp("site")
    for name, files in (("aiodb", AIODB), ("aiosrv", AIOSRV), ("taskpkg", TASKPKG)):
        _install(site, name, files)
    return site


def _symbols(site, name):
    return summarize_package(find_distribution(name, paths=[str(site)])).symbols


def _mask(symbols, qname):
    return symbols[qname].mask if qname in symbols else 0


# --------------------------------------------------------------------- #
# 1. Layer 2: asyncio network, DNS, LISTEN and subprocess primitives
# --------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "entry,bit",
    [
        ("asyncio.AbstractEventLoop.create_connection", NET),
        ("asyncio.open_connection", NET),
        ("asyncio.AbstractEventLoop.sock_connect", NET),
        ("asyncio.start_server", LISTEN),
        ("asyncio.AbstractEventLoop.create_server", LISTEN),
        ("asyncio.AbstractEventLoop.sock_accept", LISTEN),
        ("asyncio.AbstractEventLoop.getaddrinfo", DNS),
        ("socket.getaddrinfo", DNS),
        ("socket.gethostbyname", DNS),
        ("asyncio.create_subprocess_exec", SPAWN),
        ("asyncio.AbstractEventLoop.subprocess_shell", SPAWN),
    ],
)
def test_asyncio_entries_derive_their_primitive(entry, bit):
    category = "process_io" if bit == SPAWN else "network_io"
    assert derive_primitive_bits(category, entry) == bit


def test_event_loop_methods_come_from_the_layer_2_table():
    assert EVENT_LOOP_METHODS["create_connection"] == "network_io"
    assert EVENT_LOOP_METHODS["subprocess_exec"] == "process_io"
    assert "run_in_executor" not in EVENT_LOOP_METHODS  # scheduling, not I/O


def test_event_loop_fallback_needs_a_receiver():
    assert fallback_category("create_connection", on_receiver=True) == "network_io"
    assert fallback_category("create_connection", on_receiver=False) is None


# --------------------------------------------------------------------- #
# 2. Untyped event loops inside a summarized package
# --------------------------------------------------------------------- #
def test_untyped_loop_create_connection_is_network(site):
    symbols = _symbols(site, "aiodb")
    assert _mask(symbols, "aiodb.conn.connect") & NET
    assert _mask(symbols, "aiodb.conn.stream") & NET
    assert not _mask(symbols, "aiodb.conn.create_connection")


def test_servers_listen_and_resolvers_resolve(site):
    symbols = _symbols(site, "aiosrv")
    assert _mask(symbols, "aiosrv.server.serve") & LISTEN
    assert _mask(symbols, "aiosrv.server.App.serve_loop") & LISTEN
    assert _mask(symbols, "aiosrv.server.resolve") & DNS
    assert _mask(symbols, "aiosrv.server.run_tool") & SPAWN


# --------------------------------------------------------------------- #
# 3. Functions handed on by reference (summary mode only)
# --------------------------------------------------------------------- #
def test_callback_arguments_reach_the_scheduled_function(site):
    symbols = _symbols(site, "taskpkg")
    for qname in ("taskpkg.worker.Pool.acquire", "taskpkg.worker.start_thread", "taskpkg.worker.submit_job"):
        mask = _mask(symbols, qname)
        assert mask & NET, qname
        assert not symbols[qname].confident_mask & NET  # "may run": a TENTATIVE edge
    assert not _mask(symbols, "taskpkg.worker.plain")


def test_host_builds_do_not_link_callback_arguments(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "m.py").write_text(textwrap.dedent(TASKPKG["worker.py"]))
    files = [str(tmp_path / "pkg" / "__init__.py"), str(tmp_path / "pkg" / "m.py")]

    def targets(all_members, caller):
        builder = ConcreteGraphBuilder(str(tmp_path), GlobalSymbolTable())
        builder.dispatch_all_members = all_members
        builder.pass1_collect_definitions(files)
        builder.pass2_resolve_calls(files)
        return {
            (t, d.get("kind"))
            for _, t, d in builder.graph.out_edges(caller, data=True)
            if d.get("relation") == "CALLS"
        }

    assert ("pkg.m.worker", "TENTATIVE_CALL") in targets(True, "pkg.m.start_thread")
    assert ("pkg.m.Pool._connect", "TENTATIVE_CALL") in targets(True, "pkg.m.Pool.acquire")
    assert not any(t == "pkg.m.worker" for t, _ in targets(False, "pkg.m.start_thread"))
