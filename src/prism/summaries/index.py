"""The offline summary index (`.prism/summaries.json`) and the weight rule.

`prism summarize` (`prism.summaries.pipeline`) writes one plain-English
sentence per indexed function or method; `prism.ask` (`NLQIndex(...,
summaries="auto")`) scores `BM25(code) + w * BM25(summary)` with the
summary as an independent field. Serving never calls a model.

The weight is a rule, not a classifier:

    w = SUMMARY_WEIGHT (0.5)  when the index was built by the locked
                              pipeline (`LOCKED_PIPELINE`) and its valid
                              summaries cover >= MIN_COVERAGE (95%) of the
                              repository's functions and methods;
    w = 0.0                   otherwise (no index, another pipeline, or too
                              many functions changed since it was built).

A summary is valid only while the function's source is byte-identical to
the source it was written from (`source_sha`); an edited function loses
its summary on the next index build, so a stale sentence never ranks it.
w = 0.5 is the setting the pre-registered set-4 rule shipped
(`reports/purpose_search_set4/DECISION.md`).
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

#: The shipped summary-field weight and the coverage it requires.
SUMMARY_WEIGHT = 0.5
MIN_COVERAGE = 0.95
INDEX_FORMAT = 1


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def locked_pipeline() -> dict:
    """The locked candidate (harness/experiments/purpose_search/CANDIDATE.md):
    summarizer, card version, prompt and repair pass, by value."""
    from prism.summaries.pipeline import LOCKED_MODEL, REPAIR_ATTEMPTS, REPAIR_PROMPT, SUMMARY_PROMPT, VARIANT
    return {"model": LOCKED_MODEL, "variant": VARIANT, "card": "v2", "prompt_sha": _sha(SUMMARY_PROMPT)[:16],
            "repair_sha": _sha(REPAIR_PROMPT)[:16], "repair_attempts": REPAIR_ATTEMPTS}


def index_path(repo_root: str) -> Path:
    return Path(repo_root) / ".prism" / "summaries.json"


def source_sha(lines: list[str], line_range: tuple[int, int]) -> str:
    """Hash of a symbol's source lines - the same lines NLQIndex indexes."""
    return _sha("\n".join(lines[line_range[0] - 1:line_range[1]]))[:16]


def function_symbols(pool) -> list:
    """The functions and methods a summary index covers (classes are not
    summarized: their documents already contain their methods' text)."""
    return [s for s in pool if s.kind in ("function", "method")]


@dataclass
class SummaryState:
    """What `prism.ask` uses: the valid summaries and the weight it applies."""
    summaries: dict[str, str] = field(default_factory=dict)
    weight: float = 0.0
    present: bool = False
    locked: bool = False
    functions: int = 0
    valid: int = 0
    stale: int = 0
    reason: str = "no summary index"

    @property
    def coverage(self) -> float:
        return round(self.valid / self.functions, 4) if self.functions else 0.0

    def status(self) -> dict:
        return {"present": self.present, "locked_pipeline": self.locked, "functions": self.functions,
                "summarized": self.valid, "stale": self.stale, "coverage": self.coverage,
                "summary_weight": self.weight, "reason": self.reason}


def read_index(path: "str | Path") -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("format") == INDEX_FORMAT else None


def load_state(pool, source_of, path: "str | Path") -> SummaryState:
    """Validate the index at `path` against the current symbols. `pool`:
    NLQIndex's candidate symbols; `source_of(file) -> lines`."""
    fns = function_symbols(pool)
    state = SummaryState(functions=len(fns))
    data = read_index(path)
    if data is None:
        return state
    state.present = True
    state.locked = data.get("pipeline") == locked_pipeline()
    entries = data.get("functions") or {}
    for s in fns:
        e = entries.get(s.qualified_name)
        if not isinstance(e, dict) or not e.get("summary"):
            continue
        if e.get("source_sha") != source_sha(source_of(s.file), s.line_range):
            state.stale += 1
            continue
        state.summaries[s.qualified_name] = str(e["summary"])
    state.valid = len(state.summaries)
    if not state.locked:
        state.reason = "index not built by the locked pipeline"
    elif state.coverage < MIN_COVERAGE:
        state.reason = f"coverage {state.coverage:.3f} < {MIN_COVERAGE} ({state.stale} stale)"
    else:
        state.weight, state.reason = SUMMARY_WEIGHT, "locked pipeline, coverage >= 0.95"
    return state


def state_for(builder, repo_root: str) -> SummaryState:
    """`load_state` for a builder's candidate pool (what `get_graph_status`
    reports; `NLQIndex(..., summaries="auto")` computes the same)."""
    from prism.query.nlq import candidate_pool, read_lines
    root = os.path.abspath(repo_root)
    lines: dict[str, list[str]] = {}
    return load_state(candidate_pool(builder), lambda f: lines.setdefault(f, read_lines(root, f)), index_path(root))


def write_index(path: "str | Path", pipeline: dict, functions: dict[str, dict]) -> None:
    """Atomic write: a reader never sees half an index."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"format": INDEX_FORMAT, "pipeline": pipeline,
                               "functions": dict(sorted(functions.items()))}, indent=0) + "\n", encoding="utf-8")
    os.replace(tmp, path)
