"""Verification suite for `prism.graph.subgraph_validator.SubgraphValidator`.

Every fixture here is a real, tiny multi-file repo run through the actual
`build_pipeline`/`compute_contracts` pipeline (`ConcreteGraphBuilder` +
`BehavioralContract`) - not a hand-built mock graph - so a test failure
means the validator disagrees with what Prism's own real indexer produced,
not with an assumption about its shape. Confirmed empirically (direct
script runs against these same fixtures) before being written up as
pytest tests, matching this project's standing verification discipline.

Deviation from a literal implementation-spec test list, where one applies:
- `test_aliased_import_canonicalization` here proves canonicalization is
  a *no-op* by the time a candidate reaches the validator, not that the
  validator performs it: `ConcreteGraphBuilder`'s own two-pass linker
  already resolves aliased imports/re-exports to one canonical qualified
  name before any node/edge enters `builder.graph` (see
  `subgraph_validator`'s own module docstring). A validator that
  operates on `builder.graph` node identities (the Component Reuse
  mandate) structurally cannot observe two node IDs for one aliased
  reference - there is only ever the one canonical ID - so the real,
  verifiable property is "no duplicate/alias node ever appears", not
  "the validator merges duplicates it is never actually given".
- The latency assertion targets `SubgraphValidator.validate()` alone,
  not `build_pipeline`/`compute_contracts` (one-time indexing cost, off
  the validator's own hot path per its module docstring), and uses a
  generous CI-safe bound rather than the design target's raw 2ms - a
  shared, possibly loaded CI runner is not a clean benchmark environment.
"""
from __future__ import annotations

import json
import time

import pytest

from prism.cli import build_pipeline
from prism.graph.contracts import compute_contracts
from prism.graph.subgraph_validator import (
    BudgetLimits,
    CandidateSubgraph,
    GroundingState,
    MutationIntent,
    SubgraphValidator,
    ViolationType,
)


def _build(repo_path) -> tuple:
    builder, _tags = build_pipeline(str(repo_path), use_cache=False)
    contracts = compute_contracts(builder)
    return builder, contracts


def _write(repo_path, name: str, source: str) -> None:
    (repo_path / name).write_text(source)


