"""Stage 1 of the v3 validation (`harness.experiments.purpose_search.stage1`):
v2 -> v3-name / v3 conversion through the real `prism summarize` CLI, resume,
and the approved gates."""
from __future__ import annotations

import json
import re
import shutil

import pytest

from harness.experiments.purpose_search import stage1
from prism.cli import build_pipeline
from prism.summaries import index as S
from prism.summaries.pipeline import build_index
from tests.test_summarize_checkpoints import GOOD, FakeOllama

N = 8  # functions; op0-op3 get thin first-pass summaries


class ThinLLM:
    def json(self, prompt, max_out, seed=42):
        return {q: ("Runs the op." if int(q.rsplit("op", 1)[-1]) < 4 else GOOD)
                for q in re.findall(r"^Qualified Name: (\S+)$", prompt, re.M)}


@pytest.fixture()
def setup(tmp_path):
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "app/__init__.py").write_text("")
    (root / "app/ops.py").write_text("".join(f"def op{i}(x):\n    return x + {i}\n\n\n" for i in range(N)))
    builder = build_pipeline(str(root), use_cache=False)[0]
    build_index(builder, str(root), llm=ThinLLM())  # a v2 index with 4 thin summaries
    out = tmp_path / "out"
    (out / "repo").mkdir(parents=True)
    shutil.copy(S.index_path(str(root)), out / "repo" / "v2.json")
    manifest = {"repo": {"root": str(root), "cache": None, "live": None}}
    return manifest, out, root


def test_conversion_repairs_only_flagged_summaries_and_a_rerun_skips_finished_steps(setup):
    manifest, out, root = setup
    srv = FakeOllama()
    try:
        status = stage1.run(manifest, out, srv.url, threads=1, checkpoint_every=1, log=lambda m: None)
        assert status == {"repo/v2": "done (kept)", "repo/v3-name": "done", "repo/v3": "done"}
        assert srv.calls == 8  # 4 repairs per candidate, no summarizing
        v2, v3 = (S.read_index(out / "repo" / f"{c}.json") for c in ("v2", "v3"))
        assert v3["pipeline"]["lint"] == "v3" and S.read_index(out / "repo" / "v3-name.json")["pipeline"]["lint"] == "v3-name"
        changed = {q for q in v2["functions"] if v2["functions"][q]["summary"] != v3["functions"][q]["summary"]}
        assert changed == {f"app.ops.op{i}" for i in range(4)}
        calls = srv.calls
        assert set(stage1.run(manifest, out, srv.url, 1, 1, log=lambda m: None).values()) == {"done (kept)"}
        assert srv.calls == calls  # nothing redone
    finally:
        srv.release()


def test_an_interrupted_step_resumes_from_its_partial_snapshot(setup):
    manifest, out, root = setup
    v2 = S.read_index(out / "repo" / "v2.json")
    partial = {q: {**e, "checked": q in ("app.ops.op0", "app.ops.op1")} for q, e in v2["functions"].items()}
    partial["app.ops.op0"]["summary"] = partial["app.ops.op1"]["summary"] = GOOD  # repaired before the kill
    S.write_index(out / "repo" / "v3.partial.json", {**v2["pipeline"], "lint": "v3"}, partial)
    srv = FakeOllama()
    try:
        assert stage1.run_step("repo", manifest["repo"], "v3", out, srv.url, 1, 1,
                               [__import__("sys").executable, "-m", "prism.cli"], log=lambda m: None) == "done"
        assert srv.calls == 2  # only op2, op3 were left to repair
        assert not (out / "repo" / "v3.partial.json").exists()
    finally:
        srv.release()


def test_the_gate_passes_a_clean_conversion_and_fails_thin_or_lost_summaries(setup):
    manifest, out, root = setup
    srv = FakeOllama()
    try:
        stage1.run(manifest, out, srv.url, 1, 1, log=lambda m: None)
    finally:
        srv.release()
    r = stage1.gate(manifest, out)
    assert r["verdict"]["v3"]["passes"] and r["verdict"]["v3-name"]["passes"]
    assert r["pooled"]["v3"]["first_pass_flagged"] == 4 and r["pooled"]["v3"]["repaired"] == 4
    assert "## v3: **PASS**" in (out / "STAGE1.md").read_text()
    bad = S.read_index(out / "repo" / "v3.json")
    bad["functions"]["app.ops.op0"]["summary"] = "Runs the op."  # repair left it thin
    del bad["functions"]["app.ops.op7"]  # and a summary went missing
    S.write_index(out / "repo" / "v3.json", bad["pipeline"], bad["functions"])
    v = stage1.gate(manifest, out)["verdict"]["v3"]
    assert not v["passes"] and not v["checks"]["no summary lost"]
    assert not v["checks"]["restates_name <= 0.1"]  # 1 of 7 = 14%
    assert json.loads((out / "stage1.json").read_text())["verdict"]["v3"]["passes"] is False
