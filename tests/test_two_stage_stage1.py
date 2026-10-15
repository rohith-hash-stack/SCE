"""Stage 1 of the two-stage validation (`harness/experiments/final_validation/stage1.py`):
SLM intent schema validation, caching, and an end-to-end tournament on the
Python fixture repo with deterministic stand-ins for jina and the reranker."""
from __future__ import annotations

import json
import os

import pytest

from harness.experiments.final_validation import stage1 as S
from tests.test_final_sweep_8arms import FIXTURE_REPO
from tests.test_two_stage_arms import HashEncoder, OverlapReranker

CHARGE = "src.services.billing.PaymentProcessor.charge"
MARK_PAID = "src.repositories.orders.OrderRepository.mark_paid"
VERIFY = "src.auth.jwt.verify_session"


def test_valid_intent_passes_through():
    intent, problems = S.validate_slm_intent('{"stems": ["Charge", "payment"], "action": "send", "capability": "PRIM_NET_CONNECT"}')
    assert problems == []
    assert intent == {"stems": ["charge", "payment"], "action": "SEND", "capability": "PRIM_NET_CONNECT"}


def test_fenced_reply_and_nulls():
    intent, problems = S.validate_slm_intent('Sure:\n```json\n{"stems": ["order"], "action": null, "capability": null}\n```')
    assert intent == {"stems": ["order"]} and problems == []


def test_invalid_parts_are_dropped_and_named():
    intent, problems = S.validate_slm_intent(
        '{"stems": ["ok", "has space", 3, "aa","bb","cc","dd","ee","ff","gg"], "action": "FLY", "capability": "GPU"}')
    assert intent["stems"] == ["ok", "aa", "bb", "cc", "dd", "ee"]
    assert "action" not in intent and "capability" not in intent
    assert any("action" in p for p in problems) and any("capability" in p for p in problems)
    assert any("more than 8" in p for p in problems)


@pytest.mark.parametrize("raw", ["no json here", "[1, 2]", "{broken"])
def test_unparseable_reply_is_none(raw):
    intent, problems = S.validate_slm_intent(raw)
    assert intent is None and problems


class FakeSLM:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def __call__(self, system, user, max_tokens, seed, purpose):
        self.calls += 1
        return type("Completion", (), {"text": self.reply})()


def test_extractor_caches_across_instances(tmp_path):
    llm = FakeSLM('{"stems": ["charge"], "action": "SEND", "capability": null}')
    cache = tmp_path / "slm.jsonl"
    first = S.SLMIntentExtractor(llm, "m", cache).extract("charge the card")
    again = S.SLMIntentExtractor(llm, "m", cache).extract("charge the card")
    assert llm.calls == 1 and first["intent"] == again["intent"] == {"stems": ["charge"], "action": "SEND"}
    S.SLMIntentExtractor(llm, "other-model", cache).extract("charge the card")
    assert llm.calls == 2  # the model is part of the key


def test_sign_test_and_bootstrap():
    assert S.sign_test_p(0, 0) == 1.0
    assert S.sign_test_p(10, 0) == pytest.approx(2 / 1024)
    lo, hi = S.stratified_bootstrap({"a": [1.0] * 5, "b": [0.0] * 5})
    assert lo == hi == 0.5


@pytest.fixture(scope="module")
def tournament(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("stage1")
    repos, sites = tmp / "repos", tmp / "sites"
    repos.mkdir()
    (sites / "fixture").mkdir(parents=True)
    os.symlink(FIXTURE_REPO, repos / "fixture")
    questions = [
        {"id": "f1", "repo": "fixture", "category": "behaviour", "question": "charge the customer's card for an order",
         "gold": [CHARGE], "decoys": [MARK_PAID]},
        {"id": "f2", "repo": "fixture", "category": "behaviour", "question": "record that an order has been paid",
         "gold": [MARK_PAID], "decoys": [CHARGE]},
        {"id": "f3", "repo": "fixture", "category": "behaviour", "question": "check the session token is valid",
         "gold": [VERIFY], "decoys": []},
    ]
    (tmp / "dataset.json").write_text(json.dumps({"questions": questions}))
    (tmp / "oracle.json").write_text(json.dumps({"intents": {
        "f1": {"action": "SEND", "capability": None}, "f2": {"action": "MUTATE", "capability": None}, "f3": {}}}))
    slm = S.SLMIntentExtractor(FakeSLM('{"stems": ["charge", "paid"], "action": "MUTATE", "capability": null}'),
                               "fake", tmp / "slm.jsonl")
    return S.run_stage1(str(repos), str(sites), str(tmp / "out"), slm=slm, encoder_factory=HashEncoder,
                        reranker_factory=OverlapReranker, question_sets=(("setx", tmp / "dataset.json", tmp / "oracle.json"),))


def test_every_engine_scores_every_question(tournament):
    rows = tournament["per_question"]
    assert len(rows) == 3
    for row in rows:
        assert set(row["systems"]) == set(S.ENGINES), row["id"]
    assert tournament["summary"]["engines"]["normal_oracle"]["overall"]["top1"] == 1.0


def test_shipped_ask_ranks_exactly_as_rule_intent(tournament):
    for row in tournament["per_question"]:
        assert row["systems"]["prism_shipped_ask"]["top5"] == row["systems"]["prism_rule_intent"]["top5"]
        assert row["systems"]["prism_shipped_ask"]["gold_ranks"] == row["systems"]["prism_rule_intent"]["gold_ranks"]
    assert any("shipped_payload" in r for r in tournament["per_question"])


def test_slm_accounting_and_markdown(tournament):
    s = tournament["summary"]
    assert s["slm"]["n"] == 3 and s["slm"]["fully_valid"] == 3 and s["slm"]["action_matches_oracle"] == 1
    assert {(c["a"], c["b"]) for c in s["contrasts"]} >= {("prism_slm_live", "hybrid_rrf_rerank")}
    md = S.render_stage1_markdown(tournament)
    assert "| `prism_slm_live` |" in md and "sign-test p" in md


def test_stage2_pools_seeds_within_tasks():
    from harness.experiments.final_validation import stage2 as T
    rows = []
    for repo in ("trpc", "django"):
        for t in range(4):
            for seed in range(10):
                rows.append({"repo": repo, "task_id": f"{repo}-{t}", "seed": seed, "engine_id": "prism_full",
                             "status": "ok", "tsr": 1.0 if t < 3 else 0.0, "context_tokens": 100})
                rows.append({"repo": repo, "task_id": f"{repo}-{t}", "seed": seed, "engine_id": "arm1_hybrid_rag",
                             "status": "ok", "tsr": 1.0 if t < 1 else 0.0, "context_tokens": 200})
    res = T.analyse(rows, {"prism_full": "prism_full", "arm1_hybrid_rag": "arm1_hybrid_rag"})
    assert res["arms"]["prism_full"]["tsr"] == 0.75 and res["arms"]["prism_full"]["n_tasks"] == 8
    (c,) = res["contrasts"]
    assert (c["n_tasks"], c["task_wins"], c["task_losses"]) == (8, 4, 0)  # tasks, not 80 cells
    assert c["delta_tsr"] == 0.5 and c["sign_p"] == 0.125
    md = T.render_stage2_markdown(res, "local-14b")
    assert "causal-trace success rate" in md and "| `prism_full` | 0.750 |" in md
