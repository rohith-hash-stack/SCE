"""Natural-language code search: a question in, ranked symbols out.

    from prism.query.nlq import NLQIndex
    index = NLQIndex(builder, repo_root)
    result = index.search("Where are notifications saved to the database?", top_k=5)

The pipeline, end to end:

1. **Intent** (`rule_intent`): a deterministic Turn-1 interpretation of the
   question - a physical action (`PHYSICAL_ACTIONS`: the first family one
   of whose verbs occurs in the text) and a capability target
   (`CAPABILITY_WORDS`: a resource word such as "database" or "socket").
   No model is called. A caller with a better interpretation (an LLM's)
   passes `intent=` explicitly; the same dict shape is accepted.
2. **Stems** (BM25): every candidate - each indexed non-test function,
   method and class outside test files (`candidate_pool`) - is a document
   of its qualified name, its
   repository-relative path and its source lines (`tokenize`: identifier
   split, one-suffix stemming).
3. **Triad** (`prism.semantics.triad.triad_rerank`) over the top
   `TRIAD_WINDOW`: `stems * (1 + 0.25 * capability + 0.15 * action)`.
   Capability comes from index-time profiles (L2 primitives, L3 stubs and
   dependency summaries when the index was built with them); capability
   that is OPAQUE or PROXY is neutral, never a boost.
4. **Priors**: a class never outranks its own best-matching method;
   candidates under `migrations/`, `alembic/` or `migrate/` go below every
   other hit unless the question is about migrations.
5. **Summary field** (optional, `summaries=`): `BM25(code) + w *
   BM25(summary)`, the offline one-sentence summaries
   (`prism summarize`, `prism.summaries`) as an independent field with its
   own IDF and length norm. `summaries="auto"` (what `prism.ask` uses)
   applies the shipped rule - w = 0.5 when `.prism/summaries.json` was built
   by the locked pipeline and covers >= 95% of the functions, else 0 (see
   `prism.summaries.index`). At w = 0 the field is not consulted at all.
6. **Route and exact-name pin** (`prism.query.route`, `search` only): an
   identifier or mixed query puts the symbols its names denote
   (`qualified_name_matches`) first, best-scored first; the ranked hits
   follow. A natural-language query is not pinned.
7. **Entry context** (`prism.graph.entry_points`, display only): how a
   registered entry point (a route, command, task - never a wrapper
   decorator) reaches the hit, at most one validated path
   (`prism.graph.render_breadcrumbs`), e.g.
   `[Entry: POST /follow/<username>] -> follow() => enters at User.follow()`.

The ranking was measured on two frozen held-out question sets
(`harness/experiments/heldout_retrieval/`).
"""
from __future__ import annotations

import collections
import math
import os
import re
from typing import Any

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.symbol_table import SymbolRole

# --------------------------------------------------------------------- #
# Tokens and BM25
# --------------------------------------------------------------------- #
#: Function words. `get` is kept on purpose: it is a real identifier token.
STOP_WORDS = frozenset(
    set("a an the of in on to and or is are be by for from with what which how when does do did its it if i my "
        "our we that this those these into out up as at whose has have there their them they get".split()) - {"get"}
)
_SUFFIXES = ("ing", "ed", "es", "s")
BM25_K1, BM25_B = 1.2, 0.75


def stem(token: str) -> str:
    """Strip one suffix (`ing`, `ed`, `es`, `s`) from a token long enough."""
    for suf in _SUFFIXES:
        if len(token) > len(suf) + 2 and token.endswith(suf):
            return token[: -len(suf)]
    return token


def tokenize(text: str, stop: "frozenset[str] | set[str]" = STOP_WORDS) -> list[str]:
    """Identifier-aware tokens: split on non-alphanumerics and camelCase,
    lower-case, drop stop words and 1-char tokens, then strip one suffix."""
    out = []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9]*", text):
        for p in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+", w) or [w]:
            p = p.lower()
            if p not in stop and len(p) > 1:
                out.append(stem(p))
    return out


