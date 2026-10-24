"""Phase P3 arms (harness/experiments/retrieval_pivot/p3.py) plugged into the Layer 4 runner."""
from __future__ import annotations

import threading
from types import SimpleNamespace

from harness.experiments.layer4 import context as X
from harness.experiments.layer4.runner import run_cell
from harness.experiments.retrieval_pivot import p3
from tests.test_p1_payload import _engine


def test_ten_arms_five_designs_two_budgets_registered_with_the_runner():
    assert len(p3.ARMS) == 10 and set(p3.ARMS) <= set(X.P3_ARMS)
    assert p3.ARMS["p3_R1_A5@8k"] == ("R1", "A4", 8000) and p3.ARMS["p3_R1_A4@4k"] == ("R1", "A6", 4000)
    assert p3.ARMS["p3_R0_A0@4k"] == ("R0", "A0", 4000)


def _task():
    return {"task_id": "t_fwd", "family": "forward_blast", "corpus": "fastapi", "prompt": "Which callers of seed?",
            "focal": {"qname": "app.m.seed", "file": "app/m.py", "language": "python"},
            "arm_view": {"exclude_paths": []}, "answer_contract": "forward_json_v1", "gold": {"affected_symbols": ["app.m.caller", "app.m.grand"]}}


def test_a_forward_context_is_built_rendered_and_leak_checked(tmp_path):
    e = _engine(tmp_path)
    corpus = SimpleNamespace(state=SimpleNamespace(builder=e.builder, contracts=e._contracts), clone_root=str(tmp_path),
                             lock=threading.RLock(), known={"app.m.caller", "app.m.grand"}, common={})
    r0 = p3.build_context(corpus, "p3_R0_A0@4k", _task(), None, None, None, None, None)
    r1 = p3.build_context(corpus, "p3_R1_A5@4k", _task(), None, None, None, None, None)
    assert r0["focal_in_context"] and not r0["leaked"] and "def seed(x):" in r0["rendered"]
    assert "app.m.grand" not in r0["selected_symbols"] and "app.m.grand" in r1["selected_symbols"]
    assert "[profile]" in r1["rendered"] and r1["engine_id"] == "p3_payload/p3_R1_A5@4k"


def test_the_runner_records_a_dry_run_cell_for_a_p3_arm(tmp_path):
    e = _engine(tmp_path)
    corpus = SimpleNamespace(state=SimpleNamespace(builder=e.builder, contracts=e._contracts), clone_root=str(tmp_path),
                             lock=threading.RLock(), known=set(), common={})
    cfg = SimpleNamespace(local=True, num_ctx=8192, budget=4000, token_ceiling=8000, model="m", temperature=0.0,
                          max_tokens=100)
    rec = run_cell(corpus, _task(), "p3_R1_A0@8k", 42, cfg, None, {}, "")
    assert rec["status"] == "dry_run", rec["error"]
    assert rec["arm"] == "p3_R1_A0@8k" and rec["tokens"]["context"] > 0
