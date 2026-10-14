"""Deterministic lexical candidate search over an existing symbol index.

Extracted verbatim (logic, constants, ordering) from the two read-only
probes behind the NLQ investigation report (`lexical_probe.py`,
`expand_probe.py`), so the recorded measurements in
`fixtures/lexical_recorded.json` can be reproduced and regression-tested.
No model, no embeddings; BM25 is stdlib only (the optional triad re-rank
imports Prism's semantics); it reads the index, never changes it.

Documents (one per *pool* symbol):
  N  = tokens of the qualified name + tokens of `SymbolInfo.file` as indexed
  NB = N + tokens of the symbol's own source lines (`line_range`)
Pool = every symbol that is not an attribute and not `SymbolRole.VERIFICATION`,
in symbol-table iteration order. That order is the tie-break: equal BM25
scores rank by pool position (ascending).

Known property, kept on purpose because the recorded results depend on it:
`SymbolInfo.file` is an absolute path, so path tokens include the checkout
location (e.g. `home`, `user`). Two ways to control that:
  path_tokens_root=R  tokenize each file as R + its path relative to the corpus
                      root. With R = the root the recording was made under
                      (lexical_eval.RECORDED_CORPUS_ROOTS) the documents are
                      token-for-token the recorded ones wherever the corpus is
                      checked out; at the recording location it is the identity.
  relative_paths=True tokenize the repo-relative path only - a *different*
                      variant that does not reproduce the recorded ranks.

Turn-1 triad (optional): given a `TriadContext` and a retrieval intent (a
physical action and/or a capability target, the interpretation's
`retrieval_intent`), `ranked` re-ranks BM25 candidates with
`prism.semantics.triad.triad_rerank` - BM25 is the stem leg; capability comes
from index-time profiles (`prism.semantics.profile_cache`, depth-1 dependency
summaries included when the index was built with them). Without an intent the
ranking is exactly the BM25 one above.
"""
from __future__ import annotations

import collections
import math
import os
import re

#: Stop words (probe's list). `get` is deliberately kept - it is a real
#: identifier token - and the corpus names carry no discrimination.
STOP = (set("a an the of in on to and or is are be by for from with what which how when does do did its it if i my "
            "our we that this those these into out up as at whose has have there their them they get".split())
        - {"get"}) | {"django", "fastapi"}
_SUFFIXES = ("ing", "ed", "es", "s")
VARIANTS = ("N", "NB")
#: Relations followed by the 1-hop neighbour expansion, both directions.
EXPANSION_RELATIONS = frozenset({"CALLS", "INSTANTIATES", "OVERRIDES"})
BM25_K1, BM25_B = 1.2, 0.75


def stem(t: str) -> str:
    for suf in _SUFFIXES:
        if len(t) > len(suf) + 2 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def tokenize(text: str) -> list[str]:
    """Identifier-aware tokens: split on non-alphanumerics and camelCase,
    lower-case, drop stop words and 1-char tokens, then strip one suffix."""
    out = []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9]*", text):
        for p in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+", w) or [w]:
            p = p.lower()
            if p not in STOP and len(p) > 1:
                out.append(stem(p))
    return out


