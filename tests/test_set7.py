"""Held-out set 7 (harness/experiments/heldout_retrieval/set7) and the DIGEST / V2 arms of the summary-grounding
harness - frozen-set integrity, the draw rule, the pre-registered decision, and generation with a mocked model."""
import hashlib
import json

from harness.experiments.heldout_retrieval.set7 import check as C
from harness.experiments.heldout_retrieval.set7 import score as S
from harness.experiments.summary_grounding import run as G
from prism.summaries.index import read_index


def test_set7_is_frozen_with_its_hash():
    ds = S.frozen_dataset()
    assert ds["status"] == "frozen" and len(ds["questions"]) == 120
    assert hashlib.sha256(json.dumps(ds["questions"], sort_keys=True).encode()).hexdigest() == \
        "d6f3b12fbe0bf8bf8d9e7cd96d6603f9aa45f450c9e10ad751ea95458c51ec2f"
    assert {r: sum(q["repo"] == r for q in ds["questions"]) for r in ds["repos"]} == \
        {"werkzeug": 30, "aiohttp": 30, "kysely": 30, "nest": 30}
    assert all(s["reason"] in C.SKIP_REASONS for s in ds["skips"])


def test_set7_questions_follow_the_draw():
    ds = json.loads((C.HERE / "dataset.json").read_text())
    draw = json.loads((C.HERE / "draw.json").read_text())
    assert C.draw_problems(ds, draw, partial=False) == []
    assert draw["seed"] == 7007


def test_draw_rule_rejects_a_skipped_over_function():
    draw = {"repos": {"r": {"order": [{"name": n} for n in ("a", "b", "c", "d")]}}}
    ds = {"repos": {"r": {}}, "skips": [{"repo": "r", "name": "b", "reason": "trivial"}],
          "questions": [{"id": "r1", "repo": "r", "gold": ["a"]}, {"id": "r2", "repo": "r", "gold": ["c"]}]}
    assert C.draw_problems(ds, draw, partial=True) == []
    ds["questions"][1]["gold"] = ["d"]  # c was neither asked nor skipped
    assert any("not a prefix" in p for p in C.draw_problems(ds, draw, partial=True))
    ds["skips"][0]["reason"] = "boring"
    assert any("reason" in p for p in C.draw_problems(ds, draw, partial=True))


def _rows(ranks: dict[str, list[int]]) -> dict:
    """{arm: [best gold rank per question]} -> score rows."""
    return {arm: {f"q{i}": {"ranks": {"g": r}, "recall@5": float(r <= 5), "recall@20": float(r <= 20), "mrr": 1 / r}
                  for i, r in enumerate(rs)} for arm, rs in ranks.items()}


def test_decision_rule_verdicts():
    n = 40
    worse, better = [10] * n, [1] * n
    q = S.decide(_rows({"BASE": worse, "V2": worse, "SRC": better, "DIGEST": better}))
    assert q["verdict"] == "DIGEST QUALIFIES to replace V2" and all(q["conditions"].values())
    src_better = S.decide(_rows({"BASE": [20] * n, "V2": [20] * n, "SRC": better, "DIGEST": [4] * n}))
    assert src_better["verdict"] == "DIGEST does not qualify: the raw source is better input than the digest"
    no_gain = S.decide(_rows({"BASE": worse, "V2": worse, "SRC": better, "DIGEST": worse}))
    assert no_gain["verdict"] == "DIGEST does not qualify: digest summaries do not improve on V2"
    assert "DIGEST vs V2" in no_gain["contrasts"] and not no_gain["contrasts"]["DIGEST vs V2"]["beats"]


def test_digest_prompt_differs_from_src_only_in_its_input_block():
    back = (G.DIGEST_PROMPT.replace(G._DIGEST_BLOCK, G._SRC_BLOCK).replace("Base it on the digest:", "Base it on the source:")
            .replace("present in the profile and digest;", "present in the profile and source;"))
    assert back == G.SRC_PROMPT


SOURCE = '''def normalize_port(port, scheme):
    if port is None or port == "":
        return None
    try:
        value = int(port)
    except ValueError:
        raise ValueError("bad port")
    return None if value == {"http": 80}.get(scheme) else value


def build(url):
    return normalize_port(url, "http")
'''


class FakeLLM:
    def __init__(self):
        self.prompts = []
        self.usage = {"calls": 0, "failed": 0}

    def json(self, prompt, max_out, seed=42):
        self.prompts.append(prompt)
        names = [ln.split(":", 1)[1].strip() for ln in prompt.splitlines() if ln.startswith("Qualified Name:")]
        if "Write one plain-English sentence" in prompt:  # SRC / DIGEST: one card
            return {"summary": "Converts a port to a number and drops the default port of the scheme"}
        return {q: "Converts the given port value into a number or nothing at all" for q in names}  # v2 batches


def _toy(tmp_path):
    repo = tmp_path / "apps" / "toy"
    repo.mkdir(parents=True)
    (repo / "ports.py").write_text(SOURCE)
    return tmp_path / "apps", tmp_path / "out" / "toy"


