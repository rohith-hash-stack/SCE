"""Offline export summarization (`prism.deps`): discovery and cache keys,
signature-only headers, isolated per-package summaries (Protocol dispatch,
Layer 2 callback contracts, cross-package headers), the SQLite store, and
host builds resolving dependency entry points as boundary nodes at hop 1.

The synthetic `lowlib`/`highlib` pair mirrors urllib3/requests in miniature
and is installed into a temporary site directory with real dist-info
metadata; the last section runs against the installed requests/urllib3
when they are present.
"""
from __future__ import annotations

import hashlib
import importlib.util
import textwrap

import pytest

from prism.cli import build_pipeline
from prism.deps import DependencyIndex, SummaryStore
from prism.deps.discover import find_distribution
from prism.deps.headers import header_source
from prism.deps.summarizer import CONFIDENT, TENTATIVE, summarize_package
from prism.semantics.bitmask import FeatureBit
from prism.semantics.capabilities import capability_score, composites_of, compute_capability_profiles

NET = int(FeatureBit.PRIM_NET_CONNECT)
FS_READ = int(FeatureBit.PRIM_FS_READ)

LOWLIB = {
    "__init__.py": "from .pool import Pool, parse_url\n",
    "_base.py": """
        from typing import Protocol


        class BaseConn(Protocol):
            def request(self, method, url): ...

            def close(self): ...
    """,
    "conn.py": """
        import http.client
        import socket


        class Conn(http.client.HTTPConnection):
            def connect(self):
                self.sock = socket.create_connection((self.host, self.port))

            def request(self, method, url):
                self.putrequest(method, url)
                self.endheaders()

            def close(self):
                return None
    """,
    "pool.py": """
        from ._base import BaseConn
        from .conn import Conn


        class Pool:
            def _new(self) -> BaseConn:
                return Conn("example.invalid")

            def urlopen(self, method, url):
                conn = self._new()
                return conn.request(method, url)


        def parse_url(url):
            return url.split("/")
    """,
}

HIGHLIB = {
    "__init__.py": "from .api import post\nfrom .sessions import Session\n",
    "api.py": """
        from . import sessions


        def post(url, data=None):
            session = sessions.Session()
            return session.request("POST", url)
    """,
    "sessions.py": """
        from lowlib import Pool


        class Session:
            def __init__(self):
                self.pool = Pool()

            def request(self, method, url):
                return self.pool.urlopen(method, url)

            def get(self, url):
                return self.request("GET", url)
    """,
    "helpers.py": "def slug(text):\n    return text.lower()\n",
}

HOST = """
import highlib


def post_event(payload):
    return highlib.post("https://example.invalid/events", payload)


def fetch(path):
    session = highlib.Session()
    return session.get(path)


def upload(path):
    with open(path, "rb") as fh:
        data = fh.read()
    return highlib.post("https://example.invalid/upload", data)


def pure(a, b):
    return a + b
"""


def _install(site, name, files, requires=()):
    pkg = site / name
    record = []
    for rel, text in files.items():
        path = pkg / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
        record.append(f"{name}/{rel},sha256=x,1")
    info = site / f"{name}-1.0.dist-info"
    info.mkdir()
    meta = f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n" + "".join(f"Requires-Dist: {r}\n" for r in requires)
    (info / "METADATA").write_text(meta)
    (info / "top_level.txt").write_text(name + "\n")
    (info / "RECORD").write_text("\n".join(record) + "\n")


