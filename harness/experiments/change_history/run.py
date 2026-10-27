"""Run the change-propagation tasks against a model, in two arms, and score them against the commits.

    python -m harness.experiments.change_history.run prompts TASKS.json OUT       # write the prompts (no model)
    python -m harness.experiments.change_history.run generate TASKS.json OUT [--base-url URL] [--model M]
    python -m harness.experiments.change_history.run score TASKS.json OUT

Arms (the same instructions and the same anchor change in both):

    FILE   the anchor's file as an editor shows it (a window of at most FILE_WINDOW lines around the anchor)
    PRISM  FILE + PRISM's change context for the anchor (`prism.impact.context`, built at the parent commit)

Scoring, per task and arm, against what the developer changed in the commit:

    found        changed callers the model named (matched by name; the anchor itself is ignored)
    missed       changed callers it did not name
    extra        functions it named that the commit did not change (unchanged callers counted apart)
    added_kept   of the lines the developer added to each found caller, the share in the model's code
    removed_gone of the lines the developer removed, the share no longer in the model's code
    parses       the model's code for the caller parses
"""
from __future__ import annotations

import argparse
import concurrent.futures
import difflib
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ARMS = ("FILE", "PRISM")
ARMS_WITH_MAP = ("FILE", "PRISM", "PRISM_MAP")


def arms_for(tasks: list[dict]) -> tuple[str, ...]:
    """PRISM_MAP runs when every task carries an overview plan (`plan.py`); older task files keep two arms."""
    return ARMS_WITH_MAP if tasks and all(t.get("overview_plan") for t in tasks) else ARMS
FILE_WINDOW = 300
MODEL = "qwen2.5-coder:14b-instruct-q8_0"
NUM_CTX = 20480

INSTRUCTIONS = """You are a software engineer working in the repository `{repo}`.

The function `{anchor}` in `{file}` has just been changed. Before:
```
{old}
```
After:
```
{new}
```

Other code in the repository may have to change because of this. Find every OTHER function that must change
so the code keeps working, and write its complete new code. Change nothing that does not need to change, and
follow the style of the surrounding code.

Respond with a JSON object:
{{"changes": [{{"function": "<function name, with its class if it is a method>", "file": "<path>", "reason": "<one sentence>", "new_code": "<the complete new function>"}}]}}
If no other function must change, respond {{"changes": []}}.
"""

FILE_BLOCK = """The file `{file}` (lines {lo}-{hi}, before the change):
```
{text}
```
"""

PRISM_BLOCK = """PRISM change context for `{anchor}` (computed before the change):

{context}
"""


