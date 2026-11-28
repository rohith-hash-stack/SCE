"""K4: absolute imports of a package outside the repository root (`prism.graph.import_roots`), and its safeguards."""
import os

from prism.graph.import_roots import ImportRoots


def _tree(root, files):
    for rel, text in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)
    return [os.path.join(root, r) for r in files if r.endswith(".py")]


def test_src_layout_package_resolves_to_the_repository_module(tmp_path):
    py = _tree(tmp_path, {"src/shelf/__init__.py": "", "src/shelf/core/__init__.py": "", "src/shelf/core/parse.py": ""})
    r = ImportRoots(str(tmp_path), py)
    assert r.resolve("shelf.core.parse") == "src.shelf.core.parse"
    assert r.resolve("shelf") == "src.shelf"


def test_root_level_and_unknown_imports_are_unchanged(tmp_path):
    py = _tree(tmp_path, {"pkg/__init__.py": "", "pkg/mod.py": ""})
    r = ImportRoots(str(tmp_path), py)
    assert r.resolve("pkg.mod") == "pkg.mod"            # already PRISM's name
    assert r.resolve("json.decoder") == "json.decoder"  # not in the repository


def test_a_declared_dependency_stays_external_even_with_a_local_module_of_that_name(tmp_path):
    py = _tree(tmp_path, {"pyproject.toml": '[project]\nname = "app"\ndependencies = ["requests>=2"]\n',
                          "src/requests/__init__.py": "", "src/app/__init__.py": ""})
    r = ImportRoots(str(tmp_path), py)
    assert r.resolve("requests") == "requests"
    assert r.resolve("app") == "src.app"


def test_requirements_and_lockfiles_count_as_declared(tmp_path):
    py = _tree(tmp_path, {"requirements-dev.txt": "Rich==13.0  # comment\n-e .\n",
                          "uv.lock": 'version = 1\n[[package]]\nname = "attrs"\n',
                          "src/rich/__init__.py": "", "src/attrs/__init__.py": "", "src/mine/__init__.py": ""})
    r = ImportRoots(str(tmp_path), py)
    assert r.resolve("rich") == "rich" and r.resolve("attrs") == "attrs" and r.resolve("mine") == "src.mine"


def test_a_workspace_member_declared_as_a_dependency_is_internal(tmp_path):
    """pydantic-ai's `pydantic_ai_slim` depends on `pydantic-graph`, a member of the same repository."""
    py = _tree(tmp_path, {
        "pydantic_ai_slim/pyproject.toml": '[project]\nname = "pydantic-ai-slim"\ndependencies = ["pydantic-graph"]\n',
        "pydantic_graph/pyproject.toml": '[project]\nname = "pydantic-graph"\n',
        "pydantic_ai_slim/pydantic_ai/__init__.py": "", "pydantic_graph/pydantic_graph/__init__.py": "",
        "pydantic_graph/pydantic_graph/nodes.py": ""})
    r = ImportRoots(str(tmp_path), py)
    assert r.resolve("pydantic_graph.nodes") == "pydantic_graph.pydantic_graph.nodes"
    assert r.resolve("pydantic_ai") == "pydantic_ai_slim.pydantic_ai"


def test_vendored_code_is_never_bound(tmp_path):
    py = _tree(tmp_path, {"vendor/six/__init__.py": "", "src/app/__init__.py": ""})
    assert ImportRoots(str(tmp_path), py).resolve("six") == "six"


def test_ambiguous_roots_prefer_src_then_non_test_and_otherwise_stay_unresolved(tmp_path):
    py = _tree(tmp_path, {"src/util/__init__.py": "", "tests/util/__init__.py": "",
                          "a/lib/__init__.py": "", "b/lib/__init__.py": "",
                          "tests/fixtures/helper/__init__.py": "", "app/helper/__init__.py": ""})
    r = ImportRoots(str(tmp_path), py)
    assert r.resolve("util") == "src.util"
    assert r.resolve("lib") == "lib"              # two equal roots: not guessed
    assert r.resolve("helper") == "app.helper"    # the test-fixture root loses


def test_namespace_package_resolves_and_its_external_part_stays_external(tmp_path):
    """poetry: `src/poetry` has no __init__.py; `poetry.core` comes from the external `poetry-core`."""
    py = _tree(tmp_path, {"pyproject.toml": '[project]\nname = "poetry"\ndependencies = ["poetry-core"]\n',
                          "src/poetry/console/__init__.py": "", "src/poetry/console/application.py": ""})
    r = ImportRoots(str(tmp_path), py)
    assert r.resolve("poetry.console.application") == "src.poetry.console.application"
    assert r.resolve("poetry.core.packages") == "poetry.core.packages"


def test_builder_links_callers_through_src_layout_imports(tmp_path):
    from prism.cli import build_pipeline
    _tree(tmp_path, {"src/shelf/__init__.py": "from .core import parse\n",
                     "src/shelf/core.py": "def parse(t):\n    return t\n",
                     "src/shelf/cli.py": "from shelf.core import parse\nimport shelf.core as c\n\n"
                                         "def main():\n    return parse('x')\n\ndef other():\n    return c.parse('y')\n"})
    b, _ = build_pipeline(str(tmp_path))
    callers = {c for c, _ in b.graph.in_edges("src.shelf.core.parse")}
    assert {"src.shelf.cli.main", "src.shelf.cli.other"} <= callers
    assert not b.graph.nodes["src.shelf.core.parse"].get("external")
