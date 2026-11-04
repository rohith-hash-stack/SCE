"""R2 harness: section removal, prompt parity with R1, and the analysis rules on synthetic scores (no model)."""
from __future__ import annotations

import json
import random

import pytest

from harness.experiments.r2 import analyze, build

CTX = """# Change context: `m.f`
m.py, lines 1-3

## 1. Codebase
10 functions in 2 directories

## 2. Group `m`
3 functions

## 3. Anchor
Code: already in view.

## 4. Who depends on it
- `m.g` (m.py)
  - L5 result: used

## 5. What it depends on
nothing

## 6. Style to follow
Conventions in `m`:
- snake_case

## 7. Callers' code
`m.g` (m.py):
```python
def g(): f()
```

## 8. Wider impact
1 transitive callers

Omitted: 2 callers without code or digest (over budget)
"""


def test_drop_sections_removes_exactly_the_named_sections():
    assert build.drop_sections(CTX, frozenset()) == CTX
    no47 = build.drop_sections(CTX, frozenset({4, 7}))
    assert "## 4." not in no47 and "## 7." not in no47 and "def g()" not in no47
    for n in (1, 2, 3, 5, 6, 8):
        assert f"## {n}." in no47
    assert no47.endswith("Omitted: 2 callers without code or digest (over budget)\n")
    secs = build.sections(CTX)
    assert sorted(secs) == list(range(1, 9))
    assert "`m.g`" in secs[4] and "`m.g`" in secs[7] and "`m.g`" not in secs[5]
    # removing a section removes its text and nothing else
    assert len(CTX) - len(build.drop_sections(CTX, frozenset({6}))) == len(secs[6])


def test_full_prompt_is_r1_prism_prompt():
    from harness.experiments.change_history.run import prompt as r1_prompt
    t = json.loads((build.SUITE / "axios" / "forward.json").read_text())["tasks"][0]
    assert build.base_prompt(t, "FULL") == r1_prompt(t, "PRISM")
    assert build.prompt(t, "FULL", {}) == r1_prompt(t, "PRISM")


def test_versions_and_seeds_are_the_registered_ones():
    from harness.experiments.r2 import run
    assert set(build.VERSIONS) == {"FULL", "NO4", "NO6", "NO7", "NO47"}
    assert run.SEEDS == (42, 1001)
    assert analyze.MARGIN == 0.03 and analyze.FLOOR == 0.05


def _write(tmp_path, split, effects, n=60, seed=0):
    """Synthetic scores: per task and seed, FULL finds `base` of 4 callers; version V finds base - effect[V]."""
    rng = random.Random(seed)
    rows = {}
    for i in range(n):
        base = rng.choice((1, 2, 3, 4))
        for s in analyze.SEEDS:
            for v in build.VERSIONS:
                e = effects.get(v, 0.0)
                k = base - (round(e * 4) if rng.random() < 0.9 else 0) if e else base
                k = max(0, min(4, k))
                rows[f"t{i}/{v}@{s}"] = {"task": f"t{i}", "repo": "r", "split": split, "language": "python",
                                         "file_language": "python", "tier": "easy", "version": v, "seed": s,
                                         "changed": 4, "found": ["c"] * k, "missed": ["c"] * (4 - k), "extra": [],
                                         "done_reason": "stop"}
    d = tmp_path / "reports" / split / "r"
    d.mkdir(parents=True)
    (d / "scores.json").write_text(json.dumps(rows))


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "OUT", tmp_path / "reports")
    monkeypatch.setattr(analyze, "PADS", tmp_path / "pads")

    def make(effects):
        for split in build.SPLITS:
            _write(tmp_path, split, effects, seed=1 if split == "dev" else 2)
        return analyze.analyze()
    return make


def test_identical_versions_are_dead_weight_and_pruned(synthetic):
    res = synthetic({})
    for s in (4, 6, 7):
        assert res["sections"][s]["dead_weight"] and res["sections"][s]["prune"]
        assert not res["sections"][s]["harmful"] and not res["sections"][s]["load_bearing"]
    assert res["hypotheses"]["R2-H6"]["label"] == "equivalent"


def test_a_section_that_helps_is_load_bearing_and_kept(synthetic):
    res = synthetic({"NO4": 0.5, "NO47": 0.75})
    s4 = res["sections"][4]
    assert s4["load_bearing"] and not s4["prune"]
    assert res["hypotheses"]["R2-H4"]["label"] == "pass"


def test_a_section_that_hurts_is_harmful_and_pruned(synthetic):
    res = synthetic({"NO6": -0.5})
    s6 = res["sections"][6]
    assert s6["harmful"] and s6["prune"] and not s6["dead_weight"]
    assert 6 in res["prune"]


def test_truncation_over_20_percent_makes_a_comparison_invalid(synthetic, tmp_path):
    synthetic({})
    p = tmp_path / "reports" / "dev" / "r" / "scores.json"
    rows = json.loads(p.read_text())
    for k, r in rows.items():
        if r["version"] == "NO6" and int(r["task"][1:]) < 20:
            r["done_reason"] = "length"
    p.write_text(json.dumps(rows))
    res = analyze.analyze()
    assert res["hypotheses"]["R2-H6"]["label"] == "invalid"
    assert not res["sections"][6]["prune"]


def test_tost_rejects_a_clear_difference():
    assert analyze.tost([0.2] * 30 + [0.3] * 30)["p"] > 0.05
    assert analyze.tost([0.0] * 30 + [0.01, -0.01] * 15)["p"] < 0.05
