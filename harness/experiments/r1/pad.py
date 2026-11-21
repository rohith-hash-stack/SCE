"""Token-matched baselines for R1 (README, "The padded versions"): ordinary code, chosen without the call graph.

    python -m harness.experiments.r1.pad build TYPE REPO CLONE [TOKENIZER]   # -> pads/TYPE/REPO.json (resumable)
    python -m harness.experiments.r1.pad freeze                               # -> pads/manifest.json

TYPE is `forward`, `blast` or `newfn`. For each task, each padded version's prompt is the base prompt plus one block of
code, filled until the whole prompt has the real token count (Qwen2.5 tokenizer) of the PRISM prompt, within 5%.
At most BATCH tasks per process (memory); exit code 3 means run it again.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
PADS = HERE / "pads"
SUITE = HERE.parent / "suite" / "repos"
DEFAULT_TOKENIZER = "/home/user/models/qwen2-tokenizer/tokenizer.json"
BATCH = int(__import__('os').environ.get('R1_PAD_BATCH', '8'))
BAND = 0.05
SKIP_BELOW = 0.95          # a base prompt already at 95% of PRISM's size gets no padding
CANDIDATES = 400           # BM25 ranks looked at

TYPES = {"forward": {"rev": "parent", "versions": ("FILE_BM25PAD", "FILE_GREPPAD")},
         "blast": {"rev": "commit", "versions": ("GREP_PAD",)},
         "newfn": {"rev": "parent", "versions": ("FILE_BM25PAD",)}}
BASE = {"FILE_BM25PAD": "FILE", "FILE_GREPPAD": "FILE", "GREP_PAD": "GREP"}

BM25_HEAD = "Other code from the repository (the functions a keyword search ranks highest for this {what}):\n\n"
BODIES_HEAD = "The code of the functions those lines are in:\n\n"
MORE_HEAD = "\n\nMore code from the repository (keyword search):\n\n"
NEAR_HEAD = "\n\nMore code from the repository (the same file, then the same folder, then the rest):\n\n"


def base_prompt(kind: str, task: dict, version: str) -> str:
    """The suite's own prompt for `version` (FILE, GREP or PRISM)."""
    if kind == "forward":
        from harness.experiments.change_history.run import prompt
    elif kind == "blast":
        from harness.experiments.suite.blast import prompt
    else:
        from harness.experiments.suite.newfn import prompt
    return prompt(task, version)


def pad_prompt(kind: str, task: dict, version: str, pads: dict) -> str:
    """The prompt of a padded version: its base prompt, then the frozen block."""
    block = pads[task["id"]][version]["block"]
    base = base_prompt(kind, task, BASE[version])
    return base + "\n" + block if block else base


# ------------------------------------------------------------------ what each task shows already
def _shown(kind: str, task: dict) -> tuple[str, int, int, str | None]:
    """(file, first line, last line) of the file window, and the function the task is about (excluded)."""
    if kind == "forward":
        from harness.experiments.change_history.run import file_window
        _text, lo, hi = file_window(task)
        return task["anchor_file"], lo, hi, task["anchor"]
    lo, hi = task["window"]
    return task["file"], lo, hi, task.get("anchor")


def _query(kind: str, task: dict) -> str:
    if kind == "blast":
        return task["anchor"].rsplit(".", 1)[-1]
    if kind == "forward":
        return task["anchor"].rsplit(".", 1)[-1] + "\n" + task["anchor_new"]
    return task["signature"] + "\n" + task["purpose"]


