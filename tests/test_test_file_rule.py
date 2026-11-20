"""PRISM's test-file rule (`prism.scanner.entrypoint_gate.is_test_file`), Phase 2 bug fix (AMENDMENTS.md):

- a `spec/` directory holds tests only for JS/TS/Ruby files (RSpec, Jasmine); litestar's `litestar/openapi/spec`
  is Python production code;
- a module named `test.py`/`tests.py` is a test file only when its code is test code; werkzeug's public
  `werkzeug/test.py` (its test client) is production code, Django's `app/tests.py` is tests.
Through the builder, the harness's `include_tests=False` then shows these files' uses again.
"""
import os
import subprocess

from prism.scanner.entrypoint_gate import is_test_file


def test_spec_directories_by_language():
    assert not is_test_file("litestar/openapi/spec/components.py")
    assert not is_test_file("pkg/specs/model.py")
    assert not is_test_file("cmd/spec/main.go")
    for p in ("web/spec/a.spec.ts", "web/spec/helpers.js", "spec/models/user_spec.rb", "src/specs/x.tsx"):
        assert is_test_file(p), p


def test_test_module_by_content(tmp_path):
    (tmp_path / "werkzeug").mkdir()
    (tmp_path / "werkzeug" / "test.py").write_text("class Client:\n    def open(self):\n        pass\n\n\n"
                                                    "class TestResponse:\n    pass\n")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "tests.py").write_text("from django.test import TestCase\n\n\nclass T(TestCase):\n    pass\n")
    (tmp_path / "app" / "test.py").write_text("def test_add():\n    assert 1 + 1 == 2\n")
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "tests.py").write_text("import pytest\n\n\ndef helper():\n    return 1\n")
    root = str(tmp_path)
    assert not is_test_file("werkzeug/test.py", root)
    assert is_test_file("app/tests.py", root) and is_test_file("app/test.py", root)
    assert is_test_file("lib/tests.py", root)                         # imports pytest: test support
    assert is_test_file(str(tmp_path / "app" / "test.py"))            # an absolute path is read as is
    assert not is_test_file(str(tmp_path / "werkzeug" / "test.py"))
    assert is_test_file("missing/test.py", root)                      # unreadable: the name rule, as before


def test_builder_shows_spec_production_code(tmp_path):
    from prism.cli import LANGUAGE_TIER_PERMISSIVE, build_pipeline
    from prism.graph.references import references_to
    files = {"pkg/__init__.py": "", "pkg/openapi/__init__.py": "", "pkg/openapi/spec/__init__.py": "",
             "pkg/openapi/spec/example.py": "class Example:\n    pass\n",
             "pkg/openapi/spec/components.py": ("from pkg.openapi.spec.example import Example\n\n\n"
                                                 "class Components:\n    examples: Example = None\n"),
             "pkg/test.py": "from pkg.openapi.spec.example import Example\n\n\nclass Client:\n"
                            "    def make(self) -> Example:\n        return Example()\n"}
    for p, text in files.items():
        os.makedirs(os.path.dirname(tmp_path / p), exist_ok=True)
        (tmp_path / p).write_text(text)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    b, _ = build_pipeline(str(tmp_path), LANGUAGE_TIER_PERMISSIVE, use_cache=False)
    assert not b._in_test_file(str(tmp_path / "pkg/openapi/spec/components.py"))
    assert not b._in_test_file(str(tmp_path / "pkg/test.py"))
    sources = {r["source"] for r in references_to(b, "pkg.openapi.spec.example.Example", include_tests=False)}
    assert {"pkg.openapi.spec.components", "pkg.test"} <= sources