def test_generate_digest_arm_sends_the_digest_not_the_source(tmp_path):
    apps, out = _toy(tmp_path)
    llm = FakeLLM()
    stats = G.generate_repo(llm, apps, "toy", {"root": "."}, out, threads=1, log=lambda _m: None, arms=("digest",))
    assert stats["digest"]["written"] == 2
    prompt = next(p for p in llm.prompts if "Qualified Name: ports.normalize_port" in p)
    assert "Digest of its code (lines 1-8)" in prompt and 'L7 raise ValueError("bad port")' in prompt
    assert "when port is None or port == \"\":" in prompt and "Source (lines" not in prompt
    data = json.loads((out / "digest.json").read_text())
    assert set(data) == {"ports.normalize_port", "ports.build"} and data["ports.build"]["omitted"] == 0
    calls = len(llm.prompts)
    G.generate_repo(llm, apps, "toy", {"root": "."}, out, threads=1, log=lambda _m: None, arms=("digest",))
    assert len(llm.prompts) == calls  # resumed


def test_generate_v2_arm_runs_the_shipped_summarizer_and_resumes(tmp_path):
    apps, out = _toy(tmp_path)
    llm = FakeLLM()
    stats = G.generate_repo(llm, apps, "toy", {"root": "."}, out, threads=1, log=lambda _m: None, arms=("v2",))
    assert stats["v2"]["summarized"] == 2
    index = read_index(out / "v2.json")
    assert set(index["functions"]) == {"ports.normalize_port", "ports.build"}
    assert index["pipeline"].get("lint", "v2") == "v2" and index["pipeline"]["model"] == G.LOCKED_MODEL
    (apps / "toy" / ".prism" / "summaries.json").unlink()  # a fresh session: the checkout's own index is gone
    calls = len(llm.prompts)
    again = G.generate_repo(llm, apps, "toy", {"root": "."}, out, threads=1, log=lambda _m: None, arms=("v2",))
    assert len(llm.prompts) == calls and again["v2"]["kept"] == 2  # restored from out/v2.json, nothing re-written


def test_set7_generate_cli_defaults_and_rejects_unknown_arms(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(S, "frozen_dataset", lambda: {"repos": {}})
    assert G.main(["generate", str(tmp_path), str(tmp_path / "out"), "--set", "7"]) == 0
    assert json.loads((tmp_path / "out" / "generation.json").read_text())["arms"] == list(G.SET7_ARMS)
    with pytest.raises(SystemExit):
        G.main(["generate", str(tmp_path), str(tmp_path / "out"), "--set", "7", "--arms", "digest,bogus"])


def test_render_lists_the_verdict_and_conditions():
    n = 40
    rows = _rows({"BASE": [10] * n, "V2": [10] * n, "SRC": [1] * n, "DIGEST": [1] * n})
    d = S.decide(rows)
    text = S.render({"verdict": d["verdict"], "conditions": d["conditions"], "contrasts": d["contrasts"],
                     "metrics": {a: {"recall@5": 1, "recall@20": 1, "mrr": 1} for a in rows},
                     "recall@5_by_repo": {a: {"r": 1} for a in rows}, "mean_words": {},
                     "gold_length_split": {"g": {"n": 1, "DIGEST vs SRC": {"better": 0, "worse": 0},
                                                 "DIGEST vs V2": {"better": 0, "worse": 0},
                                                 "SRC vs V2": {"better": 0, "worse": 0}}},
                     "coverage": {}, "questions_sha256": "x"})
    assert "**DIGEST QUALIFIES to replace V2**" in text and "1 DIGEST beats V2: yes" in text


def test_score_runs_end_to_end_on_a_toy_repository(tmp_path, monkeypatch):
    """The real scoring path (summary files -> NLQIndex -> rule -> DECISION.md) on a toy repo, not on set 7."""
    apps, out = _toy(tmp_path)
    llm = FakeLLM()
    G.generate_repo(llm, apps, "toy", {"root": "."}, out, threads=1, log=lambda _m: None, arms=G.SET7_ARMS)
    questions = [{"id": "t1", "repo": "toy", "category": "feature_logic", "question": "drop the default port of a scheme",
                  "gold": ["ports.normalize_port"], "decoys": []}]
    monkeypatch.setattr(S, "frozen_dataset", lambda: {"repos": {"toy": {"root": "."}}, "questions": questions,
                                                      "questions_sha256": "toy"})
    result = S.score(apps, tmp_path / "out", tmp_path / "decision")
    assert set(result["metrics"]) == set(S.ARMS) and result["coverage"] == {f"toy/{a}": 1.0 for a in S.ARMS[1:]}
    assert (tmp_path / "decision" / "DECISION.md").read_text().startswith("# Held-out set 7")
    assert json.loads((tmp_path / "decision" / "results.json").read_text())["verdict"] == result["verdict"]
