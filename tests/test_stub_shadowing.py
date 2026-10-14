"""Summary-first precedence over Layer 3 boundary stubs, entry by entry.

Synthetic packages named after real stub-table packages, so the real Layer
3 entries apply (installed into a private site; never the real libraries):

- `redis`    - `Redis.get` is inherited from a barrel-imported mixin and
  reaches a socket: the summary is actionable, `redis.Redis.get` is
  shadowed. `Redis()` itself does no I/O (lazy constructor), so the
  `redis.Redis` stub stays as the fallback; `redis.Redis.set` names nothing
  the package defines - unresolved, stays.
- `httpx`    - `httpx.get` reaches a socket (shadowed); `httpx.Client()` is
  lazy (fallback).
- `django`   - `django.db.connection` is a module-level runtime proxy
  (`ConnectionProxy` forwards `__getattr__`): calls through it are PROXY
  boundaries - capability unknown - so the curated
  `django.db.connection.cursor` stub stands (known beats unknown).
- `psycopg2` - `connect` is re-exported straight from the compiled
  `_psycopg` module: an OPAQUE boundary, and the `psycopg2.connect` stub
  stands next to it.
"""
from __future__ import annotations

import textwrap

import pytest

from prism.cli import build_pipeline
from prism.deps import DependencyIndex, SummaryStore
from prism.deps.shadow import shadows
from prism.deps.summarizer import CONFIDENT, NATIVE, TENTATIVE, BitEvidence, SymbolSummary
from prism.semantics.bitmask import AMBIENT_PRIMITIVE_MASK, OPERATIONAL_IO_MASK, PRIMITIVE_BITS, FeatureBit
from prism.semantics.capabilities import compute_capability_profiles
from prism.semantics.extractor import compute_feature_masks, compute_feature_masks_cached
from prism.semantics.substance import compute_substance_bits
from prism.semantics.triad import action_tokens, database_io

NET = int(FeatureBit.PRIM_NET_CONNECT)
DB = int(FeatureBit.PRIM_DB_CONNECT | FeatureBit.PRIM_DB_READ | FeatureBit.PRIM_DB_WRITE)
SO = ".cpython-311-x86_64-linux-gnu.so"

PACKAGES = {
    "redis": {
        "__init__.py": "from redis.client import Redis\n",
        "client.py": """
            import socket
            import time

            from redis.commands import KeyCommands


            class Redis(KeyCommands):
                def __init__(self, host="localhost"):
                    self.host = host
                    self.created = time.monotonic()

                def execute_command(self, *args):
                    sock = socket.create_connection((self.host, 6379))
                    sock.sendall(repr(args).encode())
        """,
        "commands/__init__.py": "from .core import KeyCommands\n",
        "commands/core.py": """
            class KeyCommands:
                def get(self, name):
                    return self.execute_command("GET", name)
        """,
    },
    "httpx": {
        "__init__.py": "from ._api import get\nfrom ._client import Client\n",
        "_api.py": """
            import socket


            def get(url):
                return socket.create_connection((url, 443))
        """,
        "_client.py": """
            class Client:
                def __init__(self, timeout=5):
                    self.timeout = timeout
        """,
    },
    "django": {
        "__init__.py": "",
        "db/__init__.py": """
            class ConnectionProxy:
                def __init__(self, connections, alias):
                    self._connections = connections
                    self._alias = alias

                def __getattr__(self, item):
                    return getattr(self._connections[self._alias], item)


            connection = ConnectionProxy({}, "default")
        """,
    },
    "psycopg2": {"__init__.py": "from psycopg2._psycopg import connect\n"},
    "asyncpg": {
        "__init__.py": "from .connection import connect\n",
        "connection.py": """
            import random


            class Connection:
                def __init__(self, dsn):
                    self.dsn = dsn
                    self.backoff = random.random()


            def connect(dsn):
                return Connection(dsn)
        """,
    },
    "boto3": {
        "__init__.py": """
            import os


            def client(service):
                with open(os.path.expanduser("~/.aws/config")) as fh:
                    return fh.read()
        """,
    },
}

