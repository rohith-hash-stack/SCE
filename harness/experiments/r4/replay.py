"""Phase 0b: replay a logged run deterministically, and compare two replays.

    python -B -m harness.experiments.r4.replay run RUN.jsonl TASK.json OUT_DIR --clone DIR --cache DIR \
        --version PRISM|BASELINE [--seed 42] [--turn-cap 28] [--ledger] [--plan]
    python -B -m harness.experiments.r4.replay compare A_DIR B_DIR [--out report.json]

**run:**
- **Model side:** the model is replaced by the run's recorded replies, served in order: the reply **text** (so
  `parse_call` runs too), the thinking, the token counts and the finish reason. When the recorded replies run out,
  the replay model raises, as the original call did when a run ended on a model error.
- **Everything else is real:** worktree, environment, index, tools, gate, ledger, window, scoring.
- **Recorded in full:** the replay logs every prompt and output (`AgentRun(record_full=True)`).
- **`replay.json`** describes the run's environment:
  - Python version and implementation;
  - OS and machine;
  - every installed package and its version;
  - the environment variables (names; values only as one hash, with secret-looking values left out);
  - the code commit (and whether the tree had uncommitted changes).

**compare:** "identical" (amendment Phase 0b) means the same Python version, the same installed package versions,
the same OS and the same environment variables; comparison across machines is out of scope. If the environments
differ, the result is `not_comparable`, with the differences listed. Otherwise every turn is compared (tool,
arguments, prompt, output, gate decision), then the outcome and the per-test scores:
- `identical`: byte for byte;
- `identical_normalized`: equal once per-run temporary worktree paths and test timings are masked (they differ
  between any two runs);
- `different`: the first difference is reported, with turn and field.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys

SECRET = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)", re.I)
VOLATILE_ENV = {"PWD", "OLDPWD", "SHLVL", "_", "TERM_SESSION_ID", "RANDOM"}
TMP_WORK = re.compile(r"/tmp/[^\s'\"]*?r3run_[A-Za-z0-9_]+/(work|shadow)")
TIMING = re.compile(r"\b\d+(\.\d+)?\s?(ms|s)\b")


class ReplayExhausted(RuntimeError):
    pass


class ReplayModel:
    """Serves a logged run's recorded replies in order; raises when they run out."""

    def __init__(self, turns: list[dict]):
        self.turns = [t for t in turns if "reply" in t]
        self.i = 0

    def __call__(self, msgs, seed):
        if self.i >= len(self.turns):
            raise ReplayExhausted(f"replay: no recorded reply for turn {self.i + 1}")
        t = self.turns[self.i]
        self.i += 1
        return {"text": t["reply"], "thinking": t.get("thinking"), "prompt_tokens": t.get("prompt_tokens", 0),
                "answer_tokens": t.get("answer_tokens", 0), "done_reason": t.get("done_reason"), "seconds": 0.0}


def environment() -> dict:
    """What 'same machine' is checked against: Python, OS, packages, environment variables, code commit."""
    pkgs = sorted({(d.metadata["Name"] or "").lower(): d.version for d in importlib.metadata.distributions()
                   if d.metadata["Name"]}.items())
    env = {k: v for k, v in os.environ.items() if k not in VOLATILE_ENV}
    hashed = sorted((k, "" if SECRET.search(k) else v) for k, v in env.items())
    here = os.path.dirname(os.path.abspath(__file__))
    commit = subprocess.run(["git", "-C", here, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "-C", here, "status", "--porcelain", "--untracked-files=no"],
                                capture_output=True, text=True).stdout.strip())
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "os": {"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
            "packages": dict(pkgs),
            "packages_sha": hashlib.sha256(json.dumps(pkgs).encode()).hexdigest(),
            "env_names": sorted(env), "env_sha": hashlib.sha256(json.dumps(hashed).encode()).hexdigest(),
            "code_commit": commit, "code_dirty": dirty}


def run(log: str, task_path: str, out: str, clone: str, cache: str, version: str, seed: int = 42,
        turn_cap: int = 28, ledger: bool = False, plan: bool = False) -> dict:
    from harness.experiments.r3.runner import run_task
    turns = [json.loads(ln) for ln in open(log)]
    task = json.load(open(task_path))
    os.makedirs(out, exist_ok=True)
    res = run_task(task, version, seed, os.path.abspath(clone), out, cache, ReplayModel(turns), turn_cap,
                   plan=plan, ledger=ledger, record_full=True)
    meta = {"source_log": os.path.abspath(log), "source_sha": hashlib.sha256(open(log, "rb").read()).hexdigest(),
            "task": task["id"], "task_sha": hashlib.sha256(json.dumps(task, sort_keys=True).encode()).hexdigest(),
            "version": version, "seed": seed, "turn_cap": turn_cap, "flags": {"ledger": ledger, "plan": plan},
            "run": res.get("run"), "outcome": res.get("outcome"), "environment": environment()}
    json.dump(meta, open(os.path.join(out, "replay.json"), "w"), indent=1)
    return meta