class BM25:
    """Okapi BM25 over pre-tokenized documents (stdlib only)."""

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
        descending score, ties by ascending index (unmatched: not ranked)."""
        sc = self.scores(query)
        return sorted(sc, key=lambda i: (-sc[i], i))


# --------------------------------------------------------------------- #
# Intent
# --------------------------------------------------------------------- #
#: Question words naming a physical resource -> capability target.
CAPABILITY_WORDS = {
    "database": "DATABASE_IO", "sql": "DATABASE_IO", "db": "DATABASE_IO",
    "file": "PRIM_FS_READ", "files": "PRIM_FS_READ", "disk": "PRIM_FS_READ", "directory": "PRIM_FS_READ",
    "socket": "PRIM_NET_CONNECT", "network": "PRIM_NET_CONNECT", "connection": "PRIM_NET_CONNECT",
    "subprocess": "PRIM_PROC_SPAWN", "process": "PRIM_PROC_SPAWN",
    "expire": "PRIM_TIME_CLOCK", "expired": "PRIM_TIME_CLOCK", "expiry": "PRIM_TIME_CLOCK", "timeout": "PRIM_TIME_CLOCK",
    "clock": "PRIM_TIME_CLOCK", "random": "PRIM_RAND_PRNG",
}


def rule_intent(question: str) -> dict | None:
    """`{"action": ..., "capability": ...}` (either key optional) read off
    the question text, or `None` when it names neither. Deterministic: the
    action is the first `PHYSICAL_ACTIONS` family whose verbs occur in the
    text; the capability is set only when the question names a resource."""
    from prism.semantics.triad import PHYSICAL_ACTIONS, _words, action_score
    words = set(_words(question))
    intent: dict[str, str] = {}
    action = next((name for name in PHYSICAL_ACTIONS if action_score(words, name)), None)
    if action:
        intent["action"] = action
    capability = next((CAPABILITY_WORDS[w] for w in _words(question) if w in CAPABILITY_WORDS), None)
    if capability:
        intent["capability"] = capability
    return intent or None


def intent_legs(intent: dict | None) -> tuple[str | None, "str | int | None"]:
    """`(action, capability target)` for `triad_rerank`; a `PRIM_*`
    capability name becomes its bit."""
    if not intent:
        return None, None
    capability = intent.get("capability")
    if capability and capability.startswith("PRIM_"):
        from prism.semantics.bitmask import FeatureBit
        capability = int(FeatureBit[capability])
    return intent.get("action"), capability


# --------------------------------------------------------------------- #
# Priors
# --------------------------------------------------------------------- #
#: Directory names holding generated schema migrations (Django, Alembic,
#: Flask-Migrate, Rails `db/migrate`).
MIGRATION_DIRS = frozenset({"migrations", "alembic", "migrate"})
#: Question words that make migration files a legitimate answer.
MIGRATION_TERMS = frozenset({"migration", "migrations", "migrate", "alembic", "schema", "upgrade", "downgrade", "revision"})


def raw_words(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[A-Za-z]+", text)]


def is_migration_path(relative_path: str) -> bool:
    return bool(MIGRATION_DIRS & set(relative_path.replace(os.sep, "/").split("/")[:-1]))


def apply_priors(
    rows: list[tuple[str, float]], question: str, members: dict[str, list[str]], migration: "set[str] | frozenset[str]",
) -> list[str]:
    """Re-order `(name, score)` rows, best first:

    - a class never outranks its own best-scoring method (`members`: class
      -> its methods) - the class document contains its methods' text, so
      it matches whatever they match; it is placed just below that member;
    - names in `migration` go below every other hit unless the question is
      about migrations (`MIGRATION_TERMS`).
    Stable otherwise: equal keys keep the incoming order."""
    score = dict(rows)
    for cls, methods in members.items():
        if cls in score:
            best = max((score[m] for m in methods if score.get(m, 0) > 0), default=None)
            if best is not None and score[cls] >= best:
                score[cls] = best * (1 - 1e-9)
    demote = bool(migration) and not (MIGRATION_TERMS & set(raw_words(question)))
    position = {n: i for i, (n, _) in enumerate(rows)}
    return sorted(score, key=lambda n: (demote and n in migration, -score[n], position[n]))


# --------------------------------------------------------------------- #
# The index
# --------------------------------------------------------------------- #
#: The triad re-ranks exactly within this many BM25 candidates; below
#: them the BM25 order is kept (a candidate whose best possible boosted
#: score is under the window's last BM25 score can never enter it).
TRIAD_WINDOW = 200


#: Type-test files (`expectTypeOf` assertions, tsd): test code the generic
#: test-file rule (`prism.scanner.entrypoint_gate.is_test_file`) does not name.
_TYPE_TEST_FILE = re.compile(r"\.test-d\.(ts|tsx|mts|cts)$")


def is_search_excluded_file(builder: ConcreteGraphBuilder, path: str) -> bool:
    """Whether a file's symbols stay out of natural-language search: test
    files (`tests/`, `__tests__/`, `*.test.ts`, `test_*.py`, ...) and type
    tests (`*.test-d.ts`). Search only - the graph, `prism.slice` and
    `prism.blast_radius` index and traverse test code as before."""
    if builder._in_test_file(path):
        return True
    rel = os.path.relpath(path, builder.repo_root) if os.path.isabs(path) and builder.repo_root else path
    return bool(_TYPE_TEST_FILE.search(rel.replace(os.sep, "/")))


def candidate_pool(builder: ConcreteGraphBuilder, exclude_test_files: bool = True) -> list:
    """Every symbol search ranks: functions, methods and classes, no
    attributes, no test (VERIFICATION) code and - by default - nothing
    defined in a test file. A helper in a test file is not a test by role
    (`_classify_symbol_role` needs a test name or an assertion body), but it
    is never an answer to a question about the code under test; with
    arrow-style JS/TS test helpers indexed they crowded library code out of
    the ranking (`docs/capabilities_and_routing.md`)."""
    excluded: dict[str, bool] = {}
    return [s for s in builder.symbol_table if s.kind != "attribute" and s.role != SymbolRole.VERIFICATION
            and not (exclude_test_files and excluded.setdefault(s.file, is_search_excluded_file(builder, s.file)))]


def read_lines(root: str, path: str) -> list[str]:
    p = path if os.path.isabs(path) else os.path.join(root, path)
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


class NLQIndex:
    """Natural-language search over one indexed repository (see module
    docstring). Built once per index; `search` is cheap."""

    def __init__(
        self, builder: ConcreteGraphBuilder, repo_root: str, *, member_over_class: bool = True,
        demote_migrations: bool = True, unknown_to_action: bool = False, hop_gamma: float | None = None,
        summaries: "dict[str, str] | str | None" = None, summary_weight: float | None = None,
        exclude_test_files: bool = True,
    ):
        """`summaries`: `None` (off), `"auto"` (the shipped rule over
        `.prism/summaries.json`) or a `{qualified name: sentence}` dict, used
        at `summary_weight` (default `SUMMARY_WEIGHT`). `exclude_test_files`:
        keep symbols defined in test files out of the pool (`candidate_pool`)."""
        from prism.graph.entry_points import EntryIndex
        from prism.semantics.profile_cache import cached_capability_profiles
        self.builder, self.root = builder, os.path.abspath(repo_root)
        self.pool = candidate_pool(builder, exclude_test_files)
        self.names = [s.qualified_name for s in self.pool]
        self._lines: dict[str, list[str]] = {}
        docs = [
            tokenize(s.qualified_name) + tokenize(self._relative(s.file))
            + tokenize("\n".join(self._source(s.file)[s.line_range[0] - 1:s.line_range[1]]))
            for s in self.pool
        ]
        self.bm25 = BM25(docs)
        self.profiles = cached_capability_profiles(builder, self.root)
        self._tokens: dict[str, frozenset[str]] = {}
        self.unknown_to_action, self.hop_gamma = unknown_to_action, hop_gamma
        kinds = {s.qualified_name: s.kind for s in self.pool}
        self.members: dict[str, list[str]] = {}
        if member_over_class:
            for s in self.pool:
                if s.kind in ("function", "method") and s.enclosing_class and kinds.get(s.enclosing_class) == "class":
                    self.members.setdefault(s.enclosing_class, []).append(s.qualified_name)
        self.migration = {s.qualified_name for s in self.pool if is_migration_path(self._relative(s.file))} \
            if demote_migrations else set()
        self.entries = EntryIndex(builder)
        self._init_summaries(summaries, summary_weight)

    def _init_summaries(self, summaries: "dict[str, str] | str | None", weight: float | None) -> None:
        from prism.summaries.index import SUMMARY_WEIGHT, SummaryState, index_path, load_state
        if summaries == "auto":
            self.summary_state = load_state(self.pool, self._source, index_path(self.root))
            weight = self.summary_state.weight if weight is None else weight
        else:
            self.summary_state = SummaryState(summaries=dict(summaries or {}), present=summaries is not None,
                                              reason="caller-supplied" if summaries is not None else "off")
            weight = (SUMMARY_WEIGHT if summaries else 0.0) if weight is None else weight
        self.summary_weight = float(weight)
        self.summary_bm25 = BM25([tokenize(self.summary_state.summaries.get(n, "")) for n in self.names]) \
            if self.summary_weight else None

    # -- helpers -------------------------------------------------------- #
    def _relative(self, path: str) -> str:
        return os.path.relpath(path, self.root) if os.path.isabs(path) else path

    def _source(self, path: str) -> list[str]:
        if path not in self._lines:
            self._lines[path] = read_lines(self.root, path)
        return self._lines[path]

    def action_tokens(self, qname: str) -> frozenset[str]:
        if qname not in self._tokens:
            from prism.semantics.triad import action_tokens
            self._tokens[qname] = action_tokens(self.builder, qname)
        return self._tokens[qname]

    # -- ranking -------------------------------------------------------- #
    def scored(self, question: str, intent: dict | None) -> list:
        """`TriadScore` rows, best first, before the priors: every BM25
        match, re-ranked by the triad within `TRIAD_WINDOW` when `intent`
        has an action or a capability."""
        from prism.semantics.triad import ACTION_WEIGHT, CAPABILITY_WEIGHT, TriadScore, triad_rerank
        query = tokenize(question)
        sc = self.bm25.scores(query)
        if self.summary_bm25 is not None:  # BM25(code) + w * BM25(summary); never consulted at w = 0
            sc = dict(sc)
            for i, s in self.summary_bm25.scores(query).items():
                sc[i] = sc.get(i, 0.0) + self.summary_weight * s
        order = sorted(sc, key=lambda i: (-sc[i], i))
        action, capability = intent_legs(intent)
        if action is None and capability is None:
            return [TriadScore(self.names[i], sc[i], sc[i], 0.0, 0.0, "none") for i in order]
        floor = sc[order[TRIAD_WINDOW - 1]] if len(order) >= TRIAD_WINDOW else 0.0
        reach = 1.0 + CAPABILITY_WEIGHT + ACTION_WEIGHT
        window = [i for i in order if sc[i] * reach >= floor]
        names = [self.names[i] for i in window]
        rows = triad_rerank(
            names, {self.names[i]: sc[i] for i in window}, self.profiles,
            {n: self.action_tokens(n) for n in names}, target=capability, action=action,
            hop_gamma=self.hop_gamma, unknown_to_action=self.unknown_to_action,
        )
        rest = [TriadScore(self.names[i], sc[i], sc[i], 0.0, 0.0, "none") for i in order[len(window):]]
        return rows + rest

    def ranked(self, question: str, intent: "dict | None | str" = "rule") -> list[str]:
        """Candidate names, best first. `intent`: a dict, `None` (stems
        only) or `"rule"` (`rule_intent(question)`)."""
        if intent == "rule":
            intent = rule_intent(question)
        rows = self.scored(question, intent)
        return apply_priors([(r.qname, r.score) for r in rows], question, self.members, self.migration)

    def pinned(self, question: str, route: str, order: list[str]) -> list[str]:
        """Exact-name pin: pool symbols the query's names denote
        (`qualified_name_matches`), in `order` (best-scored first), then any
        the ranking missed, by name. Empty for natural-language queries."""
        from prism.query.locate import qualified_name_matches
        from prism.query.route import name_candidates
        pool = set(self.names)
        matches = {m for name in name_candidates(question, route)
                   for m in qualified_name_matches(self.builder, name) if m in pool}
        position = {n: i for i, n in enumerate(order)}
        return sorted(matches, key=lambda n: (position.get(n, len(position)), n))

    def served(self, question: str, intent: dict | None) -> tuple[str, list[str], list[str], dict]:
        """`(route, pinned names, full served order, TriadScore rows by
        name)`: `ranked()` with the exact-name pin applied in front."""
        from prism.query.route import classify
        route = classify(question)
        rows = {r.qname: r for r in self.scored(question, intent)}
        order = apply_priors([(n, r.score) for n, r in rows.items()], question, self.members, self.migration)
        pins = self.pinned(question, route, order)
        pin_set = set(pins)
        return route, pins, pins + [n for n in order if n not in pin_set], rows

    def search(self, question: str, top_k: int = 10, intent: "dict | None | str" = "rule") -> dict[str, Any]:
        """The query result payload:

            {"question": ...,
             "intent": {"action": ..., "capability": ..., "source": "rule" | "caller" | None,
                        "route": "identifier" | "mixed" | "natural_language", "summary_weight": w},
             "hits": [{"rank", "qname", "kind", "location", "score", "pinned",
                       "legs": {"stems", "capability", "action", "capability_source"},
                       "entry_context": ["[Entry: POST /x] -> f() => enters at g()"]}, ...]}

        `pinned` hits are the exact name matches an identifier or mixed
        query placed first (`pinned()`)."""
        from prism.semantics.triad import TriadScore
        if top_k < 1:
            raise ValueError("top_k must be >= 1")
        source = "rule" if intent == "rule" else ("caller" if intent else None)
        resolved = rule_intent(question) if intent == "rule" else intent
        route, pins, order, rows = self.served(question, resolved)
        pin_set = set(pins)
        hits = []
        for rank, name in enumerate(order[:top_k], start=1):
            row = rows.get(name) or TriadScore(name, 0.0, 0.0, 0.0, 0.0, "none")
            symbol = self.builder.symbol_table.get(name)
            hits.append({
                "rank": rank,
                "qname": name,
                "kind": symbol.kind,
                "location": f"{self._relative(symbol.file)}:{symbol.line_range[0]}",
                "score": round(row.score, 4),
                "pinned": name in pin_set,
                "legs": {
                    "stems": round(row.stems, 4), "capability": round(row.capability, 4),
                    "action": round(row.action, 4), "capability_source": row.capability_source,
                },
                "entry_context": self.entries.context(name) if symbol.kind != "class" else [],
            })
        return {"question": question,
                "intent": {**(resolved or {}), "source": source if resolved else None,
                           "route": route, "summary_weight": self.summary_weight},
                "hits": hits}
