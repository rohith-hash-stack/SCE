"""Stage 1 of the v3 validation (CANDIDATE_V3.md): convert v2 indexes to the
unvalidated candidates on the production path, then apply the approved gates.

`run`: per repository, three indexes that differ only in the lint -
  v2       `prism summarize` (locked): the cached / live v2 summaries whose card
           keys still match are installed, the rest summarized with v2;
  v3-name  `prism summarize --candidate v3-name` from a copy of v2 (repair only);
  v3       `prism summarize --candidate v3` from a copy of v2 (repair only).
Each step's index is copied to OUT/<repo>/<step>.json; while a step runs its
index is snapshotted to OUT/<repo>/<step>.partial.json, so a new session
resumes (a finished step is skipped, a partial one continues).

`gate`: per candidate, pooled over the repositories, after repair -
  restates_name <= 0.10, too_short <= 0.15 (v3 only), every v2 flag <= 0.05,
  no summary lost against v2, every entry checked - plus first-pass counts and
  before/after samples. Writes OUT/STAGE1.md and OUT/stage1.json.

Usage:
  python -m harness.experiments.purpose_search.stage1 run  MANIFEST OUT --base-urls URLS [--threads N]
  python -m harness.experiments.purpose_search.stage1 gate MANIFEST OUT
MANIFEST: {"<repo>": {"root": path, "cache": harness summary cache or null, "live": live index or null}}
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

STEPS = ("v2", "v3-name", "v3")
GATES = {"restates_name": 0.10, "too_short": 0.15}
V2_GATE = 0.05


def _read(path) -> dict | None:
    from prism.summaries.index import read_index
    return read_index(path) if path and Path(path).exists() else None


def _lint_of(index: dict) -> str:
    return (index.get("pipeline") or {}).get("lint", "v2")


def _complete(index: dict | None, step: str) -> bool:
    return bool(index) and _lint_of(index) == step and all(e.get("checked") for e in index["functions"].values())


def run_step(repo: str, spec: dict, step: str, out: Path, base_urls: str, threads: int, checkpoint_every: int,
             prism: list[str], log=print) -> str:
    from prism.summaries.index import index_path
    root = spec["root"]
    final, partial, live_index = out / repo / f"{step}.json", out / repo / f"{step}.partial.json", index_path(root)
    if _complete(_read(final), step):
        return "done (kept)"
    live_index.parent.mkdir(parents=True, exist_ok=True)
    live_index.unlink(missing_ok=True)
    started = _read(partial)
    if started and _lint_of(started) == step:
        shutil.copy(partial, live_index)  # continue an interrupted step
    elif step == "v2":
        if spec.get("live"):
            shutil.copy(spec["live"], live_index)
    else:
        if not _complete(_read(out / repo / "v2.json"), "v2"):
            raise SystemExit(f"{repo}: v2 index missing - run v2 first")
        shutil.copy(out / repo / "v2.json", live_index)
    cmd = prism + ["summarize", "--repo", root, "--base-urls", base_urls, "--threads", str(threads),
                   "--checkpoint-every", str(checkpoint_every)]
    if step != "v2":
        cmd += ["--candidate", step]
    if step == "v2" and spec.get("cache"):
        cmd += ["--from-cache", spec["cache"]]
    log(f"[{repo} {step}] $ {' '.join(cmd)}")
    stop = threading.Event()

    def snapshot():  # resume point for a new session
        while not stop.wait(60):
            idx = _read(live_index)
            if idx and _lint_of(idx) == step:
                shutil.copy(live_index, partial)

    t = threading.Thread(target=snapshot, daemon=True)
    t.start()
    with open(out / repo / f"{step}.log", "a") as fh:
        code = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT).returncode
    stop.set()
    t.join()
    idx = _read(live_index)
    if code != 0 or not _complete(idx, step):
        if idx:
            shutil.copy(live_index, partial)
        raise SystemExit(f"{repo} {step}: prism summarize exit {code}, complete={_complete(idx, step)} - see {step}.log")
    shutil.copy(live_index, final)
    partial.unlink(missing_ok=True)
    return "done"


def run(manifest: dict, out: Path, base_urls: str, threads: int, checkpoint_every: int, prism=None, log=print) -> dict:
    prism = prism or [sys.executable, "-m", "prism.cli"]
    status = {}
    for repo, spec in manifest.items():
        (out / repo).mkdir(parents=True, exist_ok=True)
        for step in STEPS:
            t = time.time()
            status[f"{repo}/{step}"] = run_step(repo, spec, step, out, base_urls, threads, checkpoint_every, prism, log)
            log(f"[{repo} {step}] {status[f'{repo}/{step}']} in {time.time() - t:.0f}s")
    return status


def gate(manifest: dict, out: Path, sample: int = 6) -> dict:
    from prism.cli import build_pipeline
    from prism.graph.entry_points import find_entry_points
    from prism.summaries.cards import build_card
    from prism.summaries.lint import FLAGS, lint
    from prism.summaries.pipeline import CANDIDATES
    per_repo, pooled = {}, {c: {"n": 0, "first_pass_flagged": 0, "repaired": 0, "lost": 0, "unchecked": 0,
                                "flags": {}, "samples": []} for c in STEPS[1:]}
    rng = random.Random(0)
    for repo, spec in manifest.items():
        v2 = _read(out / repo / "v2.json")
        if not v2:
            raise SystemExit(f"{repo}: no v2 index in {out / repo}")
        builder = build_pipeline(spec["root"], use_cache=False)[0]
        entries = find_entry_points(builder)
        cards = {q: c["card"] for q in v2["functions"] if (c := build_card(builder, {}, entries, q, axes=False))}
        per_repo[repo] = {}
        for cand in STEPS[1:]:
            extra = CANDIDATES[cand]["lint_flags"]
            idx = _read(out / repo / f"{cand}.json")
            if not idx:
                raise SystemExit(f"{repo}: no {cand} index")
            f2, fc = v2["functions"], idx["functions"]
            names = [q for q in fc if q in cards]
            flags = {q: lint(fc[q]["summary"], cards[q], extra) for q in names}
            first = sum(1 for q in names if q in f2 and any(lint(f2[q]["summary"], cards[q], extra).values()))
            changed = [q for q in names if q in f2 and fc[q]["summary"] != f2[q]["summary"]]
            row = {"n": len(names), "first_pass_flagged": first, "repaired": len(changed),
                   "lost": len(set(f2) - set(fc)), "unchecked": sum(1 for e in fc.values() if not e.get("checked")),
                   "flags": {k: sum(f[k] for f in flags.values()) for k in FLAGS + extra}}
            per_repo[repo][cand] = {**row, "rates": {k: round(v / max(1, row["n"]), 3) for k, v in row["flags"].items()}}
            p = pooled[cand]
            for k in ("n", "first_pass_flagged", "repaired", "lost", "unchecked"):
                p[k] += row[k]
            for k, v in row["flags"].items():
                p["flags"][k] = p["flags"].get(k, 0) + v
            p["samples"] += [{"repo": repo, "qname": q, "v2": f2[q]["summary"], cand: fc[q]["summary"]}
                             for q in rng.sample(changed, min(sample, len(changed)))]
    verdict = {}
    for cand, p in pooled.items():
        rates = {k: round(v / max(1, p["n"]), 3) for k, v in p["flags"].items()}
        checks = {f"{k} <= {V2_GATE}": rates[k] <= V2_GATE for k in rates if k not in GATES}
        checks |= {f"{k} <= {GATES[k]}": rates[k] <= GATES[k] for k in rates if k in GATES}
        checks |= {"no summary lost": p["lost"] == 0, "every entry checked": p["unchecked"] == 0}
        verdict[cand] = {"rates": rates, "checks": checks, "passes": all(checks.values())}
    result = {"per_repo": per_repo, "pooled": pooled, "verdict": verdict}
    (out / "stage1.json").write_text(json.dumps(result, indent=1) + "\n")
    (out / "STAGE1.md").write_text(render(result))
    return result


def render(r: dict) -> str:
    L = ["# Stage 1: candidate conversion gates (CANDIDATE_V3.md)", ""]
    for cand, v in r["verdict"].items():
        p = r["pooled"][cand]
        L += [f"## {cand}: **{'PASS' if v['passes'] else 'FAIL'}**", "",
              f"{p['n']} summaries over {len(r['per_repo'])} repositories; first pass flagged {p['first_pass_flagged']} "
              f"({p['first_pass_flagged'] / max(1, p['n']):.0%}), rewritten by repair {p['repaired']}, lost {p['lost']}, "
              f"unchecked {p['unchecked']}.", "", "| check | pooled rate | result |", "|---|---|---|"]
        for check, ok in v["checks"].items():
            flag = check.split(" <= ")[0]
            rate = v["rates"].get(flag, "")
            L.append(f"| {check} | {rate} | {'pass' if ok else '**FAIL**'} |")
        L += ["", "| repo | n | first pass flagged | repaired | " + " | ".join(v["rates"]) + " |",
              "|---|---|---|---|" + "---|" * len(v["rates"])]
        for repo, rows in r["per_repo"].items():
            row = rows[cand]
            L.append(f"| {repo} | {row['n']} | {row['first_pass_flagged']} | {row['repaired']} | "
                     + " | ".join(str(row["rates"].get(k, "")) for k in v["rates"]) + " |")
        L += ["", "Before / after samples:", ""]
        L += [f"- `{s['qname']}` ({s['repo']}): {s['v2']!r} -> {s[cand]!r}" for s in p["samples"]]
        L.append("")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("run", "gate"))
    ap.add_argument("manifest", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--base-urls", default="http://127.0.0.1:11434")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--checkpoint-every", type=int, default=25)
    args = ap.parse_args(argv)
    manifest = json.loads(args.manifest.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    if args.command == "run":
        print(json.dumps(run(manifest, args.out, args.base_urls, args.threads, args.checkpoint_every), indent=1))
        return 0
    r = gate(manifest, args.out)
    print(render(r))
    return 0 if all(v["passes"] for v in r["verdict"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
