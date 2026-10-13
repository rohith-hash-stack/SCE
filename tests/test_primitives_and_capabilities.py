"""Phase 2 (Operation Groundwire): Layer 2 primitives, the temporary Layer 3
boundary stubs, atomic primitive bits, composite profiles, bounded 1-2 hop
capability propagation and soft re-ranking."""
from __future__ import annotations

import textwrap

import pytest

from prism.cli import build_pipeline
from prism.semantics.bitmask import ALL_KNOWN_BITS, AXIS_BITS, PRIMITIVE_BITS, FeatureBit
from prism.semantics.boundary_stubs_l3 import L3_BOUNDARY_STUBS
from prism.semantics.canonical_sinks import CANONICAL_SINKS
from prism.semantics.capabilities import (
    HOP_DECAY,
    CapabilityProfile,
    capability_score,
    composites_of,
    compute_capability_profiles,
    soft_rerank,
)
from prism.semantics.primitives import (
    CATEGORY_PRIMITIVES,
    COMPOSITE_PROFILES,
    derive_primitive_bits,
    primitive_registry,
)
from prism.semantics.primitives_l2 import L2_PRIMITIVE_ENTRIES
from prism.semantics.substance import compute_substance_bits

F = FeatureBit


def _entries(table):
    return {(cat, lang, e) for cat, langs in table.items() for lang, es in langs.items() for e in es}


# --------------------------------------------------------------------- #
# Bit layout
# --------------------------------------------------------------------- #
def test_sixteen_primitive_bits_in_the_reserved_range():
    assert len(PRIMITIVE_BITS) == 16
    positions = sorted(int(b).bit_length() - 1 for b in PRIMITIVE_BITS)
    assert positions == list(range(45, 61))
    assert PRIMITIVE_BITS <= ALL_KNOWN_BITS
    assert not PRIMITIVE_BITS & AXIS_BITS


def test_category_primitives_partition_the_primitive_bits():
    seen = 0
    for bits in CATEGORY_PRIMITIVES.values():
        assert not seen & bits
        seen |= bits
    assert seen == sum(int(b) for b in PRIMITIVE_BITS)


# --------------------------------------------------------------------- #
# Catalog split
# --------------------------------------------------------------------- #
def test_layer_tables_partition_the_retired_catalog():
    l2, l3 = _entries(L2_PRIMITIVE_ENTRIES), _entries(L3_BOUNDARY_STUBS)
    assert not l2 & l3
    assert l2 | l3 == _entries(CANONICAL_SINKS)
    assert len(l2 | l3) == 639


@pytest.mark.parametrize(
    "lang,entry,layer",
    [
        ("python", "socket.socket.send", "L2"),
        ("python", "subprocess.Popen", "L2"),
        ("python", "open", "L2"),
        ("python", "requests.post", "L3"),
        ("python", "psycopg2.connect", "L3"),
        ("javascript", "fs.readFileSync", "L2"),
        ("javascript", "fetch", "L2"),
        ("javascript", "axios.get", "L3"),
        ("go", "net/http.Get", "L2"),
        ("go", "database/sql.Open", "L2"),
        ("go", "github.com/jackc/pgx.Connect", "L3"),
    ],
)
def test_entries_land_in_the_expected_layer(lang, entry, layer):
    assert primitive_registry()[lang][entry][1] == layer


# --------------------------------------------------------------------- #
# Primitive derivation
# --------------------------------------------------------------------- #
def test_every_entry_maps_to_a_nonempty_subset_of_its_category():
    for cat, lang, entry in _entries(CANONICAL_SINKS):
        bits = derive_primitive_bits(cat, entry)
        assert bits and not bits & ~CATEGORY_PRIMITIVES[cat], (cat, lang, entry)


