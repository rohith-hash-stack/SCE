"""Resolution gaps found by the Phase 2 stub-table sweep, and the triad's
synthesized DATABASE_IO capability.

Each synthetic package reproduces one shape seen in a real library:

- `ctorpkg`  - construction runs `__init__` (`redis.Redis()`, `httpx.Client()`):
  a class boundary node carries its constructor's summary, own or inherited.
- `mixpkg`   - methods inherited from mixins imported through a barrel
  (`redis.Redis.get` -> `redis.commands.core.BasicKeyCommands.get`, with
  `client.py` sorting before `commands/__init__.py`).
- `unipkg`   - an attribute bound on different branches (`httpx`'s
  `self._pool = ConnectionPool(...)` / `HTTPProxy(...)`), a `X | None`
  annotated attribute called from a closure the method hands on
  (`aiohttp`'s `_connect_and_send_request`).
- `dotpkg`   - a dotted constructor through a sub-package re-export
  (`import sqlalchemy.orm; sqlalchemy.orm.Session().commit()`).
- `wiredb`   - a pure-Python wire-protocol database driver: it reaches a
  socket, never a DB primitive; DATABASE_IO comes from socket + DB verb.
"""
from __future__ import annotations

import textwrap

import pytest

from prism.cli import build_pipeline
from prism.deps import DependencyIndex, SummaryStore
from prism.deps.discover import find_distribution
from prism.deps.summarizer import summarize_package
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.symbol_table import GlobalSymbolTable
from prism.semantics.bitmask import FeatureBit
from prism.semantics.capabilities import compute_capability_profiles
from prism.semantics.triad import DATABASE_IO, action_tokens, database_io, has_database_verb, triad_rerank

NET = int(FeatureBit.PRIM_NET_CONNECT)
SO = ".cpython-311-x86_64-linux-gnu.so"

CTORPKG = {
    "__init__.py": "from .client import Client, Lazy, Pooled\n",
    "client.py": """
        import socket


        class Base:
            def __init__(self, host):
                self.sock = socket.create_connection((host, 80))


        class Client:
            def __init__(self, host):
                self.sock = socket.create_connection((host, 80))


        class Pooled(Base):
            pass


        class Lazy:
            def __init__(self, host):
                self.host = host
    """,
}

MIXPKG = {
    "__init__.py": "from .client import Client\n",
    "client.py": """
        import socket

        from mixpkg.commands import CoreCommands


        class Client(CoreCommands):
            def execute_command(self, *args):
                sock = socket.create_connection(("localhost", 6379))
                sock.sendall(repr(args).encode())
    """,
    "commands/__init__.py": "from .core import CoreCommands\n",
    "commands/core.py": """
        class KeyCommands:
            def get(self, name):
                return self.execute_command("GET", name)


        class CoreCommands(KeyCommands):
            pass
    """,
}

UNIPKG = {
    "__init__.py": "from .transport import Session, Transport\n",
    "transport.py": """
        import socket


        class Pool:
            def handle(self, request):
                return socket.create_connection((request, 443))


        class Proxy:
            def handle(self, request):
                return request


        class Transport:
            def __init__(self, proxy=None):
                if proxy is None:
                    self._pool = Pool()
                else:
                    self._pool = Proxy()

            def send(self, request):
                return self._pool.handle(request)


        class Connector:
            def connect(self, request):
                return socket.create_connection((request, 80))


        class Session:
            def __init__(self, connector=None):
                self._connector: Connector | None = None
                self._connector = connector

            def request(self, url):
                def _connect_and_send(req):
                    return self._connector.connect(req)

                handler = _connect_and_send
                return handler(url)
    """,
}

DOTPKG = {
    "__init__.py": "",
    "orm/__init__.py": "from .session import Session\n",
    "orm/session.py": """
        import socket


        class Session:
            def commit(self):
                return socket.create_connection(("db", 5432))
    """,
}

WIREDB = {
    "__init__.py": "from .connections import Connection, connect\n",
    "connections.py": """
        import socket


        class Connection:
            def __init__(self, host):
                self.host = host
                self._sock = None

            def _ensure(self):
                if self._sock is None:
                    self._sock = socket.create_connection((self.host, 3306))
                return self._sock

            def execute(self, sql):
                self._ensure().sendall(sql.encode())

            def ping(self):
                self._ensure()


        def connect(host):
            return Connection(host)
    """,
}

