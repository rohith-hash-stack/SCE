"""Summary-grounding experiment (harness/experiments/summary_grounding): numbered bodies, fact validation,
and resumable generation with a mocked model - no model calls."""
import json

from harness.experiments.summary_grounding import run as G

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
        if "Step 1." in prompt:
            return {"facts": [{"type": "exception", "text": "rejects a port that is not a number", "lines": [4, 7]},
                              {"type": "return", "text": "outside the function", "lines": [90, 91]},
                              {"type": "guess", "text": "unknown type", "lines": [2, 2]}],
                    "summary": "Rejects port values that are not numbers and drops default ports"}
        return {"summary": "Converts a port to a number and drops the scheme default port"}


def test_numbered_body_cuts_at_cap():
    lines = [f"line {i}" for i in range(1, 101)]
    text, shown_end, cut = G.numbered_body(lines, (10, 90), cap=5)
    assert text.splitlines()[0] == "10: line 10" and shown_end == 14 and cut
    assert text.endswith("(76 more lines)")
    text, shown_end, cut = G.numbered_body(lines, (10, 12), cap=5)
    assert shown_end == 12 and not cut


def test_valid_facts_drops_out_of_range_and_unknown_types():
    kept, dropped = G.valid_facts([{"type": "condition", "text": "checks x", "lines": [3, 4]},
                                   {"type": "condition", "text": "too late", "lines": [3, 40]},
                                   {"type": "opinion", "text": "nice", "lines": [3, 3]},
                                   {"type": "return", "text": "", "lines": [3, 3]},
                                   "not a dict"], 1, 10)
    assert kept == [{"type": "condition", "text": "checks x", "lines": [3, 4]}] and dropped == 4


def test_generate_writes_both_arms_and_resumes(tmp_path):
    repo = tmp_path / "apps" / "toy"
    repo.mkdir(parents=True)
    (repo / "ports.py").write_text(SOURCE)
    llm = FakeLLM()
    out = tmp_path / "out" / "toy"
    stats = G.generate_repo(llm, tmp_path / "apps", "toy", {"root": "."}, out, threads=1, log=lambda _m: None)
    assert stats["src"]["written"] == stats["src"]["functions"] == 2
    src = json.loads((out / "src.json").read_text())
    srcc = json.loads((out / "srcc.json").read_text())
    assert set(src) == set(srcc) == {"ports.normalize_port", "ports.build"}
    # the source reaches the prompt, numbered with its file lines
    assert any("Source (lines 1-8):" in p and '7:         raise ValueError("bad port")' in p for p in llm.prompts)
    fact = srcc["ports.normalize_port"]
    assert fact["facts"] == [{"type": "exception", "text": "rejects a port that is not a number", "lines": [4, 7]}]
    assert fact["facts_dropped"] == 2 and not fact["repaired"] and not fact["truncated"]
    calls = len(llm.prompts)
    G.generate_repo(llm, tmp_path / "apps", "toy", {"root": "."}, out, threads=1, log=lambda _m: None)
    assert len(llm.prompts) == calls  # resumed: nothing written twice


def test_accuracy_sample_is_the_audited_sixty():
    assert len(G.ACCURACY_SAMPLE) == 60 and len({tuple(x) for x in G.ACCURACY_SAMPLE}) == 60
    assert {r for r, _q in G.ACCURACY_SAMPLE} == {"httpx", "rich", "elysia", "oclif"}
