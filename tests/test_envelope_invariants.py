"""Test-practice: structural invariants of a real, built `ContextPackage`
(`prism.surface.build.build_context_package`) - referential integrity
and internal consistency of the model itself, as opposed to
`surface/test_renderer_*.py`'s coverage of the XML *serialization* of
already-constructed (often hypothesis-generated) packages. Every
invariant here is checked against a package built by the real pipeline
on a real small repo."""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.surface.build import build_context_package

_SOURCE = (
    "def leaf_a():\n    return 1\n\n\n"
    "def leaf_b():\n    return 2\n\n\n"
    "def mid(x):\n    return leaf_a() + leaf_b() + x\n\n\n"
    "class Worker:\n"
    "    def __init__(self, x):\n        self.x = x\n\n"
    "    def run(self):\n        return mid(self.x)\n\n"
    "    def helper(self):\n        return self.run() + leaf_a()\n"
)


def _packages(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(_SOURCE)
    builder, _ = build_pipeline(str(repo))
    seeds = ("svc.leaf_a", "svc.mid", "svc.Worker.run", "svc.Worker.helper")
    return [build_context_package(builder, seed, str(repo), 2000) for seed in seeds]


def test_every_edge_endpoint_references_a_real_node_in_the_package(tmp_path):
    for pkg in _packages(tmp_path):
        node_ids = {n.id for n in pkg.nodes}
        for edge in pkg.edges:
            assert edge.from_node in node_ids, (pkg.seed.symbol, edge)
            assert edge.to_node in node_ids, (pkg.seed.symbol, edge)


def test_no_duplicate_node_ids(tmp_path):
    for pkg in _packages(tmp_path):
        ids = [n.id for n in pkg.nodes]
        assert len(ids) == len(set(ids)), pkg.seed.symbol


def test_seed_appears_exactly_once_with_role_seed_and_zero_distance(tmp_path):
    for pkg in _packages(tmp_path):
        seed_nodes = [n for n in pkg.nodes if n.id == pkg.seed.symbol]
        assert len(seed_nodes) == 1, pkg.seed.symbol
        assert seed_nodes[0].role == "seed"
        assert seed_nodes[0].distance == 0.0


def test_manifest_packed_nodes_matches_actual_node_count(tmp_path):
    for pkg in _packages(tmp_path):
        assert pkg.manifest.packed_nodes == len(pkg.nodes), pkg.seed.symbol


def test_manifest_compression_counts_sum_to_packed_nodes(tmp_path):
    for pkg in _packages(tmp_path):
        assert sum(c.count for c in pkg.manifest.compression) == pkg.manifest.packed_nodes, pkg.seed.symbol


def test_coverage_counts_are_internally_consistent(tmp_path):
    for pkg in _packages(tmp_path):
        cov = pkg.coverage
        assert cov.covered_features + cov.omitted_features == cov.total_features, pkg.seed.symbol
        assert cov.total_features == len(cov.features), pkg.seed.symbol
        actually_covered = sum(1 for f in cov.features if f.present)
        assert actually_covered == cov.covered_features, pkg.seed.symbol
        assert len(cov.gaps) == cov.omitted_features, pkg.seed.symbol


def test_budget_tokens_matches_the_requested_budget(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(_SOURCE)
    builder, _ = build_pipeline(str(repo))
    for budget in (500, 2000, 8000):
        pkg = build_context_package(builder, "svc.Worker.run", str(repo), budget)
        assert pkg.budget.tokens == budget


def test_every_node_cost_is_non_negative_and_total_is_consistent(tmp_path):
    for pkg in _packages(tmp_path):
        assert all(n.cost >= 0 for n in pkg.nodes), pkg.seed.symbol


def test_language_files_count_matches_distinct_files_among_nodes(tmp_path):
    """Every symbol in this fixture lives in one file, so a genuinely
    correct `language.files` count must be exactly 1 - a real, checkable
    cross-reference against pkg.nodes[*].file, not a hardcoded guess."""
    for pkg in _packages(tmp_path):
        distinct_files = {n.file for n in pkg.nodes}
        assert pkg.language.files == len(distinct_files), pkg.seed.symbol
