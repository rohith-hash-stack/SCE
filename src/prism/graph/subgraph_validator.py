"""Subgraph Validator: a deterministic, AST/symbol-grounded gatekeeper
between Prism's high-recall candidate extraction (multi-hypothesis anchor
seeding + traversal) and any consumer that needs a structurally-verified
closure - a Transformation Action DAG planner, a mutation engine, or a
serialization layer. No LLMs, no embeddings, no probabilistic heuristics:
every check here is a deterministic lookup or bounded traversal against
`ConcreteGraphBuilder`'s own real graph, symbol table, and behavioral
contracts.

**Component reuse, not reinvention.** A `CandidateSubgraph` is a set of
node IDs *into* `ConcreteGraphBuilder.graph` (plus the anchor subset), not
a parallel `CodeNode`/`CodeEdge` object model. `ConcreteGraphBuilder.graph`
already carries every field a hand-rolled node/edge type would duplicate
(file, span-equivalent via `def_node`, kind via `symbol_table`, call-site
context via edge attributes) - and Pass 2's own two-pass linker
(`concrete_builder.py`'s own module docstring) already resolves aliased
imports/re-exports to a single canonical qualified name *before* any node
or edge ever enters `builder.graph`. A design that instead re-derived
`CodeNode`/`CodeEdge` from scratch would risk exactly the drift a second,
independently-shaped copy of the same information always risks, and would
have to re-implement canonicalization the linker has already done. See
`_check_staleness`'s own docstring for the one place this module still has
real, novel work to do that nothing upstream already covers.

**Grounding is a 4-state model here, not the 3 states a naive spec might
assume.** `ConcreteGraphBuilder` already distinguishes, via real node
attributes:
  - a **local, defined-in-repo** symbol (present in `builder.symbol_table`)
  - an **external** symbol - a real, resolved name outside the repo
    (`requests.get`, `os.path.join`) - `external=True`, no sentinel type
  - a **dynamic-dispatch hazard** - `getattr`/`eval`/subscript-dispatch -
    `sentinel_type="dynamic_edge"` (`prism.graph.call_site.
    DynamicEdgeSentinel`)
  - an **ambiguous polysemy** sentinel - a bare call whose simple name
    matched multiple repo-wide candidates below the disambiguation
    confidence threshold - `sentinel_type="unresolved_polymorphic"`
    (`prism.graph.symbol_table.unresolved_polymorphic_node_id`)
Collapsing "external" into "unbound" would make every ordinary stdlib/
third-party call in a candidate subgraph a false rejection risk; treating
"ambiguous" as fully bound would silently pretend a real resolution gap
was resolved. `GroundingState` below keeps all four distinct.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import networkx as nx

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.contracts import BehavioralContract
from prism.parser.lang_config import ASSIGNMENT_NODE_TYPE, IDENTIFIER_NODE_TYPES, iter_scoped_nodes
from prism.parser.tree_sitter_loader import LanguageID, node_text

#: A node whose real, observed in-degree in `builder.graph` exceeds this
#: is treated as a hub and absorbs incoming attribution without
#: propagating further outbound traversal (Step 3). Not derived from any
#: paper's centrality formula - see the module docstring for why a
#: precomputed relative threshold, not live betweenness centrality, is
#: the only shape that fits a sub-millisecond budget; 50 is a
#: conservative starting default a caller is expected to tune per
#: corpus size via `SubgraphValidator(hub_in_degree_threshold=...)`.
DEFAULT_HUB_IN_DEGREE_THRESHOLD = 50

#: Relations `builder.graph` actually uses that represent a real
#: mutation-relevant structural dependency between two symbols - reused
#: directly rather than re-deriving a second relation taxonomy. Deliberately
#: narrower than `concrete_builder.TRAVERSABLE_RELATIONS` (excludes
#: EXTENDS/IMPLEMENTS/EMBEDS/READS_STATE): a mutation-dependency cycle is
#: about two symbols each needing the other's call site patched, which is
#: a CALLS/INSTANTIATES/OVERRIDES property, not an inheritance-shape one.
_MUTATION_DEPENDENCY_RELATIONS = frozenset({"CALLS", "INSTANTIATES", "OVERRIDES"})

#: Relations counted as "a real usage/reference" of a mutation target for
#: Step 4's usage-closure check.
_USAGE_RELATIONS = frozenset({"CALLS", "INSTANTIATES", "OVERRIDES"})


class GroundingState(str, Enum):
    BOUND = "BOUND"
    EXTERNAL = "EXTERNAL"
    DYNAMIC_UNRESOLVED = "DYNAMIC_UNRESOLVED"
    UNBOUND = "UNBOUND"


class ViolationType(str, Enum):
    UNRESOLVED_ANCHOR = "UNRESOLVED_ANCHOR"
    INCOMPLETE_USAGE_CLOSURE = "INCOMPLETE_USAGE_CLOSURE"
    MUTATION_CYCLE_DETECTED = "MUTATION_CYCLE_DETECTED"
    SCOPE_SHADOWING_COLLISION = "SCOPE_SHADOWING_COLLISION"
    SIGNATURE_MISMATCH = "SIGNATURE_MISMATCH"
    STALE_FILE_STATE = "STALE_FILE_STATE"
    VISIBILITY_VIOLATION = "VISIBILITY_VIOLATION"
    DISCONNECTED_ISLAND = "DISCONNECTED_ISLAND"


@dataclass(frozen=True)
class ValidationViolation:
    violation_type: ViolationType
    symbol_id: Optional[str]
    file_path: Optional[str]
    details: str
    blocking_mutation: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "violation_type": self.violation_type.value,
            "symbol_id": self.symbol_id,
            "file_path": self.file_path,
            "details": self.details,
            "blocking_mutation": self.blocking_mutation,
        }


@dataclass(frozen=True)
class MutationIntent:
    target_symbol_id: str
    operation: str  # "RENAME" | "ADD_PARAM" | "MOVE" | "DELETE"
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BudgetLimits:
    max_nodes: Optional[int] = None
    max_depth: Optional[int] = None


@dataclass(frozen=True)
class CandidateSubgraph:
    """`node_ids` are IDs into `ConcreteGraphBuilder.graph` - real
    definitions, external references, or dynamic/ambiguous sentinels -
    never a parallel node representation. `anchor_ids` must be a subset
    of `node_ids`."""

    anchor_ids: tuple[str, ...]
    node_ids: frozenset[str]
    budget_limits: Optional[BudgetLimits] = None


@dataclass
class ValidatedGraphResult:
    is_valid: bool
    sanitized_nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    sanitized_edges: list[dict[str, Any]] = field(default_factory=list)
    dynamic_boundary_nodes: set[str] = field(default_factory=set)
    violations: list[ValidationViolation] = field(default_factory=list)

    @property
    def is_mutation_safe(self) -> bool:
        return self.is_valid and not any(v.blocking_mutation for v in self.violations)


def _sorted_violations(violations: list[ValidationViolation]) -> list[ValidationViolation]:
    """Step 6's determinism invariant: violations sorted by
    `(violation_type, file_path, symbol_id)`, `None` sorting before any
    real string so a run is never order-dependent on dict/set iteration."""
    return sorted(
        violations,
        key=lambda v: (v.violation_type.value, v.file_path or "", v.symbol_id or ""),
    )


class SubgraphValidator:
    """Deterministic gatekeeper composing `ConcreteGraphBuilder` (grounding,
    reachability substrate) and `BehavioralContract` (signature/visibility
    checks) - see the module docstring for why neither is reimplemented
    here."""

    def __init__(
        self,
        builder: ConcreteGraphBuilder,
        contracts: Optional[dict[str, BehavioralContract]] = None,
        hub_in_degree_threshold: int = DEFAULT_HUB_IN_DEGREE_THRESHOLD,
        hub_allowlist: frozenset[str] = frozenset(),
    ) -> None:
        self.builder = builder
        self.contracts = contracts if contracts is not None else {}
        self.hub_in_degree_threshold = hub_in_degree_threshold
        self.hub_allowlist = hub_allowlist

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #
    def validate(
        self,
        candidate: CandidateSubgraph,
        mutation_intents: Optional[list[MutationIntent]] = None,
    ) -> ValidatedGraphResult:
        intents = mutation_intents or []

        # Step 1: staleness. Blocking and checked first - every later step
        # reads span/AST/contract data that a stale file would make wrong.
        stale = self._check_staleness(candidate)
        if stale:
            return ValidatedGraphResult(is_valid=False, violations=_sorted_violations(stale))

        # Step 2: three-state grounding. A primary anchor that is UNBOUND
        # rejects the whole graph immediately, per spec.
        grounding, dynamic_boundary = self._classify_grounding(candidate)
        anchor_violations = [
            ValidationViolation(
                ViolationType.UNRESOLVED_ANCHOR,
                anchor_id,
                None,
                f"primary anchor '{anchor_id}' could not be grounded against the concrete graph "
                "or symbol table (unknown symbol, not an external reference, not a recorded "
                "dynamic-dispatch/ambiguity sentinel)",
            )
            for anchor_id in candidate.anchor_ids
            if grounding.get(anchor_id) is GroundingState.UNBOUND
        ]
        if anchor_violations:
            return ValidatedGraphResult(is_valid=False, violations=_sorted_violations(anchor_violations))

        # Step 3: absorbing-boundary hub damping + reachability/island pruning.
        sanitized_ids, sanitized_edges, island_violations = self._prune(candidate, grounding)

        violations: list[ValidationViolation] = list(island_violations)

        # Step 4: mutation-specific invariants (usage closure, acyclicity).
        if intents:
            violations.extend(self._check_usage_closure(intents, sanitized_ids))
            violations.extend(self._check_mutation_acyclicity(intents))

        # Step 5: scope shadowing + signature/visibility conformance.
        if intents:
            violations.extend(self._check_scope_shadowing(intents))
            violations.extend(self._check_visibility(intents))
        violations.extend(self._check_signature_conformance(sanitized_ids, grounding))

        # Step 6: deterministic sort + assembly.
        sanitized_nodes = {
            node_id: dict(self.builder.graph.nodes[node_id])
            for node_id in sorted(sanitized_ids)
            if node_id in self.builder.graph
        }
        sanitized_edges_sorted = sorted(
            sanitized_edges, key=lambda e: (e["source_id"], e["target_id"], e["kind"])
        )
        return ValidatedGraphResult(
            is_valid=True,
            sanitized_nodes=sanitized_nodes,
            sanitized_edges=sanitized_edges_sorted,
            dynamic_boundary_nodes=dynamic_boundary & sanitized_ids,
            violations=_sorted_violations(violations),
        )

    # ------------------------------------------------------------------ #
    # Step 1: staleness
    # ------------------------------------------------------------------ #
    def _check_staleness(self, candidate: CandidateSubgraph) -> list[ValidationViolation]:
        """Compares the in-memory `ParsedFile.source` bytes `builder`
        actually indexed against a fresh read of the same path on disk -
        the real, self-contained notion of "has this file changed since
        this candidate graph's own builder observed it" (no external hash
        store needed; `builder` already holds the "indexed" bytes it read
        during Pass 1).

        Deviation from the literal spec: uses `hashlib.sha256`, matching
        this codebase's own established content-hash convention
        (`prism.runtime.index_cache.compute_file_hashes`), not
        `hashlib.blake2b` - two hash functions for the same "did this file
        change" question is a real, avoidable drift, not a meaningful
        design choice.
        """
        files = self._files_for_nodes(candidate.node_ids)
        violations: list[ValidationViolation] = []
        for file_path in sorted(files):
            parsed = self.builder.parsed_file(file_path)
            if parsed is None:
                continue
            indexed_hash = hashlib.sha256(parsed.source).hexdigest()
            try:
                with open(file_path, "rb") as fh:
                    current_hash = hashlib.sha256(fh.read()).hexdigest()
            except OSError as exc:
                violations.append(
                    ValidationViolation(
                        ViolationType.STALE_FILE_STATE, None, file_path,
                        f"indexed file could no longer be read from disk: {exc}",
                    )
                )
                continue
            if current_hash != indexed_hash:
                violations.append(
                    ValidationViolation(
                        ViolationType.STALE_FILE_STATE, None, file_path,
                        "file content on disk no longer matches the content this candidate "
                        "graph's index was built from - spans/AST references may be wrong",
                    )
                )
        return violations

    def _files_for_nodes(self, node_ids: frozenset[str]) -> set[str]:
        files: set[str] = set()
        for node_id in node_ids:
            info = self.builder.symbol_table.get(node_id)
            if info is not None:
                files.add(info.file)
                continue
            data = self.builder.graph.nodes.get(node_id) if node_id in self.builder.graph else None
            if data is not None:
                call_site_file = data.get("call_site_file")
                if call_site_file:
                    files.add(call_site_file)
        return files

    # ------------------------------------------------------------------ #
    # Step 2: grounding
    # ------------------------------------------------------------------ #
    def _classify_grounding(
        self, candidate: CandidateSubgraph
    ) -> tuple[dict[str, GroundingState], set[str]]:
        grounding: dict[str, GroundingState] = {}
        dynamic_boundary: set[str] = set()
        for node_id in candidate.node_ids:
            if node_id in self.builder.symbol_table:
                grounding[node_id] = GroundingState.BOUND
                continue
            if node_id not in self.builder.graph:
                grounding[node_id] = GroundingState.UNBOUND
                continue
            data = self.builder.graph.nodes[node_id]
            sentinel_type = data.get("sentinel_type")
            if sentinel_type in ("dynamic_edge", "unresolved_polymorphic"):
                grounding[node_id] = GroundingState.DYNAMIC_UNRESOLVED
                dynamic_boundary.add(node_id)
            elif data.get("external"):
                grounding[node_id] = GroundingState.EXTERNAL
            else:
                # A node present in the graph, not external, but absent
                # from the symbol table - not observed in practice given
                # how `ConcreteGraphBuilder` populates both together, but
                # not assumed impossible either; treated as the genuine
                # "can't map this token to anything" case rather than
                # silently defaulting to BOUND.
                grounding[node_id] = GroundingState.UNBOUND
        return grounding, dynamic_boundary

    # ------------------------------------------------------------------ #
    # Step 3: hub damping + reachability/island pruning
    # ------------------------------------------------------------------ #
    def _is_hub(self, node_id: str) -> bool:
        if node_id in self.hub_allowlist:
            return False
        if node_id not in self.builder.graph:
            return False
        return self.builder.graph.in_degree(node_id) > self.hub_in_degree_threshold

    def _prune(
        self, candidate: CandidateSubgraph, grounding: dict[str, GroundingState]
    ) -> tuple[set[str], list[dict[str, Any]], list[ValidationViolation]]:
        present = {n for n in candidate.node_ids if n in self.builder.graph}
        anchors = {a for a in candidate.anchor_ids if a in present}
        max_depth = candidate.budget_limits.max_depth if candidate.budget_limits else None

        # Multi-source BFS from every anchor, restricted to the candidate's
        # own node set. A hub node (Step 3's absorbing boundary) is reached
        # and kept - its inbound attribution is preserved - but traversal
        # never expands outward *from* it, exactly mirroring the terminal-
        # leaf treatment `_emit_dynamic_edge_sentinel` already gives a
        # dynamic-dispatch sentinel one relation over.
        reached: dict[str, int] = {}
        frontier = [(a, 0) for a in anchors]
        for anchor, _ in frontier:
            reached[anchor] = 0
        i = 0
        while i < len(frontier):
            node_id, depth = frontier[i]
            i += 1
            if max_depth is not None and depth >= max_depth:
                continue
            if self._is_hub(node_id):
                continue
            neighbors = set(self.builder.graph.successors(node_id)) | set(self.builder.graph.predecessors(node_id))
            for neighbor in neighbors:
                if neighbor not in present or neighbor in reached:
                    continue
                reached[neighbor] = depth + 1
                frontier.append((neighbor, depth + 1))

        island_violations: list[ValidationViolation] = []
        for node_id in present - anchors:
            if node_id not in reached:
                island_violations.append(
                    ValidationViolation(
                        ViolationType.DISCONNECTED_ISLAND, node_id, None,
                        f"'{node_id}' has no reachable path to any primary anchor within "
                        f"{'d_max=' + str(max_depth) if max_depth is not None else 'the candidate graph'} "
                        "- pruned as speculative-seeding noise",
                        blocking_mutation=False,
                    )
                )

        sanitized_ids = anchors | (set(reached) - anchors)
        # Budget's own maxNodes is enforced deterministically here, not as
        # a blocking violation (the current ViolationType taxonomy has no
        # BUDGET_EXHAUSTION member) - truncate by BFS distance, closest to
        # an anchor first, ties broken lexicographically for determinism.
        max_nodes = candidate.budget_limits.max_nodes if candidate.budget_limits else None
        if max_nodes is not None and len(sanitized_ids) > max_nodes:
            ordered = sorted(sanitized_ids, key=lambda n: (reached.get(n, 0), n))
            sanitized_ids = set(ordered[:max_nodes])

        sanitized_edges: list[dict[str, Any]] = []
        subgraph = self.builder.graph.subgraph(sanitized_ids)
        for source_id, target_id, data in subgraph.edges(data=True):
            sanitized_edges.append(
                {"source_id": source_id, "target_id": target_id, "kind": data.get("relation", "CALLS"), **data}
            )
        return sanitized_ids, sanitized_edges, island_violations

    # ------------------------------------------------------------------ #
    # Step 4: mutation-specific invariants
    # ------------------------------------------------------------------ #
    def _check_usage_closure(
        self, intents: list[MutationIntent], sanitized_ids: set[str]
    ) -> list[ValidationViolation]:
        violations: list[ValidationViolation] = []
        for intent in intents:
            target = intent.target_symbol_id
            if target not in self.builder.graph:
                continue
            all_callers = {
                caller
                for caller, _target, data in self.builder.graph.in_edges(target, data=True)
                if data.get("relation") in _USAGE_RELATIONS
            }
            missing = all_callers - sanitized_ids
            if missing:
                info = self.builder.symbol_table.get(target)
                violations.append(
                    ValidationViolation(
                        ViolationType.INCOMPLETE_USAGE_CLOSURE, target, info.file if info else None,
                        f"{len(missing)} of {len(all_callers)} known reference(s) to '{target}' "
                        f"are absent from the candidate graph, missing: {sorted(missing)!r} - "
                        f"{intent.operation} would silently leave these call sites unpatched",
                    )
                )
        return violations

    def _check_mutation_acyclicity(self, intents: list[MutationIntent]) -> list[ValidationViolation]:
        target_ids = [intent.target_symbol_id for intent in intents if intent.target_symbol_id in self.builder.graph]
        if len(target_ids) < 2:
            return []
        induced = self.builder.graph.subgraph(target_ids)
        dependency_edges = [
            (u, v) for u, v, data in induced.edges(data=True) if data.get("relation") in _MUTATION_DEPENDENCY_RELATIONS
        ]
        dep_graph = nx.DiGraph()
        dep_graph.add_nodes_from(target_ids)
        dep_graph.add_edges_from(dependency_edges)
        if nx.is_directed_acyclic_graph(dep_graph):
            return []
        cycle = nx.find_cycle(dep_graph)
        cycle_symbols = [u for u, _v in cycle]
        return [
            ValidationViolation(
                ViolationType.MUTATION_CYCLE_DETECTED, cycle_symbols[0], None,
                f"mutation targets form a structural dependency cycle: {' -> '.join(cycle_symbols + [cycle_symbols[0]])} "
                "- no safe linear apply order exists for these mutations together",
            )
        ]

    # ------------------------------------------------------------------ #
    # Step 5: scope shadowing + signature/visibility conformance
    # ------------------------------------------------------------------ #
    def _local_variable_names(self, def_node, parsed) -> frozenset[str]:
        """Python only - see module docstring's per-language-precision
        convention (`concrete_builder.py`, `contracts.py`): a shallow,
        real AST-verified check for one grammar rather than a guessed,
        unverified one for every grammar this codebase parses."""
        lang = parsed.language_id
        if lang != LanguageID.PYTHON:
            return frozenset()
        assign_type = ASSIGNMENT_NODE_TYPE.get(lang)
        if not assign_type:
            return frozenset()
        names: set[str] = set()
        for node in iter_scoped_nodes(def_node, {assign_type}, lang):
            lhs = node.child_by_field_name("left")
            if lhs is not None and lhs.type in IDENTIFIER_NODE_TYPES.get(lang, set()):
                names.add(node_text(lhs, parsed.source))
        return frozenset(names)

    def _check_scope_shadowing(self, intents: list[MutationIntent]) -> list[ValidationViolation]:
        violations: list[ValidationViolation] = []
        for intent in intents:
            if intent.operation not in ("ADD_PARAM", "INSERT_VARIABLE"):
                continue
            name = intent.payload.get("name")
            if not name:
                continue
            target = intent.target_symbol_id
            contract = self.contracts.get(target)
            info = self.builder.symbol_table.get(target)
            if contract is not None and any(p.name == name for p in contract.params):
                violations.append(
                    ValidationViolation(
                        ViolationType.SCOPE_SHADOWING_COLLISION, target, info.file if info else None,
                        f"'{name}' collides with an existing parameter of '{target}'",
                    )
                )
                continue
            def_node = self.builder.def_node(target)
            parsed = self.builder.parsed_file(info.file) if info else None
            if def_node is not None and parsed is not None:
                if name in self._local_variable_names(def_node, parsed):
                    violations.append(
                        ValidationViolation(
                            ViolationType.SCOPE_SHADOWING_COLLISION, target, info.file if info else None,
                            f"'{name}' collides with an existing local variable binding in '{target}'",
                        )
                    )
        return violations

    def _check_visibility(self, intents: list[MutationIntent]) -> list[ValidationViolation]:
        violations: list[ValidationViolation] = []
        for intent in intents:
            if intent.operation not in ("RENAME", "MOVE"):
                continue
            target = intent.target_symbol_id
            contract = self.contracts.get(target)
            info = self.builder.symbol_table.get(target)
            if contract is None or info is None or contract.visibility != "private":
                continue
            if target not in self.builder.graph:
                continue
            for caller, _target, data in self.builder.graph.in_edges(target, data=True):
                if data.get("relation") not in _USAGE_RELATIONS:
                    continue
                caller_info = self.builder.symbol_table.get(caller)
                if caller_info is not None and caller_info.module != info.module:
                    violations.append(
                        ValidationViolation(
                            ViolationType.VISIBILITY_VIOLATION, target, info.file,
                            f"'{target}' is declared private but is referenced from module "
                            f"'{caller_info.module}' (caller '{caller}') outside its own module "
                            f"'{info.module}' - {intent.operation} is unsafe to apply automatically",
                        )
                    )
        return violations

    def _check_signature_conformance(
        self, sanitized_ids: set[str], grounding: dict[str, GroundingState]
    ) -> list[ValidationViolation]:
        violations: list[ValidationViolation] = []
        subgraph = self.builder.graph.subgraph(sanitized_ids)
        for source_id, target_id, data in subgraph.edges(data=True):
            relation = data.get("relation")
            if relation == "CALLS" and grounding.get(target_id) is GroundingState.BOUND:
                contract = self.contracts.get(target_id)
                args_passed = data.get("args_passed_count")
                if contract is not None and args_passed is not None and args_passed > len(contract.params):
                    # Deliberately one-directional: too many positional
                    # arguments can never be explained by a defaulted
                    # parameter, so it is a real, unambiguous mismatch.
                    # Too *few* is not flagged - Prism tracks no
                    # vararg/kwarg-collector shape, so an apparent
                    # undercount is frequently a default parameter, not a
                    # real bug; flagging it would be a guess this
                    # AST-only, no-type-inference pass can't back up.
                    violations.append(
                        ValidationViolation(
                            ViolationType.SIGNATURE_MISMATCH, target_id, None,
                            f"call site '{source_id}' -> '{target_id}' passes {args_passed} argument(s), "
                            f"but '{target_id}' only declares {len(contract.params)} parameter(s)",
                        )
                    )
            elif relation == "OVERRIDES":
                source_contract = self.contracts.get(source_id)
                target_contract = self.contracts.get(target_id)
                if (
                    source_contract is not None
                    and target_contract is not None
                    and len(source_contract.params) != len(target_contract.params)
                ):
                    violations.append(
                        ValidationViolation(
                            ViolationType.SIGNATURE_MISMATCH, source_id, None,
                            f"'{source_id}' overrides '{target_id}' with {len(source_contract.params)} "
                            f"parameter(s), base declares {len(target_contract.params)}",
                        )
                    )
        return violations
