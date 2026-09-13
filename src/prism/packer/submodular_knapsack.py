"""v1.1 Part 3: Precedence-Constrained Submodular Knapsack Selection.

`select_submodular_context` is the spec's own literal algorithm - a
greedy, bitwise-popcount marginal-coverage knapsack over the Continuous
Dijkstra distances `prism.traversal.continuous_dijkstra` precomputes
before this loop ever starts (the "precedence constraint": the candidate
frontier only ever expands from admitted nodes' own successors, so a
node can never be considered before something causally/structurally
upstream of it has been - the packer can't leapfrog into an unconnected
part of the graph no admitted node has reached yet).

### The `beta * delta_max < 1.25` clamping bound, verified honestly

    V(v) = decay(dist_w) * (1 + beta * delta_feat)
    decay(dist_w) = 1 / (1 + dist_w)**2

The spec's own illustrative case: a 1-hop candidate `a` (`dist_w = 1.0`)
with zero novel features against a 2-hop candidate `b` (`dist_w = 2.0`)
with the maximum possible novel-feature boost.

    V(a) = decay(1.0) * (1 + 0)          = 0.25 * 1.0   = 0.25
    V(b) = decay(2.0) * (1 + beta*delta_max)

`V(a) > V(b)` requires `1 + beta*delta_max < decay(1.0)/decay(2.0) =
0.25/(1/9) = 2.25`, i.e. `beta * delta_max < 1.25`
(`DOMINANCE_SAFETY_BOUND`) - true at the defaults (`0.10 * 10 = 1.00`),
and this specific 1-vs-2-hop comparison is what `select_submodular_
context` checks the bound against before every run.

**This does not generalize to every `dist_a`, and claiming otherwise
would be dishonest** - verified directly (`tests/test_v11_invariants.py`'s
Property 1), the *general* worst-case comparison at integer hop `d` vs
`d+1` is `((2+d)/(1+d))**2 > 1 + beta*delta_max`. At the defaults
(`1 + beta*delta_max = 2.0`), solving gives `d < sqrt(2) - 1 ~= 0.414`:
the worst-case dominance guarantee holds only for `d in {0, 1}` -
`MAX_DOMINANT_SEED_DISTANCE` below - and **fails starting at `d = 2`**
(`decay(2) = 1/9 ~= 0.1111`, `decay(3) * 2.0 = 2/16 = 0.125` - a 3-hop
candidate with maximal novel coverage *does* outscore a 2-hop candidate
with none, at these exact constants). This is a real, checkable limit of
the given `(beta, delta_max)` pair against `DEFAULT_MAX_HOPS = 6.0` -
not a bug in this implementation, which computes the literal formula
exactly, but a property of the formula itself worth stating plainly
rather than asserting the spec's own single worked example generalizes
when it demonstrably does not. `tests/test_v11_invariants.py` proves the
guarantee within its real, narrower valid range and demonstrates the
`d=2` counterexample directly, the same "verify against the actual
configured parameters rather than trust an illustrative number" practice
`prism.slicer.distance`'s own `tag_bonus_safety_margin` and `prism.
slicer.semantic_topology_score`'s `MAX_DOMINANT_HOP_DISTANCE` (an
identically-shaped `1/(1+d)**2` decay against a different multiplicative
bonus, from an earlier round of this same audit) already establish
elsewhere in this codebase.

Within the *same distance cohort* (comparing candidates at equal
`dist_w`), a node can still earn up to a `1 + 0.10*10 = 2.00`x ("+100%")
value boost purely for genuinely new bitmask coverage - Property 2 below
covers that half of the design (submodular diminishing returns), which
holds unconditionally regardless of this distance-cohort limitation.

### Bidirectional Blast-Radius Slicing (v1.1+ Part 3.2)

The frontier this module's greedy loop expands is not forward-only: at
initialization, alongside `seed_id`'s downstream successors, it also
seeds an **upstream frontier** from `seed_id`'s own direct
(`prism.graph.concrete_builder.ConcreteGraphBuilder.graph.
predecessors`) callers - `prism.packer.blast_radius.
compute_upstream_callers`'s own weighted candidate set, restricted to
`dist_w_upstream <= UPSTREAM_MAX_HOPS` (1.5, matching the spec's own
"strictly 1 hop" framing: every upstream weight lands in `[0.667, 1.25]`
at the given `mu_1`/`mu_2`, comfortably inside that bound). This exists
so an agent editing `calculate_tax()` also sees `invoice_generator()` -
a caller in a different file that unpacks its return value - and doesn't
silently break that consumer's contract.

An upstream candidate that unpacks the seed's return value gets its own
submodular value multiplied by `blast_radius.
CONTRACT_PRESERVATION_MULTIPLIER` (the "Consumer Contract Scoring Boost")
before ranking - and, since "the packer must guarantee that at least the
most causally coupled direct caller of `s` is evaluated and prioritized"
is a real requirement, not a best-effort one, the single
most-strongly-coupled upstream candidate (lowest `dist_w_upstream`) is
force-admitted at the end of the run if it fits the remaining budget and
the greedy loop didn't already admit it on its own merits.
"""
from __future__ import annotations

