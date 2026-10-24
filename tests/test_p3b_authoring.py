"""P3b authoring (author.py --eligibility p3b), freezing and the runner guard (p3b_freeze)."""
from __future__ import annotations

import json

import pytest

from harness.experiments.layer4 import author as A
from harness.experiments.retrieval_pivot import p3b_freeze as F


def _specs(corpus, n):
    return [{"task_id": f"p3b_fwd_{corpus}_{r:04d}", "corpus": corpus, "family": "forward_blast",
             "qname": f"lib.f{r}", "draw_rank": r, "max_depth": 2} for r in range(1, n + 1)]


def _fake_author(out, outcome):
    """outcome: draw_rank -> gold count, or an exception message."""
    def author(spec, *a, **k):
        assert spec["min_gold"] == A.P3B_MIN_GOLD
        got = outcome[spec["draw_rank"]]
        if isinstance(got, str):
            raise SystemExit(f"{spec['task_id']}: {got}")
        task = {"task_id": spec["task_id"], "corpus": spec["corpus"], "focal": {"qname": spec["qname"]},
                "gold": {"affected_symbols": [f"g{i}" for i in range(got)],
                         "trace_manifest": f"{out.name}/manifests/{spec['task_id']}.json"}}
        (out / "tasks").mkdir(parents=True, exist_ok=True)
        (out / "manifests").mkdir(parents=True, exist_ok=True)
        (out / "tasks" / f"{spec['task_id']}.json").write_text(json.dumps(task))
        (out / "manifests" / f"{spec['task_id']}.json").write_text("{}")
        return task
    return author


def test_eligibility_keeps_the_first_eligible_in_draw_order_and_logs_every_skip(tmp_path, monkeypatch):
    outcome = {1: 5, 2: "trace exited 1", 3: "the focal function never ran in the traced tests",
               4: "2 gold symbols < 3 within 2 call hops", 5: "20 gold symbols > 15; lower max_depth", 6: 3, 7: 9, 8: 4}
    monkeypatch.setattr(A, "author", _fake_author(tmp_path, outcome))
    log = A.author_p3b(_specs("express", 8), {}, None, {"express": object()}, "abc", per_corpus=3,
                       log_path=tmp_path / "authoring_log.json")
    d = log["decisions"]
    assert [k for k, v in d.items() if v["status"] == "kept"] == ["p3b_fwd_express_0001", "p3b_fwd_express_0006",
                                                                 "p3b_fwd_express_0007"]
    assert [d[f"p3b_fwd_express_{r:04d}"]["reason"] for r in (2, 3, 4, 5)] == [
        "trace_exit_nonzero", "focal_not_run", "gold_below_min", "gold_above_max"]
    assert "p3b_fwd_express_0008" not in d  # three kept: the rest of the draw is never traced
    calls = []
    monkeypatch.setattr(A, "author", lambda spec, *a, **k: calls.append(spec) or (_ for _ in ()).throw(SystemExit("x")))
    A.author_p3b(_specs("express", 8), {}, None, {"express": object()}, "abc", per_corpus=3,
                 log_path=tmp_path / "authoring_log.json")
    assert calls == []  # resumed: nothing decided is traced again


def test_a_tooling_fault_aborts_instead_of_skipping_the_draw(tmp_path, monkeypatch):
    outcome = {1: "invalid task:\n  task_id: 'x' does not match '^l4_fwd_'", 2: 5}
    monkeypatch.setattr(A, "author", _fake_author(tmp_path, outcome))
    with pytest.raises(SystemExit, match="not an eligibility failure"):
        A.author_p3b(_specs("express", 2), {}, None, {"express": object()}, "abc", per_corpus=1,
                     log_path=tmp_path / "authoring_log.json")
    assert not (tmp_path / "authoring_log.json").exists()  # nothing logged: rank 1 is traced again on resume


def _authored(tmp_path, monkeypatch, n_per_corpus, corpora=("django", "fastapi", "express", "trpc")):
    out = tmp_path / "layer4_p3b"
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(F, "SCE", tmp_path)
    monkeypatch.setattr(F, "SPECS_FILE", tmp_path / "specs.py")
    (tmp_path / "specs.py").write_text("SPECS = []\n")
    out.mkdir()
    (out / "draw.json").write_text(json.dumps({"seed": F_seed(), "corpora": {c: {"skipped_before_specs": [
        {"rank": 99, "qname": "lib.x", "reason": "no_test_mention"}]} for c in corpora}}))
    for c in corpora:
        monkeypatch.setattr(A, "author", _fake_author(out, {r: 4 for r in range(1, 20)}))
        A.author_p3b(_specs(c, n_per_corpus), {}, None, {c: object()}, "abc", per_corpus=8, log_path=out / "authoring_log.json")
    return out


def F_seed():
    from benchmarks.layer4 import specs_p3b
    return specs_p3b.SEED


def test_freeze_records_hashes_seed_and_skips_and_verify_guards_the_files(tmp_path, monkeypatch):
    out = _authored(tmp_path, monkeypatch, 8)
    frozen = F.freeze(out)
    assert frozen["total"] == 32 and frozen["seed"] == 20261024 and frozen["short_corpora"] == []
    assert len(frozen["skipped"]["draw"]["express"]) == 1 and frozen["skipped"]["trace"] == []
    assert F.verify(out / "tasks")["total"] == 32
    with pytest.raises(SystemExit, match="re-frozen"):
        F.freeze(out)
    victim = out / "tasks" / "p3b_fwd_trpc_0001.json"
    victim.write_text(victim.read_text().replace("g0", "g9"))
    with pytest.raises(SystemExit, match="changed since the freeze: p3b_fwd_trpc_0001"):
        F.verify(out / "tasks")
    victim.unlink()
    (out / "tasks" / "p3b_fwd_trpc_9999.json").write_text("{}")
    with pytest.raises(SystemExit, match="missing p3b_fwd_trpc_0001"):
        F.verify(out / "tasks")


def test_freeze_refuses_below_24_tasks_and_keeps_a_short_corpus_natural_yield(tmp_path, monkeypatch):
    out = _authored(tmp_path, monkeypatch, 5)  # 4 x 5 = 20 < 24
    with pytest.raises(SystemExit, match="20 tasks < 24"):
        F.freeze(out)
    out2 = _authored(tmp_path / "b", monkeypatch, 8, corpora=("django", "fastapi", "trpc"))
    authored = out2 / "authoring_log.json"
    monkeypatch.setattr(A, "author", _fake_author(out2, {r: 4 for r in range(1, 20)}))
    A.author_p3b(_specs("express", 5), {}, None, {"express": object()}, "abc", per_corpus=8, log_path=authored)
    frozen = F.freeze(out2)
    assert frozen["total"] == 29 and frozen["short_corpora"] == ["express"]


def test_the_runner_refuses_a_p3b_directory_without_or_against_its_freeze(tmp_path):
    from harness.experiments.layer4 import runner as R
    tasks = tmp_path / "layer4_p3b" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "p3b_fwd_django_0001.json").write_text(json.dumps({"task_id": "p3b_fwd_django_0001"}))
    with pytest.raises(SystemExit, match="not frozen"):
        R.load_tasks([tasks])
