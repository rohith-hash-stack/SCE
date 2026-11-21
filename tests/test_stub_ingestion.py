"""Type stubs and synthesized SDK methods (boto3 / botocore shape).

Synthetic packages, installed into a private site:

- `sdkcore` (botocore): `BaseClient._make_api_call` reaches a socket through
  `Session.send`, which gets its pool from `self._get_manager(...)` -
  returning `self._manager` on one branch and an unknown on the other (the
  return-type inference gap the botocore trace exposed).
  `ClientCreator._create_api_method` is a method factory: it returns a
  nested `_api_call(self, ...)` that calls `self._make_api_call(...)`.
  `BaseClient.get_paginator` is a real method.
- `sdk` (boto3): `client(service_name)` builds a client at run time.
- `sdk-stubs/` (boto3-stubs, PEP 561): `.pyi` overlay with
  `@overload def client(service_name: Literal["store"]) -> StoreClient`.
- `sdk_store` (mypy_boto3_s3): `StoreClient(BaseClient)` declares
  `put_item` / `get_paginator` with no bodies; its `client.pyi` is the
  typing contract next to a minimal `client.py`.
- `natdb`: compiled only (`_ext.so`) with an `__init__.pyi`.
"""
from __future__ import annotations

import textwrap

import pytest

from prism.cli import build_pipeline
from prism.deps import DependencyIndex, SummaryStore
from prism.deps.discover import find_stubs
from prism.deps.headers import package_headers
from prism.semantics.bitmask import FeatureBit
from prism.semantics.capabilities import compute_capability_profiles

NET = int(FeatureBit.PRIM_NET_CONNECT)
SO = ".cpython-311-x86_64-linux-gnu.so"

SDKCORE = {
    "__init__.py": "",
    "pool.py": """
        import socket


        class Connection:
            def urlopen(self, url):
                return socket.create_connection((url, 443))


        class Pool:
            def connection(self, url) -> Connection:
                return Connection()


        class LocalCache:  # same method names: no unique-name fallback can stand in for typing
            def connection(self, url):
                return self

            def urlopen(self, url):
                return url
    """,
    "session.py": """
        from sdkcore.pool import Pool


        class Session:
            def __init__(self):
                self._manager = Pool()
                self._proxies = {}

            def _get_manager(self, proxy):
                if proxy:
                    manager = self._proxies[proxy]
                else:
                    manager = self._manager
                return manager

            def send(self, url, proxy=None):
                manager = self._get_manager(proxy)
                conn = manager.connection(url)
                return conn.urlopen(url)
    """,
    "client.py": """
        from sdkcore.session import Session


        class BaseClient:
            def __init__(self):
                self._session = Session()

            def _make_api_call(self, operation, params):
                return self._session.send("https://api.example.com/" + operation)

            def get_paginator(self, name):
                return [name]


        class ClientCreator:
            def _create_api_method(self, operation):
                def _api_call(self, **kwargs):
                    return self._make_api_call(operation, kwargs)

                return _api_call

            def create_client(self, service):
                methods = {"put_item": self._create_api_method("PutItem")}
                return type(service, (BaseClient,), methods)()
    """,
}

SDK = {"__init__.py": """
    from sdkcore.client import ClientCreator


    def client(service_name):
        return ClientCreator().create_client(service_name)
"""}

SDK_STUBS = {"__init__.pyi": """
    from typing import Literal, overload

    from sdk_queue.client import QueueClient
    from sdk_store.client import StoreClient

    @overload
    def client(service_name: Literal["queue"]) -> QueueClient: ...
    @overload
    def client(service_name: Literal["store"]) -> StoreClient: ...
"""}

SDK_STORE = {
    "__init__.py": "",
    "client.py": "class StoreClient:\n    pass\n",  # the runtime file says nothing; the .pyi is the contract
    "client.pyi": """
        from sdkcore.client import BaseClient

        class StoreClient(BaseClient):
            def put_item(self, **kwargs) -> dict: ...
            def get_paginator(self, name: str) -> list: ...
    """,
}

SDK_QUEUE = {
    "__init__.py": "",
    "client.pyi": """
        from sdkcore.client import BaseClient

        class QueueClient(BaseClient):
            def send_message(self, **kwargs) -> dict: ...
    """,
    "client.py": "",
}