@pytest.fixture
def site(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    _install(site, "lowlib", LOWLIB)
    _install(site, "highlib", HIGHLIB, requires=("lowlib>=1.0",))
    return site


@pytest.fixture
def index(site, tmp_path):
    return DependencyIndex(SummaryStore(str(tmp_path / "store" / "summaries.db")), paths=[str(site)])


# --------------------------------------------------------------------- #
# Discovery and headers
# --------------------------------------------------------------------- #
def test_discovery_reads_the_cache_key_from_dist_info(site):
    low = find_distribution("lowlib", paths=[str(site)])
    high = find_distribution("highlib", paths=[str(site)])
    record = (site / "lowlib-1.0.dist-info" / "RECORD").read_text()
    assert (low.dist, low.version, low.record_hash) == ("lowlib", "1.0", hashlib.sha256(record.encode()).hexdigest())
    assert high.requires == ("lowlib",) and low.requires == ()
    assert find_distribution("os", paths=[str(site)]) is None


def test_headers_keep_signatures_and_drop_bodies():
    src = textwrap.dedent(
        """
        import typing
        from .api import post
        if typing.TYPE_CHECKING:
            class Proto(typing.Protocol):
                def m(self) -> int: ...


        class Session:
            timeout: float = 3.0

            def get(self, url: str) -> "Response":
                return self.request("GET", url)


        def helper(x):
            secret = x * 2
            return secret
        """
    )
    header = header_source(src)
    assert "from .api import post" in header and "class Proto(typing.Protocol)" in header
    assert "def get(self, url: str) -> 'Response':\n        ..." in header
    assert "timeout: float" in header and "3.0" not in header
    assert "secret" not in header and "self.request" not in header


# --------------------------------------------------------------------- #
# Isolated summaries
# --------------------------------------------------------------------- #
def test_lowlib_protocol_dispatch_and_l2_callbacks(site):
    low = summarize_package(find_distribution("lowlib", paths=[str(site)]))
    assert low.status == "summarized" and low.stats["protocol_edges"] > 0 and low.stats["callback_edges"] > 0
    connect = low.symbols["lowlib.conn.Conn.connect"].evidence[NET]
    assert connect.tier == CONFIDENT and connect.path[-1] == "lowlib.conn.Conn.connect"  # socket.create_connection
    request = low.symbols["lowlib.conn.Conn.request"].evidence[NET]
    assert request.tier == CONFIDENT  # inherited endheaders -> send (Layer 2) -> connect
    assert "lowlib.conn.Conn.endheaders" in request.path
    urlopen = low.symbols["lowlib.pool.Pool.urlopen"].evidence[NET]
    assert urlopen.tier == TENTATIVE  # through the BaseConn Protocol
    assert "lowlib._base.BaseConn.request" in urlopen.path and "lowlib.conn.Conn.request" in urlopen.path
    assert "lowlib.pool.parse_url" not in low.symbols


def test_highlib_summary_crosses_into_lowlib_headers_only(site):
    low = summarize_package(find_distribution("lowlib", paths=[str(site)]))
    high = summarize_package(find_distribution("highlib", paths=[str(site)]), [low])
    post = high.symbols["highlib.api.post"].evidence[NET]
    assert "lowlib.pool.Pool.urlopen" in post.path
    assert post.path[-1].startswith("lowlib.conn.Conn.")  # witness continues inside lowlib's own summary
    assert "highlib.helpers.slug" not in high.symbols
    assert all(q.startswith("highlib.") for q in high.symbols)  # no lowlib internals in highlib's summary


def test_opaque_package_without_python_source(tmp_path):
    site = tmp_path / "site"
    (site / "native").mkdir(parents=True)
    (site / "native" / "_core.so").write_bytes(b"\x7fELF")
    info = site / "native-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: native\nVersion: 1.0\n")
    (info / "top_level.txt").write_text("native\n")
    (info / "RECORD").write_text("native/_core.so,,\n")
    summary = summarize_package(find_distribution("native", paths=[str(site)]))
    assert summary.status == "opaque" and summary.symbols == {}


# --------------------------------------------------------------------- #
# Store and cache keys
# --------------------------------------------------------------------- #
def test_store_round_trip(site, tmp_path):
    low = summarize_package(find_distribution("lowlib", paths=[str(site)]))
    store = SummaryStore(str(tmp_path / "s.db"))
    store.put(low)
    back = store.get(low.ref)
    assert back.status == low.status and back.headers == low.headers
    assert {q: (s.mask, s.confident_mask) for q, s in back.symbols.items()} == {
        q: (s.mask, s.confident_mask) for q, s in low.symbols.items()
    }
    assert back.symbols["lowlib.pool.Pool.urlopen"].evidence == low.symbols["lowlib.pool.Pool.urlopen"].evidence


def test_index_builds_closure_once_and_rekeys_on_record_change(site, index, tmp_path):
    assert index.ensure("highlib").symbols["highlib.api.post"].mask & NET
    assert index.built == ["lowlib", "highlib"]  # declared dependency summarized first
    again = DependencyIndex(index.store, paths=[str(site)])
    again.ensure("highlib")
    assert again.built == []  # served from the cache
    record = site / "lowlib-1.0.dist-info" / "RECORD"
    record.write_text(record.read_text() + "lowlib/extra.py,sha256=y,1\n")
    third = DependencyIndex(index.store, paths=[str(site)])
    third.ensure("lowlib")
    assert third.built == ["lowlib"]  # new RECORD hash, new key


# --------------------------------------------------------------------- #
# Host boundary resolution
# --------------------------------------------------------------------- #
@pytest.fixture
def host(tmp_path):
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "main.py").write_text(HOST)
    return repo