NATIVEDB = {"__init__.py": "from ._core import connect\n"}

KVSTORE = {
    "__init__.py": "from .client import Client\n",
    "client.py": """
        import socket


        class Client:
            def _send(self, *args):
                sock = socket.create_connection(("localhost", 6379))
                sock.sendall(repr(args).encode())

            def hset(self, name, key, value):
                return self._send("HSET", name, key, value)

            def get(self, name):
                return self._send("GET", name)

            def set(self, name, value):
                return self._send("SET", name, value)
    """,
}

HOST = """
import ctorpkg
import dotpkg.orm
import kvstore
import mixpkg
import nativedb
import unipkg
import wiredb


def open_client(host):
    return ctorpkg.Client(host)


def open_pooled(host):
    return ctorpkg.Pooled(host)


def open_lazy(host):
    return ctorpkg.Lazy(host)


def read_key(name):
    client = mixpkg.Client()
    return client.get(name)


def send_request(request):
    return unipkg.Transport().send(request)


def fetch_page(url):
    return unipkg.Session().request(url)


def commit_session():
    session = dotpkg.orm.Session()
    return session.commit()


def run_report(sql):
    conn = wiredb.Connection("db")
    conn.execute(sql)


def check_alive():
    conn = wiredb.Connection("db")
    conn.ping()


def native_report(sql):
    conn = nativedb.connect("dbname=app")
    conn.cursor().execute(sql)


def store_field(user):
    client = kvstore.Client()
    client.hset("users", user, 1)


def read_value(name):
    client = kvstore.Client()
    return client.get(name)


def write_value(name, value):
    client = kvstore.Client()
    client.set(name, value)


def format_name(user):
    return user.title()
"""


def _install(site, name, files=None, binaries=()):
    pkg = site / name
    pkg.mkdir(parents=True)
    record = []
    for rel, text in (files or {}).items():
        (pkg / rel).parent.mkdir(parents=True, exist_ok=True)
        (pkg / rel).write_text(textwrap.dedent(text))
        record.append(f"{name}/{rel},sha256=x,1")
    for rel in binaries:
        (pkg / rel).write_bytes(b"\x7fELF\x02\x01\x01")
        record.append(f"{name}/{rel},sha256=x,7")
    info = site / f"{name}-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n")
    (info / "top_level.txt").write_text(name + "\n")
    (info / "RECORD").write_text("\n".join(record) + "\n")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    site = tmp_path_factory.mktemp("site")
    for name, files in (("ctorpkg", CTORPKG), ("mixpkg", MIXPKG), ("unipkg", UNIPKG), ("dotpkg", DOTPKG), ("wiredb", WIREDB), ("kvstore", KVSTORE)):
        _install(site, name, files)
    _install(site, "nativedb", NATIVEDB, binaries=(f"_core{SO}",))
    return site


@pytest.fixture(scope="module")
def host(site, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("host")
    repo = tmp / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "main.py").write_text(HOST)
    index = DependencyIndex(SummaryStore(str(tmp / "store.db")), paths=[str(site)])
    builder, _ = build_pipeline(str(repo), dependencies=index)
    return builder, compute_capability_profiles(builder)


def _summary(site, name):
    return summarize_package(find_distribution(name, paths=[str(site)]))


def _hop1(host, fn):
    return host[1][f"app.main.{fn}"].by_hop[1]


# --------------------------------------------------------------------- #
# 1. Construction runs the constructor
# --------------------------------------------------------------------- #
def test_class_boundary_node_carries_its_constructor_bits(host):
    builder, _ = host
    attrs = builder.graph.nodes["ctorpkg.client.Client"]
    assert attrs["boundary"] and attrs["boundary_bits"] & NET and attrs["boundary_tier"] == "CONFIDENT"
    assert _hop1(host, "open_client") & NET


def test_inherited_constructor_counts(host):
    assert host[0].graph.nodes["ctorpkg.client.Pooled"]["boundary_bits"] & NET
    assert _hop1(host, "open_pooled") & NET


