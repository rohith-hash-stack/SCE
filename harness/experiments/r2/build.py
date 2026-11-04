"""R2 build (A6; amendments R2-1 to R2-4): tasks, the five versions, token-matched pads, model-free measures, freeze.

    python -m harness.experiments.r2.build tasks                      # -> tasks/{dev,heldout}.json (ids; no index)
    python -m harness.experiments.r2.build repo SPLIT REPO CLONE [TOKENIZER]   # -> pads/SPLIT/REPO.json (resumable)
    python -m harness.experiments.r2.build freeze                     # -> manifest.json

Versions (A6): the full change context, and the full context with section 4, 6, 7, or 4 and 7, removed. Every
removed version is padded back to the full version's real token count (Qwen2.5 tokenizer, whole prompt, within 5%)
with R1's FILE_BM25PAD method: the functions a BM25 search over the repository ranks highest for the change (the
changed function's name and new code), then nearby code. The pad never uses the call graph, PRISM or the answer
key; the anchor and the functions already in the file window are excluded, as in R1.

The context of every task, development and held-out, is rebuilt here with the current code (amendment R2-4), at the
task's parent revision, with R1's settings (budget 6,000 tokens, tests excluded). `repo` also records:
- whether the rebuilt context equals the one stored in the task (held-out: mined with the same code, so a
  difference is a determinism bug and stops the build; development: built at R1's commit, so differences are
  expected and counted);
- the model-free measures (A6, "Without a model"): which changed callers each section lists, and which the pad of
  each removed version re-adds.

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
ROOT = HERE.parents[2]
TASKS = HERE / "tasks"
PADS = HERE / "pads"
SUITE = HERE.parent / "suite" / "repos"
DEFAULT_TOKENIZER = "/home/user/models/qwen2-tokenizer/tokenizer.json"
BATCH = int(os.environ.get("R2_BATCH", "6"))
BAND = 0.05
BUDGET = 6000
SPLITS = ("dev", "heldout")

VERSIONS = {"FULL": frozenset(), "NO4": frozenset({4}), "NO6": frozenset({6}), "NO7": frozenset({7}),
            "NO47": frozenset({4, 7})}
REMOVED = tuple(v for v in VERSIONS if v != "FULL")
_HEADER = re.compile(r"^## (\d+)\. ")


# ------------------------------------------------------------------ the versions
def sections(context: str) -> dict[int, str]:
    """The text of each numbered section, header included (what `drop_sections` removes)."""
    out, cur = {}, None
    for line in context.splitlines(keepends=True):
        m = _HEADER.match(line)
        if m:
            cur = int(m.group(1))
        elif line.startswith("Omitted: "):
            cur = None
        if cur is not None:
            out[cur] = out.get(cur, "") + line
    return out


def drop_sections(context: str, drop: frozenset[int]) -> str:
    """The context with the numbered sections in `drop` removed: each from its `## N.` header up to the next header or
    the closing `Omitted:` line. Everything else is kept byte for byte, numbering included (a removed section leaves
    a gap; no renumbering)."""
    if not drop:
        return context
    kept, cur = [], None
    for line in context.splitlines(keepends=True):
        m = _HEADER.match(line)
        if m:
            cur = int(m.group(1))
        elif line.startswith("Omitted: "):
            cur = None
        if cur not in drop:
            kept.append(line)
    return "".join(kept)


def base_prompt(task: dict, version: str) -> str:
    """The forward PRISM prompt (`change_history.run.prompt`, arm PRISM) with the version's sections removed."""
    from harness.experiments.change_history.run import prompt
    return prompt({**task, "prism_context": drop_sections(task["prism_context"], VERSIONS[version])}, "PRISM")


def prompt(task: dict, version: str, pads: dict) -> str:
    """The prompt the model sees: the version's base prompt, then its frozen pad block (none for FULL)."""
    base = base_prompt(task, version)
    if version == "FULL":
        return base
    block = pads[task["id"]][version]["block"]
    return base + "\n" + block if block else base


# ------------------------------------------------------------------ tasks
def _dev_tasks() -> list[dict]:
    out = []
    for f in sorted(SUITE.glob("*/forward.json")):
        for t in json.loads(f.read_text())["tasks"]:
            out.append({**t, "split": "dev", "language": "python" if t["anchor_file"].endswith(".py") else "javascript",
                        "r1_context_sha": hashlib.sha256(t["prism_context"].encode()).hexdigest()})
    return out


