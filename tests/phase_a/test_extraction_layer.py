import ast
import os
import subprocess
import sys
import pytest

from prism.scanner.path_norm import get_module_parts
from prism.semantics.class_extractor import (
    MethodSymbol,
    Role,
    extract_class_substance,
    classify_class_role,
)
from prism.scanner.entrypoint_gate import (
    SymbolNode,
    is_public_symbol,
    is_overview_entrypoint,
    is_test_file,
)
from prism.traversal.data_flow import (
    EdgeKind,
    FlowGraph,
    SymbolRef,
    process_async_assignment,
    process_subscript_binding,
)
from prism.parser import parse_source


# ============================================================
# 1. Path & Normalization (6)
# ============================================================

def test_module_parts_handles_happy_py():
    parts = get_module_parts("django/contrib/auth/happy.py", repo_root="")
    assert parts == ["django", "contrib", "auth", "happy"]

def test_module_parts_handles_windows_paths():
    parts = get_module_parts(r"django\core\handlers\base.py", repo_root="")
    assert parts == ["django", "core", "handlers", "base"]

def test_module_parts_handles_windows_absolute_paths(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    parts = get_module_parts(
        r"C:\Users\repo\django\core\base.py",
        repo_root=r"C:\Users\repo",
    )
    assert parts == ["django", "core", "base"]

def test_module_parts_empty_repo_root_with_absolute_path():
    parts = get_module_parts("/app/django/core/base.py", repo_root="")
    assert parts == ["app", "django", "core", "base"]

def test_module_parts_rejects_parent_traversal():
    with pytest.raises(ValueError, match="Path traversal detected"):
        get_module_parts("../../etc/passwd", repo_root="/app")

def test_vendor_path_normalization():
    parts = get_module_parts("./vendor/bundle/lib.ts", repo_root="")
    assert parts == ["vendor", "bundle", "lib"]

def test_path_empty_input_returns_empty_parts():
    assert get_module_parts("", repo_root="") == []


# ============================================================
# 2. Extraction Bounds & Roles (5)
# ============================================================

def test_class_substance_capped_at_2plus_methods():
    methods = [
        MethodSymbol("m1", {"#net"}),
        MethodSymbol("m2", {"#net"}),
        MethodSymbol("m3", {"#db"}),
        MethodSymbol("m4", {"#io"}),
    ]
    assert extract_class_substance(methods) == {"#net"}

def test_class_substance_mixed_small_class():
    methods = [
        MethodSymbol("m1", {"#net"}),
        MethodSymbol("m2", {"#db"}),
        MethodSymbol("m3", {"#fs"}),
    ]
    assert extract_class_substance(methods) == {"#net", "#db", "#fs"}

def test_class_role_numeric_thresholds():
    assert classify_class_role(fan_in=6, fan_out=1) == Role.LEAF_UTILITY
    assert classify_class_role(fan_in=6, fan_out=2) == Role.CORE_DOMAIN
    assert classify_class_role(fan_in=6, fan_out=5) == Role.BRIDGE
    assert classify_class_role(fan_in=1, fan_out=9) == Role.COORDINATOR
    assert classify_class_role(fan_in=4, fan_out=4) == Role.ADAPTER

def test_role_classification_is_total_and_unique():
    for fi in range(12):
        for fo in range(12):
            role = classify_class_role(fi, fo)
            assert role is None or isinstance(role, Role)
    assert classify_class_role(5, 1) == Role.LEAF_UTILITY
    assert classify_class_role(5, 2) == Role.CORE_DOMAIN
    assert classify_class_role(5, 3) == Role.BRIDGE
    assert classify_class_role(7, 7) == Role.BRIDGE

def test_class_form_and_output_are_none():
    class_payload = {"form": None, "output": None}
    assert class_payload["form"] is None
    assert class_payload["output"] is None


# ============================================================
# 3. Entrypoint Filtering (7)
# ============================================================

def test_overview_gate_python():
    pub = SymbolNode("Handler", "django/core/handlers.py", role=Role.FACADE)
    priv_sym = SymbolNode("_internal", "django/core/handlers.py", role=Role.FACADE)
    priv_mod = SymbolNode("run", "django/core/_internal/run.py", role=Role.FACADE)
    assert is_overview_entrypoint(pub, "python") is True
    assert is_overview_entrypoint(priv_sym, "python") is False
    assert is_overview_entrypoint(priv_mod, "python") is False

def test_overview_gate_go_language():
    exported = SymbolNode("NewClient", "pkg/client.go", role=Role.ENTRYPOINT)
    unexported = SymbolNode("newClient", "pkg/client.go", role=Role.ENTRYPOINT)
    assert is_overview_entrypoint(exported, "go") is True
    assert is_overview_entrypoint(unexported, "go") is False

def test_ts_js_test_files_excluded_from_entrypoints():
    test_node = SymbolNode("App", "src/components/App.test.tsx",
                           role=Role.FACADE, is_exported=True)
    spec_node = SymbolNode("run", "tests/integration.spec.ts",
                           role=Role.FACADE, is_exported=True)
    assert is_overview_entrypoint(test_node, "typescript") is False
    assert is_overview_entrypoint(spec_node, "typescript") is False

def test_overview_gate_excludes_coordinator():
    coord = SymbolNode("Middleware", "app/middleware.py", role=Role.COORDINATOR)
    assert is_overview_entrypoint(coord, "python") is False

def test_index_tsx_recognized_as_entrypoint():
    node = SymbolNode("App", "src/components/index.tsx", role=Role.FACADE)
    assert is_overview_entrypoint(node, "typescript") is True

def test_overview_empty_repo():
    candidates: list[SymbolNode] = []
    entrypoints = [c for c in candidates if is_overview_entrypoint(c, "python")]
    assert entrypoints == []

def test_is_test_file_rejects_false_positives():
    assert not is_test_file("django/core/latest_values.py")
    assert not is_test_file("app/contest_app.py")
    assert not is_test_file("src/contest/file.py")
    assert not is_test_file("src/contests/file.py")
    assert not is_test_file("app/protest_utils.py")

def test_is_test_file_identifies_valid_test_patterns():
    assert is_test_file("app/tests.py")
    assert is_test_file("app/test.py")
    assert is_test_file("tests/handlers/test_base.py")
    assert is_test_file("django/contrib/auth/tests/test_models.py")
    assert is_test_file("pkg/client_test.go")
    assert is_test_file("src/components/Button.test.tsx")
    assert is_test_file("src/components/Modal.spec.js")
    assert is_test_file("conftest.py")


# ============================================================
# 4. Data Flow Semantics (8)
# ============================================================

def test_subscript_slice_ignored():
    env: dict[tuple[str, str], SymbolRef] = {}
    producer = SymbolRef("f", "app.py")
    tree = ast.parse("d[1:5] = f()")
    process_subscript_binding(tree.body[0].targets[0], producer, env)
    assert len(env) == 0

def test_subscript_slice_all_forms():
    env: dict[tuple[str, str], SymbolRef] = {}
    producer = SymbolRef("f", "app.py")
    for expr in ("d[:]", "d[::2]", "d[a:b:c]"):
        tree = ast.parse(f"{expr} = f()")
        process_subscript_binding(tree.body[0].targets[0], producer, env)
    assert len(env) == 0

def test_data_flow_container_field_basic():
    env: dict[tuple[str, str], SymbolRef] = {}
    producer = SymbolRef("fetch_token", "auth.py")
    tree = ast.parse("d['auth_token'] = fetch_token()")
    process_subscript_binding(tree.body[0].targets[0], producer, env)
    assert env[("d", "auth_token")] == producer

def test_attribute_argument_yields_no_edge():
    graph = FlowGraph()
    env = {"client": SymbolRef("get_client", "app.py")}
    producer = SymbolRef("consumer", "app.py")
    tree = ast.parse("res = consumer(self.client)").body[0]
    process_async_assignment(tree.targets, tree.value, producer, env, graph)
    assert len(graph.edges) == 0

def test_async_chain_produces_all_edges():
    graph = FlowGraph()
    env: dict[str, SymbolRef] = {}
    f = SymbolRef("f", "proc.py")
    g = SymbolRef("g", "proc.py")
    h = SymbolRef("h", "proc.py")
    t1 = ast.parse("x = f()").body[0]
    process_async_assignment(t1.targets, t1.value, f, env, graph)
    t2 = ast.parse("y = g(x)").body[0]
    process_async_assignment(t2.targets, t2.value, g, env, graph)
    t3 = ast.parse("z = h(y)").body[0]
    process_async_assignment(t3.targets, t3.value, h, env, graph)
    assert (f, g, EdgeKind.DATA_FLOW) in graph.edges
    assert (g, h, EdgeKind.DATA_FLOW) in graph.edges

def test_async_tuple_assignment():
    graph = FlowGraph()
    env: dict[str, SymbolRef] = {}
    producer = SymbolRef("init_pair", "app.py")
    consumer = SymbolRef("process_item", "app.py")
    t1 = ast.parse("a, b = init_pair()").body[0]
    process_async_assignment(t1.targets, t1.value, producer, env, graph)
    assert env["a"] == producer
    assert env["b"] == producer
    t2 = ast.parse("res = process_item(b)").body[0]
    process_async_assignment(t2.targets, t2.value, consumer, env, graph)
    assert (producer, consumer, EdgeKind.DATA_FLOW) in graph.edges

def test_async_keyword_argument_binding():
    graph = FlowGraph()
    env: dict[str, SymbolRef] = {}
    producer = SymbolRef("get_client", "app.py")
    consumer = SymbolRef("send_request", "app.py")
    t1 = ast.parse("c = get_client()").body[0]
    process_async_assignment(t1.targets, t1.value, producer, env, graph)
    t2 = ast.parse("res = send_request(timeout=30, client=c)").body[0]
    process_async_assignment(t2.targets, t2.value, consumer, env, graph)
    assert (producer, consumer, EdgeKind.DATA_FLOW) in graph.edges

def test_augmented_assignment_binds_target():
    graph = FlowGraph()
    env: dict[str, SymbolRef] = {}
    producer = SymbolRef("calc_delta", "math_utils.py")
    tree = ast.parse("counter += calc_delta()").body[0]
    process_async_assignment([tree.target], tree.value, producer, env, graph)
    assert env["counter"] == producer

def test_augmented_assignment_does_not_synthesize_self_read_edge():
    graph = FlowGraph()
    env: dict[str, SymbolRef] = {}
    prior = SymbolRef("init_counter", "math_utils.py")
    new_prod = SymbolRef("calc_delta", "math_utils.py")
    env["counter"] = prior
    tree = ast.parse("counter += calc_delta()").body[0]
    process_async_assignment([tree.target], tree.value, new_prod, env, graph)
    # Augmented assignment does not read target as a call argument, so no edge.
    assert len(graph.edges) == 0


# ============================================================
# 5. Parser & Derivation Bounds (4)
# ============================================================

def test_malformed_file_does_not_crash():
    res = parse_source("def invalid_syntax(:", filename="broken.py")
    assert res.symbols == []
    assert len(res.errors) == 1
    assert "SyntaxError" in res.errors[0]

def test_empty_file_parses_to_zero_symbols():
    res = parse_source("", filename="empty.py")
    assert res.symbols == []
    assert res.errors == []

def test_large_file_parses_under_performance_budget():
    import time
    synthetic = "\n".join([f"def func_{i}(): pass" for i in range(2500)])
    t0 = time.perf_counter()
    res = parse_source(synthetic, filename="huge.py")
    elapsed = time.perf_counter() - t0
    assert len(res.symbols) == 2500
    assert elapsed < 2.0

def test_determinism_across_pythonhashseed():
    script = (
        "from prism.semantics.class_extractor import extract_class_substance, MethodSymbol\n"
        "methods = [MethodSymbol('m1', {'#b', '#a'}), MethodSymbol('m2', {'#a'})]\n"
        "res = sorted(list(extract_class_substance(methods)))\n"
        "print(','.join(res))\n"
    )
    pythonpath = os.getcwd() + os.pathsep + os.environ.get("PYTHONPATH", "")
    env1 = {**os.environ, "PYTHONHASHSEED": "0", "PYTHONPATH": pythonpath}
    env2 = {**os.environ, "PYTHONHASHSEED": "42", "PYTHONPATH": pythonpath}
    out1 = subprocess.check_output(
        [sys.executable, "-c", script], env=env1, text=True
    ).strip()
    out2 = subprocess.check_output(
        [sys.executable, "-c", script], env=env2, text=True
    ).strip()
    assert out1 == out2
