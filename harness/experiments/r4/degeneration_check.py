"""Phase 1 step 1e, part A: do the trailing-off parse errors go away with the fix?

    python -B -m harness.experiments.r4.degeneration_check LOG TASK OUT --clone DIR --cache DIR --version PRISM \
        --turns 18 19 ... --base-url URL --model M [--seeds 42 43 ...]

For one Phase 0 run whose turns `--turns` were parse errors (a reply that trailed off into whitespace):

1. **Rebuild the exact prompts**, twice, with the replay tool (recorded replies, real tools, every prompt recorded):
   - `before`: Phase 0's harness (switches off). Fidelity is checked against Phase 0's turn log: every tool output up
     to the target turn must be byte-identical (`output_sha`), except `run_tests` outputs, which carry per-run temporary
     paths and timings; those must match Phase 0's logged head (the first 300 characters). Exact `prompt_sha` matches
     are reported too;
   - `after`: the Phase 1 harness (`--compact --hygiene`): the same trajectory, with invalid replies cut in the history
     and the history compacted.
2. **Call the model** on each target turn's prompt, once per seed:
   - `before`: `OllamaChat` as in Phase 0 (one non-streamed call, JSON mode);
   - `after`: `OllamaChat(stream_cut=True)`.
3. **Record per call:** a valid tool call or not, cut for a degenerate tail, repaired, tail length, answer tokens,
   seconds. `summary.json` gives the parse-error rate per condition.

**Limit:** the trajectory is Phase 0's. In a real Phase 1 run an earlier degenerate reply might have been repaired and
the run gone elsewhere; this check holds the history fixed to isolate the reply itself.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from harness.experiments.r3.loop import OllamaChat, degenerate_tail, parse_call


def rebuild(log: str, task: str, out: str, clone: str, cache: str, version: str, turn_cap: int, phase1: bool) -> dict:
    """{turn: messages} for every turn of the replayed run."""
    from harness.experiments.r4 import replay
    if not os.path.exists(os.path.join(out, "replay.json")):
        replay.run(log, task, out, clone, cache, version, turn_cap=turn_cap, ledger=True,
                   compact=phase1, hygiene=phase1)
    path = next(os.path.join(out, f) for f in os.listdir(out) if f.endswith(".jsonl"))
    return {t["turn"]: t["prompt"] for t in map(json.loads, open(path)) if "prompt" in t}


def fidelity(orig: dict, log_dir: str, turns: list[int]) -> dict:
    """Per target turn: is the rebuilt history Phase 0's (see the module docstring)?"""
    path = next(os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.endswith(".jsonl"))
    rep = {t["turn"]: t for t in map(json.loads, open(path))}
    out = {}
    for target in turns:
        diffs = []
        for t in range(1, target):
            a, b = orig.get(t, {}), rep.get(t, {})
            if a.get("output_sha") == b.get("output_sha") and a.get("reply") == b.get("reply"):
                continue
            same_head = (b.get("output") or "")[:len(a.get("output_head") or "")] == (a.get("output_head") or "")
            diffs.append({"turn": t, "tool": a.get("tool"), "head_matches": same_head})
        ok = all(d["tool"] == "run_tests" and d["head_matches"] for d in diffs)
        out[target] = {"faithful": ok, "prompt_sha_equal": rep.get(target, {}).get("prompt_sha") == orig[target].get(
            "prompt_sha"), "differing_outputs": diffs}
    return out


def classify(reply: dict) -> dict:
    text = reply.get("text") or ""
    meta = reply.get("meta") or {}
    return {"valid": parse_call(text) is not None, "cut": bool(meta.get("degenerate_cut")),
            "repaired": meta.get("repaired_closing_braces", 0), "tail_chars": meta.get("tail_chars") or degenerate_tail(text),
            "chars": len(text), "answer_tokens": reply.get("answer_tokens"), "done_reason": reply.get("done_reason"),
            "seconds": reply.get("seconds"), "head": text[:160]}


def summarise(rows: list[dict]) -> dict:
    out = {}
    for cond in ("before", "after"):
        r = [x for x in rows if x["condition"] == cond]
        if not r:
            continue
        bad = [x for x in r if not x["valid"]]
        out[cond] = {"calls": len(r), "parse_errors": len(bad), "parse_error_rate": round(len(bad) / len(r), 3),
                     "degenerate_tails": sum(1 for x in r if x["tail_chars"] > 400),
                     "cut": sum(1 for x in r if x["cut"]), "repaired": sum(1 for x in r if x["repaired"]),
                     "mean_seconds": round(sum(x["seconds"] or 0 for x in r) / len(r), 1),
                     "by_turn": {t: sum(1 for x in r if x["turn"] == t and not x["valid"])
                                 for t in sorted({x["turn"] for x in r})}}
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("log")
    p.add_argument("task")
    p.add_argument("out")
    p.add_argument("--clone", required=True)
    p.add_argument("--cache", required=True)
    p.add_argument("--version", choices=("PRISM", "BASELINE"), required=True)
    p.add_argument("--turns", type=int, nargs="+", required=True)
    p.add_argument("--base-url", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--seeds", type=int, nargs="+", default=list(range(42, 52)))
    p.add_argument("--turn-cap", type=int, default=28)
    a = p.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    orig = {t["turn"]: t for t in map(json.loads, open(a.log))}
    prompts = {c: rebuild(a.log, a.task, os.path.join(a.out, f"replay_{c}"), a.clone, a.cache, a.version,
                          a.turn_cap, c == "after") for c in ("before", "after")}
    check = fidelity(orig, os.path.join(a.out, "replay_before"), a.turns)
    json.dump(check, open(os.path.join(a.out, "rebuild.json"), "w"), indent=1)
    print(json.dumps({"faithful_turns": sum(c["faithful"] for c in check.values()), "of": len(a.turns),
                      "exact_prompt_sha": sum(c["prompt_sha_equal"] for c in check.values())}), flush=True)
    clients = {"before": OllamaChat(a.base_url, a.model), "after": OllamaChat(a.base_url, a.model, stream_cut=True)}
    res_path = os.path.join(a.out, "calls.jsonl")
    done = {(r["condition"], r["turn"], r["seed"]) for r in map(json.loads, open(res_path))} \
        if os.path.exists(res_path) else set()
    with open(res_path, "a") as fh:
        for t in a.turns:
            for cond in ("before", "after"):
                msgs = prompts[cond].get(t)
                if msgs is None:
                    continue
                for seed in a.seeds:
                    if (cond, t, seed) in done:
                        continue
                    t0 = time.time()
                    try:
                        row = classify(clients[cond](msgs, seed))
                    except Exception as exc:                  # recorded, not fatal: the check goes on
                        row = {"valid": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                               "seconds": round(time.time() - t0, 1), "tail_chars": 0, "cut": False, "repaired": 0}
                    row.update(condition=cond, turn=t, seed=seed,
                               prompt_tokens_est=sum(len(m["content"]) for m in msgs) // 4)
                    fh.write(json.dumps(row) + "\n")
                    fh.flush()
                    print(json.dumps({k: row.get(k) for k in ("condition", "turn", "seed", "valid", "cut", "repaired",
                                                              "seconds")}), flush=True)
    rows = [json.loads(ln) for ln in open(res_path)]
    summary = {"run": os.path.basename(a.log), "turns": a.turns, "seeds": a.seeds, **summarise(rows),
               "rebuild_fidelity": {t: c["faithful"] for t, c in check.items()}}
    json.dump(summary, open(os.path.join(a.out, "summary.json"), "w"), indent=1)
    print(json.dumps({k: v for k, v in summary.items() if k in ("before", "after")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
