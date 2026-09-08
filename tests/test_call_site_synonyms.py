"""Tests for native AST call-site synonym extraction
(`prism.graph.call_site`'s `bound_to`/`call_site_role`/`is_return_bound`)
and the self-healing structural-hash guard (`prism.graph.guard`) that
reconciles a symbol rename across re-indexing without a manual alias file.
"""
from __future__ import annotations

import json

import pytest

from prism.cli import build_pipeline
from prism.graph.guard import build_fingerprints, compute_structural_hash, reconcile, save_fingerprints
from prism.runtime.reconciler import heal_and_apply_runtime_state, load_runtime_state, runtime_state_path


def _edge_data(builder, caller: str, callee: str) -> dict:
    g = builder.calls_graph
    assert g.has_edge(caller, callee), f"expected edge {caller} -> {callee}, graph has: {list(g.edges)}"
    return dict(g.edges[caller, callee])


# --------------------------------------------------------------------- #
# AST synonym binding tests
# --------------------------------------------------------------------- #
def test_typescript_await_binding_sets_bound_to(tmp_path) -> None:
    repo = tmp_path / "ts_repo"
    repo.mkdir()
    (repo / "sample.ts").write_text(
        "async function getUser(): Promise<any> { return {}; }\n"
        "\n"
        "async function login() {\n"
        "    const user = await getUser();\n"
        "    return user;\n"
        "}\n"
    )
    builder, _tags = build_pipeline(str(repo))
    data = _edge_data(builder, "sample.login", "sample.getUser")
    assert data["bound_to"] == "user"
    assert data["call_kind"] == "awaited"
    assert data["is_return_bound"] is True


def test_python_assignment_binding_sets_bound_to(tmp_path) -> None:
    repo = tmp_path / "py_repo"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "def calculate():\n"
        "    return 42\n"
        "\n"
        "def use_it():\n"
        "    result = calculate()\n"
        "    print(result)\n"
    )
    builder, _tags = build_pipeline(str(repo))
    data = _edge_data(builder, "sample.use_it", "sample.calculate")
    assert data["bound_to"] == "result"


def test_this_property_assignment_binds_to_property_name(tmp_path) -> None:
    repo = tmp_path / "ts_repo2"
    repo.mkdir()
    (repo / "sample.ts").write_text(
        "function fetchToken(): string { return \"\"; }\n"
        "\n"
        "class Auth {\n"
        "    authToken: string = \"\";\n"
        "    login() {\n"
        "        this.authToken = fetchToken();\n"
        "    }\n"
        "}\n"
    )
    builder, _tags = build_pipeline(str(repo))
    data = _edge_data(builder, "sample.Auth.login", "sample.fetchToken")
    assert data["bound_to"] == "authToken"


def test_conditional_guard_sets_predicate_guard_role(tmp_path) -> None:
    repo = tmp_path / "py_repo3"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "def is_valid(x):\n"
        "    return x > 0\n"
        "\n"
        "def process(x):\n"
        "    if is_valid(x):\n"
        "        return x\n"
        "    return None\n"
    )
    builder, _tags = build_pipeline(str(repo))
    data = _edge_data(builder, "sample.process", "sample.is_valid")
    assert data["call_site_role"] == "predicate_guard"
    assert data["bound_to"] is None


def test_while_condition_sets_predicate_guard_role(tmp_path) -> None:
    repo = tmp_path / "py_repo4"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "def has_more():\n"
        "    return False\n"
        "\n"
        "def drain():\n"
        "    while has_more():\n"
        "        pass\n"
    )
    builder, _tags = build_pipeline(str(repo))
    data = _edge_data(builder, "sample.drain", "sample.has_more")
    assert data["call_site_role"] == "predicate_guard"


def test_assertion_argument_sets_assertion_subject_role(tmp_path) -> None:
    repo = tmp_path / "ts_repo5"
    repo.mkdir()
    (repo / "sample.ts").write_text(
        "function getMetric(): number { return 10; }\n"
        "\n"
        "function testMetric() {\n"
        "    expect(getMetric()).toBe(10);\n"
        "}\n"
    )
    builder, _tags = build_pipeline(str(repo))
    data = _edge_data(builder, "sample.testMetric", "sample.getMetric")
    assert data["call_site_role"] == "assertion_subject"


def test_python_bare_assert_sets_assertion_subject_role(tmp_path) -> None:
    repo = tmp_path / "py_repo6"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "def calculate():\n"
        "    return 42\n"
        "\n"
        "def test_calculate():\n"
        "    assert calculate() == 42\n"
    )
    builder, _tags = build_pipeline(str(repo))
    data = _edge_data(builder, "sample.test_calculate", "sample.calculate")
    assert data["call_site_role"] == "assertion_subject"


def test_bare_unassigned_call_has_no_role_or_binding(tmp_path) -> None:
    repo = tmp_path / "py_repo7"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "def notify():\n"
        "    return None\n"
        "\n"
        "def run():\n"
        "    notify()\n"
    )
    builder, _tags = build_pipeline(str(repo))
    data = _edge_data(builder, "sample.run", "sample.notify")
    assert data["bound_to"] is None
    assert data["call_site_role"] is None
    assert data["is_return_bound"] is False


