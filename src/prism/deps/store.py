"""SQLite cache of package summaries (offline export summarization).

One database (default `~/.cache/prism/deps/summaries.db`, or
`$PRISM_DEPS_CACHE/summaries.db`) shared by every repository on the
machine, keyed by distribution name, version, dist-info RECORD hash,
import name and `SUMMARY_SCHEMA_VERSION` - never by Prism's engine commit.

Schema:
  summaries(id, dist, version, record_hash, package, schema, status, stats, created)
  symbols(summary_id, qualified_name, kind, mask, confident_mask)
  evidence(summary_id, qualified_name, bit, tier, depth, path)   -- witness per bit
  headers(summary_id, relpath, source)                           -- signature-only package
  proxies(summary_id, name, class)                               -- module-level runtime proxies
Masks are plain integers (primitive bits 45-60 fit a signed 64-bit column).
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing

from prism.deps.discover import DistributionRef
from prism.deps.summarizer import SUMMARY_SCHEMA_VERSION, BitEvidence, PackageSummary, SymbolSummary

_SCHEMA = """
CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY,
    dist TEXT NOT NULL, version TEXT NOT NULL, record_hash TEXT NOT NULL, package TEXT NOT NULL,
    schema INTEGER NOT NULL, status TEXT NOT NULL, stats TEXT NOT NULL, created REAL NOT NULL,
    package_dir TEXT NOT NULL, requires TEXT NOT NULL,
    UNIQUE (dist, version, record_hash, package, schema)
);
CREATE TABLE IF NOT EXISTS symbols (
    summary_id INTEGER NOT NULL, qualified_name TEXT NOT NULL, kind TEXT NOT NULL,
    mask INTEGER NOT NULL, confident_mask INTEGER NOT NULL,
    PRIMARY KEY (summary_id, qualified_name)
);
CREATE TABLE IF NOT EXISTS evidence (
    summary_id INTEGER NOT NULL, qualified_name TEXT NOT NULL, bit INTEGER NOT NULL,
    tier INTEGER NOT NULL, depth INTEGER NOT NULL, path TEXT NOT NULL,
    PRIMARY KEY (summary_id, qualified_name, bit)
);
CREATE TABLE IF NOT EXISTS headers (
    summary_id INTEGER NOT NULL, relpath TEXT NOT NULL, source TEXT NOT NULL,
    PRIMARY KEY (summary_id, relpath)
);
CREATE TABLE IF NOT EXISTS proxies (
    summary_id INTEGER NOT NULL, name TEXT NOT NULL, class TEXT NOT NULL,
    PRIMARY KEY (summary_id, name)
);
"""


def default_store_path() -> str:
    root = os.environ.get("PRISM_DEPS_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "prism", "deps")
    return os.path.join(root, "summaries.db")


class SummaryStore:
    """Read/write package summaries; one row set per cache key."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or default_store_path()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    @staticmethod
    def _key(ref: DistributionRef) -> tuple:
        return (ref.dist, ref.version, ref.record_hash, ref.package, SUMMARY_SCHEMA_VERSION)

    def get(self, ref: DistributionRef) -> PackageSummary | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT id, status, stats FROM summaries WHERE dist=? AND version=? AND record_hash=? AND package=? AND schema=?",
                self._key(ref),
            ).fetchone()
            if row is None:
                return None
            sid, status, stats = row
            symbols: dict[str, SymbolSummary] = {}
            for qname, kind in conn.execute("SELECT qualified_name, kind FROM symbols WHERE summary_id=?", (sid,)):
                symbols[qname] = SymbolSummary(kind)
            for qname, bit, tier, depth, path in conn.execute(
                "SELECT qualified_name, bit, tier, depth, path FROM evidence WHERE summary_id=?", (sid,)
            ):
                symbols[qname].evidence[bit] = BitEvidence(tier, depth, tuple(json.loads(path)))
            headers = dict(conn.execute("SELECT relpath, source FROM headers WHERE summary_id=?", (sid,)))
            proxies = dict(conn.execute("SELECT name, class FROM proxies WHERE summary_id=?", (sid,)))
        return PackageSummary(ref, status, symbols, headers, json.loads(stats), proxies)

    def put(self, summary: PackageSummary) -> None:
        ref = summary.ref
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "DELETE FROM summaries WHERE dist=? AND version=? AND record_hash=? AND package=? AND schema=?", self._key(ref)
            )
            cur = conn.execute(
                "INSERT INTO summaries (dist, version, record_hash, package, schema, status, stats, created, package_dir, requires)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*self._key(ref), summary.status, json.dumps(summary.stats, sort_keys=True), time.time(),
                 ref.package_dir, json.dumps(list(ref.requires))),
            )
            sid = cur.lastrowid
            conn.executemany(
                "INSERT INTO symbols VALUES (?, ?, ?, ?, ?)",
                [(sid, q, s.kind, s.mask, s.confident_mask) for q, s in summary.symbols.items()],
            )
            conn.executemany(
                "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?)",
                [(sid, q, bit, ev.tier, ev.depth, json.dumps(list(ev.path)))
                 for q, s in summary.symbols.items() for bit, ev in s.evidence.items()],
            )
            conn.executemany("INSERT INTO headers VALUES (?, ?, ?)", [(sid, rel, src) for rel, src in summary.headers.items()])
            conn.executemany("INSERT INTO proxies VALUES (?, ?, ?)", [(sid, n, c) for n, c in summary.proxies.items()])
            # stale rows from deleted summaries
            conn.execute("DELETE FROM symbols WHERE summary_id NOT IN (SELECT id FROM summaries)")
            conn.execute("DELETE FROM evidence WHERE summary_id NOT IN (SELECT id FROM summaries)")
            conn.execute("DELETE FROM headers WHERE summary_id NOT IN (SELECT id FROM summaries)")
            conn.execute("DELETE FROM proxies WHERE summary_id NOT IN (SELECT id FROM summaries)")

    def keys(self) -> list[tuple]:
        with closing(self._connect()) as conn:
            return list(conn.execute("SELECT dist, version, record_hash, package, schema, status FROM summaries ORDER BY dist"))
