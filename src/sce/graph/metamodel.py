"""Top layer: the Semantic Metamodel (G_T).

A small, fixed vocabulary of architectural/behavioral tags (`Sigma_T`) plus
directed relations between them (`REQUIRES_BEFORE`, `TRIGGERS`,
`CONFLICTS_WITH`, `IS_A`). This is intentionally static: the metamodel is
part of the engine's deterministic contract, not something inferred from a
given repository.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import networkx as nx


class TagRelation(str, Enum):
    REQUIRES_BEFORE = "REQUIRES_BEFORE"
    TRIGGERS = "TRIGGERS"
    CONFLICTS_WITH = "CONFLICTS_WITH"
    IS_A = "IS_A"


@dataclass(frozen=True)
class Tag:
    name: str
    description: str


# Default penalty distance in G_T for tag sets that share no path (or where
# either side is untagged). Mirrors the token-budget "max resolution decay"
# treatment used by the slicer's distance metric.
MAX_TAG_DISTANCE = 5.0


class SemanticMetamodel:
    """The fixed tag vocabulary `V_T` and relation graph `G_T`."""

    def __init__(self) -> None:
        self.graph = nx.DiGraph()
        self._initialize_metamodel()

    def _initialize_metamodel(self) -> None:
        tags = [
            Tag("#route_handler", "External entrypoint"),
            Tag("#auth_guard", "Validates identity/permissions"),
            Tag("#db_read", "Queries persistence layer"),
            Tag("#db_write", "Mutates persistence layer"),
            Tag("#payment_charge", "Transacts financial value"),
            Tag("#external_io", "Outbound network call"),
            Tag("#state_mutation", "Mutates instance/global state"),
            Tag("#event_producer", "Publishes message to queue"),
            Tag("#event_consumer", "Handles message from queue"),
        ]
        for tag in tags:
            self.graph.add_node(tag.name, data=tag)

        # Invariant rules: architectural relationships between tags.
        self.graph.add_edge("#db_write", "#auth_guard", relation=TagRelation.REQUIRES_BEFORE)
        self.graph.add_edge("#payment_charge", "#auth_guard", relation=TagRelation.REQUIRES_BEFORE)
        self.graph.add_edge("#payment_charge", "#db_write", relation=TagRelation.TRIGGERS)
        self.graph.add_edge("#event_producer", "#event_consumer", relation=TagRelation.TRIGGERS)
        self.graph.add_edge("#db_write", "#state_mutation", relation=TagRelation.TRIGGERS)
        self.graph.add_edge("#route_handler", "#auth_guard", relation=TagRelation.REQUIRES_BEFORE)

    def tags(self) -> list[str]:
        return list(self.graph.nodes)

    def get_tag_distance(self, tags_a: set[str], tags_b: set[str]) -> float:
        """Minimum hop distance between two tag sets in `G_T` (undirected)."""
        if not tags_a or not tags_b:
            return MAX_TAG_DISTANCE
        if tags_a.intersection(tags_b):
            return 0.0

        undirected = self.graph.to_undirected()
        min_dist = float("inf")
        for ta in tags_a:
            for tb in tags_b:
                try:
                    dist = nx.shortest_path_length(undirected, ta, tb)
                    if dist < min_dist:
                        min_dist = dist
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    continue

        return min_dist if min_dist != float("inf") else MAX_TAG_DISTANCE