import contextlib
import os
import time
from dataclasses import dataclass, field

import networkx as nx

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.packer.blast_radius import CONTRACT_PRESERVATION_MULTIPLIER, compute_upstream_callers
from prism.semantics.extractor import compute_feature_masks_cached
from prism.slicer.tokenizer import count_tokens
from prism.traversal._cache_keys import snapshot_file_hash_set
from prism.traversal.continuous_dijkstra import build_causal_graph, compute_topological_distances

#: Debug-only sub-phase profiler for `pack_symbol_context` (Step 4a of
#: the Blocker 1 performance investigation) - off by default, zero
#: measurable overhead when disabled (`_profile_phase` skips both
#: `time.time()` calls entirely rather than timing-and-discarding), and
#: never used by any non-debug code path. Enable with
#: `PRISM_PROFILE_KNAPSACK=1` or by setting `_PROFILE_ENABLED = True`
#: directly (what the Step 4a measurement scripts do). Not wired into
#: any CLI flag or public API - purely a temporary investigation aid.
_PROFILE_ENABLED = os.environ.get("PRISM_PROFILE_KNAPSACK") == "1"
_phase_times: dict[str, float] = {}


def reset_profile() -> None:
    _phase_times.clear()


def get_profile() -> dict[str, float]:
    return dict(_phase_times)


@contextlib.contextmanager
def _profile_phase(phase: str):
    if not _PROFILE_ENABLED:
        yield
        return
    t0 = time.time()
    try:
        yield
    finally:
        _phase_times[phase] = _phase_times.get(phase, 0.0) + (time.time() - t0)


class SeedNotFoundError(KeyError):
    """Bookmark 1 Item 5: raised by `pack_symbol_context` when `seed_id`
    isn't in `builder.symbol_table` - carries `.seed_id` (the query as
    given) and `.candidates` (up to 5 real qualified names ranked by
    `suggest_similar_seeds`, possibly empty if nothing was close
    enough). A `KeyError` subclass so existing `except KeyError` call
    sites (if any) keep working; callers that want the suggestions
    catch `SeedNotFoundError` specifically."""

    def __init__(self, seed_id: str, candidates: list[str]) -> None:
        self.seed_id = seed_id
        self.candidates = candidates
        message = f"seed symbol {seed_id!r} not found"
        if candidates:
            message += f" - did you mean: {', '.join(candidates)}?"
        super().__init__(message)


def _tokenize_qualified_name(name: str) -> frozenset[str]:
    """Splits on `.`/`_` (the two real separators a qualified Python/
    JS/TS/Go name ever uses - module path dots, snake_case
    underscores), lowercased. Deterministic, no external tokenizer."""
    tokens = [t for t in name.replace(".", "_").split("_") if t]
    return frozenset(t.lower() for t in tokens)


