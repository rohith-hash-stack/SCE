"""Phase 3b's offline replay of the whitespace-tolerant edit fix (AMENDMENTS "Phase 3b"; no Kaggle arm has it).

    python -B -m harness.experiments.r4.phase3b_edit_replay OUT.json --clones DIR [--runs 'reports/r4/**/*.jsonl']

**The events:** every recorded `edit_file` call that the edit tool refused because its `old` matched once apart from
whitespace. Both of the tool's messages are counted, and reported apart:
- `current`: the one that shows the file's lines;
- `old`: before R4-SANITY-4.

**The file state at each call, rebuilt:**
1. the task's start commit (`parent`);
2. its `start_patch` and `after_index_patch`;
3. every `edit_file` the run applied before that call, in order, with the tool's own semantics (exact text,
   `occurrence`).

**Checking the rebuild:** each rebuilt state is checked against the line the recorded error showed. A state that
does not match is excluded and counted (`unverified`).

**The existing tool**, on the rebuilt state: the call must be refused there again (`occurrences == 0`, a single
whitespace match). This confirms the event.

**The proposed rule** (v2; v1, pinned before the first replay, mishandled two cases, see `phase3b/IMPLEMENTATION.md`):
- **Applies only when:**
  - `old` and `new` are each one line (a single trailing newline aside);
  - and `old` with its leading whitespace removed equals exactly one whole line of the file with its leading
    whitespace removed.
- **What is applied:** that line is replaced by `new`, with `new`'s leading whitespace replaced by the line's own.
- **Refused, never applied:**
  - several such lines: `ambiguous`;
  - none, because `old` is only part of a line or differs by more than leading whitespace: `out_of_scope`;
  - multi-line: `multi_line`.

**For each applied event, the result is judged against the run's own later edit of that line, else the answer key:**
- **The run's later edit:** the first later applied single-line edit in the run whose `old` is the line exactly.
- **The answer key:** a `-`/`+` line pair whose `-` line is the line exactly and occurs once.
- **Verdicts:**
  - `intended`: the applied line equals that expected line;
  - `unintended`: it differs;
  - `undetermined`: neither exists.
- **Also recorded:**
  - whether the file still parses (Python), and whether it did before;
  - and the same verdict for the variant that writes `new` as given (no re-basing).

Writes every event, with the text sent, the source line and the applied line, and the totals.
"""
from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TASK_FILES = ["phase2/tasks.json", "phase3/poc.json", "phase3/poc2.json", "phase3/tasks.json", "phase3/candidates.json",
              "sanity/tasks.json", "sanity/tasks_v2.json", "sanity/tasks_v3.json"]
GOLD_DIRS = ["phase2/gold", "phase3/gold", "sanity/gold"]


def _git(*args, cwd=None, inp=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=inp is not None or True, input=inp,
                          errors="replace")


def task_versions() -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Every version of every task in the task files (an id can appear in several), and the repositories' URLs."""
    out: dict[str, list[dict]] = {}
    urls: dict[str, str] = {}
    for rel in TASK_FILES:
        p = os.path.join(HERE, rel)
        if not os.path.exists(p):
            continue
        doc = json.load(open(p))
        urls.update(doc.get("repo_urls", {}))
        for t in doc.get("tasks", doc.get("candidates", [])):
            vs = out.setdefault(t["id"], [])
            if all(json.dumps(v, sort_keys=True) != json.dumps(t, sort_keys=True) for v in vs):
                vs.append(t)
    return out, urls


def gold_patch(tid: str) -> str | None:
    for d in GOLD_DIRS:
        p = os.path.join(HERE, d, f"{tid}.patch")
        if os.path.exists(p):
            return open(p).read()
    return None


def gold_pairs(patch: str | None) -> dict[str, str]:
    """Removed line -> added line, from hunks whose removed and added runs have the same length; a removed line that
    occurs more than once is dropped."""
    if not patch:
        return {}
    pairs, seen = {}, {}
    minus, plus = [], []

    def flush():
        if minus and len(minus) == len(plus):
            for a, b in zip(minus, plus):
                seen[a] = seen.get(a, 0) + 1
                pairs[a] = b
        minus.clear()
        plus.clear()
    for ln in patch.splitlines():
        if ln.startswith(("---", "+++", "diff ", "index ", "@@")):
            flush()
        elif ln.startswith("-"):
            if plus:
                flush()
            minus.append(ln[1:])
        elif ln.startswith("+"):
            plus.append(ln[1:])
        else:
            flush()
    flush()
    return {a: b for a, b in pairs.items() if seen[a] == 1}