FASTLIB = {  # a Python package with one compiled submodule typed by its .pyi (numpy.random._generator)
    "__init__.py": "from ._fast import fast_hash\n",
    "_fast.pyi": "def fast_hash(data: bytes) -> int: ...\n",
}

NATDB = {"__init__.pyi": "class Connection:\n    def execute(self, sql: str) -> None: ...\ndef connect(dsn: str) -> Connection: ...\n"}

HOST = """
import fastlib
import natdb
import sdk
from sdk_store.client import StoreClient


def archive_invoice(pdf):
    store = sdk.client("store")
    store.put_item(Key="invoice", Body=pdf)


def archive_invoice_kw(pdf):
    store = sdk.client(service_name="store")
    store.put_item(Key="invoice", Body=pdf)


def archive_typed(store: StoreClient, pdf):
    store.put_item(Key="invoice", Body=pdf)


def list_pages(store: StoreClient):
    return store.get_paginator("items")


def enqueue_invoice(pdf):
    queue = sdk.client("queue")
    queue.send_message(Body=pdf)


def query_native(sql):
    conn = natdb.connect("dsn")
    return conn


def checksum(data):
    return fastlib.fast_hash(data)
"""


def _install(site, name, files, requires=(), binaries=(), stubs=False):
    pkg = site / (f"{name}-stubs" if stubs else name)
    pkg.mkdir(parents=True)
    for rel, text in files.items():
        (pkg / rel).write_text(textwrap.dedent(text))
    for rel in binaries:
        (pkg / rel).write_bytes(b"\x7fELF\x02\x01\x01")
    dist = f"{name}-stubs" if stubs else name
    info = site / f"{dist.replace('-', '_')}-1.0.dist-info"
    info.mkdir()
    meta = f"Metadata-Version: 2.1\nName: {dist}\nVersion: 1.0\n" + "".join(f"Requires-Dist: {r}\n" for r in requires)
    (info / "METADATA").write_text(meta)
    (info / "top_level.txt").write_text(pkg.name + "\n")
    record = [f"{pkg.name}/{rel},sha256=x,1" for rel in [*files, *binaries]]
    (info / "RECORD").write_text("\n".join(record) + "\n")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    site = tmp_path_factory.mktemp("site")
    _install(site, "sdkcore", SDKCORE)
    _install(site, "sdk", SDK, requires=("sdkcore",))
    _install(site, "sdk", SDK_STUBS, stubs=True)
    _install(site, "sdk_store", SDK_STORE, requires=("sdkcore",))
    _install(site, "sdk_queue", SDK_QUEUE, requires=("sdkcore",))
    _install(site, "natdb", NATDB, binaries=(f"_ext{SO}",))
    _install(site, "fastlib", FASTLIB, binaries=(f"_fast{SO}",))
    return site


@pytest.fixture(scope="module")
def index(site, tmp_path_factory):
    return DependencyIndex(SummaryStore(str(tmp_path_factory.mktemp("store") / "s.db")), paths=[str(site)])


@pytest.fixture(scope="module")
def host(index, tmp_path_factory):
    repo = tmp_path_factory.mktemp("host")
    (repo / "app").mkdir()
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "main.py").write_text(HOST)
    builder, _ = build_pipeline(str(repo), dependencies=index)
    return builder, compute_capability_profiles(builder)


def _boundary(builder, fn):
    return {t: builder.graph.nodes[t] for _, t in builder.graph.out_edges(f"app.main.{fn}") if builder.graph.nodes[t].get("boundary")}


# --------------------------------------------------------------------- #
# Summaries
# --------------------------------------------------------------------- #
def test_return_inference_reaches_the_transport_in_summaries(index):
    core = index.ensure("sdkcore")
    # `manager = self._manager` (known) / `self._proxies[proxy]` (unknown)
    assert core.symbols["sdkcore.session.Session.send"].mask & NET
    assert core.symbols["sdkcore.client.BaseClient._make_api_call"].mask & NET


