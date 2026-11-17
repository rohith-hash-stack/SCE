"""The 3-arm re-baseline's Arm 3 (INJECTED): what is retrieved, and that the arm is otherwise PRISM's (its tools)."""
import json

from harness.experiments.r3 import loop as L
from harness.experiments.r4.inject import HEADER, SEPARATOR, dotted, feature_question, injected_context


class Layer:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def call(self, tool, **args):
        self.calls.append((tool, args))
        return self.answers.get(tool, "ok")


def test_delete_uses_change_context_on_the_named_target_and_falls_back_to_ask():
    task = {"family": "delete", "must_be_absent": "litestar/params.py::Dependency", "prompt": "Remove ..."}
    block, info = injected_context(task, Layer({"prism_change_context": "# Change context: x"}))
    assert info == {"tool": "prism_change_context", "query": "litestar.params.Dependency", "error": False,
                    "chars": len("# Change context: x")}
    assert block == f"{HEADER}\n\n# Change context: x{SEPARATOR}"
    lay = Layer({"prism_change_context": "error: symbol not found", "prism_ask": "hits"})
    block, info = injected_context({**task, "must_be_absent": "a/b.ts::f.g"}, lay)
    assert lay.calls == [("prism_change_context", {"symbol": "a.b.f.g"}), ("prism_ask", {"question": "f.g in a/b.ts"})]
    assert info["tool"] == "prism_ask" and info["fallback_from"].startswith("error") and "hits" in block


def test_feature_asks_with_the_description_from_the_prompt_only():
    prompt = ("Implement the following feature.\n\nfeat: add X\n\n* detail one\n\nTests for the feature must pass. "
              "They are already in the working tree and fail now:\n- t.py::test_x")
    assert feature_question(prompt) == "feat: add X * detail one"
    lay = Layer({"prism_ask": "hits"})
    injected_context({"family": "feature", "prompt": prompt, "gold_files": ["secret.py"]}, lay)
    assert lay.calls == [("prism_ask", {"question": "feat: add X * detail one"})]


def test_dotted_names():
    assert dotted("packages/zod/src/v4/classic/schemas.ts::object.shape") == "packages.zod.src.v4.classic.schemas.object.shape"
    assert dotted("pydantic/json_schema.py::GenerateJsonSchema._generate_definitions") == \
        "pydantic.json_schema.GenerateJsonSchema._generate_definitions"


def test_injected_arm_gets_baselines_tools_and_system_prompt(tmp_path):
    class Model:
        seen = []

        def __call__(self, msgs, seed):
            self.seen.append(msgs)
            return {"text": json.dumps({"tool": "done", "args": {}}), "thinking": None, "prompt_tokens": 1,
                    "answer_tokens": 1, "done_reason": "stop", "seconds": 0}

    class Shared:
        done_summary = None

        def call(self, tool, **args):
            return "done"
    m = Model()
    run = L.AgentRun({"id": "t", "prompt": "BLOCK\n\n---\n\nTASK"}, "INJECTED", 42, m, Shared())
    assert run.allowed == set(L.SHARED_DOC) | set(L.PRISM_DOC)
    run.run(str(tmp_path))
    assert m.seen[0][0]["content"] == L.system_prompt("PRISM") == L.system_prompt("INJECTED")
    assert m.seen[0][1]["content"] == "BLOCK\n\n---\n\nTASK"
    assert L.native_tools("INJECTED") == L.native_tools("PRISM")
    assert "prism_read_code" in {t["function"]["name"] for t in L.native_tools("INJECTED")}
    assert not {t["function"]["name"] for t in L.native_tools("BASELINE")} & set(L.PRISM_DOC)


def test_three_arm_report_and_holm(tmp_path):
    from harness.experiments.r4 import rebaseline_report as R
    assert R.holm({"a": 0.01, "b": 0.04, "c": None}) == {"a": 0.02, "b": 0.04, "c": None}
    tasks = [{"id": f"r4s-t{i}", "family": "feature", "fail_to_pass": {"x.py": ["f"]}, "pass_to_pass": {}}
             for i in range(4)]
    json.dump({"tasks": tasks}, open(tmp_path / "tasks.json", "w"))
    passed = {"BASELINE": [0, 0, 1, 0], "PRISM": [1, 0, 1, 0], "INJECTED": [1, 1, 1, 1]}
    for arm, vals in passed.items():
        for i, v in enumerate(vals):
            for seed in (42, 43):
                run = f"r4s-t{i}__{arm}__{seed}"
                doc = {"run": run, "task": f"r4s-t{i}", "version": arm, "seed": seed, "end": "done", "turns": 3,
                       "used_tokens": 10, "score": {"hidden_patch_applied": True, "resolved": bool(v),
                                                    "tests": {"x.py::f": "passed" if v else "failed"}}}
                if arm == "INJECTED":
                    doc["injected"] = {"tool": "prism_ask", "query": "q", "error": False, "chars": 9, "tokens": 5}
                json.dump(doc, open(tmp_path / f"{run}.score.json", "w"))
    rep = R.analyse(str(tmp_path), str(tmp_path / "tasks.json"))
    assert rep["arms"] == ["BASELINE", "PRISM", "INJECTED"]
    assert set(rep["primary"]) == {"PRISM vs BASELINE", "INJECTED vs BASELINE"}
    assert rep["primary"]["INJECTED vs BASELINE"]["mean_diff"] == 0.75
    assert rep["primary"]["PRISM vs BASELINE"]["tasks_favouring"] == {"PRISM": 1, "BASELINE": 0, "tie": 3}
    assert "wilcoxon_p_holm" in rep["primary"]["INJECTED vs BASELINE"]
    assert rep["descriptive"]["INJECTED vs PRISM"]["mean_diff"] == 0.5
    assert rep["injection"] == {"runs": 8, "tools": {"prism_ask": 8}, "fallbacks": 0, "errors": 0, "mean_tokens": 5}
