"""Phase B, Step 1 (SymbolRole taxonomy): verifies `SymbolRole` is
assigned correctly during Pass 1 (`ConcreteGraphBuilder._register_
definition`) from deterministic AST/naming-convention signals -
`prism.graph.symbol_table.SymbolRole`'s own docstring has the full
rationale for why this replaced a hardcoded module-path blacklist
(`prism.packer.submodular_knapsack`'s old `_NEVER_PIPELINE_MODULE_
PREFIXES`), which had a real, measured gap (`django_t02_017` at
budget=2000: a test symbol scoring nonzero novelty sailed straight
through a check gated on `delta_feat == 0`).

Data-only: this file never touches selection/packing - see
`tests/test_knapsack_role_gate.py` for the Step 2 admissibility-gate
behavior these roles feed into.
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.graph.symbol_table import SymbolRole


def _roles(tmp_path, filename: str, source: str, subdir: str = "repo") -> dict[str, SymbolRole]:
    repo = tmp_path / subdir
    repo.mkdir()
    (repo / filename).write_text(source)
    builder, _tags = build_pipeline(str(repo))
    return {qname: info.role for qname, info in builder.symbol_table._symbols.items()}


# --- Python: base-class pattern (unittest.TestCase / pytest Test* mixin) --- #

def test_unittest_testcase_subclass_and_its_methods_are_verification(tmp_path):
    source = (
        "import unittest\n\n"
        "class WidgetTests(unittest.TestCase):\n"
        "    def test_render(self):\n"
        "        self.assertEqual(1, 1)\n\n"
        "    def helper_no_assert_no_test_name(self):\n"
        "        self.x = 1\n"
    )
    roles = _roles(tmp_path, "mod.py", source)
    assert roles["mod.WidgetTests"] == SymbolRole.VERIFICATION
    assert roles["mod.WidgetTests.test_render"] == SymbolRole.VERIFICATION
    # The class-level signal alone must be enough - a plain helper method
    # with no assert calls and no test_-prefixed name of its own still
    # inherits VERIFICATION from its enclosing test-shaped class.
    assert roles["mod.WidgetTests.helper_no_assert_no_test_name"] == SymbolRole.VERIFICATION


def test_ordinary_class_and_methods_are_implementation(tmp_path):
    source = (
        "class Widget:\n"
        "    def render(self):\n"
        "        self.prepare()\n"
        "        return 1\n\n"
        "    def prepare(self):\n"
        "        self.ready = True\n"
    )
    roles = _roles(tmp_path, "mod.py", source)
    assert roles["mod.Widget"] == SymbolRole.IMPLEMENTATION
    assert roles["mod.Widget.render"] == SymbolRole.IMPLEMENTATION
    assert roles["mod.Widget.prepare"] == SymbolRole.IMPLEMENTATION


# --- Python: decorator pattern (pytest fixture / mark / parametrize) ------ #

def test_pytest_fixture_decorated_function_is_verification(tmp_path):
    source = (
        "import pytest\n\n"
        "@pytest.fixture\n"
        "def widget():\n"
        "    return object()\n"
    )
    roles = _roles(tmp_path, "mod.py", source)
    assert roles["mod.widget"] == SymbolRole.VERIFICATION


def test_pytest_mark_parametrize_decorated_function_is_verification(tmp_path):
    source = (
        "import pytest\n\n"
        "@pytest.mark.parametrize('x', [1, 2])\n"
        "def test_values(x):\n"
        "    assert x > 0\n"
    )
    roles = _roles(tmp_path, "mod.py", source)
    assert roles["mod.test_values"] == SymbolRole.VERIFICATION


# --- Python: assertion-density / name-pattern fallback -------------------- #

def test_bare_assert_function_with_test_prefix_is_verification(tmp_path):
    source = (
        "def seed():\n"
        "    return 1\n\n"
        "def test_seed_returns_one():\n"
        "    assert seed() == 1\n"
    )
    roles = _roles(tmp_path, "test_mod.py", source)  # a test file: where pytest collects `test_*`
    assert roles["test_mod.test_seed_returns_one"] == SymbolRole.VERIFICATION
    assert roles["test_mod.seed"] == SymbolRole.IMPLEMENTATION


# --- Python: interface-shaped body ----------------------------------------- #

def test_not_implemented_error_stub_is_interface(tmp_path):
    source = (
        "class Base:\n"
        "    def render(self):\n"
        "        raise NotImplementedError\n"
    )
    roles = _roles(tmp_path, "mod.py", source)
    assert roles["mod.Base.render"] == SymbolRole.INTERFACE
    # The class itself is not test-shaped, so it stays IMPLEMENTATION -
    # INTERFACE is a per-method body-shape signal, not inherited from a
    # class the way VERIFICATION is.
    assert roles["mod.Base"] == SymbolRole.IMPLEMENTATION


def test_bare_pass_stub_is_interface(tmp_path):
    source = (
        "class Base:\n"
        "    def render(self):\n"
        "        pass\n"
    )
    roles = _roles(tmp_path, "mod.py", source)
    assert roles["mod.Base.render"] == SymbolRole.INTERFACE


def test_docstring_only_pass_stub_is_still_interface(tmp_path):
    source = (
        "class Base:\n"
        "    def render(self):\n"
        "        '''Subclasses must implement this.'''\n"
        "        pass\n"
    )
    roles = _roles(tmp_path, "mod.py", source)
    assert roles["mod.Base.render"] == SymbolRole.INTERFACE


# --- Go: structural signal (TestXxx(t *testing.T)), no decorators/classes - #

def test_go_test_function_with_testing_t_param_is_verification(tmp_path):
    source = (
        "package mod\n\n"
        "func Seed() int {\n"
        "    return 1\n"
        "}\n\n"
        "func TestSeed(t *testing.T) {\n"
        "    if Seed() != 1 {\n"
        "        t.Fatal(\"unexpected\")\n"
        "    }\n"
        "}\n"
    )
    roles = _roles(tmp_path, "mod.go", source)
    assert roles["mod.TestSeed"] == SymbolRole.VERIFICATION
    assert roles["mod.Seed"] == SymbolRole.IMPLEMENTATION


def test_go_function_named_test_prefix_without_testing_param_is_not_verification(tmp_path):
    # Name alone isn't enough for Go - a function that merely starts with
    # "Test" but doesn't take a *testing.T/B is real production code (the
    # structural param-type check exists precisely to avoid this
    # misclassification).
    source = (
        "package mod\n\n"
        "func TestConnection(host string) bool {\n"
        "    return len(host) > 0\n"
        "}\n"
    )
    roles = _roles(tmp_path, "mod.go", source)
    assert roles["mod.TestConnection"] == SymbolRole.IMPLEMENTATION


# --------------------------------------------------------------------- #
# Weak test conventions apply only inside test files (held-out set 2)
# --------------------------------------------------------------------- #
def _roles_by_file(tmp_path, files):
    from prism.graph.concrete_builder import ConcreteGraphBuilder
    from prism.graph.symbol_table import GlobalSymbolTable
    paths = []
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        paths.append(str(path))
    builder = ConcreteGraphBuilder(str(tmp_path), GlobalSymbolTable())
    builder.pass1_collect_definitions(paths)
    return {s.qualified_name: s.role for s in builder.symbol_table}


def test_a_test_named_endpoint_in_application_code_is_implementation(tmp_path):
    from prism.graph.symbol_table import SymbolRole
    roles = _roles_by_file(tmp_path, {
        "app/routes.py": "@router.post('/test-email/')\ndef test_email(to):\n    return send(to)\n",
        "tests/test_routes.py": "def test_email_endpoint():\n    assert client.post('/test-email/').ok\n",
    })
    assert roles["app.routes.test_email"] == SymbolRole.IMPLEMENTATION
    assert roles["tests.test_routes.test_email_endpoint"] == SymbolRole.VERIFICATION


def test_calling_an_assert_named_helper_is_not_a_test_body_outside_tests(tmp_path):
    from prism.graph.symbol_table import SymbolRole
    body = "def s3_get_credentials():\n    config = assert_settings(['KEY'])\n    return Credentials(config)\n"
    roles = _roles_by_file(tmp_path, {"app/aws.py": body, "tests/helpers.py": body.replace("s3_get", "check")})
    assert roles["app.aws.s3_get_credentials"] == SymbolRole.IMPLEMENTATION
    assert roles["tests.helpers.check_credentials"] == SymbolRole.VERIFICATION  # inside tests the convention holds


def test_explicit_test_signals_still_apply_everywhere(tmp_path):
    from prism.graph.symbol_table import SymbolRole
    roles = _roles_by_file(tmp_path, {"app/fixtures.py": "import pytest\n\n\n@pytest.fixture\ndef user():\n    return 1\n"})
    assert roles["app.fixtures.user"] == SymbolRole.VERIFICATION