def test_host_resolves_entry_points_as_boundary_nodes(host, index):
    builder, _ = build_pipeline(str(host), dependencies=index)
    assert all(s.qualified_name.startswith("app.") for s in builder.symbol_table)
    boundary = {n: a for n, a in builder.graph.nodes(data=True) if a.get("boundary")}
    assert {"highlib.api.post", "highlib.sessions.Session.get"} <= set(boundary)
    assert boundary["highlib.api.post"]["boundary_bits"] & NET
    assert boundary["highlib.api.post"]["boundary_package"] == "highlib"
    assert not any(n.startswith("lowlib.") for n in builder.graph.nodes)  # no transitive internals
    assert builder.graph.has_edge("app.main.post_event", "highlib.api.post")


def test_host_capability_at_hop_one_with_tentative_and_distinct_sources(host, index):
    builder, _ = build_pipeline(str(host), dependencies=index)
    profiles = compute_capability_profiles(builder)
    for fn in ("post_event", "fetch", "upload"):
        p = profiles[f"app.main.{fn}"]
        assert p.by_hop[1] & NET and p.tentative & NET  # summary bits, TENTATIVE (Protocol dispatch)
        assert capability_score(p, NET) == 0.5  # accepted as a soft signal at hop 1
    assert not profiles["app.main.pure"].within(2)
    assert composites_of(profiles["app.main.upload"]) == ["ASSET_EGRESS"]  # own read + summarized send
    assert composites_of(profiles["app.main.post_event"]) == []


def test_default_build_has_no_boundary_nodes(host):
    builder, _ = build_pipeline(str(host), use_cache=False)
    assert not any(a.get("boundary") for _, a in builder.graph.nodes(data=True))


# --------------------------------------------------------------------- #
# Installed requests + urllib3
# --------------------------------------------------------------------- #
needs_requests = pytest.mark.skipif(
    importlib.util.find_spec("requests") is None or importlib.util.find_spec("urllib3") is None,
    reason="requests/urllib3 not installed",
)

REAL_HOST = """
import requests
from requests.adapters import HTTPAdapter

API = "https://api.example.invalid"


def post_event(payload):
    return requests.post(f"{API}/events", json=payload, timeout=5)


def get_status():
    return requests.get(f"{API}/status", timeout=5).json()


def generic_request(method, path):
    return requests.request(method, f"{API}/{path}")


def session_fetch(path):
    session = requests.Session()
    return session.get(f"{API}/{path}")


def typed_session_fetch(session: requests.Session, path):
    return session.get(f"{API}/{path}")


def mounted_session_upload(path, data):
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=3))
    return s.put(f"{API}/{path}", data=data)


class EventClient:
    def __init__(self, session: requests.Session):
        self.session = session

    def send(self, payload):
        return self.session.post(f"{API}/events", json=payload)


def upload_file(path):
    with open(path, "rb") as fh:
        data = fh.read()
    return requests.post(f"{API}/upload", data=data)


def encode(payload):
    return str(payload)


def total(values):
    return sum(values)
"""


@needs_requests
def test_installed_requests_summary(tmp_path):
    index = DependencyIndex(SummaryStore(str(tmp_path / "s.db")))
    summary = index.ensure("requests")
    assert summary.status == "summarized" and "urllib3" in index.built
    for entry in ("requests.api.post", "requests.api.get", "requests.sessions.Session.get"):
        assert summary.mask(entry) & NET, entry
    path = summary.symbols["requests.api.post"].evidence[NET].path
    assert "urllib3.connectionpool.HTTPConnectionPool.urlopen" in path
    utils = [q for q in summary.symbols if q.startswith("requests.utils.") and not q.rsplit(".", 1)[1].startswith("_")]
    assert not any(summary.mask(q) & NET for q in utils)


@needs_requests
def test_installed_requests_host_detects_every_network_function(tmp_path):
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "client.py").write_text(REAL_HOST)
    builder, _ = build_pipeline(str(repo), dependencies=DependencyIndex(SummaryStore(str(tmp_path / "s.db"))))
    profiles = compute_capability_profiles(builder)
    network = ["post_event", "get_status", "generic_request", "session_fetch", "typed_session_fetch",
               "mounted_session_upload", "EventClient.send", "upload_file"]
    for fn in network:
        assert profiles[f"app.client.{fn}"].by_hop[1] & NET, fn
    for fn in ("encode", "total", "EventClient.__init__"):
        assert not profiles[f"app.client.{fn}"].within(2), fn
    # The summary replaces the requests stub: no double-counted composite.
    assert composites_of(profiles["app.client.post_event"]) == []
    assert composites_of(profiles["app.client.upload_file"]) == ["ASSET_EGRESS"]