def _heldout_tasks() -> list[dict]:
    mined = json.loads((TASKS / "heldout_mined.json").read_text())
    out, n = [], {}
    for t in sorted(mined, key=lambda t: (t["repo"], t["commit"])):
        n[t["repo"]] = n.get(t["repo"], 0) + 1
        out.append({**t, "id": f"{t['repo']}-heldout-{n[t['repo']]:03d}"})
    return out


def make_tasks() -> int:
    """tasks/dev.json and tasks/heldout.json: the task lists with ids (contexts are replaced by `repo`)."""
    for split, tasks in (("dev", _dev_tasks()), ("heldout", _heldout_tasks())):
        path = TASKS / f"{split}.json"
        if path.exists():
            have = json.loads(path.read_text())["tasks"]
            if [t["id"] for t in have] != [t["id"] for t in tasks]:
                raise SystemExit(f"{path}: exists with other tasks")
            continue
        path.write_text(json.dumps({"split": split, "tasks": tasks}, indent=1) + "\n")
        print(f"{split}: {len(tasks)} tasks -> {path}")
    return 0


def tasks_of(split: str, repo: str | None = None) -> list[dict]:
    """The split's tasks, with each context replaced by the rebuilt one where `repo` has built it."""
    tasks = json.loads((TASKS / f"{split}.json").read_text())["tasks"]
    pads: dict[str, dict] = {}
    for f in sorted((PADS / split).glob("*.json")):
        pads.update(json.loads(f.read_text())["tasks"])
    out = []
    for t in tasks:
        if repo is not None and t["repo"] != repo:
            continue
        p = pads.get(t["id"])
        if p is not None:
            t = {**t, "prism_context": p["context"], "prism_tokens": p["context_tokens_prism"]}
        out.append(t)
    return out


def pads_of(split: str, repo: str) -> dict:
    path = PADS / split / f"{repo}.json"
    return json.loads(path.read_text())["tasks"] if path.exists() else {}


# ------------------------------------------------------------------ model-free measures
def _listed(text: str, qname: str) -> bool:
    """A caller counts as listed when its qualified name appears in backticks (how the context names functions)."""
    return f"`{qname}`" in text


def measures(task: dict, context: str, pad_items: dict[str, list[str]]) -> dict:
    callers = [c["qname"] for c in task["changed_callers"]]
    secs = sections(context)
    out = {"changed_callers": len(callers),
           "listed_in_section": {str(k): sum(_listed(v, q) for q in callers) for k, v in sorted(secs.items())},
           "listed_anywhere": sum(_listed(context, q) for q in callers)}
    for v in REMOVED:
        kept = drop_sections(context, VERSIONS[v])
        out[f"{v}_listed_in_context"] = sum(_listed(kept, q) for q in callers)
        out[f"{v}_readded_by_pad"] = sum(q in pad_items.get(v, ()) for q in callers)
    return out


# ------------------------------------------------------------------ building one repository
def build_task(task: dict, repo, cb, count) -> dict:
    """Rebuild the context, then pad each removed version to the full version's real token count."""
    from harness.experiments.change_history.run import file_window
    from harness.experiments.r1.pad import BM25_HEAD, NEAR_HEAD, _fill, _query
    from harness.experiments.suite.bugs import _hit_text
    from prism.impact.context import render
    ctx = cb.build(task["anchor"], budget_tokens=BUDGET, include_tests=False)
    context = render(ctx)
    t = {**task, "prism_context": context}
    _text, lo, hi = file_window(t)
    rel, own = task["anchor_file"], task["anchor"]
    keep = lambda q: q != own and not repo.in_window(q, rel, lo, hi)
    text = lambda q: _hit_text(repo.b, repo.work, q)
    bm25 = [q for q in repo.bm25(_query("forward", t)) if keep(q)]
    near = [(q, text(q)) for q in repo.nearby(rel) if keep(q) and q not in bm25][:200]
    target = count(base_prompt(t, "FULL"))
    rec = {"context": context, "context_sha": hashlib.sha256(context.encode()).hexdigest(),
           "context_tokens_prism": ctx.token_count, "stored_context_equal": context == task["prism_context"],
           "target": target}
    groups = [("", [(q, text(q)) for q in bm25[:80]]), (NEAR_HEAD, near)]
    for v in REMOVED:
        base = base_prompt(t, v)
        r = {"base_tokens": count(base)}
        r.update(_fill(count, base, BM25_HEAD.format(what="change"), groups, target))
        full = base + "\n" + r["block"] if r["block"] else base
        r["tokens"] = count(full)
        r["within"] = abs(r["tokens"] - target) <= BAND * target
        rec[v] = r
    rec["measures"] = measures(task, context, {v: rec[v]["items"] for v in REMOVED})
    return rec


