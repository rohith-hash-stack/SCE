"""The T5 Oracle is decoupled from PRISM's symbol table (M4): it delivers
exactly the gold affected set, reading a name PRISM does not index from the
gold's recorded definition location, and `adapt_oracle` raises when the
delivered symbols are not the gold set."""
from types import SimpleNamespace

import pytest

from harness.arms.oracle import Oracle
from harness.scoring.adapters import OracleCeilingError, adapt_oracle
from harness.tasks.schema import BlastRadiusTask, GroundTruth


class Words:
    name = "words"
    def count(self, t): return len(t.split())


class Table:
    """A stand-in symbol table that indexes only `known`."""
    def __init__(self, known):
        self.known = known
    def get(self, name):
        return self.known.get(name)


def _setup(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "init.ts").write_text(
        "export function createInner() {\n  const initInner = () => {\n    return build();\n  };\n"
        "  return initInner();\n}\n\nexport function build() {\n  return 1;\n}\n")
    known = {"core.init.build": SimpleNamespace(file=str(tmp_path / "core" / "init.ts"), line_range=(8, 10))}
    oracle = Oracle(tokenizer=Words(), builder=SimpleNamespace(symbol_table=Table(known)))
    oracle.index(str(tmp_path))
    gold = ["core.init.build", "core.init.createInner.initInner"]          # the second: not indexed by PRISM
    task = BlastRadiusTask(task_id="trpc_t5_x", repo_id="trpc", repo_root=str(tmp_path), query="What breaks?",
                           seed_symbol="core.init.seed", ground_truth=GroundTruth(pipeline_symbols=gold,
                                                                                  context_symbols=["core.init.ctx"]))
    return oracle, task, gold


def _raw(ctx):
    return {"bundle": ctx.to_dict(), "completion": {"text": "x", "generation_tokens": 1, "latency_seconds": 0.0,
                                                    "prompt_tokens_server": 0, "finish_reason": "stop", "model": "m"}}


def _seed(task, locations):
    return {**task.seed_dict(), "oracle_pipeline": list(task.ground_truth.pipeline_symbols),
            "oracle_universe": sorted(task.ground_truth.universe_symbols()), "oracle_locations": locations}


def test_t5_oracle_delivers_a_gold_name_prism_does_not_index(tmp_path):
    oracle, task, gold = _setup(tmp_path)
    locations = {"core.init.createInner.initInner": {"file": "core/init.ts", "start": 2, "end": 4}}
    ctx = oracle.retrieve(task.query, _seed(task, locations))
    assert ctx.delivered_symbols == set(gold)                      # exactly the gold: no seed, no context symbols
    assert ctx.build_meta["delivered_from_gold_location"] == ["core.init.createInner.initInner"]
    inner = next(i for i in ctx.items if i.symbols == ["core.init.createInner.initInner"])
    assert "const initInner = () => {" in inner.content and inner.source_id == "core/init.ts:2-4"
    ctx2, _ = adapt_oracle(_raw(ctx), task)                         # the assertion passes
    assert ctx2.delivered_symbols == set(gold)


def test_adapt_oracle_raises_when_a_gold_name_is_not_delivered(tmp_path):
    oracle, task, _ = _setup(tmp_path)
    ctx = oracle.retrieve(task.query, _seed(task, {}))             # no location for the unindexed name
    assert "core.init.createInner.initInner" in ctx.build_meta["unresolved_universe"]
    with pytest.raises(OracleCeilingError, match="delivered 1/2 gold names"):
        adapt_oracle(_raw(ctx), task)