def test_lazy_constructor_stays_empty(host):
    assert host[0].graph.nodes["ctorpkg.client.Lazy"]["boundary_bits"] == 0
    assert _hop1(host, "open_lazy") == 0


def test_construction_propagates_inside_a_summary(site):
    summary = _summary(site, "wiredb")
    # `connect` only builds a Connection whose __init__ does no I/O.
    assert "wiredb.connections.connect" not in summary.symbols
    assert summary.stats["ctor_edges"] >= 1
    ctor = _summary(site, "ctorpkg")
    assert ctor.symbols["ctorpkg.client.Client.__init__"].mask & NET


# --------------------------------------------------------------------- #
# 2. Mixin / inherited methods through a barrel import
# --------------------------------------------------------------------- #
def test_inherited_mixin_method_resolves_to_its_definition(host):
    builder, _ = host
    targets = {t for _, t in builder.graph.out_edges("app.main.read_key")}
    assert "mixpkg.commands.core.KeyCommands.get" in targets
    assert "mixpkg.client.Client.get" not in targets
    assert _hop1(host, "read_key") & NET


def test_barrel_imported_base_is_linked_regardless_of_file_order(site):
    summary = _summary(site, "mixpkg")
    # KeyCommands.get -> self.execute_command -> (CHA) Client.execute_command
    assert summary.symbols["mixpkg.commands.core.KeyCommands.get"].mask & NET


def test_self_attribute_call_walks_the_mro(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "m.py").write_text(textwrap.dedent("""
        class Base:
            def run(self):
                return 1


        class Child(Base):
            pass


        class Owner:
            def __init__(self):
                self.child = Child()

            def go(self):
                return self.child.run()
    """))
    builder = ConcreteGraphBuilder(str(tmp_path), GlobalSymbolTable())
    files = [str(tmp_path / "pkg" / "__init__.py"), str(tmp_path / "pkg" / "m.py")]
    builder.pass1_collect_definitions(files)
    builder.pass2_resolve_calls(files)
    assert ("pkg.m.Owner.go", "pkg.m.Base.run") in builder.graph.edges
    assert "pkg.m.Child.run" not in builder.graph


# --------------------------------------------------------------------- #
# 3. Union / branch-bound attributes and closures (summaries)
# --------------------------------------------------------------------- #
def test_branch_bound_attribute_reaches_every_member(site):
    summary = _summary(site, "unipkg")
    send = summary.symbols["unipkg.transport.Transport.send"]
    assert send.mask & NET and send.mask == send.mask & ~send.confident_mask  # via a TENTATIVE member edge


def test_annotated_optional_attribute_called_from_a_closure(site):
    summary = _summary(site, "unipkg")
    assert summary.stats["closure_edges"] >= 1
    assert summary.symbols["unipkg.transport.Session.request"].mask & NET
    assert "unipkg.transport.Proxy.handle" not in summary.symbols  # a member without bits stays empty


def test_host_builds_keep_a_single_dispatch_target(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "m.py").write_text(textwrap.dedent(UNIPKG["transport.py"]))
    files = [str(tmp_path / "pkg" / "__init__.py"), str(tmp_path / "pkg" / "m.py")]

    def send_targets(all_members):
        builder = ConcreteGraphBuilder(str(tmp_path), GlobalSymbolTable())
        builder.dispatch_all_members = all_members
        builder.pass1_collect_definitions(files)
        builder.pass2_resolve_calls(files)
        return {t for _, t, d in builder.graph.out_edges("pkg.m.Transport.send", data=True) if d.get("relation") == "CALLS"}

    assert send_targets(False) == {"pkg.m.Proxy.handle"}  # most recent binding, as before
    assert send_targets(True) == {"pkg.m.Proxy.handle", "pkg.m.Pool.handle"}


def test_host_reaches_union_and_closure_bits(host):
    assert _hop1(host, "send_request") & NET
    assert _hop1(host, "fetch_page") & NET