def build_repo(split: str, repo_name: str, clone: str, tokenizer: str = DEFAULT_TOKENIZER) -> int:
    from tokenizers import Tokenizer
    from harness.experiments.r1.pad import Repo
    from prism.impact.context import ChangeContextBuilder
    tok = Tokenizer.from_file(tokenizer)
    count = lambda s: len(tok.encode(s).ids)
    tasks = [t for t in json.loads((TASKS / f"{split}.json").read_text())["tasks"] if t["repo"] == repo_name]
    path = PADS / split / f"{repo_name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(path.read_text()) if path.exists() else {"split": split, "repo": repo_name, "tasks": {}}
    cur, repo, cb, wt, done, code = None, None, None, None, 0, 0
    try:
        for t in sorted(tasks, key=lambda t: t["parent"]):
            if t["id"] in data["tasks"]:
                continue
            if done >= BATCH:
                code = 3
                break
            if t["parent"] != cur:  # one revision (worktree and index) at a time
                if wt:
                    subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", wt], capture_output=True)
                wt = tempfile.mkdtemp(prefix="r2_")
                subprocess.run(["git", "-C", clone, "worktree", "add", "-q", "--detach", wt, t["parent"]], check=True,
                               capture_output=True)
                cur, repo = t["parent"], Repo(wt)
                cb = ChangeContextBuilder(repo.b, wt)
            rec = build_task(t, repo, cb, count)
            if split == "heldout" and not rec["stored_context_equal"]:
                raise SystemExit(f"{t['id']}: the rebuilt context differs from the mined one (same code): a bug")
            data["tasks"][t["id"]] = rec
            path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
            done += 1
            print(f"  {t['id']}: target {rec['target']} " + " ".join(
                f"{v} {rec[v]['tokens']}{'' if rec[v]['within'] else ' OUT'}" for v in REMOVED)
                + ("" if rec["stored_context_equal"] else "  (context differs from R1's)"), flush=True)
    finally:
        if wt:
            subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", wt], capture_output=True)
    return code


# ------------------------------------------------------------------ freeze
def freeze() -> int:
    """manifest.json: sha256 of every task and pad file, with the band audit. Refuses if a task has no pads."""
    entries, missing = [], []
    for split in SPLITS:
        tpath = TASKS / f"{split}.json"
        ids = [t["id"] for t in json.loads(tpath.read_text())["tasks"]]
        entries.append({"path": os.path.relpath(tpath, ROOT), "sha256": hashlib.sha256(tpath.read_bytes()).hexdigest(),
                        "split": split, "tasks": len(ids)})
        have = set()
        for f in sorted((PADS / split).glob("*.json")):
            d = json.loads(f.read_text())
            have |= set(d["tasks"])
            recs = [r[v] for r in d["tasks"].values() for v in REMOVED]
            entries.append({"path": os.path.relpath(f, ROOT), "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
                            "split": split, "repo": d["repo"], "tasks": len(d["tasks"]),
                            "padded": len(recs), "within": sum(r["within"] for r in recs),
                            "contexts_differ_from_stored": sum(not r["stored_context_equal"] for r in d["tasks"].values())})
        missing += [i for i in ids if i not in have]
    if missing:
        raise SystemExit(f"{len(missing)} tasks have no pads yet, e.g. {missing[:3]}")
    (HERE / "manifest.json").write_text(json.dumps({"status": "frozen", "files": entries}, indent=1) + "\n")
    print(json.dumps({"files": len(entries), "padded": sum(e.get("padded", 0) for e in entries),
                      "within": sum(e.get("within", 0) for e in entries)}, indent=1))
    return 0


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args[:1] == ["tasks"]:
        return make_tasks()
    if args[:1] == ["freeze"]:
        return freeze()
    if len(args) >= 4 and args[0] == "repo" and args[1] in SPLITS:
        return build_repo(args[1], args[2], args[3], *(args[4:5] or [DEFAULT_TOKENIZER]))
    raise SystemExit(__doc__)


if __name__ == "__main__":
    sys.exit(main())
