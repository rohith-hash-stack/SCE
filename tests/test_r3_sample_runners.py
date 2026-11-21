import os
import types

from harness.experiments.r3.gate import sample_runners


def _env(tmp_path, files):
    for f in files:
        p = tmp_path / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
    return types.SimpleNamespace(work=str(tmp_path))


def test_sample_folder_maps_to_suite_runner(tmp_path):
    env = _env(tmp_path, ["pkg/tests/runtime-runes/test.ts", "pkg/tests/css/test.ts"])
    changed = ["pkg/tests/runtime-runes/samples/a/_config.js", "pkg/tests/runtime-runes/samples/b/main.svelte",
               "pkg/tests/css/samples/c/expected.css"]
    assert sample_runners(env, changed) == ["pkg/tests/runtime-runes/test.ts", "pkg/tests/css/test.ts"]


def test_browser_suite_and_missing_runner_are_skipped(tmp_path):
    env = _env(tmp_path, ["pkg/tests/runtime-browser/test.ts"])
    changed = ["pkg/tests/runtime-browser/samples/a/_config.js", "pkg/tests/nothing/samples/b/x.js", "src/a.js"]
    assert sample_runners(env, changed) == []
