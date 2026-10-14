"""The OPAQUE tier: dependencies backed by compiled native code.

- The summarizer marks every function reaching a package's own compiled
  extension module as opaque (`NATIVE` evidence) and never invents
  capability bits for it; a package without any Python source is
  `status="opaque"`.
- Host builds turn calls into either case into OPAQUE boundary nodes
  (`boundary_tier="OPAQUE"`, `boundary_bits=0`).
- Triad scoring treats an opaque candidate's capability as unknown, not as
  a mismatch: the capability weight moves onto the physical-action leg,
  and the provenance says `opaque-native`.

`nativedb` mimics psycopg2 (a Python wrapper over a C core), `fastser` a
fully compiled package, `netlib` a pure-Python one with known capability.
The `.so` files are never loaded - detection reads file names only.
"""
from __future__ import annotations

import importlib.util
import os
import textwrap

import pytest

from prism.cli import build_pipeline
from prism.deps import DependencyIndex, SummaryStore
from prism.deps.discover import find_distribution
from prism.deps.host import TIER_OPAQUE
from prism.deps.summarizer import NATIVE, native_modules, summarize_package
from prism.semantics.bitmask import FeatureBit
from prism.semantics.capabilities import capability_score, compute_capability_profiles
from prism.semantics.triad import PHYSICAL_ACTIONS, action_score, action_tokens, database_io, triad_rerank

DB_WRITE = int(FeatureBit.PRIM_DB_WRITE)
NET = int(FeatureBit.PRIM_NET_CONNECT)
SO = ".cpython-311-x86_64-linux-gnu.so"

NATIVEDB = {
    "__init__.py": """
        from ._core import connect as _connect


        def connect(dsn):
            return _connect(dsn)
    """,
    "extras.py": "def quote_ident(name):\n    return '\"' + name + '\"'\n",
    "extensions.py": "from nativedb._core import connection, cursor\n",
}

NETLIB = {
    "__init__.py": "from .client import ping\n",
    "client.py": """
        import socket


        def ping(host):
            sock = socket.create_connection((host, 80))
            sock.close()
    """,
}

HOST = """
import fastser
import nativedb
import nativedb.extensions
import netlib


def save_user(user):
    conn = nativedb.connect("dbname=app")
    cur = conn.cursor()
    cur.execute("INSERT INTO users VALUES (%s)", (user,))
    conn.commit()


def load_report(report_id):
    conn = nativedb.connect("dbname=app")
    return conn.cursor().fetchall()


def dump_user(user):
    return fastser.dumps(user)


def check_host(host):
    return netlib.ping(host)


def save_preferences(prefs):
    return dict(prefs)


def format_name(user):
    return user.title()


def post_ledger(conn: nativedb.extensions.connection, entry):
    conn.cursor().execute("INSERT INTO ledger VALUES (%s)", (entry,))
    conn.commit()


def post_ledger_entry(conn: nativedb.extensions.connection, entry):
    cur = conn.cursor()
    cur.execute("INSERT INTO ledger VALUES (%s)", (entry,))
    conn.commit()


def encode_user(user):
    codec = fastser.Codec()
    return codec.encode(user)
"""


def _install(site, name, files=None, binaries=()):
    pkg = site / name
    pkg.mkdir(parents=True)
    record = []
    for rel, text in (files or {}).items():
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


@pytest.fixture
def site(tmp_path):
    site = tmp_path / "site"
    _install(site, "nativedb", NATIVEDB, binaries=(f"_core{SO}",))
    _install(site, "fastser", binaries=(f"_impl{SO}", "speedups.abi3.so"))
    _install(site, "netlib", NETLIB)
    return site


@pytest.fixture
def host_build(site, tmp_path):
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "main.py").write_text(HOST)
    index = DependencyIndex(SummaryStore(str(tmp_path / "store.db")), paths=[str(site)])
    builder, _ = build_pipeline(str(repo), dependencies=index)
    return builder


# --------------------------------------------------------------------- #
# Summaries
# --------------------------------------------------------------------- #
def test_native_modules_are_detected_by_file_name(site):
    assert native_modules(str(site / "nativedb"), "nativedb") == {"nativedb._core"}
    assert native_modules(str(site / "fastser"), "fastser") == {"fastser._impl", "fastser.speedups"}


