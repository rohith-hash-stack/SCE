"""Static audit (no model calls) of entry-point breadcrumbs over the 90
Stage 2 debug tasks: the pre-patch rule (`prism.graph.entry_points` at
LEGACY_COMMIT - non-builtin decorator + no in-repo caller, every path of
every slice symbol appended as a block) against the patched one (decorator
taxonomy + call registrations, slice-boundary paths, invariants, inline).

Slices are the ones the Stage 2 arms actually packed (seed 42 cells):
`pragmatic_oracle`'s gold slice (what `prism_oracle` decorates) and
`prism_ask`'s slice. Output: `breadcrumb_audit.json` and `.md`.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

LEGACY_COMMIT = "15baac1"  # last commit before the breadcrumb patch
REPO = Path(__file__).resolve().parents[3]
CORPORA = ("fastapi", "django", "express", "trpc")


def load_legacy():
    import tempfile

    src = subprocess.run(["git", "show", f"{LEGACY_COMMIT}:src/prism/graph/entry_points.py"], cwd=REPO,
                         capture_output=True, text=True, check=True).stdout
    path = Path(tempfile.mkdtemp()) / "legacy_entry_points.py"
    path.write_text(src)
    spec = importlib.util.spec_from_file_location("legacy_entry_points", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["legacy_entry_points"] = module
    spec.loader.exec_module(module)
    return module


def slices(cells_path: Path, engine: str) -> dict[str, list[str]]:
    out = {}
    for line in cells_path.read_text().splitlines():
        r = json.loads(line)
        if r["engine_id"] == engine and r["seed"] == 42 and r["status"] == "ok":
            out[r["task_id"]] = r["selected_symbols"]
    return out


def audit_corpus(repo: str, legacy, results_dir: Path) -> dict:
    from benchmarks.final_sweep.arms import CorpusState
    from benchmarks.final_sweep.runner import load_debug_tasks
    from benchmarks.corpora.resolver import resolve
    from prism.graph.entry_points import EntryIndex
    from prism.graph.render_breadcrumbs import Breadcrumb, slice_roots, validate, MAX_TOKENS
    from prism.graph.entry_points import format_entry_context
    from prism.slicer.tokenizer import count_tokens

    t = time.perf_counter()
    corpus = CorpusState(repo, str(resolve(repo)))
    builder = corpus.builder
    new = EntryIndex(builder)
    old = legacy.EntryIndex(builder)
    build_s = time.perf_counter() - t
    tasks = [x.task_id for x in load_debug_tasks(repo)]
    cells = results_dir / repo / "cells.jsonl"
    per_task = []
    for arm in ("pragmatic_oracle", "prism_ask"):
        for task_id, sl in sorted(slices(cells, arm).items()):
            funcs = [s for s in sl if builder.symbol_table.get(s) is not None and builder.symbol_table.get(s).kind != "class"]
            old_lines = [f"{q}: {p}" for q in funcs for p in old.context(q)]
            old_block = ("\n<entry_context>\n" + "\n".join(old_lines) + "\n</entry_context>\n") if old_lines else ""
            members = set(s for s in sl if s in new.functions)
            violations, crumbs, total = [], [], 0
            for root in slice_roots(new, sl):
                paths = new.paths(root, blocked=members - {root})
                if not paths:
                    continue
                chain = tuple(paths[0])
                text = format_entry_context(list(chain), new.entries)
                crumb = Breadcrumb(root, chain, text, count_tokens(text))
                if total + crumb.tokens > MAX_TOKENS or any(c.text == text for c in crumbs):
                    continue
                problems = validate(crumb, members, new)
                if problems:
                    violations.append({"root": root, "problems": problems})
                    continue
                crumbs.append(crumb)
                total += crumb.tokens
            old_heads = Counter(line.split("[Entry: ", 1)[1].split("]", 1)[0] for line in old_lines)
            per_task.append({
                "repo": repo, "task_id": task_id, "arm": arm, "slice_size": len(sl), "roots": len(slice_roots(new, sl)),
                "old_fires": bool(old_lines), "old_paths": len(old_lines), "old_tokens": count_tokens(old_block) if old_block else 0,
                "old_heads": dict(old_heads),
                "new_fires": bool(crumbs), "new_paths": len(crumbs), "new_tokens": total,
                "new_lines": [c.text for c in crumbs], "violations": violations,
            })
    entry_sources = Counter(e.source + (f":{e.kind}" if e.kind else "") for e in new.entries.values())
    return {
        "repo": repo, "build_s": round(build_s), "n_tasks": len(tasks),
        "entries_old": len(old.entries), "entries_new": len(new.entries), "entry_sources_new": dict(entry_sources),
        "old_entry_heads_top": Counter(e.decorator for e in old.entries.values()).most_common(10),
        "per_task": per_task,
    }


def summarize(corpora: list[dict], affected: set[tuple[str, str]]) -> str:
    def stats(rows, key):
        vals = [r[key] for r in rows if r[key]]
        return (len(vals), round(statistics.fmean(vals), 1) if vals else 0, max(vals, default=0), sum(vals))

    lines = ["# Breadcrumb static audit (no model calls)", "",
             f"Old rule: `prism.graph.entry_points` at `{LEGACY_COMMIT}` (non-builtin decorator + no in-repo caller; "
             "every path of every slice symbol, appended as a block). New rule: decorator taxonomy + call "
             "registrations, slice-boundary paths, invariants, <= 80 tokens per slice, inline.", ""]
    for arm, title in (("pragmatic_oracle", "prism_oracle slices (the gold slice)"), ("prism_ask", "prism_ask slices")):
        lines += [f"## {title}", "",
                  "| corpus | tasks | old fires | new fires | old tokens mean / max / total | new tokens mean / max / total | violations caught |",
                  "|---|---|---|---|---|---|---|"]
        allrows = []
        for c in corpora:
            rows = [r for r in c["per_task"] if r["arm"] == arm]
            allrows += rows
            o, n = stats(rows, "old_tokens"), stats(rows, "new_tokens")
            v = sum(len(r["violations"]) for r in rows)
            lines.append(f"| {c['repo']} | {len(rows)} | {o[0]} | {n[0]} | {o[1]} / {o[2]} / {o[3]} | {n[1]} / {n[2]} / {n[3]} | {v} |")
        o, n = stats(allrows, "old_tokens"), stats(allrows, "new_tokens")
        v = sum(len(r["violations"]) for r in allrows)
        lines += [f"| **all** | {len(allrows)} | {o[0]} | {n[0]} | {o[1]} / {o[2]} / {o[3]} | {n[1]} / {n[2]} / {n[3]} | {v} |", ""]
    lines += ["## Entry points per corpus", "", "| corpus | old | new | new by source |", "|---|---|---|---|"]
    for c in corpora:
        lines.append(f"| {c['repo']} | {c['entries_old']} | {c['entries_new']} | "
                     + ", ".join(f"{k} {v}" for k, v in sorted(c["entry_sources_new"].items())) + " |")
    lines += ["", "## The 12 tasks where breadcrumbs fired in Stage 2 (prism_oracle slice)", "",
              "| task | old paths / tokens / heads | new paths / tokens | new breadcrumb |", "|---|---|---|---|"]
    for c in corpora:
        for r in c["per_task"]:
            if r["arm"] == "pragmatic_oracle" and (r["repo"], r["task_id"]) in affected:
                heads = ", ".join(f"{k} x{v}" for k, v in r["old_heads"].items())
                new = "<br>".join(f"`{x}`" for x in r["new_lines"]) or "-"
                lines.append(f"| {r['task_id']} | {r['old_paths']} / {r['old_tokens']} / {heads} | {r['new_paths']} / {r['new_tokens']} | {new} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--results", default=str(REPO / "reports/two_stage/stage2"))
    p.add_argument("--out", required=True)
    p.add_argument("--corpora", default=",".join(CORPORA))
    args = p.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    legacy = load_legacy()
    affected = set()
    for repo in CORPORA:
        for line in (Path(args.results) / repo / "cells.jsonl").read_text().splitlines():
            r = json.loads(line)
            if r["engine_id"] == "prism_oracle" and r.get("n_breadcrumb_symbols"):
                affected.add((repo, r["task_id"]))
    corpora = []
    for repo in args.corpora.split(","):
        print(f"[audit] {repo} ...", file=sys.stderr, flush=True)
        corpora.append(audit_corpus(repo, legacy, Path(args.results)))
    (out / "breadcrumb_audit.json").write_text(json.dumps({"legacy_commit": LEGACY_COMMIT, "corpora": corpora}, indent=1))
    md = summarize(corpora, affected)
    (out / "breadcrumb_audit.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