@pytest.fixture()
def basic_repo(tmp_path):
    """mod_a.seed -> mod_b.helper (real CALLS edge) plus a getattr-based
    dynamic-dispatch hazard, mod_a.caller_of_helper_2 -> mod_b.helper2
    called with 4 positional args against a 2-parameter definition
    (a real, unambiguous SIGNATURE_MISMATCH), a leading-underscore
    (private, by Python convention - see contracts.py's own `_visibility`)
    helper called from a different module, and an orphaned function with
    no path back to any anchor."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo, "mod_b.py",
        "def helper():\n    return 42\n\n\n"
        "def helper2(a, b):\n    return a + b\n\n\n"
        "def orphan_island():\n    return 'never reached'\n",
    )
    _write(
        repo, "mod_a.py",
        "import mod_b\n\n\n"
        "def seed():\n    x = mod_b.helper()\n    getattr(mod_b, 'helper')()\n    return x\n\n\n"
        "def caller_of_helper_2():\n    return mod_b.helper2(1, 2, 3, 4)\n\n\n"
        "def _private_helper():\n    return 1\n",
    )
    _write(
        repo, "mod_a_user.py",
        "import mod_a\n\n\ndef use_private():\n    return mod_a._private_helper()\n",
    )
    return repo


@pytest.fixture()
def mutual_recursion_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "mut.py", "def a():\n    return b()\n\n\ndef b():\n    return a()\n")
    return repo


@pytest.fixture()
def hub_repo(tmp_path):
    """Five distinct callers into `util.log`, which itself calls
    `util.downstream_of_log` - real fan-in high enough to exercise the
    absorbing-boundary hub rule at a test-local threshold."""
    repo = tmp_path / "repo"
    repo.mkdir()
    callers = "\n".join(f"def caller_{i}():\n    return util.log()\n" for i in range(5))
    _write(repo, "callers.py", "import util\n\n\n" + callers)
    _write(repo, "util.py", "def log():\n    return downstream_of_log()\n\n\ndef downstream_of_log():\n    return 2\n")
    return repo


def _dynamic_sentinel_id(builder) -> str:
    return next(n for n in builder.graph.nodes if n.startswith("<dynamic:"))


# ============================================================
# Group 1: Grounding & Canonicalization
# ============================================================

def test_unresolved_primary_anchor_rejection(basic_repo):
    builder, contracts = _build(basic_repo)
    validator = SubgraphValidator(builder, contracts)
    candidate = CandidateSubgraph(
        anchor_ids=("mod_a.calc_profit_margin",), node_ids=frozenset({"mod_a.calc_profit_margin"})
    )
    result = validator.validate(candidate)
    assert result.is_valid is False
    assert result.violations
    assert result.violations[0].violation_type is ViolationType.UNRESOLVED_ANCHOR
    assert result.violations[0].blocking_mutation is True


def test_dynamic_boundary_node_allowance(basic_repo):
    builder, contracts = _build(basic_repo)
    validator = SubgraphValidator(builder, contracts)
    sentinel_id = _dynamic_sentinel_id(builder)
    candidate = CandidateSubgraph(
        anchor_ids=("mod_a.seed",), node_ids=frozenset({"mod_a.seed", "mod_b.helper", sentinel_id})
    )
    result = validator.validate(candidate)
    assert result.is_valid is True
    assert sentinel_id in result.dynamic_boundary_nodes
    assert sentinel_id in result.sanitized_nodes


def test_aliased_import_canonicalization(tmp_path):
    """See module docstring: proves the real property (one canonical node
    ID, never a duplicate alias-form node) rather than active merging the
    validator itself never needs to perform."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "engine.py", "def run():\n    return 1\n")
    _write(
        repo, "caller.py",
        "import engine as eng\nimport engine\n\n\n"
        "def use_alias():\n    return eng.run()\n\n\n"
        "def use_full_path():\n    return engine.run()\n",
    )
    builder, contracts = _build(repo)
    run_nodes = [n for n in builder.graph.nodes if n.endswith(".run")]
    assert run_nodes == ["engine.run"], "aliased and full-path references must resolve to one canonical node"

    validator = SubgraphValidator(builder, contracts)
    candidate = CandidateSubgraph(
        anchor_ids=("caller.use_alias", "caller.use_full_path"),
        node_ids=frozenset({"caller.use_alias", "caller.use_full_path", "engine.run"}),
    )
    result = validator.validate(candidate)
    assert result.is_valid is True
    assert not any(v.violation_type is ViolationType.DISCONNECTED_ISLAND for v in result.violations)


# ============================================================
# Group 2: Staleness & Environmental Integrity
# ============================================================

def test_stale_file_hash_detection(basic_repo):
    builder, contracts = _build(basic_repo)
    validator = SubgraphValidator(builder, contracts)

    (basic_repo / "mod_a.py").write_text(
        (basic_repo / "mod_a.py").read_text() + "\n\ndef extra():\n    return 99\n"
    )

    candidate = CandidateSubgraph(anchor_ids=("mod_a.seed",), node_ids=frozenset({"mod_a.seed"}))
    result = validator.validate(candidate, mutation_intents=[MutationIntent("mod_a.seed", "RENAME")])
    assert result.is_valid is False
    assert result.is_mutation_safe is False
    assert any(v.violation_type is ViolationType.STALE_FILE_STATE for v in result.violations)


# ============================================================
# Group 3: Hub Damping & Reachability
# ============================================================

def test_absorbing_hub_prevents_edge_explosion(hub_repo):
    builder, contracts = _build(hub_repo)
    assert builder.graph.in_degree("util.log") == 5
    validator = SubgraphValidator(builder, contracts, hub_in_degree_threshold=3)

    candidate = CandidateSubgraph(
        anchor_ids=("callers.caller_0",),
        node_ids=frozenset({"callers.caller_0", "util.log", "util.downstream_of_log"}),
    )
    result = validator.validate(candidate)
    assert result.is_valid is True
    assert "util.log" in result.sanitized_nodes
    assert any(e["source_id"] == "callers.caller_0" and e["target_id"] == "util.log" for e in result.sanitized_edges)
    assert "util.downstream_of_log" not in result.sanitized_nodes, "hub must absorb, not propagate, outward traversal"


