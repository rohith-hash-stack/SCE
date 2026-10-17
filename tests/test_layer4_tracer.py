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


RUNTESTS = '''
import sys
import unittest

import helper_on_tests_path  # only importable when tests/ is on sys.path, as runtests.py assumes

from pkg.core import focal


class T(unittest.TestCase):
    def test_calls_focal(self):
        self.assertEqual(focal(2), 4)

    def test_other(self):
        self.assertTrue(True)


if __name__ == "__main__":
    labels = [a for a in sys.argv[1:] if not a.startswith("-") and a != "0"]
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(T)
    result = unittest.TextTestRunner(verbosity=0).run(suite)
    sys.exit(bool(result.failures or result.errors) or "boom" in labels)
'''


def _django_like(tmp_path, runtests=RUNTESTS):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "core.py").write_text("def double(x):\n    return x * 2\n\n\ndef focal(x):\n    return double(x)\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "helper_on_tests_path.py").write_text("")
    (tmp_path / "tests" / "runtests.py").write_text(textwrap.dedent(runtests))


def _trace_django(tmp_path, *labels):
    out = tmp_path / "m.json"
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(tmp_path), str(SCE)]))
    run = subprocess.run([sys.executable, "-m", "harness.experiments.layer4.trace_django", "--root", str(tmp_path),
                          "--focal", "pkg.core.focal", "--out", str(out), "--", *labels],
                         cwd=tmp_path, env=env, capture_output=True, text=True)
    return run.returncode, json.loads(out.read_text())


def test_django_adapter_runs_runtests_in_process(tmp_path):
    _django_like(tmp_path)
    code, m = _trace_django(tmp_path, "app")
    assert code == 0 and m["exit_code"] == 0 and m["labels"] == ["app"]
    assert m["focal_calls"] == {"pkg.core.focal": 1}
    assert m["descendants"] == {"pkg.core.double": 1}
    assert m["tests"] == ["__main__.T.test_calls_focal"]  # unittest.TestCase.run hook (class defined in the script)


def test_django_adapter_reports_failures_and_crashes(tmp_path):
    _django_like(tmp_path)
    assert _trace_django(tmp_path, "boom")[1]["exit_code"] == 1  # runtests' sys.exit(True)
    crash = tmp_path / "crash"
    crash.mkdir()
    _django_like(crash, "raise RuntimeError('settings missing')\n")
    code, m = _trace_django(crash, "app")
    assert code != 0 and m["exit_code"] == 1  # a crash is never reported as a pass


def test_transaction_statements_get_their_own_bucket(tmp_path):
    t = Tracer(str(tmp_path), {"x"})
    t._stack()
    t.local.depth = 1
    for sql in ('SAVEPOINT "s1"', 'RELEASE SAVEPOINT "s1"', "BEGIN", 'DELETE FROM "a" WHERE "a"."id" IN (SELECT 1 FROM "a")'):
        t.on_sql(sql)
    assert t.manifest()["sql"] == {"_transaction": {"BEGIN": 1, "RELEASE": 1, "SAVEPOINT": 1}, "a": {"DELETE": 1}}