class Repo:
    def __init__(self, clones: str, name: str, url: str):
        self.path = os.path.join(clones, name)
        if not os.path.isdir(self.path):
            subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout", url, self.path], check=True)

    def start_text(self, task: dict, rel: str) -> str | None:
        r = _git("-C", self.path, "show", f"{task['parent']}:{rel}")
        text = r.stdout if r.returncode == 0 else None
        for key in ("start_patch", "after_index_patch"):
            patch = task.get(key)
            if patch and f"b/{rel}" in patch:
                text = _apply_patch(text, rel, patch)
        return text


def _apply_patch(text: str | None, rel: str, patch: str) -> str | None:
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if text is not None:
            open(p, "w").write(text)
        subprocess.run(["git", "init", "-q", d], check=True)
        r = subprocess.run(["git", "-C", d, "apply", "--whitespace=nowarn", f"--include={rel}", "-"], input=patch,
                           text=True, capture_output=True)
        if r.returncode:
            return text
        return open(p).read() if os.path.exists(p) else None


def apply_edit(text: str | None, old: str, new: str, occurrence=None) -> str | None:
    """The edit tool's own semantics on `text` (exact text; `occurrence` picks among several), or None if refused."""
    if old == "":
        return new if text is None else None
    if text is None:
        return None
    starts, i = [], text.find(old)
    while i != -1:
        starts.append(i)
        i = text.find(old, i + 1)
    if not starts:
        return None
    if len(starts) > 1:
        try:
            k = int(occurrence) if occurrence is not None else None
        except (TypeError, ValueError):
            return None
        if k is None or not 1 <= k <= len(starts):
            return None
        at = starts[k - 1]
    else:
        at = starts[0]
    return text[:at] + new + text[at + len(old):]


def _one_nl(s: str) -> tuple[str, bool]:
    """`s` without one trailing newline, and whether it had one."""
    return (s[:-1], True) if s.endswith("\n") else (s, False)


def rebase(line_indent: str, new: str) -> str:
    """`new` with its leading whitespace replaced by the file line's own; an empty `new` stays empty."""
    return line_indent + new.lstrip() if new.strip() else ""


def proposed(text: str, old: str, new: str) -> dict:
    """The pinned rule on one refused call (rule v2: one trailing newline aside, and an empty `new` deletes)."""
    o, o_nl = _one_nl(old)
    n, n_nl = _one_nl(new)
    if "\n" in o or "\n" in n:
        return {"decision": "multi_line"}
    lines = text.split("\n")
    hits = [i for i, ln in enumerate(lines) if o.strip() and ln.lstrip() == o.lstrip()]
    if len(hits) > 1:
        return {"decision": "ambiguous", "lines": [h + 1 for h in hits]}
    if not hits:
        return {"decision": "out_of_scope"}
    i = hits[0]
    indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
    start = sum(len(ln) + 1 for ln in lines[:i])                   # the line's offset in `text`
    span = len(lines[i]) + (1 if o_nl and i < len(lines) - 1 else 0)

    def result(repl: str) -> str:
        return text[:start] + repl + text[start + span:]
    applied = rebase(indent, n) + ("\n" if o_nl and n_nl else "")
    as_given = n + ("\n" if o_nl and n_nl else "")
    return {"decision": "applied", "line": i + 1, "source": lines[i], "applied": applied.rstrip("\n"),
            "as_given": as_given.rstrip("\n"), "text_after": result(applied), "text_as_given": result(as_given)}


def _parses(text: str) -> bool:
    try:
        ast.parse(text)
        return True
    except SyntaxError:
        return False