def test_disconnected_island_pruning(basic_repo):
    builder, contracts = _build(basic_repo)
    validator = SubgraphValidator(builder, contracts)
    sentinel_id = _dynamic_sentinel_id(builder)

    candidate = CandidateSubgraph(
        anchor_ids=("mod_a.seed",),
        node_ids=frozenset({"mod_a.seed", "mod_b.helper", sentinel_id, "mod_b.orphan_island"}),
    )
    result = validator.validate(candidate)
    assert result.is_valid is True
    assert "mod_b.orphan_island" not in result.sanitized_nodes
    island_violations = [v for v in result.violations if v.violation_type is ViolationType.DISCONNECTED_ISLAND]
    assert len(island_violations) == 1
    assert island_violations[0].blocking_mutation is False
    assert "mod_a.seed" in result.sanitized_nodes  # the anchor itself is never pruned


def test_budget_max_nodes_truncates_deterministically(basic_repo):
    builder, contracts = _build(basic_repo)
    validator = SubgraphValidator(builder, contracts)
    sentinel_id = _dynamic_sentinel_id(builder)

    candidate = CandidateSubgraph(
        anchor_ids=("mod_a.seed",),
        node_ids=frozenset({"mod_a.seed", "mod_b.helper", sentinel_id}),
        budget_limits=BudgetLimits(max_nodes=2),
    )
    result = validator.validate(candidate)
    assert result.is_valid is True
    assert len(result.sanitized_nodes) == 2
    assert "mod_a.seed" in result.sanitized_nodes


# ============================================================
# Group 4: Mutation-Specific Invariants
# ============================================================

def test_incomplete_usage_closure_fails_rename(basic_repo):
    builder, contracts = _build(basic_repo)
    validator = SubgraphValidator(builder, contracts)

    # mod_b.helper's only real caller is mod_a.seed - omitting it from the
    # candidate must be flagged, not silently accepted.
    candidate = CandidateSubgraph(anchor_ids=("mod_b.helper",), node_ids=frozenset({"mod_b.helper"}))
    result = validator.validate(candidate, mutation_intents=[MutationIntent("mod_b.helper", "RENAME")])
    assert result.is_mutation_safe is False
    closure_violations = [v for v in result.violations if v.violation_type is ViolationType.INCOMPLETE_USAGE_CLOSURE]
    assert len(closure_violations) == 1
    assert "mod_a.seed" in closure_violations[0].details


def test_usage_closure_passes_when_all_callers_present(basic_repo):
    builder, contracts = _build(basic_repo)
    validator = SubgraphValidator(builder, contracts)
    candidate = CandidateSubgraph(anchor_ids=("mod_b.helper",), node_ids=frozenset({"mod_b.helper", "mod_a.seed"}))
    result = validator.validate(candidate, mutation_intents=[MutationIntent("mod_b.helper", "RENAME")])
    assert result.is_mutation_safe is True


def test_circular_mutation_dependency_fails(mutual_recursion_repo):
    builder, contracts = _build(mutual_recursion_repo)
    validator = SubgraphValidator(builder, contracts)
    candidate = CandidateSubgraph(anchor_ids=("mut.a", "mut.b"), node_ids=frozenset({"mut.a", "mut.b"}))
    result = validator.validate(
        candidate, mutation_intents=[MutationIntent("mut.a", "RENAME"), MutationIntent("mut.b", "RENAME")]
    )
    assert result.is_mutation_safe is False
    assert any(v.violation_type is ViolationType.MUTATION_CYCLE_DETECTED for v in result.violations)


def test_scope_shadowing_collision_detected(basic_repo):
    builder, contracts = _build(basic_repo)
    validator = SubgraphValidator(builder, contracts)
    candidate = CandidateSubgraph(anchor_ids=("mod_a.seed",), node_ids=frozenset({"mod_a.seed"}))

    # collides with the local variable `x = mod_b.helper()` bound in seed()'s own body.
    result = validator.validate(
        candidate, mutation_intents=[MutationIntent("mod_a.seed", "ADD_PARAM", {"name": "x"})]
    )
    assert result.is_mutation_safe is False
    assert any(v.violation_type is ViolationType.SCOPE_SHADOWING_COLLISION for v in result.violations)

    # a fresh, unused name must not collide.
    result_ok = validator.validate(
        candidate, mutation_intents=[MutationIntent("mod_a.seed", "ADD_PARAM", {"name": "timeout"})]
    )
    assert result_ok.is_mutation_safe is True