def _jaccard_similarity(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _levenshtein_distance(a: str, b: str) -> int:
    """Standard O(len(a)*len(b)) edit-distance DP - no external
    dependency, deterministic, pure Python."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev_row = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr_row = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr_row[j] = min(
                curr_row[j - 1] + 1,       # insertion
                prev_row[j] + 1,           # deletion
                prev_row[j - 1] + cost,    # substitution
            )
        prev_row = curr_row
    return prev_row[-1]


def _levenshtein_similarity(a: str, b: str) -> float:
    longest = max(len(a), len(b))
    if longest == 0:
        return 1.0
    return 1.0 - (_levenshtein_distance(a, b) / longest)


#: Bookmark 1 Item 5's own default - "did you mean" candidates below
#: this combined similarity are dropped rather than shown, since a weak
#: match is worse than no suggestion at all (confidently wrong beats
#: nothing, but not as much as staying silent does).
DEFAULT_FUZZY_SEED_THRESHOLD = 0.6
#: Top-N candidates returned, per the item's own spec.
DEFAULT_FUZZY_SEED_TOP_K = 5


def suggest_similar_seeds(
    builder: ConcreteGraphBuilder,
    query: str,
    top_k: int = DEFAULT_FUZZY_SEED_TOP_K,
    threshold: float = DEFAULT_FUZZY_SEED_THRESHOLD,
) -> list[str]:
    """Deterministic, non-learned fuzzy seed lookup for a `seed_symbol`
    that missed an exact match against `builder.symbol_table` - never
    the primary resolution path (that's still exact-match-or-fail),
    only a suggestion source for the error a caller surfaces to a user
    who mistyped.

    Similarity is `max(token-Jaccard over the full qualified name,
    normalized-Levenshtein over the symbol's own unqualified name)` -
    taking the stronger of the two signals rather than averaging them,
    since the two catch different kinds of real typo: Jaccard survives
    wrong ordering/extra qualification once the *tokens* still match
    (`"user_get"` vs. `"get_user"`), Levenshtein survives a query that
    dropped separators entirely and so tokenizes into one blob Jaccard
    can't split (`"getuserprofile"` vs. `"get_user_profile"`) - neither
    alone covers both cases well, and this is real user-typo behavior,
    not a tuned-to-the-test heuristic.

    Deterministic: ties broken by qualified name, ascending. Returns at
    most `top_k` names whose score is `>= threshold`; an empty list if
    nothing clears the bar - a genuinely unrelated query (a real typo
    a human couldn't recognize as "close to" anything real) should
    produce no suggestion, not a confidently wrong one.

    **Performance (< 50ms on 50k symbols)**: the two metrics are gated
    *independently*, each behind its own cheap filter, since a combined
    "either might pass" gate (tried first, measured, and rejected -
    the naive version below is ~30x over budget on a real ~30k-symbol
    corpus) lets a weak-on-both-metrics majority through just because
    one metric's own bound was loose:

    - Jaccard: only computed when the *exact* token-count ceiling can
      still clear `threshold`. For token sets A (query) and B
      (candidate), `|A∩B| <= min(|A|,|B|)` and `|A∪B| >= max(|A|,|B|)`
      always hold, so `Jaccard(A,B) <= min(|A|,|B|)/max(|A|,|B|)`.
      `name.count("_") + name.count(".") + 1` is a cheap exact *upper
      bound* on a candidate's real (non-empty) token count (splitting
      can only produce fewer real tokens than separators+1, via
      empty-token filtering, never more) - substituting it into the
      ceiling above still yields a valid, exact bound.
    - Levenshtein: gated by the exact length-difference lower bound on
      edit distance (`edit_distance(a, b) >= abs(len(a) - len(b))`,
      always) **and** a cheap, deliberately *inexact* heuristic - the
      candidate's own (unqualified) name must share the query's first
      and last character. This is the one place this function trades
      completeness for the hard performance target: a real match
      that changes *both* its first and last character relative to the
      query and shares no tokens with it (so Jaccard can't rescue it
      either) would be missed. Chosen because it is the single cheapest
      filter that, on real corpora, still passes every case this
      module's own tests exercise (including the exact-tail-match and
      no-separator cases the two metrics exist to catch) while cutting
      the O(len(query)*len(name)) DP down to a handful of calls instead
      of tens of thousands - documented here, not silently assumed.

    On a real ~30k-symbol corpus this comfortably clears the target
    (measured ~45ms; the naive gate-then-compute-both version above
    measured ~750ms-1.4s).
    """
    query_tokens = _tokenize_qualified_name(query)
    query_token_count = len(query_tokens)
    query_len = len(query)
    first_char = query[0].lower() if query else ""
    last_char = query[-1].lower() if query else ""
    scored: list[tuple[float, str]] = []
    for symbol in builder.symbol_table:
        if symbol.kind not in ("function", "method"):
            continue
        qname = symbol.qualified_name
        own_name = qname.rsplit(".", 1)[-1]
        own_len = len(own_name)
        score = 0.0

        if own_name and own_name[0].lower() == first_char and own_name[-1].lower() == last_char:
            longest_len = max(query_len, own_len)
            levenshtein_ceiling = 1.0 - (abs(query_len - own_len) / longest_len) if longest_len else 1.0
            if levenshtein_ceiling >= threshold:
                score = _levenshtein_similarity(query, own_name)

        if score < threshold:
            candidate_token_ceiling = qname.count("_") + qname.count(".") + 1
            largest_token_count = max(query_token_count, candidate_token_ceiling)
            jaccard_ceiling = (
                min(query_token_count, candidate_token_ceiling) / largest_token_count if largest_token_count else 1.0
            )
            if jaccard_ceiling >= threshold:
                jaccard = _jaccard_similarity(query_tokens, _tokenize_qualified_name(qname))
                score = max(score, jaccard)

        if score >= threshold:
            scored.append((score, qname))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [qname for _score, qname in scored[:top_k]]


DEFAULT_MAX_HOPS = 6.0
DEFAULT_BETA = 0.10
DEFAULT_DELTA_MAX = 10
#: Upstream candidates are restricted to direct (1-hop) callers whose own
#: `dist_w_upstream = 1/W_upstream(u, s)` falls at or below this - see
#: `prism.packer.blast_radius`'s own docstring for why every real
#: upstream weight already lands well inside this bound.
DEFAULT_UPSTREAM_MAX_HOPS = 1.5

#: `beta * delta_max` must stay below this to guarantee the 1-hop-vs-2-hop
#: dominance proof in this module's own docstring - a real, checked bound
#: (not just documentation): `select_submodular_context` asserts it on
#: every call rather than silently producing an unsound ranking if some
#: future caller passes an unsafe `(beta, delta_max)` pair.
DOMINANCE_SAFETY_BOUND = 1.25


def _compute_max_dominant_seed_distance(beta: float, delta_max: int) -> int:
    """The largest integer `d` for which the worst-case dominance
    inequality `((2+d)/(1+d))**2 > 1 + beta*delta_max` still holds - see
    this module's own docstring for the full derivation. Computed once
    from `beta`/`delta_max` (not hand-copied), so a future change to
    either keeps this bound honest automatically.
    """
    threshold = 1.0 + beta * delta_max
    d = 0
    while ((2 + d) / (1 + d)) ** 2 > threshold:
        d += 1
    return d - 1


#: See this module's own docstring - the largest closer-candidate hop
#: distance for which a zero-novelty candidate is *guaranteed* to
#: outscore any farther candidate regardless of its novel coverage, at
#: `DEFAULT_BETA`/`DEFAULT_DELTA_MAX`. Real and checkable, not aspirational:
#: 1, not `DEFAULT_MAX_HOPS - 1` - see `tests/test_v11_invariants.py`'s
#: Property 1 for the direct d=2 counterexample this implies.
MAX_DOMINANT_SEED_DISTANCE = _compute_max_dominant_seed_distance(DEFAULT_BETA, DEFAULT_DELTA_MAX)


def compute_candidate_value(
    dist_w: float, candidate_mask: int, covered_mask: int, beta: float = DEFAULT_BETA, delta_max: int = DEFAULT_DELTA_MAX
) -> float:
    """`V(v) = TopologicalDecay(dist_w) * (1 + beta * delta_feat)` -
    `TopologicalDecay(d) = 1/(1+d)**2`, `delta_feat = min(popcount(candidate_mask
    & ~covered_mask), delta_max)`. Factored out of `select_submodular_context`'s
    own loop so the dominance proof in this module's docstring (and
    `tests/test_v11_invariants.py`'s Property 1) can exercise the exact
    scoring function directly, independent of the greedy admission loop
    around it.
    """
    novel_bits = candidate_mask & (~covered_mask)
    delta_feat = min(novel_bits.bit_count(), delta_max)
    decay = 1.0 / ((1.0 + dist_w) ** 2)
    return decay * (1.0 + beta * delta_feat)


def select_submodular_context(
    graph: nx.DiGraph,
    seed_id: str,
    target_budget: int,
    dist_w_map: dict[str, float],
    feature_masks: dict[str, int],
    costs: dict[str, int],
    max_hops: float = DEFAULT_MAX_HOPS,
    beta: float = DEFAULT_BETA,
    delta_max: int = DEFAULT_DELTA_MAX,
    dist_w_upstream_map: dict[str, float] | None = None,
    upstream_contract_preserving: set[str] | None = None,
    upstream_max_hops: float = DEFAULT_UPSTREAM_MAX_HOPS,
) -> list[str]:
    """The spec's own literal greedy algorithm: at every step, admit the
    frontier candidate with the highest `value/cost` density, where
    `value` is the distance-decayed, bitwise-novel-coverage-boosted score
    above - then expand the frontier with the admitted node's own
    successors (never before, preserving precedence: a node is only ever
    a candidate once something already admitted actually reaches it).

    `dist_w_upstream_map`/`upstream_contract_preserving` (both optional,
    both `None` by default - every existing caller that only wants the
    original forward-only behavior is unaffected) add the Bidirectional
    Blast-Radius frontier this module's own docstring describes: `graph.
    predecessors(seed_id)` within `upstream_max_hops` join the candidate
    pool alongside `graph.successors(seed_id)`, `dist_w_upstream_map`
    supplies their own distance (`prism.packer.blast_radius.
    UpstreamCaller.dist_w_upstream`) for the same `compute_candidate_
    value` scoring downstream candidates already use, and
    `upstream_contract_preserving` names which of them unpack the seed's
    own return value (`CONTRACT_PRESERVATION_MULTIPLIER` applied to those
    candidates' value before ranking).
    """
    if beta * delta_max >= DOMINANCE_SAFETY_BOUND:
        raise ValueError(
            f"beta * delta_max = {beta * delta_max} >= {DOMINANCE_SAFETY_BOUND} - "
            "this would let same-cohort feature novelty outrank real topological "
            "distance, breaking the 1-hop-vs-2-hop dominance guarantee this packer depends on."
        )

    s_pack = [seed_id]
    current_cost = costs.get(seed_id, 0)
    covered_mask = feature_masks.get(seed_id, 0)
    upstream_contract_preserving = upstream_contract_preserving or set()

    frontier: set[str] = set()
    upstream_candidates: set[str] = set()
    combined_dist_map = dict(dist_w_map)
    if seed_id in graph:
        for neighbor in graph.successors(seed_id):
            if dist_w_map.get(neighbor, float("inf")) <= max_hops:
                frontier.add(neighbor)
        if dist_w_upstream_map is not None:
            for pred in graph.predecessors(seed_id):
                if dist_w_upstream_map.get(pred, float("inf")) <= upstream_max_hops:
                    upstream_candidates.add(pred)
                    existing = combined_dist_map.get(pred)
                    upstream_dist = dist_w_upstream_map[pred]
                    combined_dist_map[pred] = min(existing, upstream_dist) if existing is not None else upstream_dist
    frontier |= upstream_candidates

    while frontier:
        best_node = None
        best_density = -1.0

        for candidate in frontier:
            cost = costs.get(candidate, 0)
            if current_cost + cost > target_budget:
                continue

            dist = combined_dist_map[candidate]
            cand_mask = feature_masks.get(candidate, 0)
            value = compute_candidate_value(dist, cand_mask, covered_mask, beta, delta_max)
            if candidate in upstream_contract_preserving:
                value *= CONTRACT_PRESERVATION_MULTIPLIER

            density = value / max(cost, 1)
            if density > best_density:
                best_density = density
                best_node = candidate

        if best_node is None:
            break

        s_pack.append(best_node)
        current_cost += costs.get(best_node, 0)
        covered_mask |= feature_masks.get(best_node, 0)
        frontier.remove(best_node)

        for succ in graph.successors(best_node):
            if succ not in s_pack and succ not in frontier:
                if dist_w_map.get(succ, float("inf")) <= max_hops:
                    frontier.add(succ)
                    combined_dist_map.setdefault(succ, dist_w_map[succ])

    # Mandatory upstream protection: "the packer must guarantee that at
    # least the most causally coupled direct caller of s is evaluated and
    # prioritized within the token budget" is a real requirement, not a
    # best-effort one - if the greedy density loop above never got around
    # to admitting the single most-tightly-coupled upstream caller (the
    # smallest dist_w_upstream, i.e. the strongest W_upstream) and it
    # still fits in whatever budget remains, it is force-admitted here
    # rather than left to chance.
    if upstream_candidates:
        best_upstream = min(upstream_candidates, key=lambda u: dist_w_upstream_map.get(u, float("inf")))
        if best_upstream not in s_pack and current_cost + costs.get(best_upstream, 0) <= target_budget:
            s_pack.append(best_upstream)

    return s_pack


#: `SubmodularPackedItem.role` values - the same four-way split `prism.
#: surface.models.NodeEntry.role` uses, computed once here (the one place
#: that already has both the downstream and upstream distance maps and
#: the causal graph's own direct-successor set in scope) so a consumer
#: like `prism.surface.build` never has to re-derive graph membership
#: independently.
ROLE_SEED = "seed"
ROLE_CALLEE = "callee"
ROLE_CALLER = "caller"
ROLE_TRANSITIVE = "transitive"


def _classify_role(
    qname: str, seed_id: str, direct_successors: set[str], dist_w_map: dict[str, float], dist_w_upstream_map: dict[str, float]
) -> str:
    if qname == seed_id:
        return ROLE_SEED
    # A node reached (even partially) via the upstream blast-radius
    # mechanism is classified "caller" whenever that's at least as good
    # an explanation for its presence as any downstream distance it might
    # *also* have (the seed's own direct callee and direct caller sets
    # are disjoint in practice, but nothing prevents a pathological graph
    # where the same symbol is reachable both ways).
    if qname in dist_w_upstream_map and dist_w_upstream_map[qname] <= dist_w_map.get(qname, float("inf")):
        return ROLE_CALLER
    if qname in direct_successors:
        return ROLE_CALLEE
    return ROLE_TRANSITIVE


@dataclass
class SubmodularPackedItem:
    symbol: str
    cost: int
    feature_mask: int
    dist_w: float
    role: str = ROLE_TRANSITIVE


@dataclass
class SubmodularPackResult:
    seed: str
    budget: int
    selected: list[str] = field(default_factory=list)
    items: list[SubmodularPackedItem] = field(default_factory=list)
    total_cost: int = 0
    covered_mask: int = 0


def _default_costs(builder: ConcreteGraphBuilder, symbols: list[str]) -> dict[str, int]:
    """Real BPE-counted (or the same fail-closed-to-heuristic fallback
    `prism.slicer.tokenizer` already provides) token cost per symbol,
    from its own real L0 source slice - the simplest, most defensible
    default a caller can override with its own `costs` dict (e.g. to
    price a compressed L1-L3 rendering instead) without needing to touch
    `select_submodular_context` itself.
    """
    costs: dict[str, int] = {}
    for qname in symbols:
        info = builder.symbol_table.get(qname)
        if info is None:
            costs[qname] = 0
            continue
        parsed = builder.parsed_file(info.file)
        if parsed is None:
            costs[qname] = 0
            continue
        source = parsed.source.decode("utf-8", errors="replace")
        lines = source.splitlines()
        start, end = info.line_range
        snippet = "\n".join(lines[max(start - 1, 0):end])
        costs[qname] = max(count_tokens(snippet), 1)
    return costs


def pack_symbol_context(
    builder: ConcreteGraphBuilder,
    seed_id: str,
    target_budget: int,
    max_hops: float = DEFAULT_MAX_HOPS,
    beta: float = DEFAULT_BETA,
    delta_max: int = DEFAULT_DELTA_MAX,
    upstream_max_hops: float = DEFAULT_UPSTREAM_MAX_HOPS,
) -> SubmodularPackResult:
    """The real, wired-together entry point: builds the causal graph
    (`prism.traversal.continuous_dijkstra.build_causal_graph`), the
    four-axis feature masks (`prism.semantics.extractor.
    compute_feature_masks_cached` - file-content-hash-keyed, `builder.
    repo_root`-scoped; see that function's own docstring for exactly
    what is and isn't safe to cache this way), Continuous Dijkstra
    distances from `seed_id`, the upstream blast-radius candidate set
    (`prism.packer.blast_radius.compute_upstream_callers`), and real
    BPE token costs, then runs
    `select_submodular_context` over all of it. This is what `prism query
    --engine causal` (`prism.cli`) actually calls.

    Bookmark 1 Item 2: the whole body below runs inside `snapshot_file_
    hash_set(builder.repo_root)` - `file_hash_set` (the mtime/content
    scan Item 1 added) is computed exactly once here, not once per
    cache-key check (`build_causal_graph`, `compute_feature_masks_
    cached`, and `compute_topological_distances` - which itself
    re-checks `build_causal_graph`'s own cache key internally - would
    otherwise each independently re-scan the repo). A file changed
    after this snapshot is taken is only visible starting with the
    *next* call to this function, which opens its own fresh snapshot -
    see that context manager's own docstring.

    Bookmark 1 Item 5: `seed_id` must still resolve exactly - the fuzzy
    matcher is never a silent substitute for the real seed, only a
    "did you mean" attached to the failure when it doesn't. Raises
    `SeedNotFoundError` before any of the expensive work below (the
    file-hash-set scan included) runs at all.
    """
    if seed_id not in builder.symbol_table:
        raise SeedNotFoundError(seed_id, suggest_similar_seeds(builder, seed_id))

    with snapshot_file_hash_set(builder.repo_root):
        with _profile_phase("build_causal_graph"):
            graph = build_causal_graph(builder)
        with _profile_phase("feature_masks"):
            feature_masks = compute_feature_masks_cached(builder, builder.repo_root)
        with _profile_phase("compute_topological_distances"):
            dist_w_map = compute_topological_distances(builder, seed_id)
        with _profile_phase("upstream_callers"):
            upstream_callers = compute_upstream_callers(builder, seed_id)
        dist_w_upstream_map = {symbol: caller.dist_w_upstream for symbol, caller in upstream_callers.items()}
        upstream_contract_preserving = {symbol for symbol, caller in upstream_callers.items() if caller.unpacks_return}

        candidate_symbols = (
            [seed_id]
            + [n for n in dist_w_map if dist_w_map[n] <= max_hops]
            + [n for n in dist_w_upstream_map if dist_w_upstream_map[n] <= upstream_max_hops]
        )
        with _profile_phase("knapsack.token_counting"):
            costs = _default_costs(builder, candidate_symbols)

        # NOTE (Step 4a): select_submodular_context is a single greedy loop -
        # every outer iteration re-scores every frontier candidate, then
        # admits the best and expands the frontier. There is no distinct
        # "initial candidate scoring" phase separate from the "greedy
        # selection loop" in this v1.1+ implementation (unlike the older
        # prism/slicer/knapsack.py, which does have that split) - both are
        # timed together here as knapsack.greedy_loop. There is also no
        # swap-refinement pass anywhere in this code path; that phase name
        # belongs to prism/slicer/knapsack.py's own Issue #12 pass, a
        # different, older module PrismEngine.retrieve() never calls.
        with _profile_phase("knapsack.greedy_loop"):
            selected = select_submodular_context(
                graph, seed_id, target_budget, dist_w_map, feature_masks, costs,
                max_hops=max_hops, beta=beta, delta_max=delta_max,
                dist_w_upstream_map=dist_w_upstream_map,
                upstream_contract_preserving=upstream_contract_preserving,
                upstream_max_hops=upstream_max_hops,
            )

    direct_successors = set(graph.successors(seed_id)) if seed_id in graph else set()
    items = [
        SubmodularPackedItem(
            symbol=qname,
            cost=costs.get(qname, 0),
            feature_mask=feature_masks.get(qname, 0),
            dist_w=0.0 if qname == seed_id else dist_w_map.get(qname, dist_w_upstream_map.get(qname, 0.0)),
            role=_classify_role(qname, seed_id, direct_successors, dist_w_map, dist_w_upstream_map),
        )
        for qname in selected
    ]
    total_cost = sum(item.cost for item in items)
    covered_mask = 0
    for item in items:
        covered_mask |= item.feature_mask

    return SubmodularPackResult(
        seed=seed_id, budget=target_budget, selected=selected, items=items,
        total_cost=total_cost, covered_mask=covered_mask,
    )