HOST = """
import asyncpg
import boto3
import django.db
import httpx
import psycopg2
import redis


def open_cache():
    return redis.Redis()


def cache_lookup(key):
    r = redis.Redis()
    return r.get(key)


def fetch(url):
    return httpx.get(url)


def make_client():
    return httpx.Client()


def raw_query(sql):
    cur = django.db.connection.cursor()
    cur.execute(sql)


def pg_connect():
    return psycopg2.connect("dbname=app")


def pool_connect(dsn):
    return asyncpg.connect(dsn)


def make_s3():
    return boto3.client("s3")
"""


def _install(site, name, files, binaries=()):
    pkg = site / name
    pkg.mkdir(parents=True)
    record = []
    for rel, text in files.items():
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


def _repo(tmp):
    repo = tmp / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "main.py").write_text(HOST)
    return repo


@pytest.fixture(scope="module")
def host(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("shadow")
    site = tmp / "site"
    for name, files in PACKAGES.items():
        _install(site, name, files, binaries=(f"_psycopg{SO}",) if name == "psycopg2" else ())
    repo = _repo(tmp)
    index = DependencyIndex(SummaryStore(str(tmp / "store.db")), paths=[str(site)])
    builder, _ = build_pipeline(str(repo), dependencies=index)
    return builder, compute_capability_profiles(builder), str(repo)


@pytest.fixture(scope="module")
def default_build(tmp_path_factory):
    builder, _ = build_pipeline(str(_repo(tmp_path_factory.mktemp("plain"))), use_cache=False)
    return builder, compute_capability_profiles(builder)


def _p(profiles, fn):
    return profiles[f"app.main.{fn}"]


# --------------------------------------------------------------------- #
# Which entries are shadowed
# --------------------------------------------------------------------- #
def test_actionable_summaries_shadow_their_stub_entries(host):
    shadow = host[0].stub_shadow
    assert shadow["redis.Redis.get"] == "redis.commands.core.KeyCommands.get"  # via the mixin, through the barrel
    assert shadow["httpx.get"] == "httpx._api.get"


def test_unshadowed_entries_stay_as_fallback(host):
    shadow = host[0].stub_shadow
    assert "httpx.Client" not in shadow  # lazy constructor: empty summary
    assert "redis.Redis.set" not in shadow  # not defined by the package: unresolved
    assert "psycopg2.connect" not in shadow  # OPAQUE: unknown never shadows a known stub
    assert "django.db.connection.cursor" not in shadow  # PROXY: likewise
    assert not any(e.split(".")[0] not in PACKAGES for e in shadow)  # only installed, summarized packages


def test_default_build_shadows_nothing(default_build):
    assert default_build[0].stub_shadow == {}
    assert _p(default_build[1], "fetch").own & NET  # the stub, as before


# --------------------------------------------------------------------- #
# Capability profiles
# --------------------------------------------------------------------- #
def test_shadowed_call_reads_the_summary_not_the_stub(host):
    fetch = _p(host[1], "fetch")
    assert fetch.own == 0 and fetch.own_l3 == 0  # httpx.get stub ignored
    assert fetch.by_hop[1] & NET  # the summary, at hop 1


def test_lazy_constructor_keeps_its_stub(host):
    assert _p(host[1], "make_client").own_l3 & NET  # httpx.Client stub
    assert _p(host[1], "open_cache").own_l3 & DB  # redis.Redis stub


def test_runtime_proxy_keeps_its_stub_and_records_the_proxy(host):
    builder, profiles, _ = host
    node = builder.graph.nodes["django.db.connection.cursor"]
    assert node["boundary_tier"] == "PROXY" and node["boundary_bits"] == 0
    assert node["boundary_proxy_class"] == "django.db.ConnectionProxy"
    raw = _p(profiles, "raw_query")
    assert raw.own_l3 & DB  # the curated stub's known category stands
    # `cur = connection.cursor(); cur.execute(sql)`: the cursor is proxy-derived too
    assert raw.proxy == ("django.db.connection.cursor", "django.db.connection.cursor().execute") and raw.opaque == ()


def test_shadowed_method_adds_no_stub_signal_beyond_its_constructor(host):
    # `r = redis.Redis(); r.get(k)`: the lazy constructor keeps its fallback
    # stub; `r.get` adds no stub site of its own - its summary is at hop 1.
    lookup, ctor_only = _p(host[1], "cache_lookup"), _p(host[1], "open_cache")
    assert lookup.own == ctor_only.own
    assert len(lookup.sources[0]) == len(ctor_only.sources[0])  # no extra stub site for r.get
    assert lookup.by_hop[1] & NET


def test_known_stub_beats_an_opaque_native_export(host):
    builder, profiles, _ = host
    assert builder.graph.nodes["psycopg2.connect"]["boundary_tier"] == "OPAQUE"  # still recorded
    pg = _p(profiles, "pg_connect")
    assert pg.own_l3 & DB and pg.opaque == ("psycopg2.connect",)  # the stub's DB_CONNECT, plus the opaque entry
    tokens = action_tokens(builder, "app.main.pg_connect")
    assert database_io(pg, tokens)[1] == "capability"  # scored on known capability, not the unknown fallback


# --------------------------------------------------------------------- #
# Substance axis
# --------------------------------------------------------------------- #
def test_substance_recognizes_summarized_entry_points_as_sinks(host):
    bits = compute_substance_bits(host[0])
    assert bits["app.main.fetch"] & FeatureBit.SINK_NETWORK_IO  # from the summary
    assert not bits["app.main.fetch"] & FeatureBit.SINK_PURE_COMPUTE
    assert bits["app.main.raw_query"] & FeatureBit.SINK_DATABASE_IO  # the stub (proxy call next to it)
    assert not bits["app.main.raw_query"] & FeatureBit.SINK_PURE_COMPUTE
    assert bits["app.main.cache_lookup"] & FeatureBit.SINK_NETWORK_IO


def test_substance_keeps_the_stub_category_next_to_an_opaque_call(host):
    bits = compute_substance_bits(host[0])
    assert bits["app.main.pg_connect"] & int(FeatureBit.SINK_PURE_COMPUTE) == 0
    assert bits["app.main.pg_connect"] & int(FeatureBit.SINK_DATABASE_IO)  # the psycopg2.connect stub


def test_cached_feature_masks_match_uncached_with_shadowing(host):
    builder, _, repo = host
    uncached = compute_feature_masks(builder)
    assert compute_feature_masks_cached(builder, repo) == uncached


# --------------------------------------------------------------------- #
# Only operational I/O shadows - ambient bits and unknowns never do
# --------------------------------------------------------------------- #
def _summary(*bits, tier=CONFIDENT):
    return SymbolSummary("function", {int(b): BitEvidence(tier, 1, ("x",)) for b in bits})


def test_operational_and_ambient_masks_partition_the_primitives():
    assert OPERATIONAL_IO_MASK & AMBIENT_PRIMITIVE_MASK == 0
    assert OPERATIONAL_IO_MASK | AMBIENT_PRIMITIVE_MASK == sum(int(b) for b in PRIMITIVE_BITS)


def test_shadowing_predicate():
    F = FeatureBit
    assert shadows(_summary(F.PRIM_NET_CONNECT)) and shadows(_summary(F.PRIM_FS_READ, tier=TENTATIVE))
    assert shadows(_summary(F.PRIM_DB_WRITE)) and shadows(_summary(F.PRIM_PROC_SPAWN, F.PRIM_TIME_CLOCK))
    assert not shadows(_summary(F.PRIM_TIME_CLOCK, F.PRIM_TIME_WAIT, F.PRIM_RAND_PRNG, F.PRIM_RAND_CSPRNG))
    assert not shadows(_summary())
    assert not shadows(_summary(NATIVE)) and not shadows(_summary(NATIVE, F.PRIM_TIME_CLOCK))  # unknown
    assert shadows(_summary(NATIVE, F.PRIM_NET_CONNECT))  # known operational bits beside native code


def test_ambient_only_summary_keeps_the_operational_stub(host):
    builder, profiles, _ = host
    for entry, fn in (("redis.Redis", "open_cache"), ("asyncpg.connect", "pool_connect")):
        assert entry not in builder.stub_shadow, entry
        p = _p(profiles, fn)
        assert p.by_hop[1] & AMBIENT_PRIMITIVE_MASK and not p.by_hop[1] & OPERATIONAL_IO_MASK  # summary: ambient only
        assert p.own_l3 & DB, fn  # the database stub still applies


def test_filesystem_summary_shadows_a_network_stub(host):
    builder, profiles, _ = host
    assert builder.stub_shadow["boto3.client"] == "boto3.client"
    s3 = _p(profiles, "make_s3")
    assert s3.own_l3 == 0 and s3.by_hop[1] & int(FeatureBit.PRIM_FS_READ) and not s3.within(2) & NET