def test_visibility_violation_on_cross_module_private_access(basic_repo):
    builder, contracts = _build(basic_repo)
    validator = SubgraphValidator(builder, contracts)
    assert contracts["mod_a._private_helper"].visibility == "private"

    candidate = CandidateSubgraph(
        anchor_ids=("mod_a._private_helper",),
        node_ids=frozenset({"mod_a._private_helper", "mod_a_user.use_private"}),
    )
    result = validator.validate(candidate, mutation_intents=[MutationIntent("mod_a._private_helper", "RENAME")])
    assert result.is_mutation_safe is False
    assert any(v.violation_type is ViolationType.VISIBILITY_VIOLATION for v in result.violations)


def test_signature_mismatch_on_call_site_argument_overshoot(basic_repo):
    builder, contracts = _build(basic_repo)
    validator = SubgraphValidator(builder, contracts)
    candidate = CandidateSubgraph(
        anchor_ids=("mod_a.caller_of_helper_2",),
        node_ids=frozenset({"mod_a.caller_of_helper_2", "mod_b.helper2"}),
    )
    result = validator.validate(candidate)
    assert result.is_valid is True  # a non-mutation SIGNATURE_MISMATCH is informational, not a rejection
    mismatches = [v for v in result.violations if v.violation_type is ViolationType.SIGNATURE_MISMATCH]
    assert len(mismatches) == 1
    assert "4 argument" in mismatches[0].details


# ============================================================
# Group 5: Determinism & Benchmark Invariants
# ============================================================

def _snapshot(result) -> str:
    payload = {
        "is_valid": result.is_valid,
        "nodes": sorted(result.sanitized_nodes),
        "edges": [(e["source_id"], e["target_id"], e["kind"]) for e in result.sanitized_edges],
        "dynamic_boundary_nodes": sorted(result.dynamic_boundary_nodes),
        "violations": [v.to_dict() for v in result.violations],
    }
    return json.dumps(payload, sort_keys=True)


def test_byte_identical_output_determinism(basic_repo):
    import random

    builder, contracts = _build(basic_repo)
    validator = SubgraphValidator(builder, contracts)
    sentinel_id = _dynamic_sentinel_id(builder)
    base_ids = ["mod_a.seed", "mod_b.helper", sentinel_id, "mod_b.orphan_island"]

    snapshots = set()
    for _ in range(50):
        shuffled = list(base_ids)
        random.shuffle(shuffled)
        candidate = CandidateSubgraph(anchor_ids=("mod_a.seed",), node_ids=frozenset(shuffled))
        snapshots.add(_snapshot(validator.validate(candidate)))
    assert len(snapshots) == 1, "identical input must produce byte-identical output regardless of insertion order"


def test_validator_latency_benchmark(tmp_path):
    """150 chained functions, precomputed builder/contracts (the one-time
    indexing cost this module's own docstring places outside its hot
    path) - only `SubgraphValidator.validate()` itself is timed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lines = []
    for i in range(150):
        call = f"    return f{i + 1}()\n" if i < 149 else "    return 0\n"
        lines.append(f"def f{i}():\n{call}")
    _write(repo, "chain.py", "\n\n".join(lines) + "\n")

    builder, contracts = _build(repo)
    validator = SubgraphValidator(builder, contracts)
    node_ids = frozenset(f"chain.f{i}" for i in range(150))
    candidate = CandidateSubgraph(anchor_ids=("chain.f0",), node_ids=node_ids)

    validator.validate(candidate)  # warm-up: first call may pay import/attribute-lookup cost
    start = time.perf_counter()
    for _ in range(10):
        result = validator.validate(candidate)
    elapsed_ms = (time.perf_counter() - start) / 10 * 1000
    assert result.is_valid is True
    assert len(result.sanitized_nodes) == 150
    assert elapsed_ms <= 50.0, f"validate() took {elapsed_ms:.3f}ms/call on a 150-node graph"