@pytest.mark.parametrize(
    "cat,entry,expected",
    [
        ("network_io", "requests.post", F.PRIM_NET_CONNECT),
        ("network_io", "socket.socket.accept", F.PRIM_NET_LISTEN),
        ("network_io", "dns.resolve4", F.PRIM_NET_DNS),
        ("network_io", "https.request", F.PRIM_NET_CONNECT | F.PRIM_NET_TLS),
        ("network_io", "grpc.insecure_channel", F.PRIM_NET_CONNECT),
        ("network_io", "crypto/tls.Listen", F.PRIM_NET_LISTEN | F.PRIM_NET_TLS),
        ("filesystem_io", "fs.readdirSync", F.PRIM_FS_READ),
        ("filesystem_io", "os.ReadDir", F.PRIM_FS_READ),
        ("filesystem_io", "fs.writeFileSync", F.PRIM_FS_WRITE),
        ("filesystem_io", "shutil.rmtree", F.PRIM_FS_DELETE),
        ("filesystem_io", "open", F.PRIM_FS_READ | F.PRIM_FS_WRITE),
        ("filesystem_io", "os.File.ReadFrom", F.PRIM_FS_WRITE),
        ("database_io", "psycopg2.connect", F.PRIM_DB_CONNECT),
        ("database_io", "knex", F.PRIM_DB_CONNECT),
        ("database_io", "pymongo.collection.Collection.find_one", F.PRIM_DB_READ),
        ("database_io", "pymongo.collection.Collection.insert_one", F.PRIM_DB_WRITE),
        ("database_io", "sqlite3.Cursor.execute", F.PRIM_DB_READ | F.PRIM_DB_WRITE),
        ("process_io", "subprocess.run", F.PRIM_PROC_SPAWN),
        ("process_io", "os.Process.Kill", F.PRIM_PROC_SIGNAL),
        ("time_io", "time.sleep", F.PRIM_TIME_WAIT),
        ("time_io", "time.monotonic", F.PRIM_TIME_CLOCK),
        ("randomness", "secrets.token_hex", F.PRIM_RAND_CSPRNG),
        ("randomness", "uuid.uuid4", F.PRIM_RAND_CSPRNG),
        ("randomness", "uuid.uuid3", F.PRIM_RAND_PRNG),
        ("randomness", "random.choice", F.PRIM_RAND_PRNG),
    ],
)
def test_derivation_spot_checks(cat, entry, expected):
    assert derive_primitive_bits(cat, entry) == int(expected)


# --------------------------------------------------------------------- #
# Composite profiles
# --------------------------------------------------------------------- #
def test_composites_are_bitwise_ors_of_primitives():
    assert COMPOSITE_PROFILES["ASSET_EGRESS"].mask == int(F.PRIM_FS_READ | F.PRIM_NET_CONNECT)
    assert COMPOSITE_PROFILES["OFFLINE_BATCH"].mask == int(F.PRIM_FS_WRITE | F.PRIM_PROC_SPAWN)
    for composite in COMPOSITE_PROFILES.values():
        assert composite.mask and not composite.mask & ~sum(int(b) for b in PRIMITIVE_BITS)


def test_composite_coverage_is_a_fraction_of_groups():
    egress = COMPOSITE_PROFILES["ASSET_EGRESS"]
    assert egress.coverage(int(F.PRIM_FS_READ)) == 0.5
    assert egress.coverage(int(F.PRIM_FS_READ | F.PRIM_NET_CONNECT)) == 1.0
    ingest = COMPOSITE_PROFILES["STREAMING_INGEST"]
    assert ingest.coverage(int(F.PRIM_NET_LISTEN | F.PRIM_DB_WRITE)) == 1.0  # any socket, any persistence


# --------------------------------------------------------------------- #
# Profiles on real code
# --------------------------------------------------------------------- #
APP = """
import requests
import subprocess
import time

URL = "https://example.invalid/upload"


def upload(path):
    with open(path, "rb") as fh:
        return requests.post(URL, data=fh.read())


def handler(path):
    result = upload(path)
    return result


def outer(path):
    return handler(path)


def outermost(path):
    return outer(path)


def heartbeat():
    while True:
        requests.get(URL)
        time.sleep(30)


def rebuild(target):
    subprocess.run(["make", target])
    with open("build.log", "w") as fh:
        fh.write("ok")


def pure(a, b):
    return a + b
"""


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    repo = tmp_path_factory.mktemp("cap") / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(textwrap.dedent(APP))
    builder, _ = build_pipeline(str(repo))
    return builder, compute_capability_profiles(builder)


def test_own_profile_reads_layer2_and_layer3(built):
    _, profiles = built
    upload = profiles["app.upload"]
    assert upload.own & int(F.PRIM_FS_READ) and upload.own & int(F.PRIM_NET_CONNECT)
    # `requests.post` is only known through the Layer 3 stub; `open` is Layer 2.
    assert upload.own_l3 == int(F.PRIM_NET_CONNECT)
    assert composites_of(upload) == ["ASSET_EGRESS"]
    assert capability_score(upload, "ASSET_EGRESS") == 1.0