# --------------------------------------------------------------------- #
# 4. Dotted constructor through a sub-package re-export
# --------------------------------------------------------------------- #
def test_dotted_constructor_types_the_local(host):
    builder, _ = host
    targets = {t for _, t in builder.graph.out_edges("app.main.commit_session")}
    assert "dotpkg.orm.session.Session.commit" in targets
    assert not any(t.startswith("<ambiguous:") for t in targets)
    assert _hop1(host, "commit_session") & NET


# --------------------------------------------------------------------- #
# Triad: DATABASE_IO without a driver catalog
# --------------------------------------------------------------------- #
def test_wire_protocol_driver_is_database_io_by_socket_and_verb(host):
    builder, profiles = host
    q = "app.main.run_report"
    assert profiles[q].within(2) & NET and not profiles[q].within(2) & int(FeatureBit.PRIM_DB_WRITE)
    score, source = database_io(profiles[q], action_tokens(builder, q))
    assert source == "net-socket+db-verb" and score == 0.5


def test_socket_without_a_database_verb_is_not_database_io(host):
    builder, profiles = host
    for q in ("app.main.check_alive", "app.main.send_request"):  # ping / send: network, no DB verb
        assert profiles[q].within(2) & NET
        assert database_io(profiles[q], action_tokens(builder, q)) == (0.0, "none")


def test_opaque_native_driver_with_a_database_verb(host):
    builder, profiles = host
    q = "app.main.native_report"
    assert database_io(profiles[q], action_tokens(builder, q)) == (0.0, "opaque-native")


def test_triad_rerank_with_database_io(host):
    builder, profiles = host
    candidates = ["app.main.format_name", "app.main.check_alive", "app.main.native_report", "app.main.run_report"]
    tokens = {q: action_tokens(builder, q) for q in candidates}
    ranked = triad_rerank(candidates, {q: 1.0 for q in candidates}, profiles, tokens, target=DATABASE_IO)
    by = {r.qname: r for r in ranked}
    assert by["app.main.run_report"].capability_source == "net-socket+db-verb"
    assert by["app.main.run_report"].score == pytest.approx(1.0 + 0.25 * 0.5)
    assert by["app.main.native_report"].capability_source == "opaque-native"
    assert by["app.main.native_report"].score == pytest.approx(1.40)
    assert by["app.main.check_alive"].score == 1.0 and by["app.main.format_name"].score == 1.0
    assert [r.qname for r in ranked][:2] == ["app.main.native_report", "app.main.run_report"]


# --------------------------------------------------------------------- #
# Headers keep methods declared under class-body branches
# --------------------------------------------------------------------- #
def test_header_keeps_methods_declared_under_class_body_branches():
    from prism.deps.headers import header_source

    source = textwrap.dedent("""
        import sys
        from typing import TYPE_CHECKING


        class ClientSession:
            if TYPE_CHECKING:
                def get(self, url) -> "Ctx": ...
            else:
                def get(self, url, *, allow_redirects=True):
                    return self._request("GET", url)

            try:
                def close(self):
                    return 1
            except NameError:
                pass

            def _request(self, method, url):
                return method
    """)
    header = header_source(source)
    assert header.count("def get(") == 1 and "def close(" in header and "def _request(" in header
    assert "return" not in header


# --------------------------------------------------------------------- #
# Unambiguous key-value commands are DATABASE_IO; bare get/set are not
# --------------------------------------------------------------------- #
def test_unambiguous_kv_command_is_database_io(host):
    builder, profiles = host
    q = "app.main.store_field"
    assert "hset" in action_tokens(builder, q)
    assert database_io(profiles[q], action_tokens(builder, q)) == (0.5, "net-socket+db-verb")


def test_bare_get_and_set_stay_out_of_database_io(host):
    builder, profiles = host
    for q in ("app.main.read_value", "app.main.write_value"):
        assert profiles[q].within(2) & NET  # same socket, same client
        assert database_io(profiles[q], action_tokens(builder, q)) == (0.0, "none")


def test_kv_verbs_match_whole_words_only():
    assert has_database_verb({"hset"}) and has_database_verb({"decr"}) and has_database_verb({"zadd"})
    for words in ({"decrypt"}, {"increment"}, {"expired"}, {"get"}, {"set"}, {"delete"}, {"msets"}):
        assert not has_database_verb(words), words
