"""R3a's interim look and final threshold (amendment R3-1 item 3; α from amendment R3-9)."""
import random

from harness.experiments.r3.analyze import interim_boundaries, interim_look, threshold

SEEDS = [42, 1001]


def _rows(n_tasks, p_prism, p_base, missing=()):
    rng = random.Random(7)
    tasks = [{"id": f"t{i:02d}", "family": "B"} for i in range(n_tasks)]
    rows = []
    for t in tasks:
        for v, p in (("PRISM", p_prism), ("BASELINE", p_base)):
            for s in SEEDS:
                out = "missing" if (t["id"], v, s) in missing else ("resolved" if rng.random() < p else "unresolved")
                rows.append({"run": f"{t['id']}__{v}__{s}", "task": t["id"], "family": "B", "version": v, "seed": s,
                             "outcome": out, "turns": 10, "file_f1": 0.5, "function_f1": 0.5, "used_tokens": 1000,
                             "run_tests_calls": 1, "depth": 1, "stratum": "1"})
    return tasks, rows


def test_r3_9_boundaries_at_half_information():
    b = interim_boundaries(0.5, 0.05)
    assert (b["interim_p"], b["interim_z"], b["final_p"], b["final_z"]) == (0.0031, 2.96, 0.049, 1.97)
    assert threshold("B") == 0.049


def test_a_large_effect_stops_at_the_interim_with_a_label():
    tasks, rows = _rows(80, 0.9, 0.1)
    look = interim_look(rows, tasks, "B", SEEDS)
    assert look["ready"] and look["tasks"] == 40 and look["stop"] and look["label"] == "pass"


def test_no_effect_continues():
    tasks, rows = _rows(80, 0.3, 0.3)
    look = interim_look(rows, tasks, "B", SEEDS)
    assert look["ready"] and not look["stop"] and look["label"] == "continue"


def test_the_look_waits_for_the_first_40_tasks_complete():
    tasks, rows = _rows(80, 0.9, 0.1, missing={("t05", "PRISM", 1001)})
    look = interim_look(rows, tasks, "B", SEEDS)
    assert look == {"ready": False, "tasks_missing_runs": ["t05"]}
