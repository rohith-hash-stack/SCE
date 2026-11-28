"""Phase 3b's output comparisons for the tests before the cell is pinned (AMENDMENTS "Phase 3b").

    python -B -m harness.experiments.r4.phase3b_compare recorded RECORDED_GLOB REPLAY_DIR
    python -B -m harness.experiments.r4.phase3b_compare pair A_DIR B_DIR --mode identical|indent|exact_fix

**`recorded`** (test 1, against Kaggle):
- each replayed turn's answer is hashed and compared with the `output_sha` Kaggle recorded for that turn;
- the turns are classed by tool;
- `run_tests`, and a `done` that the done gate checked, depend on the test environment (timings, paths). They are
  reported apart, never as PRISM's answers.

**`pair`** compares two replays of the same calls, turn by turn, on the full answers (`record_full`):
- `identical` (tests 1 and 6, the old code against the new): every answer byte-identical.
- `indent` (test 2, A against E): every answer identical, except lines where B is A with leading whitespace inserted
  at one point, right after the listing's separator (`: ` or `] `), and nothing else.
- `exact_fix` (test 3, A against C):
  - B is A, or A followed by exactly one note (`\\n\\nPRISM gateway: the exact text ...`), on a turn whose call was
    an `edit_file` the tool refused;
  - the gateway's interventions on every turn are A's, plus `exact_fix` ones only.

Prints a JSON summary, with each mismatch's run, turn, tool, arguments and the first differing line. Exits 1 if
anything mismatches.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import sys

ENV_DEPENDENT = ("run_tests",)
NOTE = "\n\nPRISM gateway: the exact text for the nearest place"
SEPARATORS = (": ", "] ")


def _load(path: str) -> list[dict]:
    return [json.loads(ln) for ln in open(path)]


def _runs(d: str) -> dict[str, str]:
    return {os.path.basename(p)[:-6]: p for p in glob.glob(os.path.join(d, "*.jsonl"))
            if os.path.basename(p).count("__") == 2}


def _key(name: str) -> tuple[str, str]:
    t, _v, s = name.split("__")
    return t, s


def _first_diff(a: str, b: str) -> dict:
    al, bl = a.split("\n"), b.split("\n")
    for i in range(max(len(al), len(bl))):
        x = al[i] if i < len(al) else None
        y = bl[i] if i < len(bl) else None
        if x != y:
            return {"line": i + 1, "a": None if x is None else x[:300], "b": None if y is None else y[:300]}
    return {}


def indent_only(a: str, b: str) -> bool:
    """`b` is `a` with leading whitespace put back after a listing separator on some lines, and nothing else."""
    al, bl = a.split("\n"), b.split("\n")
    if len(al) != len(bl):
        return False
    for x, y in zip(al, bl):
        if x == y:
            continue
        k = len(os.path.commonprefix([x, y]))
        w = y[k:k + len(y) - len(x)]
        if len(y) <= len(x) or not w or w.strip(" \t") or y[k + len(w):] != x[k:]:
            return False
        if not y[:k].endswith(SEPARATORS):
            return False
    return True


def recorded(rec_glob: str, replay_dir: str) -> dict:
    reps = _runs(replay_dir)
    out = {"runs": 0, "turns": 0, "match": 0, "mismatch": [], "env_dependent": {"turns": 0, "match": 0},
           "by_tool": {}, "missing": []}
    for path in sorted(glob.glob(rec_glob)):
        name = os.path.basename(path)[:-6]
        if name.count("__") != 2:
            continue
        rep = next((p for n, p in reps.items() if _key(n) == _key(name)), None)
        if rep is None:
            out["missing"].append(name)
            continue
        out["runs"] += 1
        a, b = _load(path), _load(rep)
        for r, s in zip(a, b):
            if "tool" not in r:
                if not s.get("parse_error"):
                    out["mismatch"].append({"run": name, "turn": r.get("turn"), "why": "parse error not replayed"})
                continue
            got = hashlib.sha256(s.get("output", "").encode()).hexdigest()
            ok = got == r.get("output_sha") and s.get("tool") == r.get("tool")
            env = r["tool"] in ENV_DEPENDENT or (r["tool"] == "done" and "gate" in r)
            t = out["by_tool"].setdefault(r["tool"], {"turns": 0, "match": 0})
            t["turns"] += 1
            t["match"] += ok
            if env:
                out["env_dependent"]["turns"] += 1
                out["env_dependent"]["match"] += ok
                continue
            out["turns"] += 1
            if ok:
                out["match"] += 1
            else:
                out["mismatch"].append({"run": name, "turn": r["turn"], "tool": r["tool"],
                                        "args": json.dumps(r.get("args"))[:300],
                                        "recorded_head": r.get("output_head", "")[:300],
                                        **_first_diff(r.get("output_head", ""),
                                                      s.get("output", "")[:len(r.get("output_head", ""))])})
        if len(a) != len(b):
            out["mismatch"].append({"run": name, "turns_recorded": len(a), "turns_replayed": len(b)})
    out["ok"] = not out["mismatch"] and not out["missing"]
    return out


def _ivs(r: dict) -> list[dict]:
    drop = ("chars", "id")              # an id numbers the run's interventions: C's notes shift the later ones
    return [{k: v for k, v in iv.items() if k not in drop} for iv in r.get("gateway", [])]


def pair(a_dir: str, b_dir: str, mode: str) -> dict:
    A, B = _runs(a_dir), _runs(b_dir)
    out = {"mode": mode, "runs": 0, "turns": 0, "identical": 0, "allowed_differences": 0, "mismatch": [],
           "missing": [], "env_dependent": {"turns": 0, "identical": 0}}
    for name, pa in sorted(A.items()):
        pb = next((p for n, p in B.items() if _key(n) == _key(name)), None)
        if pb is None:
            out["missing"].append(name)
            continue
        out["runs"] += 1
        a, b = _load(pa), _load(pb)
        if len(a) != len(b):
            out["mismatch"].append({"run": name, "turns_a": len(a), "turns_b": len(b)})
        for r, s in zip(a, b):
            x, y = r.get("output", ""), s.get("output", "")
            if r.get("tool") in ENV_DEPENDENT or (r.get("tool") == "done" and "gate" in r):
                out["env_dependent"]["turns"] += 1          # the test environment's output: reported apart
                out["env_dependent"]["identical"] += x == y
                continue
            out["turns"] += 1
            if r.get("tool") != s.get("tool") or r.get("args") != s.get("args"):
                out["mismatch"].append({"run": name, "turn": r.get("turn"), "why": "different call"})
                continue
            ok = x == y
            allowed = False
            if not ok and mode == "indent":
                allowed = indent_only(x, y)
            if not ok and mode == "exact_fix":
                refused = r.get("tool") == "edit_file" and x.startswith(("error", "not answered again"))
                allowed = refused and y.startswith(x) and y[len(x):].startswith(NOTE) and y.count(NOTE) == 1
            gw_ok = True
            if mode == "exact_fix":
                extra = [iv for iv in _ivs(s) if iv.get("type") == "exact_fix"]
                gw_ok = [iv for iv in _ivs(s) if iv.get("type") != "exact_fix"] == _ivs(r) and \
                    (bool(extra) == (allowed and not ok))
            elif r.get("gateway") is not None or s.get("gateway") is not None:
                gw_ok = _ivs(r) == _ivs(s) if mode == "indent" else r.get("gateway") == s.get("gateway")
            if ok and gw_ok:
                out["identical"] += 1
            elif allowed and gw_ok:
                out["allowed_differences"] += 1
            else:
                out["mismatch"].append({"run": name, "turn": r.get("turn"), "tool": r.get("tool"),
                                        "args": json.dumps(r.get("args"))[:300], "gateway_records_equal": gw_ok,
                                        **_first_diff(x, y)})
    out["ok"] = not out["mismatch"] and not out["missing"]
    return out


def main(argv=None) -> int:
    a = list(argv if argv is not None else sys.argv[1:])
    if a[:1] == ["recorded"] and len(a) == 3:
        res = recorded(a[1], a[2])
    elif a[:1] == ["pair"] and len(a) == 5 and a[3] == "--mode":
        res = pair(a[1], a[2], a[4])
    else:
        raise SystemExit(__doc__)
    print(json.dumps(res, indent=1))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