# --------------------------------------------------------------------- #
# Self-healing guard tests
# --------------------------------------------------------------------- #
def test_structural_hash_is_stable_across_pure_rename(tmp_path) -> None:
    """Renaming a function (and every call site's reference to it) without
    touching its body/signature must leave its structural hash unchanged -
    the whole basis for `guard.reconcile` treating a vanished symbol as
    "renamed" rather than "deleted"."""
    repo = tmp_path / "hash_repo"
    repo.mkdir()
    before = repo / "sample.py"
    before.write_text(
        "def db_get(key):\n"
        "    if key:\n"
        "        return {\"id\": key}\n"
        "    return None\n"
    )
    builder, _tags = build_pipeline(str(repo))
    def_node = builder.def_node("sample.db_get")
    parsed = builder.parsed_file(str(before))
    hash_before = compute_structural_hash(def_node, parsed)

    before.write_text(
        "def fetch_record(key):\n"
        "    if key:\n"
        "        return {\"id\": key}\n"
        "    return None\n"
    )
    builder2, _tags2 = build_pipeline(str(repo))
    def_node2 = builder2.def_node("sample.fetch_record")
    parsed2 = builder2.parsed_file(str(before))
    hash_after = compute_structural_hash(def_node2, parsed2)

    assert hash_before == hash_after


def test_structural_hash_changes_when_body_shape_changes(tmp_path) -> None:
    repo = tmp_path / "hash_repo2"
    repo.mkdir()
    f = repo / "sample.py"
    f.write_text("def calc(x):\n    return x + 1\n")
    builder, _tags = build_pipeline(str(repo))
    hash_before = compute_structural_hash(builder.def_node("sample.calc"), builder.parsed_file(str(f)))

    f.write_text("def calc(x):\n    if x:\n        return x + 1\n    return 0\n")
    builder2, _tags2 = build_pipeline(str(repo))
    hash_after = compute_structural_hash(builder2.def_node("sample.calc"), builder2.parsed_file(str(f)))

    assert hash_before != hash_after


def test_guard_reconciles_rename_and_heals_runtime_state(tmp_path) -> None:
    """End-to-end: index a repo, persist fingerprints + a runtime_state
    that references the pre-rename qualified name (simulating a `prism
    trace` run recorded before the rename happened), rename the function,
    re-index, and verify the guard both proposes the rename and that
    `heal_and_apply_runtime_state` migrates the confirmed edge, its
    invocation count, and its sink tag onto the new name - no dangling
    reference left behind."""
    repo = tmp_path / "guard_repo"
    repo.mkdir()
    source_path = repo / "sample.py"
    source_path.write_text(
        "def db_get(key):\n"
        "    if key:\n"
        "        return {\"id\": key}\n"
        "    return None\n"
        "\n"
        "def caller():\n"
        "    return db_get(\"x\")\n"
    )

    builder, _tags = build_pipeline(str(repo))
    fingerprints = build_fingerprints(builder)
    save_fingerprints(str(repo), fingerprints)

    state = {
        "trace_files": ["run_fake.jsonl"],
        "confirmed_edges": [["sample.caller", "sample.db_get"]],
        "discovered_edges": [],
        "sink_symbols": {"sample.db_get": ["#db_read"]},
        "edge_invocation_counts": [["sample.caller", "sample.db_get", 42]],
        "unresolved_event_count": 0,
        "last_updated": "2026-01-01T00:00:00Z",
    }
    runtime_state_path(str(repo)).parent.mkdir(parents=True, exist_ok=True)
    runtime_state_path(str(repo)).write_text(json.dumps(state))

    # Rename db_get -> fetch_record, body untouched.
    source_path.write_text(
        "def fetch_record(key):\n"
        "    if key:\n"
        "        return {\"id\": key}\n"
        "    return None\n"
        "\n"
        "def caller():\n"
        "    return fetch_record(\"x\")\n"
    )

    builder2, tag_matrix2 = build_pipeline(str(repo))
    loaded_state = load_runtime_state(str(repo))
    reconciliation = heal_and_apply_runtime_state(builder2, tag_matrix2, loaded_state, str(repo))

    assert reconciliation.renamed == {"sample.db_get": "sample.fetch_record"}
    assert reconciliation.ambiguous == {}

    edge = builder2.graph.edges["sample.caller", "sample.fetch_record"]
    assert edge["confidence"] == "CONFIRMED_RUNTIME"
    assert edge["runtime_invocation_count"] == 42
    assert "#db_read" in tag_matrix2.get("sample.fetch_record", set())


def test_guard_leaves_genuinely_deleted_symbols_unresolved(tmp_path) -> None:
    """A symbol that's simply gone (no structural match anywhere) must not
    be guessed at - `reconcile` should report it in neither `renamed` nor
    `ambiguous`, so a caller can tell "healed" apart from "silently
    vanished"."""
    repo = tmp_path / "delete_repo"
    repo.mkdir()
    f = repo / "sample.py"
    f.write_text("def old_helper(x):\n    return x * 2\n")
    builder, _tags = build_pipeline(str(repo))
    old_fingerprints = build_fingerprints(builder)

    f.write_text("# old_helper was removed entirely\n")
    builder2, _tags2 = build_pipeline(str(repo))
    new_fingerprints = build_fingerprints(builder2)

    result = reconcile(old_fingerprints, new_fingerprints)
    assert "sample.old_helper" not in result.renamed
    assert "sample.old_helper" not in result.ambiguous
