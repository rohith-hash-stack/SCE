"""Internal-hop attenuation of the capability leg (`hop_gamma`).

`by_hop` counts a call into a summarized dependency as a hop of its own;
`CapabilityProfile.internal` counts only repo-internal call edges, so the
function making the dependency call is the sink (`internal[0]`) and each
caller above it one hop further. With `hop_gamma` the triad scores the sink
in full and its callers `hop_gamma ** h`; without it nothing changes.
"""
from __future__ import annotations

import textwrap

import pytest

from prism.cli import build_pipeline
from prism.deps import DependencyIndex, SummaryStore
from prism.semantics.bitmask import FeatureBit
from prism.semantics.capabilities import CapabilityProfile, capability_score, compute_capability_profiles
from prism.semantics.profile_cache import _dump, _load
from prism.semantics.triad import DATABASE_IO, triad_rerank

NET = int(FeatureBit.PRIM_NET_CONNECT)
DB = int(FeatureBit.PRIM_DB_WRITE)

WIRE = {
    "__init__.py": "from .client import send\n",
    "client.py": """
        import socket


        def send(host, payload):
            sock = socket.create_connection((host, 80))
            sock.sendall(payload)
    """,
}

HOST = """
import socket

import wire


def raw_send(payload):
    socket.create_connection(("h", 1)).sendall(payload)


def push_event(payload):
    return wire.send("h", payload)


def handle_event(payload):
    return push_event(payload)


def route(payload):
    return handle_event(payload)


def far_route(payload):
    return route(payload)


def local_route(payload):
    return raw_send(payload)
"""


@pytest.fixture(scope="module")
def profiles(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("hops")
    site = tmp / "site"
    (site / "wire").mkdir(parents=True)
    for rel, text in WIRE.items():
        (site / "wire" / rel).write_text(textwrap.dedent(text))
    info = site / "wire-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: wire\nVersion: 1.0\n")
    (info / "top_level.txt").write_text("wire\n")
    (info / "RECORD").write_text("wire/__init__.py,sha256=x,1\nwire/client.py,sha256=x,1\n")
    repo = tmp / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "main.py").write_text(HOST)
    builder, _ = build_pipeline(str(repo), dependencies=DependencyIndex(SummaryStore(str(tmp / "s.db")), paths=[str(site)]))
    return compute_capability_profiles(builder)


def _p(profiles, fn):
    return profiles[f"app.main.{fn}"]


def test_a_dependency_call_costs_no_internal_hop(profiles):
    push = _p(profiles, "push_event")
    assert push.by_hop[1] & NET and not push.by_hop[0] & NET  # the boundary is a graph hop...
    assert push.internal[0] & NET  # ...but push_event is the sink


def test_each_repo_caller_is_one_internal_hop_further(profiles):
    assert _p(profiles, "handle_event").internal[1] & NET and not _p(profiles, "handle_event").internal[0] & NET
    assert _p(profiles, "route").internal[2] & NET and not _p(profiles, "route").internal[1] & NET
    # a dependency call three repo edges away is past the bound, and adds nothing to `by_hop`
    far = _p(profiles, "far_route")
    assert not any(m & NET for m in far.internal) and not far.within(2) & NET


def test_a_direct_primitive_and_its_caller(profiles):
    assert _p(profiles, "raw_send").internal[0] & NET
    assert _p(profiles, "local_route").internal[1] & NET


def test_attenuated_score_puts_the_sink_first(profiles):
    scores = {fn: capability_score(_p(profiles, fn), NET, gamma=0.5) for fn in ("push_event", "handle_event", "route", "far_route")}
    assert scores == {"push_event": 1.0, "handle_event": 0.5, "route": 0.25, "far_route": 0.0}
    # the default (graph hops) scores the sink like a caller one hop above a primitive
    assert capability_score(_p(profiles, "push_event"), NET) == 0.5
    assert capability_score(_p(profiles, "handle_event"), NET) == 0.25


def test_triad_with_hop_gamma_ranks_the_sink_over_its_caller(profiles):
    names = ["app.main.handle_event", "app.main.push_event"]  # caller first, equal stems
    stems = dict.fromkeys(names, 1.0)
    tokens = dict.fromkeys(names, frozenset())
    plain = triad_rerank(names, stems, profiles, tokens, target=NET)
    attenuated = triad_rerank(names, stems, profiles, tokens, target=NET, hop_gamma=0.5)
    assert [r.qname for r in attenuated] == ["app.main.push_event", "app.main.handle_event"]
    assert attenuated[0].capability == 1.0 and attenuated[1].capability == 0.5
    assert {r.qname: r.capability for r in plain} == {"app.main.push_event": 0.5, "app.main.handle_event": 0.25}


def test_database_io_honours_hop_gamma():
    sink = CapabilityProfile((0, DB, 0), internal=(DB, 0, 0))
    caller = CapabilityProfile((0, 0, DB), internal=(0, DB, 0))
    profiles = {"sink": sink, "caller": caller}
    rows = triad_rerank(["caller", "sink"], {"caller": 1.0, "sink": 1.0}, profiles,
                        {"caller": frozenset(), "sink": frozenset()}, target=DATABASE_IO, hop_gamma=0.5)
    assert [(r.qname, r.capability) for r in rows] == [("sink", 1.0), ("caller", 0.5)]


def test_hand_built_profiles_without_internal_keep_the_default_score():
    p = CapabilityProfile((0, NET, 0))
    assert capability_score(p, NET, gamma=0.5) == capability_score(p, NET) == 0.5


def test_profile_cache_round_trips_internal(profiles):
    assert _load(_dump(profiles)) == profiles
