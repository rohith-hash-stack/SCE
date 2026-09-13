"""Bookmark 2 (G8 remediation): data-flow case 5 - instance state
(`self.x = f(); self.y(self.x)`) in `_data_flow_common._bindings`/
`extract_data_flow`. Composite `base.attr` provenance key, direct case
only - a nested chain (`self.a.b`) is deliberately deferred to v1.2.
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.traversal._data_flow_common import extract_data_flow


def _edges_for(tmp_path, source: str, qualified_name: str, subdir: str = "repo"):
    repo = tmp_path / subdir
    repo.mkdir()
    (repo / "svc.py").write_text(source)
    builder, _ = build_pipeline(str(repo))
    def_node = builder.def_node(qualified_name)
    file = next(s.file for s in builder.symbol_table if s.qualified_name == qualified_name)
    parsed = builder.parsed_file(file)
    return extract_data_flow(def_node, parsed, qualified_name, builder)


def test_instance_state_direct_case_produces_an_edge(tmp_path):
    """The exact claimed pattern: `self.x = f(); self.y(self.x)`."""
    source = (
        "def fetch_data():\n"
        "    return 1\n"
        "\n"
        "\n"
        "class Worker:\n"
        "    def run(self):\n"
        "        self.data = fetch_data()\n"
        "        self.report(self.data)\n"
        "\n"
        "    def report(self, data):\n"
        "        return data\n"
    )
    edges = _edges_for(tmp_path, source, "svc.Worker.run")
    assert edges == [("svc.fetch_data", "svc.Worker.report", 0.9)]


def test_local_variable_case_still_works(tmp_path):
    """Positive control: the pre-existing local-assignment case (G5) is
    unaffected by the instance-state extension."""
    source = "def fetch_data():\n    return 1\n\n\ndef process(data):\n    return data\n\n\ndef run():\n    x = fetch_data()\n    process(x)\n"
    edges = _edges_for(tmp_path, source, "svc.run")
    assert edges == [("svc.fetch_data", "svc.process", 0.9)]


def test_nested_attribute_chain_is_not_bound(tmp_path):
    """`self.a.b = f()` - the base of the LHS attribute is itself an
    attribute, not a plain identifier. Deliberately deferred to v1.2, so
    this must produce no edge, not a wrong/guessed one."""
    source = (
        "def fetch_data():\n"
        "    return 1\n"
        "\n"
        "\n"
        "class Worker:\n"
        "    def run(self):\n"
        "        self.holder.data = fetch_data()\n"
        "        self.report(self.holder.data)\n"
        "\n"
        "    def report(self, data):\n"
        "        return data\n"
    )
    edges = _edges_for(tmp_path, source, "svc.Worker.run")
    assert edges == []


def test_arbitrary_attribute_access_pass_still_gets_the_lower_confidence_tier(tmp_path):
    """Pre-existing case (unrelated to G8): `v(y.data)` where `y` itself
    (not `y.data`) has provenance - the composite-key lookup must not
    swallow this lower-confidence (0.7) fallback."""
    source = (
        "def fetch_data():\n"
        "    return 1\n"
        "\n"
        "\n"
        "def process(data):\n"
        "    return data\n"
        "\n"
        "\n"
        "def run():\n"
        "    y = fetch_data()\n"
        "    process(y.data)\n"
    )
    edges = _edges_for(tmp_path, source, "svc.run")
    assert edges == [("svc.fetch_data", "svc.process", 0.7)]


def test_instance_state_case_is_deterministic(tmp_path):
    source = (
        "def fetch_data():\n"
        "    return 1\n"
        "\n"
        "\n"
        "class Worker:\n"
        "    def run(self):\n"
        "        self.data = fetch_data()\n"
        "        self.report(self.data)\n"
        "\n"
        "    def report(self, data):\n"
        "        return data\n"
    )
    run_1 = _edges_for(tmp_path, source, "svc.Worker.run", subdir="repo_a")
    run_2 = _edges_for(tmp_path, source, "svc.Worker.run", subdir="repo_b")
    assert run_1 == run_2
