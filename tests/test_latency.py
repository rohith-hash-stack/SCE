import math
import time

import pytest

from harness.scoring.latency import LAYERS, latency_profile, merge, percentiles, timed


def test_timed_records_ms_even_on_error():
    m = {}
    with timed(m, "L_retrieve"):
        time.sleep(0.01)
    with pytest.raises(RuntimeError):
        with timed(m, "L_retrieve"):
            raise RuntimeError
    assert len(m["L_retrieve"]) == 2 and m["L_retrieve"][0] >= 9.0


def test_warm_percentiles_drop_cold_sample():
    p = percentiles([1000.0, 10, 20, 30])
    assert p["n"] == 3 and p["p50"] == 20 and p["p99"] < 1000
    assert percentiles([5.0])["p50"] == 5.0
    assert math.isnan(percentiles([])["p50"])
    prof = latency_profile({"L_generate": [900.0, 100.0, 110.0]})
    assert prof["L_generate"]["cold_first_ms"] == 900.0 and prof["L_generate"]["p50"] == 105.0
    assert merge({"a": [1.0]}, {"a": [2.0], "b": [3.0]}) == {"a": [1.0, 2.0], "b": [3.0]}
    assert LAYERS == ("L_index", "L_retrieve", "L_generate", "L_e2e")


def test_module_never_uses_wall_clock():
    import inspect
    import harness.scoring.latency as L
    src = inspect.getsource(L)
    assert "perf_counter_ns" in src and "time.time(" not in src