def file_window(task: dict) -> tuple[str, int, int]:
    lines = task["anchor_file_text"].splitlines()
    lo, hi = task["anchor_lines"]
    if len(lines) <= FILE_WINDOW:
        return "\n".join(lines), 1, len(lines)
    pad = max(0, (FILE_WINDOW - (hi - lo + 1)) // 2)
    start = max(1, lo - pad)
    end = min(len(lines), start + FILE_WINDOW - 1)
    return "\n".join(lines[start - 1:end]), start, end


MAP_BLOCK = """PRISM change context for `{anchor}` (computed before the change), with the map of the repository and the
second ring of related functions:

{map}
{context}
{ring}
"""


def prompt(task: dict, arm: str, overview: dict | None = None) -> str:
    text, lo, hi = file_window(task)
    parts = [INSTRUCTIONS.format(repo=task["repo"], anchor=task["anchor"], file=task["anchor_file"],
                                 old=task["anchor_old"], new=task["anchor_new"]),
             FILE_BLOCK.format(file=task["anchor_file"], lo=lo, hi=hi, text=text)]
    if arm == "PRISM":
        parts.append(PRISM_BLOCK.format(anchor=task["anchor"], context=task["prism_context"]))
    elif arm == "PRISM_MAP":
        from prism.impact.overview import OverviewPlan, render_overview
        if overview is None:
            raise ValueError(f"{task['id']}: PRISM_MAP needs the written overview")
        map_text, ring_text = render_overview(OverviewPlan.from_dict(task["overview_plan"]), overview)
        parts.append(MAP_BLOCK.format(anchor=task["anchor"], map=map_text, context=task["prism_context"], ring=ring_text))
    return "\n".join(parts)


def write_overviews(tasks: list[dict], out: Path, base_url: str, model: str) -> None:
    """The map and second-ring sentences for every task (one task at a time, saved as it goes)."""
    from prism.impact.overview import OverviewPlan, write_overview
    from prism.summaries.pipeline import Ollama
    path = out / "overview.json"
    done = _load(path)
    llm = Ollama(model, [base_url])
    for t in tasks:
        if t["id"] in done:
            continue
        t0 = time.time()
        done[t["id"]] = write_overview(OverviewPlan.from_dict(t["overview_plan"]), llm)
        done[t["id"]]["seconds"] = round(time.time() - t0, 1)
        path.write_text(json.dumps(done, indent=1, sort_keys=True) + "\n")
        print(f"  overview {t['id']}: {done[t['id']]['seconds']}s", flush=True)


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def ollama(base_url: str, model: str, text: str, timeout: int = 900) -> tuple[str, float]:
    body = json.dumps({"model": model, "prompt": text, "stream": False, "format": "json",
                       "options": {"temperature": 0.2, "seed": 42, "num_ctx": NUM_CTX, "num_predict": 4096}}).encode()
    req = urllib.request.Request(f"{base_url}/api/generate", data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())["response"], time.time() - t0


def generate(tasks: list[dict], out: Path, base_url: str, model: str, threads: int = 2) -> None:
    path = out / "answers.json"
    answers = _load(path)
    overviews = _load(out / "overview.json")
    arms = arms_for(tasks)
    if "PRISM_MAP" in arms and any(t["id"] not in overviews for t in tasks):
        raise SystemExit("write the overviews first (the `overview` command)")
    todo = [(t, arm) for t in tasks for arm in arms if f"{t['id']}/{arm}" not in answers]
    print(f"{len(todo)} model calls to make ({len(answers)} done)", flush=True)

    def one(item):
        t, arm = item
        try:
            text, secs = ollama(base_url, model, prompt(t, arm, overviews.get(t["id"])))
            return f"{t['id']}/{arm}", {"raw": text, "seconds": round(secs, 1), "model": model}
        except Exception as exc:  # recorded; scored as no answer
            return f"{t['id']}/{arm}", {"raw": "", "error": f"{type(exc).__name__}: {exc}", "model": model}

    with concurrent.futures.ThreadPoolExecutor(threads) as pool:
        for key, value in pool.map(one, todo):
            answers[key] = value
            path.write_text(json.dumps(answers, indent=1, sort_keys=True) + "\n")
            print(f"  {key}: {value.get('seconds', '-')}s {value.get('error', '')}", flush=True)


# ---------------------------------------------------------------- scoring
def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def _meaningful(lines, lang: str) -> list[str]:
    comment = "#" if lang == "python" else "//"
    return [n for n in (_norm(x) for x in lines) if n and not n.startswith(comment) and n not in ("{", "}", ")", "]")]


def _diff(old: str, new: str, lang: str) -> tuple[list[str], list[str]]:
    added, removed = [], []
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(line[1:])
    return _meaningful(added, lang), _meaningful(removed, lang)


def _leaf(name: str) -> str:
    return re.split(r"[.:#]", name.strip().strip("`").rstrip("()"))[-1]


def _owner(name: str) -> str | None:
    parts = re.split(r"[.:]", name.strip().strip("`").rstrip("()"))
    return parts[-2] if len(parts) >= 2 else None


def _matches(predicted: str, qname: str) -> bool:
    if _leaf(predicted) != _leaf(qname):
        return False
    owner = _owner(predicted)
    return owner is None or owner in qname.split(".")


def _parses(code: str, lang: str) -> bool:
    if lang == "python":
        import ast
        import textwrap
        try:
            ast.parse(textwrap.dedent(code))
            return True
        except SyntaxError:
            return False
    from prism.parser.tree_sitter_loader import parse_source
    parsed = parse_source("x.ts", code.encode())
    return parsed is not None and parsed.root_node is not None and not parsed.root_node.has_error


def parse_answer(raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
    except ValueError:
        m = re.search(r"\{.*\}", raw or "", re.S)
        try:
            data = json.loads(m.group(0)) if m else {}
        except ValueError:
            data = {}
    changes = data.get("changes", []) if isinstance(data, dict) else []
    return [c for c in changes if isinstance(c, dict) and c.get("function")]


def _is_anchor(change: dict, task: dict) -> bool:
    """The model's own restatement of the anchor - not a same-named function elsewhere (`App.redirect` vs
    `helpers.redirect`): a stated file must be the anchor's, and a stated class the anchor's."""
    if not _matches(change["function"], task["anchor"]):
        return False
    stated = (change.get("file") or "").strip().lstrip("./")
    return not stated or stated == task["anchor_file"] or task["anchor_file"].endswith(stated)


def score_task(task: dict, raw: str) -> dict:
    lang = "python" if task["anchor_file"].endswith(".py") else "js"
    changes = [c for c in parse_answer(raw) if not _is_anchor(c, task)]
    found, edits = [], []
    used = set()
    for caller in task["changed_callers"]:
        hit = next((i for i, c in enumerate(changes) if i not in used and _matches(c["function"], caller["qname"])), None)
        if hit is None:
            continue
        used.add(hit)
        found.append(caller["qname"])
        code = changes[hit].get("new_code") or ""
        added, removed = _diff(caller["old"], caller["new"], lang)
        model_lines = set(_meaningful(code.splitlines(), lang))
        edits.append({
            "caller": caller["qname"],
            "added_kept": round(sum(a in model_lines for a in added) / len(added), 2) if added else None,
            "removed_gone": round(sum(r not in model_lines for r in removed) / len(removed), 2) if removed else None,
            "parses": _parses(code, lang) if code else False,
        })
    extra = [c["function"] for i, c in enumerate(changes) if i not in used]
    unchanged_named = [e for e in extra if any(_matches(e, q) for q in task["unchanged_callers"])]
    return {"found": found, "missed": [c["qname"] for c in task["changed_callers"] if c["qname"] not in found],
            "extra": extra, "extra_unchanged_callers": unchanged_named, "edits": edits}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


def score(tasks: list[dict], out: Path) -> dict:
    answers = _load(out / "answers.json")
    rows, summary = [], {}
    arms = arms_for(tasks)
    for t in tasks:
        for arm in arms:
            a = answers.get(f"{t['id']}/{arm}")
            if a is None:
                continue
            s = score_task(t, a.get("raw", ""))
            rows.append({"id": t["id"], "tier": t["tier"], "repo": t["repo"], "arm": arm,
                         "changed": len(t["changed_callers"]), **s, "error": a.get("error")})
    for group in ("all", "easy", "medium", "hard"):
        for arm in arms:
            rs = [r for r in rows if r["arm"] == arm and (group == "all" or r["tier"] == group)]
            if not rs:
                continue
            edits = [e for r in rs for e in r["edits"]]
            summary[f"{group}/{arm}"] = {
                "tasks": len(rs),
                "callers_found": f"{sum(len(r['found']) for r in rs)} / {sum(r['changed'] for r in rs)}",
                "tasks_all_found": sum(1 for r in rs if not r["missed"]),
                "extra_named": sum(len(r["extra"]) for r in rs),
                "added_kept": _mean([e["added_kept"] for e in edits]),
                "removed_gone": _mean([e["removed_gone"] for e in edits]),
                "parses": f"{sum(e['parses'] for e in edits)} / {len(edits)}",
            }
    result = {"summary": summary, "rows": rows}
    (out / "results.json").write_text(json.dumps(result, indent=1) + "\n")
    (out / "REPORT.md").write_text(render(tasks, result))
    return result


def render(tasks: list[dict], result: dict) -> str:
    lines = ["# Change propagation from git history: sanity run", "",
             "Descriptive only (a small sanity set): no significance test.", "",
             "| tier | arm | tasks | callers found | tasks with every caller found | extra functions named | "
             "added lines kept | removed lines gone | new code parses |", "|---|---|---|---|---|---|---|---|---|"]
    for key, s in result["summary"].items():
        group, arm = key.split("/")
        lines.append(f"| {group} | {arm} | {s['tasks']} | {s['callers_found']} | {s['tasks_all_found']} | "
                     f"{s['extra_named']} | {s['added_kept']} | {s['removed_gone']} | {s['parses']} |")
    arms = arms_for(tasks)
    lines += ["", "Per task:", "", "| task | tier | repo | anchor | changed callers | "
              + " | ".join(f"{a} found" for a in arms) + " |", "|---|---|---|---|---|" + "---|" * len(arms)]
    by = {(r["id"], r["arm"]): r for r in result["rows"]}
    for t in tasks:
        cells = [str(len(by[(t["id"], a)]["found"])) if (t["id"], a) in by else "-" for a in arms]
        lines.append(f"| {t['id']} | {t['tier']} | {t['repo']} | `{t['anchor']}` | {len(t['changed_callers'])} | "
                     + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("command", choices=("prompts", "overview", "generate", "score"))
    p.add_argument("tasks")
    p.add_argument("out")
    p.add_argument("--base-url", default="http://127.0.0.1:11434")
    p.add_argument("--model", default=MODEL)
    p.add_argument("--threads", type=int, default=2)
    args = p.parse_args(argv)
    data = json.loads(Path(args.tasks).read_text())
    if data.get("status") != "frozen":
        raise SystemExit("tasks are not frozen")
    tasks = data["tasks"]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.command == "prompts":
        overviews = _load(out / "overview.json")
        arms = [a for a in arms_for(tasks) if a != "PRISM_MAP" or all(t["id"] in overviews for t in tasks)]
        for t in tasks:
            for arm in arms:
                (out / f"{t['id']}_{arm}.txt").write_text(prompt(t, arm, overviews.get(t["id"])))
        print(f"{len(arms) * len(tasks)} prompts -> {out}")
    elif args.command == "overview":
        write_overviews(tasks, out, args.base_url, args.model)
    elif args.command == "generate":
        generate(tasks, out, args.base_url, args.model, args.threads)
    else:
        print(json.dumps(score(tasks, out)["summary"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