def normalize(text) -> str:
    """Mask what differs between any two runs of the same code: temporary worktree paths and timings."""
    s = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    return TIMING.sub("<T>", TMP_WORK.sub("<WORK>", s))


def _env_diff(a: dict, b: dict) -> list[str]:
    out = []
    for k in ("python", "implementation", "os", "packages_sha", "env_sha"):
        if a.get(k) != b.get(k):
            out.append(k)
    if "packages_sha" in out:
        pa, pb = a.get("packages", {}), b.get("packages", {})
        out += [f"package {n}: {pa.get(n)} vs {pb.get(n)}" for n in sorted(set(pa) | set(pb)) if pa.get(n) != pb.get(n)][:10]
    if "env_sha" in out:
        na, nb = set(a.get("env_names", [])), set(b.get("env_names", []))
        out += [f"env var only in A: {n}" for n in sorted(na - nb)][:5] + [f"env var only in B: {n}" for n in sorted(nb - na)][:5]
    return out


def compare_turns(ta: list[dict], tb: list[dict]) -> tuple[str, dict | None]:
    """'identical', 'identical_normalized' or 'different' (with the first difference)."""
    level = "identical"
    for i in range(max(len(ta), len(tb))):
        if i >= len(ta) or i >= len(tb):
            return "different", {"turn": i + 1, "field": "turn count", "a": len(ta), "b": len(tb)}
        x, y = ta[i], tb[i]
        for field in ("tool", "args", "parse_error"):
            if x.get(field) != y.get(field):
                return "different", {"turn": i + 1, "field": field, "a": x.get(field), "b": y.get(field)}
        if (x.get("gate") or {}).get("accepted") != (y.get("gate") or {}).get("accepted"):
            return "different", {"turn": i + 1, "field": "gate", "a": x.get("gate"), "b": y.get("gate")}
        for field, full in (("prompt_sha", "prompt"), ("output_sha", "output")):
            if x.get(field) == y.get(field):
                continue
            if full in x and full in y and normalize(x[full]) == normalize(y[full]):
                level = "identical_normalized"
                continue
            return "different", {"turn": i + 1, "field": full, "a": normalize(x.get(full, ""))[:300],
                                 "b": normalize(y.get(full, ""))[:300]}
    return level, None


def compare(a_dir: str, b_dir: str) -> dict:
    ma, mb = (json.load(open(os.path.join(d, "replay.json"))) for d in (a_dir, b_dir))
    env = _env_diff(ma["environment"], mb["environment"])
    rep = {"a": a_dir, "b": b_dir, "run": ma.get("run"), "environment_differences": env}
    if env:
        rep["result"] = "not_comparable"
        return rep
    if ma.get("run") != mb.get("run") or ma.get("task_sha") != mb.get("task_sha"):
        rep.update(result="not_comparable", reason="different runs or task files")
        return rep
    ta, tb = ([json.loads(ln) for ln in open(os.path.join(d, ma["run"] + ".jsonl"))] for d in (a_dir, b_dir))
    level, diff = compare_turns(ta, tb)
    sa, sb = (json.load(open(os.path.join(d, ma["run"] + ".score.json"))) for d in (a_dir, b_dir))
    if level != "different":
        for field in ("end", "outcome", "turns"):
            if sa.get(field) != sb.get(field):
                level, diff = "different", {"field": field, "a": sa.get(field), "b": sb.get(field)}
                break
    if level != "different":
        ta_s = (sa.get("score") or {}).get("tests") or {}
        tb_s = (sb.get("score") or {}).get("tests") or {}
        if {normalize(k): v for k, v in ta_s.items()} != {normalize(k): v for k, v in tb_s.items()}:
            bad = sorted(k for k in set(ta_s) | set(tb_s) if ta_s.get(k) != tb_s.get(k))[:5]
            level, diff = "different", {"field": "scored tests (per test id)", "tests": bad}
    rep.update(result=level, first_difference=diff, turns=len(ta), outcome=sa.get("outcome"))
    return rep


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("log")
    r.add_argument("task")
    r.add_argument("out")
    r.add_argument("--clone", required=True)
    r.add_argument("--cache", required=True)
    r.add_argument("--version", choices=("PRISM", "BASELINE"), required=True)
    r.add_argument("--seed", type=int, default=42)
    r.add_argument("--turn-cap", type=int, default=28)
    r.add_argument("--ledger", action="store_true")
    r.add_argument("--plan", action="store_true")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--out")
    a = p.parse_args(argv)
    if a.cmd == "run":
        meta = run(a.log, a.task, a.out, a.clone, a.cache, a.version, a.seed, a.turn_cap, a.ledger, a.plan)
        print(json.dumps({k: meta[k] for k in ("run", "outcome")}))
        return 0
    rep = compare(a.a, a.b)
    if a.out:
        json.dump(rep, open(a.out, "w"), indent=1)
    print(json.dumps(rep, indent=1)[:3000])
    return 0 if rep["result"] in ("identical", "identical_normalized") else 1


if __name__ == "__main__":
    sys.exit(main())
