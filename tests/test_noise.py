import math

import pytest

from harness import config as C
from harness.scoring import noise as N
from harness.scoring.canonical import DeliveredItem
from harness.scoring.fairness import verify_ranking


class Words:
    name = "words"
    def count(self, t): return len(t.split())
    def truncate(self, t, n): return " ".join(t.split()[:n])


def _repo(tmp_path):
    (tmp_path / "api").mkdir()
    (tmp_path / "other").mkdir()
    (tmp_path / "api" / "routing.py").write_text("\n".join(f"route_line {i}" for i in range(200)))
    (tmp_path / "api" / "params.py").write_text("\n".join(f"param_line {i}" for i in range(200)))
    (tmp_path / "other" / "routing_utils.py").write_text("\n".join(f"util_line {i}" for i in range(200)))
    (tmp_path / "other" / "zzz.py").write_text("\n".join(f"zzz_line {i}" for i in range(200)))
    return tmp_path


def _gold():
    return [DeliveredItem(f"g{i}", "gold " * 50, 50, i, "code_chunk", [f"s{i}"]) for i in (1, 2, 3, 4)]


def test_sweep_is_gated_off():
    assert C.NOISE_SWEEP_ENABLED is False
    with pytest.raises(RuntimeError):
        N.require_enabled()


def test_pools(tmp_path):
    root = _repo(tmp_path)
    gold = {"api/routing.py"}

    def names(t):
        return sorted(p.split("/")[-1] for p in N.noise_pool(str(root), gold, t))
    assert names("adjacent") == ["params.py"]
    assert names("same_domain") == ["routing_utils.py"]
    assert names("random") == ["params.py", "routing_utils.py", "zzz.py"]


def test_inject_hits_target_and_never_appends_at_end(tmp_path):
    root = _repo(tmp_path)
    v = N.inject_noise(_gold(), str(root), 0.25, "random", Words(), {"api/routing.py"}, seed=1)
    assert v.original_tokens == 200 and v.noise_tokens == 50
    verify_ranking(v.injected_items)
    assert v.injected_items[0].provenance.get("noise") is True          # at the start
    assert not v.injected_items[-1].provenance.get("noise")              # never at the end
    assert sum(1 for it in v.injected_items if not it.provenance.get("noise")) == 4
    v0 = N.inject_noise(_gold(), str(root), 0.0, "random", Words())
    assert [it.source_id for it in v0.injected_items] == ["g1", "g2", "g3", "g4"]


def test_curve_statistics():
    curve = {0.0: 0.8, 0.10: 0.75, 0.25: 0.6, 0.50: 0.4}
    assert N.noise_slope(curve) < 0
    assert N.noise_breakpoint(curve) == 0.25
    assert math.isnan(N.noise_breakpoint({0.0: 0.5, 0.5: 0.45}))
    assert N.auc_noise({0.0: 1.0, 0.5: 1.0}) == pytest.approx(1.0)
    assert 0 < N.auc_noise(curve) < 1
    assert set(N.FAILURE_REASONS) >= {"truncation", "distraction", "hallucination", "refusal", "format"}