class BM25:
    def __init__(self, docs: list[list[str]]):
        self.N = len(docs)
        self.tf = [collections.Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = sum(self.len) / max(1, self.N)
        df = collections.Counter(t for c in self.tf for t in c)
        self.idf = {t: math.log(1 + (self.N - n + .5) / (n + .5)) for t, n in df.items()}
        self.post: dict[str, list[int]] = collections.defaultdict(list)
        for i, c in enumerate(self.tf):
            for t in c:
                self.post[t].append(i)

    def scores(self, query: list[str], k1: float = BM25_K1, b: float = BM25_B) -> dict[int, float]:
        sc: dict[int, float] = collections.defaultdict(float)
        for t in set(query):
            if t not in self.idf:
                continue
            for i in self.post[t]:
                f = self.tf[i][t]
                sc[i] += self.idf[t] * f * (k1 + 1) / (f + k1 * (1 - b + b * self.len[i] / self.avg))
        return sc

    def rank(self, query: list[str]) -> list[int]:
        """Indices of every document sharing at least one query token, by
        descending score, ties by ascending index. Unmatched documents are
        not ranked (a rank of None, not "last")."""
        sc = self.scores(query)
        return sorted(sc, key=lambda i: (-sc[i], i))


def rebase_path(file: str, repo_root: str, new_root: str) -> str:
    """`file` (absolute, or relative to `repo_root`) re-expressed under
    `new_root`, '/'-separated. A file outside `repo_root` is an error, never
    silently re-mapped."""
    absolute = file if os.path.isabs(file) else os.path.join(repo_root, file)
    rel = os.path.relpath(absolute, repo_root)
    if rel == ".." or rel.startswith(".." + os.sep) or os.path.isabs(rel):
        raise ValueError(f"{file} is outside the corpus root {repo_root}")
    return new_root.rstrip("/") + "/" + rel.replace(os.sep, "/")


def candidate_pool(builder) -> list:
    from prism.graph.symbol_table import SymbolRole
    return [s for s in builder.symbol_table if s.kind != "attribute" and s.role != SymbolRole.VERIFICATION]


#: Re-rank window: the top TRIAD_WINDOW of a triad ranking are exact; below
#: them the BM25 order is kept (a candidate whose best possible boosted score
#: is under the window's last BM25 score can never enter the window).
TRIAD_WINDOW = 200


class TriadContext:
    """Index-time capability profiles plus memoized action tokens."""

    def __init__(self, builder, repo_root: str):
        from prism.semantics.profile_cache import cached_capability_profiles
        self.builder = builder
        self.profiles = cached_capability_profiles(builder, repo_root)
        self._tokens: dict[str, frozenset[str]] = {}

    def tokens(self, qname: str) -> frozenset[str]:
        if qname not in self._tokens:
            from prism.semantics.triad import action_tokens
            self._tokens[qname] = action_tokens(self.builder, qname)
        return self._tokens[qname]


def _intent_legs(intent: dict | None) -> tuple[str | None, "str | int | None"]:
    if not intent:
        return None, None
    capability = intent.get("capability")
    if capability and capability.startswith("PRIM_"):
        from prism.semantics.bitmask import FeatureBit
        capability = int(FeatureBit[capability])
    return intent.get("action"), capability


class LexicalIndex:
    """BM25 over one corpus, both document variants, plus 1-hop expansion."""

    def __init__(self, builder, repo_root: str, relative_paths: bool = False, path_tokens_root: str | None = None,
                 triad: TriadContext | None = None):
        if relative_paths and path_tokens_root is not None:
            raise ValueError("relative_paths and path_tokens_root are different variants; pass at most one")
        self.builder, self.root = builder, repo_root
        self.pool = candidate_pool(builder)
        self.names = [s.qualified_name for s in self.pool]
        self.pool_set = set(self.names)
        self._lines: dict[str, list[str]] = {}
        if path_tokens_root is not None:
            path = lambda f: rebase_path(f, repo_root, path_tokens_root)
        elif relative_paths:
            path = lambda f: os.path.relpath(f, repo_root)
        else:
            path = lambda f: f
        doc_n = [tokenize(s.qualified_name) + tokenize(path(s.file)) for s in self.pool]
        doc_nb = [doc_n[i] + tokenize("\n".join(self._source(s.file)[s.line_range[0] - 1:s.line_range[1]]))
                  for i, s in enumerate(self.pool)]
        self.bm25 = {"N": BM25(doc_n), "NB": BM25(doc_nb)}
        self.triad = triad

    def _source(self, path: str) -> list[str]:
        if path not in self._lines:
            p = path if os.path.isabs(path) else os.path.join(self.root, path)
            try:
                self._lines[path] = open(p, encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                self._lines[path] = []
        return self._lines[path]

    def ranked(self, text: str, variant: str, intent: dict | None = None) -> list[str]:
        """Every matched pool symbol, best first (see `BM25.rank`); with an
        `intent` and a `TriadContext`, triad-re-ranked (`triad_ranked`)."""
        if variant not in self.bm25:
            raise ValueError(f"unknown variant {variant!r}; expected one of {VARIANTS}")
        action, capability = _intent_legs(intent)
        if self.triad is None or (action is None and capability is None):
            return [self.names[i] for i in self.bm25[variant].rank(tokenize(text))]
        return [row.qname for row in self.triad_ranked(text, variant, intent)]

    def triad_ranked(self, text: str, variant: str, intent: dict) -> list:
        """`TriadScore` rows (provenance: stems = BM25, capability, action,
        capability_source, opaque entry points), best first. The top
        `TRIAD_WINDOW` are exact; the rest keep BM25 order, unboosted."""
        from prism.semantics.triad import ACTION_WEIGHT, CAPABILITY_WEIGHT, TriadScore, triad_rerank
        if self.triad is None:
            raise ValueError("triad_ranked needs a TriadContext")
        action, capability = _intent_legs(intent)
        bm25 = self.bm25[variant]
        sc = bm25.scores(tokenize(text))
        order = sorted(sc, key=lambda i: (-sc[i], i))
        floor = sc[order[TRIAD_WINDOW - 1]] if len(order) >= TRIAD_WINDOW else 0.0
        reach = 1.0 + CAPABILITY_WEIGHT + ACTION_WEIGHT
        window = [i for i in order if sc[i] * reach >= floor]
        names = [self.names[i] for i in window]
        rows = triad_rerank(
            names, {self.names[i]: sc[i] for i in window}, self.triad.profiles,
            {n: self.triad.tokens(n) for n in names}, target=capability, action=action,
        )
        rest = [TriadScore(self.names[i], sc[i], sc[i], 0.0, 0.0, "none") for i in order[len(window):]]
        return rows + rest

    def search(self, text: str, variant: str, k: int, intent: dict | None = None) -> list[str]:
        if k < 0:
            raise ValueError("k must be >= 0")
        return self.ranked(text, variant, intent)[:k]

    def ranks(self, text: str, variant: str, names, intent: dict | None = None) -> dict[str, int | None]:
        """1-based rank of each name; None if unmatched or not in the pool."""
        pos = {n: r + 1 for r, n in enumerate(self.ranked(text, variant, intent))}
        return {n: pos.get(n) for n in names}

    def neighbours(self, name: str) -> set[str]:
        return neighbours(self.builder.graph, name, self.pool_set)

    def expanded(self, text: str, variant: str, k: int, intent: dict | None = None) -> set[str]:
        """Top-k plus each one's 1-hop pool neighbours (EXPANSION_RELATIONS, both directions)."""
        top = self.search(text, variant, k, intent)
        out = set(top)
        for n in top:
            out |= self.neighbours(n)
        return out


def neighbours(graph, name: str, pool: set[str]) -> set[str]:
    r: set[str] = set()
    if name in graph:
        for _, v, d in graph.out_edges(name, data=True):
            if d.get("relation", "CALLS") in EXPANSION_RELATIONS and v in pool:
                r.add(v)
        for u, _, d in graph.in_edges(name, data=True):
            if d.get("relation", "CALLS") in EXPANSION_RELATIONS and u in pool:
                r.add(u)
    return r
