"""PRISM_MANIFEST_STRICT scaffold (M3 ablation lever, off by default): the
harness filters PRISM's Turn-1 manifest to downstream distance <= 2."""
import json
import os
from types import SimpleNamespace

import networkx as nx
import pytest

from harness import config as C
from harness.arms import arm5_prism
from harness.arms.arm5_prism import Arm5Prism, strict_manifest
from harness.llm import Completion

# synthetic graph: seed -> a -> b -> c (distances 1, 2, 3); x calls seed (upstream)
GRAPH = nx.DiGraph([("p.seed", "p.a"), ("p.a", "p.b"), ("p.b", "p.c"), ("p.x", "p.seed")])
SYMS = ["p.a", "p.b", "p.c", "p.seed", "p.x"]


def _distances(seed):
    return {n: float(d) for n, d in nx.single_source_shortest_path_length(GRAPH, seed).items() if n != seed}


def _manifest():
    return "<candidate_index>\n" + "\n".join(f"{s}|role|function|def {s}()|calls=[]" for s in SYMS) + "\n</candidate_index>"


class Words:
    name = "words"
    def count(self, t): return len(t.split())


class FakeEngine:
    builder = None

    def __init__(self):
        self.universe_seen = None

    def build_candidate_manifest(self, seed):
        return _manifest(), set(SYMS)

    def retrieve_requested(self, seed, budget, requested, universe, task_type=None):
        self.universe_seen = set(universe)
        nodes = [SimpleNamespace(id=s, file="p.py", line=1, end_line=2, compression="L0_full", body=f"def {s}(): ...",
                                 role="callee", cost=3, distance=1.0) for s in [seed, *requested] if s in universe]
        return SimpleNamespace(nodes=nodes), []


class EchoManifestLLM:
    """Turn 1 requests every symbol it was shown."""
    def __init__(self):
        self.shown = None

    def __call__(self, system, user, max_tokens=0, seed=None, purpose=""):
        self.shown = [ln.split("|")[0] for ln in user.split("\n") if ln.startswith("p.")]
        return Completion(json.dumps({"requested_symbols": self.shown}), 1, 5, 0.0, purpose=purpose)


def _run(monkeypatch, strict):
    monkeypatch.setattr(C, "PRISM_MANIFEST_STRICT", strict)
    monkeypatch.setattr(arm5_prism, "_downstream_distances", lambda engine, seed: _distances(seed))
    eng, llm = FakeEngine(), EchoManifestLLM()
    arm = Arm5Prism(llm=llm, tokenizer=Words(), engine=eng)
    ctx = arm.retrieve("q", {"task_id": "t", "task_type": "T2_localization", "seed_symbol": "p.seed"})
    return ctx, eng, llm


def test_default_is_off():
    assert C.PRISM_MANIFEST_STRICT is False and C.PRISM_MANIFEST_STRICT_MAX_HOPS == 2.0


def test_flag_off_keeps_distance_3_symbols(monkeypatch):
    ctx, eng, llm = _run(monkeypatch, False)
    assert "p.c" in llm.shown and "p.c" in eng.universe_seen          # distance 3 offered and hydratable
    assert ctx.build_meta["prism_manifest_strict"] is False
    assert ctx.build_meta["manifest_candidates"] == ctx.build_meta["manifest_candidates_unfiltered"] == 5


def test_flag_on_drops_distance_3_keeps_seed_and_upstream(monkeypatch):
    ctx, eng, llm = _run(monkeypatch, True)
    assert "p.c" not in llm.shown and "p.c" not in eng.universe_seen
    assert {"p.seed", "p.a", "p.b", "p.x"} == set(eng.universe_seen)  # seed, distances 1-2, upstream caller
    assert "p.c" not in ctx.delivered_symbols
    assert ctx.build_meta["prism_manifest_strict"] is True
    assert (ctx.build_meta["manifest_candidates"], ctx.build_meta["manifest_candidates_unfiltered"]) == (4, 5)


def test_strict_manifest_keeps_wrapper_lines():
    text, keep = strict_manifest(_manifest(), set(SYMS), "p.seed", _distances("p.seed"), 2.0)
    lines = text.split("\n")
    assert lines[0] == "<candidate_index>" and lines[-1] == "</candidate_index>"
    assert [ln.split("|")[0] for ln in lines[1:-1]] == ["p.a", "p.b", "p.seed", "p.x"] and keep == set(SYMS) - {"p.c"}


FASTAPI = "/home/user/SCE/.benchmarks/corpora/fastapi"


@pytest.mark.skipif(not os.path.isdir(FASTAPI), reason="FastAPI checkout missing")
def test_real_prism_manifest_t02_002():
    """On the real graph: 52 candidates -> 27 (25 within distance 2, plus 2 upstream callers); every gold
    symbol of t02_002 survives (gold read here, in the test, only)."""
    from harness.tasks.loaders import load_tasks
    task = [t for t in load_tasks("fastapi", repo_root=FASTAPI, limit=5) if "t02_002" in t.task_id][0]
    arm = Arm5Prism(tokenizer=Words())
    arm.index(FASTAPI, {})
    manifest, universe = arm.engine.build_candidate_manifest(task.seed_symbol)
    _text, keep = strict_manifest(manifest, universe, task.seed_symbol,
                                  arm5_prism._downstream_distances(arm.engine, task.seed_symbol), 2.0)
    assert (len(universe), len(keep)) == (52, 27)
    assert set(task.ground_truth.pipeline_symbols) <= keep


# ---- PRISM_TURN1_STRICT_PROMPT scaffold (off by default) ----
class SystemCapturingLLM(EchoManifestLLM):
    def __call__(self, system, user, max_tokens=0, seed=None, purpose=""):
        self.system = system
        return super().__call__(system, user, max_tokens, seed, purpose)


def test_turn1_strict_prompt_default_off_and_variants_differ():
    from benchmarks.run_two_pass_benchmark import TURN1_SYSTEM_PROMPT
    assert C.PRISM_TURN1_STRICT_PROMPT is False and C.PRISM_TURN1_TARGET_MAX == 20
    off = arm5_prism.turn1_system_prompt(TURN1_SYSTEM_PROMPT, False, 20)
    on = arm5_prism.turn1_system_prompt(TURN1_SYSTEM_PROMPT, True, 20)
    assert off == TURN1_SYSTEM_PROMPT                                   # flag off: PRISM's prompt, unchanged
    assert on != off and on.startswith(TURN1_SYSTEM_PROMPT) and "at most 20 symbols" in on


@pytest.mark.parametrize("strict", [False, True])
def test_turn1_strict_prompt_reaches_the_turn1_call_and_build_meta(monkeypatch, strict):
    from benchmarks.run_two_pass_benchmark import TURN1_SYSTEM_PROMPT
    monkeypatch.setattr(C, "PRISM_TURN1_STRICT_PROMPT", strict)
    llm = SystemCapturingLLM()
    arm = Arm5Prism(llm=llm, tokenizer=Words(), engine=FakeEngine())
    ctx = arm.retrieve("q", {"task_id": "t", "task_type": "T2_localization", "seed_symbol": "p.seed"})
    assert (llm.system != TURN1_SYSTEM_PROMPT) is strict
    assert ctx.build_meta["prism_turn1_strict_prompt"] is strict
    assert ctx.build_meta["turn1_requested_count"] == len(SYMS)        # the echo LLM requests the whole manifest