class Repo:
    """One revision's index, its production functions and their spans."""

    def __init__(self, work: str):
        from prism.cli import build_pipeline
        from prism.query.nlq import NLQIndex
        self.work = work
        self.b = build_pipeline(work, use_cache=False)[0]
        self.nlq = NLQIndex(self.b, work, summaries=None)
        self.spans: dict[str, list[tuple[int, int, str]]] = {}
        for q in self.b._def_nodes:
            s = self.b.symbol_table.get(q)
            if s is None or s.kind not in ("function", "method") or self.b._in_test_file(s.file):
                continue
            self.spans.setdefault(os.path.relpath(s.file, work), []).append((s.line_range[0], s.line_range[1], q))
        self.prod_files = sorted(self.spans)

    def enclosing(self, rel: str, line: int) -> str | None:
        inside = [(hi - lo, q) for lo, hi, q in self.spans.get(rel, ()) if lo <= line <= hi]
        return min(inside)[1] if inside else None

    def in_window(self, q: str, rel: str, lo: int, hi: int) -> bool:
        s = self.b.symbol_table.get(q)
        return s is not None and os.path.relpath(s.file, self.work) == rel and lo <= s.line_range[0] and s.line_range[1] <= hi

    def bm25(self, query: str) -> list[str]:
        from prism.query.nlq import tokenize
        ranked = [self.nlq.names[i] for i in self.nlq.bm25.rank(tokenize(query))][:CANDIDATES]
        prod = {q for fs in self.spans.values() for _lo, _hi, q in fs}
        return [q for q in ranked if q in prod]

    def nearby(self, rel: str) -> list[str]:
        """Every production function: the given file first, then its folder, then the rest, each in file order."""
        folder = os.path.dirname(rel)
        order = lambda f: (f != rel, os.path.dirname(f) != folder, f)
        return [q for f in sorted(self.spans, key=order) for _lo, _hi, q in sorted(self.spans[f])]

    def grep(self, leaf: str) -> list[tuple[str, int]]:
        hits = []
        for rel in self.prod_files:
            try:
                lines = Path(self.work, rel).read_text(errors="replace").splitlines()
            except OSError:
                continue
            hits += [(rel, i) for i, ln in enumerate(lines, 1) if re.search(rf"\b{re.escape(leaf)}\b", ln)]
        return hits


# ------------------------------------------------------------------ filling
def _fill(count, base: str, head: str, groups: list[tuple[str, list[tuple[str, str]]]], target: int) -> dict:
    """Add items in order (each group opened by its title) while the whole prompt stays within the target, then the
    first lines of the next item when the prompt is still more than 5% short."""
    block, shown, partial = head, [], None
    total = lambda b: count(base + "\n" + b)  # the whole prompt, counted exactly (token counts do not add up)
    for title, items in groups:
        for n, (q, text) in enumerate(items):
            piece = (title if n == 0 and title else ("\n\n" if shown else "")) + text
            if total(block + piece) <= target:
                block += piece
                shown.append(q)
                continue
            if total(block) < (1 - BAND) * target:
                lines = piece.splitlines()
                lo, hi = 0, len(lines)
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if total(block + "\n".join(lines[:mid]) + "\n```") <= target:
                        lo = mid
                    else:
                        hi = mid - 1
                if lo:
                    block, partial = block + "\n".join(lines[:lo]) + "\n```", q
            if total(block) < (1 - BAND) * target:  # not even this item's first lines fit (a minified file): skip it
                continue
            return {"block": block, "items": shown, "partial": partial}
    return {"block": block, "items": shown, "partial": partial}