def test_propagation_decays_and_stops_after_two_hops(built):
    _, profiles = built
    assert capability_score(profiles["app.handler"], "ASSET_EGRESS") == HOP_DECAY[1]
    assert capability_score(profiles["app.outer"], "ASSET_EGRESS") == HOP_DECAY[2]
    assert capability_score(profiles["app.outermost"], "ASSET_EGRESS") == 0.0
    assert profiles["app.handler"].own == 0


def test_other_composites(built):
    _, profiles = built
    assert "PERIODIC_HEARTBEAT" in composites_of(profiles["app.heartbeat"])
    assert "OFFLINE_BATCH" in composites_of(profiles["app.rebuild"])
    assert composites_of(profiles["app.pure"]) == []
    assert capability_score(profiles["app.pure"], int(F.PRIM_NET_CONNECT)) == 0.0


def test_max_hops_bounds_propagation(built):
    builder, _ = built
    profiles = compute_capability_profiles(builder, max_hops=1)
    assert profiles["app.handler"].by_hop[1] and not profiles["app.outer"].by_hop[2]
    with pytest.raises(ValueError):
        compute_capability_profiles(builder, max_hops=3)


def test_substance_axis_unchanged_by_the_split(built):
    builder, _ = built
    bits = compute_substance_bits(builder)
    assert bits["app.upload"] & int(F.SINK_NETWORK_IO) and bits["app.upload"] & int(F.SINK_FILESYSTEM_IO)
    assert bits["app.rebuild"] & int(F.SINK_PROCESS_IO)
    assert bits["app.pure"] & int(F.SINK_PURE_COMPUTE)


@pytest.mark.parametrize(
    "call,expected",
    [
        ("open(p)", F.PRIM_FS_READ),
        ("open(p, 'rb')", F.PRIM_FS_READ),
        ("open(p, 'w')", F.PRIM_FS_WRITE),
        ("open(p, mode='a')", F.PRIM_FS_WRITE),
        ("open(p, 'r+')", F.PRIM_FS_READ | F.PRIM_FS_WRITE),
        ("open(p, m)", F.PRIM_FS_READ | F.PRIM_FS_WRITE),
        ("builtins.open(p, 'w')", F.PRIM_FS_WRITE),
    ],
)
def test_python_open_mode_narrows_direction(tmp_path, call, expected):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(f"import builtins\n\n\ndef f(p, m):\n    return {call}\n")
    builder, _ = build_pipeline(str(repo))
    assert compute_capability_profiles(builder)["app.f"].own == int(expected)


def test_javascript_profile(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.js").write_text(
        "const fs = require('fs');\n\n"
        "async function ship(path) {\n"
        "  const body = fs.readFileSync(path);\n"
        "  return fetch('https://example.invalid', { method: 'POST', body });\n"
        "}\n"
    )
    builder, _ = build_pipeline(str(repo))
    profile = compute_capability_profiles(builder)["app.ship"]
    assert composites_of(profile) == ["ASSET_EGRESS"] and profile.own_l3 == 0


# --------------------------------------------------------------------- #
# Soft re-ranking: never a filter
# --------------------------------------------------------------------- #
PROFILES = {
    "a": CapabilityProfile((0, 0, 0)),
    "b": CapabilityProfile((int(F.PRIM_FS_READ | F.PRIM_NET_CONNECT), 0, 0)),
    "c": CapabilityProfile((0, int(F.PRIM_FS_READ | F.PRIM_NET_CONNECT), 0)),
}


def test_rerank_keeps_every_candidate_and_bounds_the_boost():
    base = {"a": 1.0, "b": 0.9, "c": 0.95, "d": 0.5}
    out = soft_rerank(["a", "b", "c", "d"], base, PROFILES, "ASSET_EGRESS", weight=0.25)
    assert sorted(q for q, _ in out) == ["a", "b", "c", "d"]
    scores = dict(out)
    assert scores["b"] == pytest.approx(0.9 * 1.25) and scores["c"] == pytest.approx(0.95 * 1.125)
    assert [q for q, _ in out] == ["b", "c", "a", "d"]
    for q, s in out:
        assert base[q] <= s <= base[q] * 1.25


def test_rerank_is_identity_without_signal_or_weight():
    base = {"a": 0.3, "b": 0.9, "c": 0.9}
    order = ["c", "b", "a"]
    assert [q for q, _ in soft_rerank(order, base, PROFILES, "OFFLINE_BATCH")] == ["c", "b", "a"]
    assert [q for q, _ in soft_rerank(order, base, PROFILES, "ASSET_EGRESS", weight=0)] == ["c", "b", "a"]
    with pytest.raises(ValueError):
        soft_rerank(order, base, PROFILES, "ASSET_EGRESS", weight=-1)
