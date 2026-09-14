"""Test-practice: all six data-flow confidence-tier cases in
`_data_flow_common.extract_data_flow`, exercised through the real
`build_pipeline` -> `extract_data_flow` path (never the internal
`_bindings` helper directly), enumerated from the function's own
docstring and consumer-argument branches:

  1. Directly-nested call argument (`v(u(x))`)             -> 1.0
  2. Variable pass via provenance (`y = u(x); v(y)`)        -> 0.9
  3. Method-chain provenance re-binding
     (`clean = raw.strip().lower(); v(clean)`)              -> 0.9
  4. Container/subscript read (`g(d['k'])` after `d['k'] = f()`) -> 0.9
  5. Instance-state direct attribute read
     (`v(self.x)` after `self.x = f()`)                     -> 0.9
  6. Arbitrary attribute-access fallback
     (`v(y.data)` where only `y` has provenance)             -> 0.7

Existing files (`test_data_flow_container_field.py`,
`test_data_flow_instance_state.py`) already cover cases 4-6 from a
different angle (G7/G8 remediation); this file is the single place all
six cases are enumerated and checked together, plus 1-3 which had no
dedicated test file before."""
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


# --- Case 1: directly-nested call argument -> 1.0 ---

def test_case_1_nested_call_argument_gets_full_confidence(tmp_path):
    source = (
        "def u(x):\n    return x\n\n\ndef v(y):\n    return y\n\n\ndef run():\n    v(u(1))\n"
    )
    edges = _edges_for(tmp_path, source, "svc.run")
    assert edges == [("svc.u", "svc.v", 1.0)]


# --- Case 2: variable pass via provenance -> 0.9 ---

def test_case_2_variable_pass_gets_provenance_confidence(tmp_path):
    source = (
        "def u(x):\n    return x\n\n\ndef v(y):\n    return y\n\n\n"
        "def run():\n    a = u(1)\n    v(a)\n"
    )
    edges = _edges_for(tmp_path, source, "svc.run")
    assert edges == [("svc.u", "svc.v", 0.9)]


# --- Case 3: method-chain provenance re-binding -> 0.9 ---

def test_case_3_method_chain_rebinding_preserves_provenance(tmp_path):
    """`clean = raw.strip().lower()` - `raw` already has a producer, so
    `clean` inherits it even though the RHS is itself a (method-chain)
    call expression, not a bare identifier."""
    source = (
        "def fetch():\n    return ' x '\n\n\ndef consume(s):\n    return s\n\n\n"
        "def run():\n    raw = fetch()\n    clean = raw.strip().lower()\n    consume(clean)\n"
    )
    edges = _edges_for(tmp_path, source, "svc.run")
    assert edges == [("svc.fetch", "svc.consume", 0.9)]


# --- Case 4: container/subscript read -> 0.9 (G7) ---

def test_case_4_container_field_subscript_read(tmp_path):
    source = (
        "def fetch():\n    return 1\n\n\ndef consume(v):\n    return v\n\n\n"
        "def run():\n    d = {}\n    d['k'] = fetch()\n    consume(d['k'])\n"
    )
    edges = _edges_for(tmp_path, source, "svc.run")
    assert edges == [("svc.fetch", "svc.consume", 0.9)]


# --- Case 5: instance-state direct attribute read -> 0.9 (G8) ---

def test_case_5_instance_state_direct_attribute_read(tmp_path):
    source = (
        "def fetch():\n    return 1\n\n\nclass Worker:\n    def run(self):\n"
        "        self.data = fetch()\n        self.report(self.data)\n\n"
        "    def report(self, data):\n        return data\n"
    )
    edges = _edges_for(tmp_path, source, "svc.Worker.run")
    assert edges == [("svc.fetch", "svc.Worker.report", 0.9)]


# --- Case 6: arbitrary attribute-access fallback -> 0.7 ---

def test_case_6_arbitrary_attribute_access_fallback(tmp_path):
    source = (
        "def fetch():\n    return 1\n\n\ndef consume(v):\n    return v\n\n\n"
        "def run():\n    y = fetch()\n    consume(y.data)\n"
    )
    edges = _edges_for(tmp_path, source, "svc.run")
    assert edges == [("svc.fetch", "svc.consume", 0.7)]


# --- Cross-case: all six confidence tiers appear together in one
# function, at their own distinct values, none clobbering another. ---

def test_all_six_cases_coexist_with_distinct_confidences(tmp_path):
    source = (
        "def a():\n    return 1\n\n\n"
        "def b():\n    return 1\n\n\n"
        "def c():\n    return 1\n\n\n"
        "def d():\n    return 1\n\n\n"
        "def sink1(x):\n    return x\n\n\n"
        "def sink2(x):\n    return x\n\n\n"
        "def sink3(x):\n    return x\n\n\n"
        "def sink4(x):\n    return x\n\n\n"
        "class Worker:\n"
        "    def run(self):\n"
        "        sink1(a())\n"
        "        p = b()\n"
        "        sink2(p)\n"
        "        self.data = c()\n"
        "        self.sink3(self.data)\n"
        "        q = d()\n"
        "        sink4(q.field)\n"
        "\n"
        "    def sink3(self, x):\n"
        "        return x\n"
    )
    edges = _edges_for(tmp_path, source, "svc.Worker.run")
    by_confidence = {round(conf, 2) for _, _, conf in edges}
    assert by_confidence == {1.0, 0.9, 0.7}
    assert ("svc.a", "svc.sink1", 1.0) in edges
    assert ("svc.b", "svc.sink2", 0.9) in edges
    assert ("svc.c", "svc.Worker.sink3", 0.9) in edges
    assert ("svc.d", "svc.sink4", 0.7) in edges


def test_data_flow_cases_are_deterministic_across_independent_builds(tmp_path):
    source = (
        "def u(x):\n    return x\n\n\ndef v(y):\n    return y\n\n\n"
        "def run():\n    a = u(1)\n    v(a)\n    v(u(2))\n"
    )
    run_1 = _edges_for(tmp_path, source, "svc.run", subdir="repo_a")
    run_2 = _edges_for(tmp_path, source, "svc.run", subdir="repo_b")
    assert run_1 == run_2