def build_task(kind: str, task: dict, repo: Repo, count) -> dict:
    from harness.experiments.suite.bugs import _hit_text
    rel, lo, hi, own = _shown(kind, task)
    target = count(base_prompt(kind, task, "PRISM"))
    keep = lambda q: q != own and not repo.in_window(q, rel, lo, hi)
    text = lambda q: _hit_text(repo.b, repo.work, q)
    near = lambda used: [(q, text(q)) for q in repo.nearby(rel) if keep(q) and q not in used][:200]
    bm25 = [q for q in repo.bm25(_query(kind, task)) if keep(q)]
    out = {"target": target}
    for version in TYPES[kind]["versions"]:
        base = base_prompt(kind, task, BASE[version])
        rec: dict = {"base_tokens": count(base)}
        if rec["base_tokens"] >= SKIP_BELOW * target:
            rec.update(block="", items=[], partial=None, empty=True)
        elif version == "FILE_BM25PAD":
            what = "change" if kind == "forward" else "new function"
            rec.update(_fill(count, base, BM25_HEAD.format(what=what),
                             [("", [(q, text(q)) for q in bm25[:80]]), (NEAR_HEAD, near(bm25))], target))
        else:
            leaf = task["anchor"].rsplit(".", 1)[-1]
            hits = repo.grep(leaf)
            head = ""
            if version == "FILE_GREPPAD":
                from harness.experiments.suite.blast import BUDGET, GREP_BLOCK, _cap
                lines = "\n".join(f"{f}:{i}: {Path(repo.work, f).read_text(errors='replace').splitlines()[i - 1].strip()[:160]}"
                                  for f, i in hits)
                lines = _cap(lines, BUDGET).splitlines()
                while lines and count(base + "\n" + GREP_BLOCK.format(leaf=leaf, lines="\n".join(lines)) + "\n"
                                      + BODIES_HEAD) > (1 - BAND) * target:  # the lines alone must leave room
                    cut = max(1, len(lines) // 10)
                    shown = [ln for ln in lines if not ln.startswith("... (")][:-cut]
                    lines = shown + [f"... ({len(hits) - len(shown)} more lines not shown)"] if shown else []
                head = GREP_BLOCK.format(leaf=leaf, lines="\n".join(lines)) + "\n" if lines else ""
            bodies = []
            for f, i in hits:
                q = repo.enclosing(f, i)
                if q and keep(q) and q not in bodies:
                    bodies.append(q)
            more = [q for q in bm25 if q not in bodies][:80]
            rec.update(_fill(count, base, head + BODIES_HEAD, [("", [(q, text(q)) for q in bodies]),
                                                               (MORE_HEAD, [(q, text(q)) for q in more]),
                                                               (NEAR_HEAD, near(bodies + more))], target))
            rec["bodies"] = sum(q in bodies for q in rec["items"])
        full = base + "\n" + rec["block"] if rec["block"] else base
        rec["tokens"] = count(full)
        rec["within"] = abs(rec["tokens"] - target) <= BAND * target
        out[version] = rec
    return out


# ------------------------------------------------------------------ driver
def build(kind: str, repo_name: str, clone: str, tokenizer: str = DEFAULT_TOKENIZER) -> int:
    from tokenizers import Tokenizer
    tok = Tokenizer.from_file(tokenizer)
    count = lambda s: len(tok.encode(s).ids)
    tasks = json.loads((SUITE / repo_name / f"{kind}.json").read_text())["tasks"]
    path = PADS / kind / f"{repo_name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(path.read_text()) if path.exists() else {"type": kind, "repo": repo_name, "tasks": {}}
    rev_key = TYPES[kind]["rev"]
    cur_rev, repo, wt = None, None, None
    done, code = 0, 0
    try:
        for t in tasks:
            if t["id"] in data["tasks"]:
                continue
            if done >= BATCH:
                code = 3
                break
            if t[rev_key] != cur_rev:  # one revision (one worktree and its index) at a time
                if wt:
                    subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", wt], capture_output=True)
                wt = tempfile.mkdtemp(prefix="pad_")
                subprocess.run(["git", "-C", clone, "worktree", "add", "-q", "--detach", wt, t[rev_key]], check=True,
                               capture_output=True)
                cur_rev, repo = t[rev_key], Repo(wt)
            data["tasks"][t["id"]] = build_task(kind, t, repo, count)
            path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
            done += 1
            r = data["tasks"][t["id"]]
            print(f"  {t['id']}: target {r['target']} " + " ".join(
                f"{v} {r[v]['tokens']}{'' if r[v]['within'] else ' OUT'}{' (no pad)' if r[v].get('empty') else ''}"
                for v in TYPES[kind]["versions"]), flush=True)
    finally:
        if wt:
            subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", wt], capture_output=True)
    return code


def freeze() -> int:
    files = sorted(PADS.glob("*/*.json"))
    entries = []
    for f in files:
        d = json.loads(f.read_text())
        recs = [r[v] for r in d["tasks"].values() for v in TYPES[d["type"]]["versions"]]
        entries.append({"path": os.path.relpath(f, HERE.parents[2]),  # relative to the repository root
                        "sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "type": d["type"],
                        "repo": d["repo"], "tasks": len(d["tasks"]), "within": sum(r["within"] for r in recs),
                        "padded": len(recs), "no_pad": sum(bool(r.get("empty")) for r in recs)})
    (PADS / "manifest.json").write_text(json.dumps({"status": "frozen", "files": entries}, indent=1) + "\n")
    print(json.dumps(entries, indent=1))
    return 0


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args and args[0] == "freeze":
        return freeze()
    if len(args) >= 4 and args[0] == "build":
        return build(args[1], args[2], args[3], *(args[4:5] or [DEFAULT_TOKENIZER]))
    raise SystemExit(__doc__)


if __name__ == "__main__":
    sys.exit(main())