def test_summary_return_inference_types_the_pool_exactly(site, monkeypatch):
    # `_get_manager` returns `self._manager` (a Pool) or an unknown dict
    # value: in summary mode the known class stands, so `send` links to
    # `Pool.connection` -> `Connection.urlopen`, not a same-named decoy.
    import prism.deps.summarizer as S
    from prism.deps.discover import find_distribution

    captured = {}
    original = S._summarize_built
    monkeypatch.setattr(S, "_summarize_built", lambda b, *a, **k: (captured.setdefault("b", b), original(b, *a, **k))[1])
    S.summarize_package(find_distribution("sdkcore", paths=[str(site)]))
    builder = captured["b"]
    assert builder._infer_return_class("sdkcore.session.Session._get_manager") == "sdkcore.pool.Pool"
    targets = {t for _, t in builder.graph.out_edges("sdkcore.session.Session.send")}
    assert {"sdkcore.pool.Pool.connection", "sdkcore.pool.Connection.urlopen"} <= targets
    assert not targets & {"sdkcore.pool.LocalCache.connection", "sdkcore.pool.LocalCache.urlopen"}


def test_method_factory_dispatcher_is_detected(index):
    assert index.ensure("sdkcore").dispatchers == {"sdkcore.client.BaseClient": "sdkcore.client.BaseClient._make_api_call"}


def test_class_bases_are_resolved_through_imports(index):
    assert index.ensure("sdk_store").bases["sdk_store.client.StoreClient"] == ("sdkcore.client.BaseClient",)


def test_pyi_is_preferred_and_stub_packages_are_overlaid(site, index):
    store_headers = package_headers(str(site / "sdk_store"))
    assert "def put_item" in store_headers["client.py"]  # from client.pyi, not the empty runtime class
    assert find_stubs("sdk", [str(site)])[0] == str(site / "sdk-stubs")
    sdk = index.ensure("sdk")
    assert sdk.stats["stubs"] == "sdk-stubs" and "+stubs:" in sdk.ref.record_hash
    assert "Literal['store']" in sdk.headers["__init__.py"]


def test_compiled_package_with_pyi_keeps_typed_headers(index):
    natdb = index.ensure("natdb")
    assert natdb.status == "opaque" and "def connect" in natdb.headers["__init__.py"]


# --------------------------------------------------------------------- #
# Host
# --------------------------------------------------------------------- #
@pytest.mark.parametrize("fn", ["archive_invoice", "archive_invoice_kw", "archive_typed"])
def test_synthesized_method_runs_the_dispatcher(host, fn):
    builder, profiles = host
    node = _boundary(builder, fn)["sdk_store.client.StoreClient.put_item"]
    assert node["boundary_dispatch"] == "sdkcore.client.BaseClient._make_api_call"
    assert node["boundary_bits"] & NET and node["boundary_tier"] in ("CONFIDENT", "TENTATIVE")
    assert profiles[f"app.main.{fn}"].by_hop[1] & NET


def test_literal_overload_picks_the_matching_client(host):
    builder, profiles = host
    node = _boundary(builder, "enqueue_invoice")["sdk_queue.client.QueueClient.send_message"]
    assert node["boundary_dispatch"] == "sdkcore.client.BaseClient._make_api_call"


def test_real_methods_of_the_dispatcher_class_are_not_dispatched(host):
    builder, profiles = host
    node = _boundary(builder, "list_pages")["sdk_store.client.StoreClient.get_paginator"]
    assert "boundary_dispatch" not in node and node["boundary_bits"] == 0
    assert profiles["app.main.list_pages"].within(2) == 0


def test_compiled_package_calls_are_opaque_through_pyi_headers(host):
    builder, profiles = host
    node = _boundary(builder, "query_native")["natdb.connect"]
    assert node["boundary_tier"] == "OPAQUE" and node["boundary_bits"] == 0
    assert profiles["app.main.query_native"].opaque == ("natdb.connect",)


def test_pyi_symbol_of_a_compiled_submodule_is_opaque(host):
    builder, profiles = host
    node = _boundary(builder, "checksum")["fastlib._fast.fast_hash"]
    assert node["boundary_tier"] == "OPAQUE" and node["boundary_bits"] == 0
    assert profiles["app.main.checksum"].opaque == ("fastlib._fast.fast_hash",)


def test_default_build_is_untouched(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "main.py").write_text(HOST)
    builder, _ = build_pipeline(str(tmp_path), use_cache=False)
    assert not any(a.get("boundary_dispatch") for _, a in builder.graph.nodes(data=True))
