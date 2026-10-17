"""Layer 4 Python runtime tracer (`harness/experiments/layer4/trace_py.py`)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from harness.experiments.layer4.trace_py import Tracer, is_test_path, qualified

SCE = Path(__file__).resolve().parents[1]

APP = '''
class Repo:
    def save(self, row):
        return self._write(row)

    def _write(self, row):
        return normalize(row)


def normalize(row):
    def inner(x):
        return x.strip()
    return inner(row)


def create(row):
    return Repo().save(row)


def unrelated():
    return 1
'''

VIEWS = '''
from app.core import create


def handler(payload):
    return create(payload)


def other():
    from app.core import unrelated
    return unrelated()
'''

TESTS = '''
from app.views import handler, other


def test_handler():
    assert handler(" a ") == "a"


def test_other():
    assert other() == 1
'''


@pytest.fixture(scope="module")
def manifest(tmp_path_factory):
    root = tmp_path_factory.mktemp("l4")
    for rel, text in {"app/__init__.py": "", "app/core.py": APP, "app/views.py": VIEWS,
                      "tests/test_flow.py": TESTS}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(textwrap.dedent(text))
    out = root / "manifest.json"
    env = dict(os.environ, L4_ROOT=str(root), L4_FOCAL="app.core.Repo.save", L4_OUT=str(out),
               PYTHONPATH=os.pathsep.join([str(root), str(SCE)]))
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "harness.experiments.layer4.trace_py",
                          "-p", "no:cacheprovider", "tests"], cwd=root, env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    return json.loads(out.read_text())


def test_ancestors_are_every_repo_caller_on_the_stack(manifest):
    assert manifest["ancestors"] == {"app.core.create": 1, "app.views.handler": 1}


def test_descendants_are_what_the_focal_function_runs(manifest):
    assert manifest["descendants"] == {"app.core.Repo._write": 1, "app.core.normalize": 2}  # inner() folds into normalize


def test_tests_and_calls(manifest):
    assert manifest["tests"] == ["tests/test_flow.py::test_handler"]
    assert manifest["focal_calls"] == {"app.core.Repo.save": 1}
    assert "app.core.unrelated" not in manifest["descendants"]


def test_names_and_test_paths():
    assert qualified("django/db/backends/base/base.py", "BaseDatabaseWrapper.cursor") == \
        "django.db.backends.base.base.BaseDatabaseWrapper.cursor"
    assert qualified("pkg/__init__.py", "f.<locals>.g") == "pkg.f"
    assert is_test_path("tests/test_x.py") and is_test_path("app/test_y.py") and not is_test_path("app/core.py")


def test_sql_is_recorded_only_while_the_focal_function_runs(tmp_path):
    t = Tracer(str(tmp_path), {"x"})
    t.on_sql("SELECT * FROM auth_user")
    assert t.sql == {}
    t._stack()
    t.local.depth = 1
    t.on_sql('INSERT INTO "auth_user" ("name") VALUES (%s)')
    t.on_sql("UPDATE auth_user SET name = %s")
    t.on_sql("SELECT a.id FROM auth_user a JOIN auth_group g ON 1")
    assert t.manifest()["sql"] == {"auth_group": {"SELECT": 1}, "auth_user": {"INSERT": 1, "SELECT": 1, "UPDATE": 1}}
