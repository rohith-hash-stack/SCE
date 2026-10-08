"""Pool shaping: which candidates enter PRISM's Turn-1 manifest, decided from
the query's intent (never from a task-type label).

  blast_radius -> Design C list (transitive callers + downstream under the
                  budget), keeping downstream rows only at structural hop 1
  localization -> PRISM's default (downstream) manifest
  mixed/unknown -> Design C list, both directions

Only PRISM's public manifest builder is called; rows are filtered, never
rewritten, so every kept row is exactly what PRISM serialized.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from harness.experiments.production_routing.intent import classify

#: The manifest row roles PRISM writes for an upstream candidate.
UPSTREAM_ROLES = ("caller",)


@dataclass
class RoutedManifest:
    text: str
    universe: set[str]
    intent: str
    confidence: float
    policy: str
    evidence: dict = field(default_factory=dict)

    def rows(self) -> list[tuple[str, str]]:
        """`(qualified_name, role)` per manifest row."""
        return [(ln.split("|")[0], ln.split("|")[1]) for ln in self.text.split("\n")[1:-1] if ln.count("|") >= 1]

    def caller_fraction(self, seed: str) -> float | None:
        rows = [(q, r) for q, r in self.rows() if q != seed]
        return sum(r in UPSTREAM_ROLES for _, r in rows) / len(rows) if rows else None


def _filter_rows(text: str, keep: set[str]) -> str:
    lines = text.split("\n")
    body = [ln for ln in lines[1:-1] if ln.split("|", 1)[0] in keep]
    return "\n".join([lines[0], *body, lines[-1]])


def route_manifest(engine, seed: str, query: str, budget_tokens: int) -> RoutedManifest:
    """Build the routed Turn-1 manifest for `seed` from `query` alone."""
    from prism.packer.candidate_index import _real_call_chain_reachable

    intent, confidence, evidence = classify(query)
    if intent == "localization":
        text, universe = engine.build_candidate_manifest(seed)
        return RoutedManifest(text, set(universe), intent, confidence, "downstream", evidence)
    text, universe = engine.build_candidate_manifest(seed, direction="both", budget_tokens=budget_tokens)
    if intent != "blast_radius":
        return RoutedManifest(text, set(universe), intent, confidence, "both", evidence)
    hop1 = set(_real_call_chain_reachable(engine.builder, seed, max_hops=1))
    roles = {q: r for q, r in RoutedManifest(text, set(), "", 0.0, "").rows()}
    keep = {q for q in universe if q == seed or roles.get(q) in UPSTREAM_ROLES or q in hop1}
    return RoutedManifest(_filter_rows(text, keep), keep, intent, confidence, "upstream+hop1", evidence)


class RoutedEngine:
    """A PrismEngine stand-in for Arm 5: `build_candidate_manifest` routes by
    the current query (set by the arm before each retrieval) and ignores any
    direction/budget arguments the caller passes; everything else is the
    wrapped engine's own."""

    def __init__(self, engine, budget_tokens: int) -> None:
        self._engine = engine
        self._budget = budget_tokens
        self.query = ""
        self.last: RoutedManifest | None = None

    def build_candidate_manifest(self, seed: str, *args, **kwargs) -> tuple[str, set[str]]:
        self.last = route_manifest(self._engine, seed, self.query, self._budget)
        return self.last.text, set(self.last.universe)

    def __getattr__(self, name):
        return getattr(self._engine, name)
