"""R3a's diagnostic counters (looping, early done, baseline-resolved check) and the missing-function covariate."""
import json

from harness.experiments.r3.analyze import diagnostics, turn_flags
from harness.experiments.r3.covariates import added_lines, missing_js, missing_python


def _log(tmp_path, turns):
    p = tmp_path / "r.jsonl"
    p.write_text("".join(json.dumps(t) + "\n" for t in turns))
    return str(p)


def test_three_identical_calls_without_an_edit_is_a_loop(tmp_path):
    c = {"tool": "prism_change_context", "args": {"symbol": "a.b"}, "output_head": "# Change context"}
    t = {"tool": "run_tests", "args": {}, "output_head": "x: 1 passed"}
    f = turn_flags(_log(tmp_path, [t, c, t, c, t, c]), {"end": "budget", "turns": 6})
    assert f == {"looping": True, "early_done": False, "applied_edits": 0}


def test_an_applied_edit_between_repeats_resets_the_count(tmp_path):
    t = {"tool": "run_tests", "args": {}, "output_head": "x"}
    e = {"tool": "edit_file", "args": {"path": "f", "old": "a", "new": "b"}, "output_head": "edited f at line 1"}
    refused = {"tool": "edit_file", "args": {"path": "f", "old": "z", "new": "b"}, "output_head": "error: not found"}
    f = turn_flags(_log(tmp_path, [t, e, t, e, t, refused, t]), {"end": "done", "turns": 7})
    assert f["looping"] is False and f["applied_edits"] == 2


def test_early_done_needs_done_within_five_turns_and_no_applied_edit(tmp_path):
    t = {"tool": "run_tests", "args": {}, "output_head": "x"}
    d = {"tool": "done", "args": {"summary": "s"}, "output_head": "done"}
    assert turn_flags(_log(tmp_path, [t, d]), {"end": "done", "turns": 2})["early_done"] is True
    e = {"tool": "edit_file", "args": {}, "output_head": "edited f at line 1"}
    assert turn_flags(_log(tmp_path, [t, e, d]), {"end": "done", "turns": 3})["early_done"] is False


def test_each_prism_loop_records_whether_baseline_resolved_the_task():
    rows = [{"run": "t1__PRISM__42", "task": "t1", "version": "PRISM", "seed": 42, "outcome": "budget", "looping": True,
             "early_done": False},
            {"run": "t1__BASELINE__42", "task": "t1", "version": "BASELINE", "seed": 42, "outcome": "resolved",
             "looping": False, "early_done": False},
            {"run": "t2__PRISM__42", "task": "t2", "version": "PRISM", "seed": 42, "outcome": "budget", "looping": True,
             "early_done": False},
            {"run": "t2__BASELINE__42", "task": "t2", "version": "BASELINE", "seed": 42, "outcome": "unresolved",
             "looping": False, "early_done": True}]
    cov = {"tasks": {"t1": {"missing_function": False}, "t2": {"missing_function": True}}}
    d = diagnostics(rows, cov)
    assert d["by_version"]["PRISM"] == {"runs": 2, "looping": 2, "early_done": 0}
    assert d["by_version"]["BASELINE"]["early_done"] == 1
    assert d["prism_loops_on_baseline_resolved_tasks"] == {"same_seed": 1, "both_failed_same_seed": 1, "of": 2}
    assert d["missing_function_split"]["PRISM/missing_function=True"]["looping"] == 1


def test_added_lines_follow_the_new_file_numbering():
    patch = "--- a/f\n+++ b/f\n@@ -3,3 +3,4 @@\n a\n-b\n+c\n+d\n e\n"
    assert added_lines(patch) == {4, 5}


def test_python_bare_call_to_an_unbound_name_is_missing():
    text = "from .helpers import set_result\n\ndef f(futs):\n    for x in futs:\n        set_result(x)\n" \
           "        set_exception(x)\n        len(futs)\n        x.done()\n"
    assert missing_python(text, {5, 6, 7, 8}) == ["set_exception"]
    assert missing_python("from m import *\n\ndef f():\n    g()\n", {4}) == "unknown"


def test_js_binding_forms_count_as_defined():
    text = ("import {a} from './a.js';\nimport * as ns from 'x';\nconst {b, c: d} = obj;\n"
            "const f = async ({p}) => {\n  a(); b(); d(); p(); ns.q(); getMessage(); setTimeout(); local();\n};\n"
            "function local() {}\n")
    assert missing_js(text, {5}, "lib/x.js") == ["getMessage"]


def test_typescript_files_parse_with_the_typescript_grammar():
    text = "export const f = (x: number): string => {\n  return fmt(x) + String(x);\n};\n"
    assert missing_js(text, {2}, "src/x.ts") == ["fmt"]
