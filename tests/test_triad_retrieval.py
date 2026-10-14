"""Turn-1 triad in retrieval: composite operational filter, datastore verbs
as physical actions, index-time profile caching, and the triad re-rank in
the NLQ lexical candidate search.

`kvc` is a synthetic key-value client: its constructor only reads a clock
(ambient), `hset` opens a socket (operational). The host is a tiny corpus
whose functions share stems so BM25 alone cannot separate them.
"""
from __future__ import annotations

import copy
import json
import sqlite3
import textwrap

import pytest

from harness.experiments.query_to_evidence.nlq import lexical as L
from harness.experiments.query_to_evidence.nlq import schema as S
from harness.experiments.query_to_evidence.run import build_engine
from prism.deps import DependencyIndex, SummaryStore
from prism.semantics import profile_cache as PC
from prism.semantics.bitmask import FeatureBit
from prism.semantics.capabilities import capability_score, composites_of, compute_capability_profiles
from prism.semantics.triad import action_score, action_tokens, database_io

NET = int(FeatureBit.PRIM_NET_CONNECT)
TIME = int(FeatureBit.PRIM_TIME_CLOCK | FeatureBit.PRIM_TIME_WAIT)

KVC = {
    "__init__.py": "from .client import Client\n",
    "client.py": """
        import socket
        import time


        class Client:
            def __init__(self, host="localhost"):
                self.host = host
                self.created = time.monotonic()

            def hset(self, name, key, value):
                sock = socket.create_connection((self.host, 6379))
                sock.sendall(repr((name, key, value)).encode())

            def hget(self, name, key):
                sock = socket.create_connection((self.host, 6379))
                return sock.recv(1024)
    """,
}

HOST = """
import time

import kvc


def user_cache_profile(user, profile):
    return "profile:" + user + ":" + str(len(profile))


def cache_user_profile(user, profile):
    client = kvc.Client()
    client.hset("profiles", user, profile)


def read_user_profile(user):
    client = kvc.Client()
    return client.hget("profiles", user)


def poll_user_profile(user, profile):
    client = kvc.Client()
    while True:
        client.hset("profiles", user, profile)
        time.sleep(30)


def decrypt_user_profile(user, profile):
    return profile[::-1]
"""


def _install(site, name, files):
    pkg = site / name
    pkg.mkdir(parents=True)
    for rel, text in files.items():
        (pkg / rel).write_text(textwrap.dedent(text))
    info = site / f"{name}-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n")
    (info / "top_level.txt").write_text(name + "\n")
    (info / "RECORD").write_text("\n".join(f"{name}/{rel},sha256=x,1" for rel in files) + "\n")


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("triad")
    site = tmp / "site"
    _install(site, "kvc", KVC)
    root = tmp / "corpus"
    (root / "shop").mkdir(parents=True)
    (root / "shop" / "__init__.py").write_text("")
    (root / "shop" / "profiles.py").write_text(HOST)
    engine = build_engine(str(root), dependencies=DependencyIndex(SummaryStore(str(tmp / "store.db")), paths=[str(site)]))
    return engine, str(root)


def _q(fn):
    return f"shop.profiles.{fn}"


# --------------------------------------------------------------------- #
# Composite operational filter
# --------------------------------------------------------------------- #
def test_ambient_summary_bits_never_complete_a_composite(corpus):
    builder = corpus[0].builder
    p = compute_capability_profiles(builder)[_q("cache_user_profile")]
    assert p.by_hop[1] & TIME and p.by_hop[1] & NET  # the bits are still reported
    assert "PERIODIC_HEARTBEAT" not in composites_of(p)  # constructor clock + hset socket: no heartbeat
    assert capability_score(p, "PERIODIC_HEARTBEAT") == pytest.approx(0.25)  # NET group only, at hop 1


def test_own_timer_plus_summarized_socket_is_a_heartbeat(corpus):
    p = compute_capability_profiles(corpus[0].builder)[_q("poll_user_profile")]
    assert "PERIODIC_HEARTBEAT" in composites_of(p)  # time.sleep here + the summary's socket: two sources


# --------------------------------------------------------------------- #
# Datastore verbs as physical actions
# --------------------------------------------------------------------- #
def test_hset_is_a_mutate_action(corpus):
    tokens = action_tokens(corpus[0].builder, _q("cache_user_profile"))
    assert "hset" in tokens and action_score(tokens, "MUTATE") == 1.0
    assert database_io(compute_capability_profiles(corpus[0].builder)[_q("cache_user_profile")], tokens)[1] == "net-socket+db-verb"


def test_read_only_kv_verbs_are_read_actions(corpus):
    tokens = action_tokens(corpus[0].builder, _q("read_user_profile"))
    assert action_score(tokens, "READ") == 1.0 and action_score(tokens, "MUTATE") == 0.0


def test_kv_action_words_match_whole_words_only():
    assert action_score({"decr"}, "MUTATE") == 1.0 and action_score({"mget"}, "READ") == 1.0
    for word in ("decrypt", "increment", "expired", "hsetx"):
        assert action_score({word}, "MUTATE") == 0.0, word


