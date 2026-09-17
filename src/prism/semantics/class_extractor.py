"""Class semantics extraction, substance synthesis, and role classification."""
import math
from dataclasses import dataclass
from enum import Enum


class Role(str, Enum):
    CORE_DOMAIN = "core_domain"
    COORDINATOR = "coordinator"
    ADAPTER = "adapter"
    BRIDGE = "bridge"
    LEAF_UTILITY = "leaf_utility"
    ENTRYPOINT = "entrypoint"
    FACADE = "facade"


@dataclass(frozen=True)
class MethodSymbol:
    name: str
    substance_tags: set[str]


def extract_class_substance(methods: list[MethodSymbol]) -> set[str]:
    """Bounded substance tag set derived from methods.

    Formula: required_count = max(1, floor(sqrt(len(methods))))

    Preserves multi-subsystem indicators for small classes (n <= 3),
    prunes transient method-level noise in large classes.
    """
    if not methods:
        return set()

    n = len(methods)
    required = max(1, math.isqrt(n))

    tag_counts: dict[str, int] = {}
    for m in methods:
        for tag in m.substance_tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    return {tag for tag, count in tag_counts.items() if count >= required}


def classify_class_role(fan_in: int, fan_out: int) -> Role | None:
    """Disjoint topological partitioning of class roles.

    Invariant: no (fan_in, fan_out) coordinate matches more than one role.
    """
    if fan_in >= 5:
        if fan_out <= 1:
            return Role.LEAF_UTILITY
        if fan_out <= 2:
            return Role.CORE_DOMAIN
        return Role.BRIDGE

    if fan_in <= 2:
        if fan_out >= 8:
            return Role.COORDINATOR
        return None

    if 3 <= fan_in <= 4:
        if 3 <= fan_out <= 7:
            return Role.ADAPTER
        return None

    return None