def events(run_glob: str):
    for path in sorted(glob.glob(run_glob, recursive=True)):
        name = os.path.basename(path)[:-6]
        if name.count("__") != 2:
            continue
        try:
            recs = [json.loads(ln) for ln in open(path)]
        except (OSError, json.JSONDecodeError):
            continue
        for i, r in enumerate(recs):
            o = r.get("output_head") or ""
            if r.get("tool") == "edit_file" and "apart from whitespace" in o:
                yield path, name, recs, i


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("out")
    p.add_argument("--clones", required=True)
    p.add_argument("--runs", default=os.path.join("reports", "r4", "**", "*.jsonl"))
    a = p.parse_args(argv)
    versions, urls = task_versions()
    repos: dict[str, Repo] = {}
    rows = []
    for path, name, recs, i in events(a.runs):
        tid = name.split("__")[0]
        r = recs[i]
        args = r.get("args") or {}
        rel = str(args.get("path", "")).replace("\\", "/").removeprefix("./")
        out = r.get("output_head") or ""
        msg = "current" if "with its spaces" in out else "old"
        m = re.search(r"at line (\d+)", out)
        line = int(m.group(1)) if m else None
        m2 = re.search(r"with its spaces\):\s*\n(\d+)\t([^\n]*)", out)
        shown = m2.group(2) if m2 else None
        ev = {"run": f"{path.split(os.sep)[2]}/{name}", "turn": r.get("turn"), "file": rel, "message": msg,
              "line": line, "sent_old": args.get("old"), "sent_new": args.get("new"),
              "single_line": "\n" not in _one_nl(str(args.get("old", "")))[0]}
        text, used = None, None
        for task in versions.get(tid, []):
            if task["repo"] not in urls:
                continue
            if task["repo"] not in repos:
                repos[task["repo"]] = Repo(a.clones, task["repo"], urls[task["repo"]])
            t = repos[task["repo"]].start_text(task, rel)
            for prev in recs[:i]:                                       # the run's earlier applied edits, in order
                pa = prev.get("args") or {}
                if prev.get("tool") == "edit_file" and str(pa.get("path", "")).replace("\\", "/").removeprefix(
                        "./") == rel and (prev.get("output_head") or "").startswith(("edited", "created")):
                    t2 = apply_edit(t, str(pa.get("old", "")), str(pa.get("new", "")), pa.get("occurrence"))
                    t = t2 if t2 is not None else t
            if t is None:
                continue
            lines = t.split("\n")
            ok = line is not None and 0 < line <= len(lines) and (
                shown is None or lines[line - 1].startswith(shown) or lines[line - 1].strip().startswith(
                    shown.strip()))
            if ok and apply_edit(t, str(args.get("old", "")), "x", args.get("occurrence")) is None:
                text, used = t, task
                break
        if text is None:
            ev["verdict"] = "unverified"
            rows.append(ev)
            continue
        ev["existing"] = "refused"
        res = proposed(text, str(args.get("old", "")), str(args.get("new", "")))
        ev["proposed"] = res["decision"]
        if res["decision"] == "applied":
            src = res["source"]
            expected, how = None, None
            for later in recs[i + 1:]:                                  # the run's own later edit of that line
                la = later.get("args") or {}
                lo, _ = _one_nl(str(la.get("old", "")))
                if later.get("tool") == "edit_file" and str(la.get("path", "")).replace("\\", "/").removeprefix(
                        "./") == rel and (later.get("output_head") or "").startswith("edited") and lo == src:
                    exp = apply_edit(text, str(la.get("old", "")), str(la.get("new", "")), la.get("occurrence"))
                    if exp is not None:
                        expected, how = exp, "the run's later edit (whole file)"
                    break
            if expected is None:
                g = gold_pairs(gold_patch(tid)).get(src)
                if g is not None:
                    lines = text.split("\n")
                    k = res["line"] - 1
                    expected, how = "\n".join(lines[:k] + [g] + lines[k + 1:]), "the answer key (line)"
            ev.update(source=src, applied=res["applied"], as_given=res["as_given"], expected_from=how,
                      applied_line=res["line"])
            judge = lambda x: "undetermined" if expected is None else ("intended" if x == expected else "unintended")
            ev["verdict"] = judge(res["text_after"])
            ev["verdict_as_given"] = judge(res["text_as_given"])
            if expected is not None and ev["verdict"] == "unintended":
                import difflib
                ev["difference"] = "".join(list(difflib.unified_diff(expected.splitlines(True),
                                                                     res["text_after"].splitlines(True), n=0))[:12])
            if rel.endswith(".py"):
                ev["parsed_before"] = _parses(text)
                ev["parses_after"] = _parses(res["text_after"])
                ev["parses_as_given"] = _parses(res["text_as_given"])
        else:
            ev["verdict"] = "not_applied"
        rows.append(ev)

    def tally(rs):
        c = lambda k, v: sum(1 for r in rs if r.get(k) == v)
        ap = [r for r in rs if r.get("proposed") == "applied"]
        return {"events": len(rs), "unverified": c("verdict", "unverified"),
                "single_line": sum(1 for r in rs if r["single_line"]),
                "multi_line": sum(1 for r in rs if not r["single_line"]),
                "existing_refused": c("existing", "refused"),
                "proposed": {k: c("proposed", k) for k in ("applied", "ambiguous", "out_of_scope", "multi_line")},
                "applied": {"intended": c("verdict", "intended"), "unintended": c("verdict", "unintended"),
                            "undetermined": c("verdict", "undetermined"),
                            "breaks_parse": sum(1 for r in ap if r.get("parsed_before") and r.get("parses_after")
                                                is False)},
                "as_given": {"intended": sum(1 for r in ap if r.get("verdict_as_given") == "intended"),
                             "unintended": sum(1 for r in ap if r.get("verdict_as_given") == "unintended"),
                             "breaks_parse": sum(1 for r in ap if r.get("parsed_before") and
                                                 r.get("parses_as_given") is False)}}
    doc = {"totals": {m: tally([r for r in rows if r["message"] == m]) for m in ("current", "old")},
           "events": rows}
    json.dump(doc, open(a.out, "w"), indent=1)
    print(json.dumps(doc["totals"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
