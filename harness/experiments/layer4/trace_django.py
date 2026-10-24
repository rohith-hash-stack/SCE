"""Runtime ground truth for a focal Django function, through Django's own
test runner (`tests/runtests.py`, not pytest).

    PYTHONPATH=<SCE> <django venv>/bin/python -m harness.experiments.layer4.trace_django \\
        --root <django checkout> --focal django.db.models.base.Model.save_base \\
        --out manifest.json -- basic update model_inheritance

Installs the same profiler as the pytest plugin (`trace_py.Tracer`), the
SQL hook (`_patch_django`, one choke point for both cursor wrappers) and a
`unittest.TestCase.run` hook for test names, then runs `runtests.py` in
this process with `--parallel=1` (parallel workers would escape the
profiler). Test labels are runtests labels: an app (`queries`), a module
(`queries.test_q`) or a test (`basic.tests.ModelTest.test_save`). The
manifest gains `exit_code`, `labels` and `env_skipped` (`ROOT_ONLY_TESTS`
skipped because the run is root's).
"""
from __future__ import annotations

import argparse
import json
import os
import runpy
import sys
import threading
import unittest

from harness.experiments.layer4.trace_py import Tracer, _patch_django

#: tests that assert a permission denial after chmod: root bypasses file permissions, so as root they
#: fail whatever the code does (Kaggle runs as root). Skipped only when the euid is 0, and listed
#: in the manifest (`env_skipped`).
ROOT_ONLY_TESTS = {
    "template_tests.test_loaders.FileSystemLoaderTests.test_permissions_error",
    "file_uploads.tests.DirectoryCreationTests.test_readonly_root",
    "i18n.test_compilation.PoFileTests.test_no_write_access",
}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--root", required=True, help="the Django checkout")
    p.add_argument("--focal", required=True, help="comma-separated qualified names")
    p.add_argument("--out", required=True)
    p.add_argument("--in-tests-only", action="store_true",
                   help="count focal calls only inside a test's run (not test-database setup / setUpClass)")
    p.add_argument("labels", nargs="+", help="runtests.py labels (after --)")
    args = p.parse_args(argv)

    root = os.path.realpath(args.root)
    tracer = Tracer(root, {f.strip() for f in args.focal.split(",") if f.strip()}, in_tests_only=args.in_tests_only)

    run_test = unittest.TestCase.run
    as_root = hasattr(os, "geteuid") and os.geteuid() == 0
    env_skipped = []

    def run(self, result=None):
        if as_root and self.id() in ROOT_ONLY_TESTS:
            env_skipped.append(self.id())
            setattr(self, self._testMethodName, unittest.skip("asserts a permission denial; running as root")(lambda: None))
        tracer.current_test = self.id()
        try:
            return run_test(self, result)
        finally:
            tracer.current_test = None
    unittest.TestCase.run = run

    _patch_django(tracer)
    tests_dir = os.path.join(root, "tests")
    sys.argv = [os.path.join(tests_dir, "runtests.py"), *args.labels, "--parallel=1", "-v", "0"]
    sys.path.insert(0, tests_dir)  # as when runtests.py runs as a script (test_sqlite settings, test apps)
    sys.setprofile(tracer.profile)
    threading.setprofile(tracer.profile)
    code = 1  # anything but a clean SystemExit is a failure
    try:
        runpy.run_path(sys.argv[0], run_name="__main__")
        code = 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code in (None, False) else 1)
    finally:
        sys.setprofile(None)
        threading.setprofile(None)
        manifest = tracer.manifest() | {"exit_code": code, "labels": args.labels, "in_tests_only": args.in_tests_only,
                                        "env_skipped": sorted(env_skipped)}
        with open(args.out, "w") as fh:
            json.dump(manifest, fh, indent=1, sort_keys=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