# --------------------------------------------------------------------- #
# Retrieval intent in the interpretation schema
# --------------------------------------------------------------------- #
def _interp(intent=None):
    rec = {"schema": S.INTERPRETATION, "schema_version": S.SCHEMA_VERSION, "question_id": "t1", "corpus": "tiny",
           "question_text": "Where is a user profile cached?",
           "interpreter": {"kind": "agent", "model_id": "m", "prompt_version": "p"}, "answerability": "answerable",
           "requirements": [{"id": "r1", "kind": "behavior", "mandatory": True,
                             "subject": {"mention": "profile cache", "proposed_names": []},
                             "scope": {"role": "any", "direct_only": False}, "evidence_source": "static_code"}]}
    if intent is not None:
        rec["retrieval_intent"] = intent
    return rec


def test_schema_accepts_a_retrieval_intent():
    assert S.validate_interpretation(_interp()) == []
    assert S.validate_interpretation(_interp({"action": "MUTATE", "capability": "DATABASE_IO"})) == []
    assert S.validate_interpretation(_interp({"capability": "PRIM_NET_CONNECT"})) == []
    assert S.validate_interpretation(_interp({"capability": "ASSET_EGRESS"})) == []


def test_schema_rejects_unknown_intent_values():
    assert S.validate_interpretation(_interp({"action": "TELEPORT"}))
    assert S.validate_interpretation(_interp({"capability": "REDIS"}))
    assert S.validate_interpretation(_interp({"stems": ["x"]}))
    assert S.validate_interpretation(_interp({}))


# --------------------------------------------------------------------- #
# Lexical candidate search
# --------------------------------------------------------------------- #
TEXT = "cache user profile"


def _index(corpus, triad=True):
    engine, root = corpus
    return L.LexicalIndex(engine.builder, root, triad=L.TriadContext(engine.builder, root) if triad else None)


def test_without_intent_the_ranking_is_bm25(corpus):
    plain, with_ctx = _index(corpus, triad=False), _index(corpus)
    for v in L.VARIANTS:
        assert with_ctx.ranked(TEXT, v) == plain.ranked(TEXT, v)
        assert with_ctx.ranked(TEXT, v, intent=None) == plain.ranked(TEXT, v)


def test_intent_reranks_toward_the_real_writer(corpus):
    ix = _index(corpus)
    bm25 = ix.ranked(TEXT, "N")
    assert bm25.index(_q("user_cache_profile")) < bm25.index(_q("cache_user_profile"))  # equal BM25 score: defined first, ranked first
    triad = ix.triad_ranked(TEXT, "N", {"action": "MUTATE", "capability": "DATABASE_IO"})
    order = [r.qname for r in triad]
    assert order.index(_q("cache_user_profile")) < order.index(_q("user_cache_profile"))
    writer = next(r for r in triad if r.qname == _q("cache_user_profile"))
    assert writer.capability_source == "net-socket+db-verb" and writer.action == 1.0
    assert sorted(order) == sorted(bm25)  # re-ranked, never filtered


def test_intent_flows_through_search_ranks_and_expansion(corpus):
    ix = _index(corpus)
    intent = {"action": "MUTATE", "capability": "DATABASE_IO"}
    assert ix.search(TEXT, "N", 1, intent) == [_q("cache_user_profile")]
    assert ix.ranks(TEXT, "N", [_q("cache_user_profile")], intent) == {_q("cache_user_profile"): 1}
    assert _q("cache_user_profile") in ix.expanded(TEXT, "N", 1, intent)


def test_window_keeps_the_top_exact(corpus, monkeypatch):
    ix = _index(corpus)
    intent = {"action": "MUTATE", "capability": "DATABASE_IO"}
    full = [r.qname for r in ix.triad_ranked(TEXT, "N", intent)]
    monkeypatch.setattr(L, "TRIAD_WINDOW", 2)
    windowed = [r.qname for r in ix.triad_ranked(TEXT, "N", intent)]
    assert windowed[:2] == full[:2] and sorted(windowed) == sorted(full)


# --------------------------------------------------------------------- #
# Index-time profile cache
# --------------------------------------------------------------------- #
def test_profiles_are_persisted_and_reloaded(corpus, monkeypatch):
    engine, root = corpus
    fresh = compute_capability_profiles(engine.builder)
    PC.cached_capability_profiles(engine.builder, root)
    path = PC.profile_cache_path(root)
    assert path.exists()
    monkeypatch.setattr(PC, "_MEMORY", PC._LRUCache(maxsize=4))  # a new process
    monkeypatch.setattr(PC, "compute_capability_profiles", lambda *a, **k: pytest.fail("recomputed"))
    assert PC.cached_capability_profiles(engine.builder, root) == fresh


def test_cache_key_separates_builds_with_and_without_summaries(corpus):
    engine, root = corpus
    plain = build_engine(root)
    assert PC.profile_cache_key(plain.builder, root, 2, True) != PC.profile_cache_key(engine.builder, root, 2, True)
    with sqlite3.connect(PC.profile_cache_path(root)) as conn:
        rows = conn.execute("SELECT payload FROM profiles").fetchall()
    assert rows and all(isinstance(json.loads(r[0]), dict) for r in rows)
