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

import os

from prism.query.nlq import BM25, BM25_B, BM25_K1, MIGRATION_DIRS, MIGRATION_TERMS, stem  # noqa: F401 - re-exported
from prism.query.nlq import intent_legs as _intent_legs
from prism.query.nlq import raw_words as _raw_words
from prism.query.nlq import tokenize as _tokenize

#: Stop words (probe's list). `get` is deliberately kept - it is a real
#: identifier token - and the corpus names carry no discrimination.
STOP = (set("a an the of in on to and or is are be by for from with what which how when does do did its it if i my "
            "our we that this those these into out up as at whose has have there their them they get".split())
        - {"get"}) | {"django", "fastapi"}
VARIANTS = ("N", "NB")
#: Relations followed by the 1-hop neighbour expansion, both directions.
EXPANSION_RELATIONS = frozenset({"CALLS", "INSTANTIATES", "OVERRIDES"})


def tokenize(text: str) -> list[str]:
    """`prism.query.nlq.tokenize` with this probe's stop words (`STOP`,
    which also drops the corpus names)."""
    return _tokenize(text, STOP)


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

    def __init__(self, builder, repo_root: str, hop_gamma: float | None = None, unknown_to_action: bool = False):
        from prism.semantics.profile_cache import cached_capability_profiles
        self.builder = builder
        self.hop_gamma = hop_gamma  # opt-in internal-hop attenuation (`triad_rerank`)
        self.unknown_to_action = unknown_to_action  # former OPAQUE/PROXY weight shift (`triad_rerank`)
        self.profiles = cached_capability_profiles(builder, repo_root)
        self._tokens: dict[str, frozenset[str]] = {}

    def tokens(self, qname: str) -> frozenset[str]:
        if qname not in self._tokens:
            from prism.semantics.triad import action_tokens
            self._tokens[qname] = action_tokens(self.builder, qname)
        return self._tokens[qname]


class LexicalIndex:
    """BM25 over one corpus, both document variants, plus 1-hop expansion."""

    def __init__(self, builder, repo_root: str, relative_paths: bool = False, path_tokens_root: str | None = None,
                 triad: TriadContext | None = None, member_over_class: bool = False, demote_migrations: bool = False):
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
        # Ranking priors (off by default; the recorded probe output predates them).
        self.member_over_class = member_over_class
        self.demote_migrations = demote_migrations
        self._members: dict[str, list[str]] = {}
        if member_over_class:
            kinds = {s.qualified_name: s.kind for s in self.pool}
            for s in self.pool:
                if s.kind in ("function", "method") and s.enclosing_class and kinds.get(s.enclosing_class) == "class":
                    self._members.setdefault(s.enclosing_class, []).append(s.qualified_name)
        self._migration: set[str] = set()
        if demote_migrations:
            self._migration = {
                s.qualified_name for s in self.pool
                if MIGRATION_DIRS & set(os.path.relpath(s.file if os.path.isabs(s.file) else os.path.join(repo_root, s.file),
                                                        repo_root).replace(os.sep, "/").split("/")[:-1])
            }
        self._entries = None

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
            if not (self.member_over_class or self.demote_migrations):
                return [self.names[i] for i in self.bm25[variant].rank(tokenize(text))]
            sc = self.bm25[variant].scores(tokenize(text))
            rows = [(self.names[i], sc[i]) for i in sorted(sc, key=lambda i: (-sc[i], i))]
        else:
            rows = [(row.qname, row.score) for row in self.triad_ranked(text, variant, intent)]
        return self._apply_priors(text, rows)

    def _apply_priors(self, text: str, rows: list[tuple[str, float]]) -> list[str]:
        """Re-order `(name, score)` rows, best first, by the enabled priors:

        - member_over_class: a class never outranks its own best-scoring
          method - the class document contains its methods' text, so it
          matches whatever they match; it is placed just below that member.
        - demote_migrations: candidates under a `migrations/` or `alembic/`
          directory go below every other hit unless the query is about
          migrations (`MIGRATION_TERMS`).
        Stable otherwise: equal keys keep the incoming order."""
        score = dict(rows)
        if self.member_over_class:
            for cls, members in self._members.items():
                if cls in score:
                    best = max((score[m] for m in members if score.get(m, 0) > 0), default=None)
                    if best is not None and score[cls] >= best:
                        score[cls] = best * (1 - 1e-9)
        demote = self.demote_migrations and not (MIGRATION_TERMS & set(_raw_words(text)))
        position = {n: i for i, (n, _) in enumerate(rows)}
        return sorted(score, key=lambda n: (demote and n in self._migration, -score[n], position[n]))

    # -- presentation ------------------------------------------------------ #
    def hits(self, text: str, variant: str, k: int, intent: dict | None = None) -> list[dict]:
        """The top-`k` hits as payload rows, each with its upstream entry
        context (`prism.graph.entry_points`) - display only: the order is
        exactly `ranked`'s."""
        from prism.graph.entry_points import EntryIndex
        if self._entries is None:
            self._entries = EntryIndex(self.builder)
        out = []
        for rank, name in enumerate(self.ranked(text, variant, intent)[:k], start=1):
            symbol = self.builder.symbol_table.get(name)
            context = self._entries.context(name) if symbol and symbol.kind != "class" else []
            out.append({
                "rank": rank,
                "qname": name,
                "location": f"{os.path.relpath(symbol.file, self.root) if os.path.isabs(symbol.file) else symbol.file}:{symbol.line_range[0]}",
                "entry_context": context,
            })
        return out

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
            hop_gamma=self.triad.hop_gamma, unknown_to_action=self.triad.unknown_to_action,
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
