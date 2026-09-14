"""Bookmark 2 (G7 remediation): data-flow case - container field
(`d['k'] = f(); g(d['k'])`) in `_data_flow_common._bindings`/
`extract_data_flow`. Composite `base[key]` provenance key, direct case
only (Python only) - a nested chain (`d['a']['b']`) is deliberately
deferred to v1.2, exact parity with G8's `self.a.b` deferral.
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


def test_container_field_case_produces_an_edge(tmp_path):
    """The exact claimed pattern: `d['k'] = f(); g(d['k'])`."""
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
        "    d = {}\n"
        "    d['k'] = fetch_data()\n"
        "    process(d['k'])\n"
    )
    edges = _edges_for(tmp_path, source, "svc.run")
    assert edges == [("svc.fetch_data", "svc.process", 0.9)]


def test_local_variable_case_still_works(tmp_path):
    """Positive control: the pre-existing local-assignment case (G5) is
    unaffected by the container-field extension."""
    source = "def fetch_data():\n    return 1\n\n\ndef process(data):\n    return data\n\n\ndef run():\n    x = fetch_data()\n    process(x)\n"
    edges = _edges_for(tmp_path, source, "svc.run")
    assert edges == [("svc.fetch_data", "svc.process", 0.9)]


def test_nested_subscript_chain_is_not_bound(tmp_path):
    """`d['a']['b'] = f()` - the base of the LHS subscript is itself a
    subscript, not a plain identifier. Deliberately deferred to v1.2, so
    this must produce no edge, not a wrong/guessed one."""
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
        "    d = {'a': {}}\n"
        "    d['a']['b'] = fetch_data()\n"
        "    process(d['a']['b'])\n"
    )
    edges = _edges_for(tmp_path, source, "svc.run")
    assert edges == []


def test_different_key_does_not_falsely_match(tmp_path):
    """`d['k1'] = f(); process(d['k2'])` - a different key on the same
    base must not be treated as the same provenance carrier."""
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
        "    d = {}\n"
        "    d['k1'] = fetch_data()\n"
        "    process(d['k2'])\n"
    )
    edges = _edges_for(tmp_path, source, "svc.run")
    assert edges == []


def test_container_field_case_is_deterministic(tmp_path):
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
        "    d = {}\n"
        "    d['k'] = fetch_data()\n"
        "    process(d['k'])\n"
    )
    run_1 = _edges_for(tmp_path, source, "svc.run", subdir="repo_a")
    run_2 = _edges_for(tmp_path, source, "svc.run", subdir="repo_b")
    assert run_1 == run_2