def test_wrapper_over_native_core_is_opaque_with_no_bits(site):
    summary = summarize_package(find_distribution("nativedb", paths=[str(site)]))
    assert summary.status == "summarized" and summary.stats["native_calls"] == 1
    connect = summary.symbols["nativedb.connect"]
    assert connect.opaque and connect.mask == 0  # unknown, never invented
    assert connect.evidence[NATIVE].path == ("nativedb.connect", "nativedb._core.connect")
    assert "nativedb.extras.quote_ident" not in summary.symbols  # pure helper: neither bits nor NATIVE


def test_package_without_python_source_is_opaque(site):
    summary = summarize_package(find_distribution("fastser", paths=[str(site)]))
    assert summary.status == "opaque" and summary.stats["native_modules"] == 2 and summary.symbols == {}


def test_opaque_evidence_survives_the_store(site, tmp_path):
    summary = summarize_package(find_distribution("nativedb", paths=[str(site)]))
    store = SummaryStore(str(tmp_path / "s.db"))
    store.put(summary)
    back = store.get(summary.ref)
    assert back.symbols["nativedb.connect"].opaque and back.symbols["nativedb.connect"].mask == 0


# --------------------------------------------------------------------- #
# Host boundary nodes
# --------------------------------------------------------------------- #
def test_host_marks_native_entry_points_opaque(host_build):
    nodes = dict(host_build.graph.nodes(data=True))
    for entry in ("nativedb.connect", "fastser.dumps"):
        attrs = nodes[entry]
        assert attrs["boundary"] and attrs["boundary_opaque"], entry
        assert attrs["boundary_tier"] == TIER_OPAQUE and attrs["boundary_bits"] == 0, entry
    assert nodes["netlib.client.ping"]["boundary_tier"] == "CONFIDENT" and not nodes["netlib.client.ping"]["boundary_opaque"]
    assert nodes["netlib.client.ping"]["boundary_bits"] & NET


def test_profiles_carry_opaque_entries_but_no_invented_bits(host_build):
    profiles = compute_capability_profiles(host_build)
    save = profiles["app.main.save_user"]
    assert save.opaque == ("nativedb.connect",) and save.within(2) == 0
    assert capability_score(save, DB_WRITE) == 0.0
    assert profiles["app.main.dump_user"].opaque == ("fastser.dumps",)
    assert profiles["app.main.check_host"].opaque == () and profiles["app.main.check_host"].by_hop[1] & NET
    assert profiles["app.main.format_name"].opaque == ()


# --------------------------------------------------------------------- #
# Triad fallback
# --------------------------------------------------------------------- #
def test_action_tokens_include_calls_on_native_objects(host_build):
    tokens = action_tokens(host_build, "app.main.save_user")
    assert {"save", "user", "connect", "cursor", "execute", "commit"} <= tokens
    assert action_score(tokens, "MUTATE") == 1.0 and action_score(tokens, "WAIT") == 0.0


def test_opaque_candidate_falls_back_to_action_and_stems(host_build):
    profiles = compute_capability_profiles(host_build)
    candidates = ["app.main.save_preferences", "app.main.format_name", "app.main.save_user", "app.main.load_report"]
    tokens = {q: action_tokens(host_build, q) for q in candidates}
    stems = {"app.main.save_user": 0.8, "app.main.save_preferences": 0.8, "app.main.format_name": 0.6, "app.main.load_report": 0.6}
    ranked = triad_rerank(candidates, stems, profiles, tokens, target=DB_WRITE, action="MUTATE")
    by = {r.qname: r for r in ranked}
    # Not a dead end: the opaque writer outranks the equally named decoy.
    assert [r.qname for r in ranked][:2] == ["app.main.save_user", "app.main.save_preferences"]
    save = by["app.main.save_user"]
    assert save.capability_source == "opaque-native" and save.opaque == ("nativedb.connect",)
    assert save.capability == 0.0 and save.action == 1.0
    assert save.score == pytest.approx(0.8 * 1.40)  # (capability + action weights) moved onto the action leg
    decoy = by["app.main.save_preferences"]
    assert decoy.capability_source == "none" and decoy.score == pytest.approx(0.8 * 1.15)
    # Opaque without a matching action gets no boost, and nothing is ever dropped.
    assert by["app.main.load_report"].capability_source == "opaque-native" and by["app.main.load_report"].score == 0.6
    assert sorted(by) == sorted(candidates)


def test_known_capability_uses_the_capability_leg(host_build):
    profiles = compute_capability_profiles(host_build)
    q = "app.main.check_host"
    ranked = triad_rerank([q], {q: 1.0}, profiles, {q: action_tokens(host_build, q)}, target=NET, action="CONNECT")
    assert ranked[0].capability_source == "capability" and ranked[0].capability == 0.5  # hop 1


