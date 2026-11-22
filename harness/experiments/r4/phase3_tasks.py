"""Phase 3's two task files, chosen by the rule pre-registered in AMENDMENTS ("Phase 3, hidden dependencies, two
stages", "Task selection, fixed now").

    python -B -m harness.experiments.r4.phase3_tasks RAW.json RAW_GOLD_DIR

From the validated hidden tasks (`hidden_tasks.py`, then `derive_scoring_check.py`; any task with `rejected` is left
out), ranked by grep's precision, lowest first, then the most look-alikes, then the repository, then the name:
- `phase3/poc.json`: the top 4, at most one per repository, and `r4s-delete-05` (`"stage": "poc"`, 45 runs);
- `phase3/tasks.json`: the POC's 4, then the next 8 with at most 2 per repository (3 if fewer than 12 qualify), and
  the controls `r4s-delete-05`, `r4s-delete-08`, `r4s-delete-10` (`"stage": "confirmatory"`, 225 runs).

The controls are copied unchanged from `phase2/tasks.json`. The answer keys go to `phase3/gold/`.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "phase3")
CONTROLS = ("r4s-delete-05", "r4s-delete-08", "r4s-delete-10")
POC_CONTROL = "r4s-delete-05"
POC_HIDDEN, CONF_HIDDEN = 4, 12
#: Phase 2's other scouted repositories (its scout covered them; no Phase 2 task came from them)
EXTRA_URLS = {"pydantic": "https://github.com/pydantic/pydantic.git", "aiohttp": "https://github.com/aio-libs/aiohttp.git",
              "typer": "https://github.com/fastapi/typer.git"}


def rank_key(t: dict):
    h = t["hidden"]
    gp = h["grep_precision"] if h["grep_precision"] is not None else 1.0
    return (gp, -h["lookalikes"], t["repo"], t["anchor_qname"])


def pick(tasks: list[dict], n: int, per_repo: int, start: list[dict] = ()) -> list[dict]:
    out = list(start)
    for t in sorted(tasks, key=rank_key):
        if len(out) >= n:
            break
        if t in out or sum(1 for x in out if x["repo"] == t["repo"]) >= per_repo:
            continue
        out.append(t)
    return out


def select(valid: list[dict]) -> tuple[list[dict], list[dict]]:
    poc = pick(valid, POC_HIDDEN, 1)
    conf = pick(valid, CONF_HIDDEN, 2, poc)
    if len(conf) < CONF_HIDDEN:
        conf = pick(valid, CONF_HIDDEN, 3, conf)
    return poc, conf


def _doc(tasks: list[dict], stage: str, runs: int, urls: dict) -> dict:
    return {"design": "phase3", "stage": stage, "expected_runs": runs,
            "repo_urls": {r: urls[r] for r in sorted({t["repo"] for t in tasks})}, "tasks": tasks,
            "tasks_sha256": hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest()}


def main(argv=None) -> int:
    raw_path, raw_gold = (argv if argv is not None else sys.argv[1:])[:2]
    raw = json.load(open(raw_path))
    valid = [t for t in raw["tasks"] if "rejected" not in t and t.get("scoring_check")]
    poc, conf = select(valid)
    p2doc = json.load(open(os.path.join(HERE, "phase2", "tasks.json")))
    p2, urls = {t["id"]: t for t in p2doc["tasks"]}, {**p2doc["repo_urls"], **EXTRA_URLS}
    controls = [p2[c] for c in CONTROLS]
    os.makedirs(os.path.join(OUT, "gold"), exist_ok=True)
    for t in conf:
        shutil.copy(os.path.join(raw_gold, f"{t['id']}.patch"), os.path.join(OUT, "gold", f"{t['id']}.patch"))
    json.dump(_doc(poc + [p2[POC_CONTROL]], "poc", (len(poc) + 1) * 3 * 3, urls), open(os.path.join(OUT, "poc.json"), "w"),
              indent=1)
    json.dump(_doc(conf + controls, "confirmatory", (len(conf) + len(controls)) * 3 * 5, urls),
              open(os.path.join(OUT, "tasks.json"), "w"), indent=1)
    print("poc:", [t["id"] for t in poc], "confirmatory:", [t["id"] for t in conf])
    return 0


if __name__ == "__main__":
    sys.exit(main())