def test_triad_is_identity_without_legs(host_build):
    profiles = compute_capability_profiles(host_build)
    order = ["app.main.format_name", "app.main.save_user"]
    ranked = triad_rerank(order, {q: 0.5 for q in order}, profiles, {})
    assert [r.qname for r in ranked] == order and all(r.score == 0.5 for r in ranked)
    assert set(PHYSICAL_ACTIONS) >= {"READ", "MUTATE", "SEND", "RECEIVE", "EXECUTE", "WAIT", "CONNECT"}


def test_receiver_annotated_with_a_compiled_type_calls_opaque_boundaries(host_build):
    # `nativedb.extensions.connection` exists only in `_core.so`: the
    # annotation still types `conn`, and its method calls are OPAQUE.
    nodes = dict(host_build.graph.nodes(data=True))
    targets = {t for _, t in host_build.graph.out_edges("app.main.post_ledger")}
    for method in ("cursor", "commit", "cursor().execute"):  # the chained `conn.cursor().execute(...)` too
        node = f"nativedb.extensions.connection.{method}"
        assert node in targets and nodes[node]["boundary_tier"] == TIER_OPAQUE and nodes[node]["boundary_bits"] == 0
    profile = compute_capability_profiles(host_build)["app.main.post_ledger"]
    assert set(profile.opaque) >= {"nativedb.extensions.connection.cursor", "nativedb.extensions.connection.commit"}
    assert profile.within(2) == 0  # unknown, never invented
    assert database_io(profile, action_tokens(host_build, "app.main.post_ledger")) == (0.0, "opaque-native")


def test_values_returned_by_opaque_calls_are_opaque(host_build):
    # `cur = conn.cursor()`: the cursor comes out of native code too, so
    # `cur.execute(...)` is an OPAQUE boundary, not an ambiguous fallback.
    nodes = dict(host_build.graph.nodes(data=True))
    targets = {t for _, t in host_build.graph.out_edges("app.main.post_ledger_entry")}
    execute = "nativedb.extensions.connection.cursor().execute"
    assert execute in targets and nodes[execute]["boundary_tier"] == TIER_OPAQUE
    assert not any(t.startswith("<ambiguous:") for t in targets)
    assert execute in compute_capability_profiles(host_build)["app.main.post_ledger_entry"].opaque
    # a fully native package's value: `codec = fastser.Codec(); codec.encode(...)`
    encode = "fastser.Codec().encode"
    assert encode in {t for _, t in host_build.graph.out_edges("app.main.encode_user")}
    assert nodes[encode]["boundary_tier"] == TIER_OPAQUE


def test_compiled_types_are_only_bound_in_dependency_builds(tmp_path):
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "main.py").write_text(HOST)
    builder, _ = build_pipeline(str(repo), use_cache=False)
    assert builder.opaque_types == frozenset()
    assert "nativedb.extensions.connection.cursor" not in builder.graph
    assert not any(n.endswith(")") or "()." in n for n in builder.graph)  # no opaque values without summaries


def test_default_build_has_no_opaque_marks(tmp_path):
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "main.py").write_text(HOST)
    builder, _ = build_pipeline(str(repo), use_cache=False)
    assert not any(a.get("boundary_opaque") for _, a in builder.graph.nodes(data=True))


# --------------------------------------------------------------------- #
# A real installed package compiled from its own shipped source
# --------------------------------------------------------------------- #
def _charset_normalizer_dir() -> str | None:
    spec = importlib.util.find_spec("charset_normalizer")
    if spec is None or not spec.submodule_search_locations:
        return None
    path = list(spec.submodule_search_locations)[0]
    return path if any(f.endswith(".so") for f in os.listdir(path)) else None


@pytest.mark.skipif(_charset_normalizer_dir() is None, reason="no mypyc-compiled charset_normalizer installed")
def test_mypyc_module_with_shipped_source_is_not_opaque(tmp_path):
    """charset_normalizer ships `md.py` next to `md.cpython-*.so`: the
    compiled module is built from that source, which is summarized as
    usual - nothing is marked opaque."""
    assert native_modules(_charset_normalizer_dir(), "charset_normalizer") == set()
    summary = DependencyIndex(SummaryStore(str(tmp_path / "s.db"))).ensure("charset_normalizer")
    assert summary.status == "summarized" and summary.stats["native_modules"] == 0
    assert not any(s.opaque for s in summary.symbols.values())
