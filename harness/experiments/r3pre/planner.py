"""A3 (before R3): can a model plan a multi-file change from digests alone? (rubric: README.md, before any call)

    python -m harness.experiments.r3pre.planner mine CLONES_DIR OUT_CANDIDATES.json      # no model
    python -m harness.experiments.r3pre.planner build CLONES_DIR CANDIDATES.json TASKS.json [TOKENIZER]
    python -m harness.experiments.r3pre.planner generate TASKS.json OUT_DIR [--base-url U] [--model M]
    python -m harness.experiments.r3pre.planner score TASKS.json OUT_DIR

A task is a merged pull request whose branch (3 or more commits) changed 30-50 existing source functions in 3-25
files. The model sees the PR's title and the digests of those functions at the PR's base (in a seeded random order,
no bodies). It answers with an ordered list of subtasks, each naming files. The truth is the order in which the PR's
own commits first touched each file, and its number of commits.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SEED = 5501
MIN_FUNCS, MAX_FUNCS = 30, 50
MIN_COMMITS, MAX_COMMITS = 3, 30
MIN_FILES, MAX_FILES = 3, 25
PER_REPO = 4
N_TASKS = 20
DIGEST_LINES = 14            # lines of each digest shown (more are cut, uniformly for every function)
PROMPT_TOKEN_CAP = 15000     # real tokens (Qwen2.5 tokenizer); the window is 20,480 with 4,096 for the answer
SOURCE = (".py", ".js", ".mjs", ".cjs", ".ts")
TEST = re.compile(r"(^|/)(tests?|testing|__tests__|spec)(/|$)|(^|/)test_[^/]*$|_test\.py$|\.(test|spec)\.[jt]s$|conftest\.py$")

INSTRUCTIONS = """You are a software engineer planning a change in the repository `{repo}`.

The change: {title}

These {n} existing functions will be changed. For each you see its file and its PRISM digest (what its code does:
calls, returns, raises, state it changes, under which conditions). You do not see their code.

{digests}

Plan the change as a sequence of subtasks, in the order you would do them. Each subtask touches one or more files.
Respond with a JSON object:
{{"subtasks": [{{"goal": "<one sentence>", "files": ["<path>", ...], "functions": ["<function>", ...]}}, ...]}}
"""


def git(clone: str, *args: str) -> str:
    return subprocess.run(["git", "-C", clone, *args], capture_output=True, text=True, errors="replace").stdout


def _show(clone: str, rev: str, path: str) -> bytes | None:
    r = subprocess.run(["git", "-C", clone, "show", f"{rev}:{path}"], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def _norm(code: str) -> str:
    return "\n".join(ln.strip() for ln in code.splitlines() if ln.strip() and not ln.strip().startswith(("#", "//")))


# ------------------------------------------------------------------ mining (no model)
def mine_repo(name: str, clone: str, limit: int = 1500) -> list[dict]:
    from harness.experiments.change_history.mine import functions
    out = []
    for h in git(clone, "log", "--merges", "--first-parent", "--format=%H", "origin/HEAD").split()[:limit]:
        head = f"{h}^2"
        base = git(clone, "merge-base", f"{h}^1", head).strip()
        if not base:
            continue
        commits = git(clone, "rev-list", "--reverse", "--no-merges", f"{base}..{head}").split()
        if not MIN_COMMITS <= len(commits) <= MAX_COMMITS:
            continue
        files = [f for f in git(clone, "diff", "--name-only", base, head).split()
                 if f.endswith(SOURCE) and not TEST.search(f)]
        if not MIN_FILES <= len(files) <= MAX_FILES:
            continue
        changed = []
        for f in files:
            old, new = _show(clone, base, f), _show(clone, head, f)
            if old is None or new is None:
                continue
            fo, fn = functions(f, old), functions(f, new)
            changed += [{"file": f, "name": q, "start": fo[q]["start"]} for q in fo
                        if q in fn and _norm(fo[q]["code"]) != _norm(fn[q]["code"])]
        if not MIN_FUNCS <= len(changed) <= MAX_FUNCS:
            continue
        touched, order = set(), []
        for c in commits:
            for f in git(clone, "diff-tree", "--no-commit-id", "--name-only", "-r", c).split():
                if f in files and f not in touched:
                    touched.add(f)
                    order.append({"file": f, "commit_index": commits.index(c)})
        msg = git(clone, "log", "-1", "--format=%B", h).strip().splitlines()
        title = next((ln.strip() for ln in msg[1:] if ln.strip()), msg[0] if msg else "")
        out.append({"repo": name, "merge": h, "base": base, "head": git(clone, "rev-parse", head).strip(),
                    "title": title, "commits": len(commits), "files": files, "functions": changed,
                    "file_order": order})
        print(f"  {name} {h[:10]}: {len(commits)} commits, {len(files)} files, {len(changed)} functions", flush=True)
    return out


def select(cands: list[dict]) -> list[dict]:
    by: dict[str, list[dict]] = {}
    for c in sorted(cands, key=lambda c: (c["repo"], c["merge"])):
        by.setdefault(c["repo"], []).append(c)
    rng = random.Random(SEED)
    for pool in by.values():
        rng.shuffle(pool)
    picked, rnd = [], 0
    while len(picked) < N_TASKS and any(len(p) > rnd for p in by.values()) and rnd < PER_REPO:
        for repo in sorted(by):
            if len(by[repo]) > rnd and len(picked) < N_TASKS:
                picked.append(by[repo][rnd])
        rnd += 1
    return picked


# ------------------------------------------------------------------ building (no model)
def build_one(c: dict, clone: str, count) -> dict | None:
    from harness.experiments.change_history.mine import _qname_for
    from prism.cli import build_pipeline
    from prism.summaries.digest import DigestBuilder
    wt = tempfile.mkdtemp(prefix="plan_")
    subprocess.run(["git", "-C", clone, "worktree", "add", "-q", "--detach", wt, c["base"]], check=True, capture_output=True)
    try:
        b = build_pipeline(wt, use_cache=False)[0]
        db = DigestBuilder(b)
        fns = []
        for f in c["functions"]:
            q = _qname_for(b, wt, f["file"], f["start"])
            if q:
                fns.append({**f, "qname": q, "digest": db.digest(q).render(header=False).splitlines()})
        qs = {f["qname"] for f in fns}
        edges = sorted({(u, v) for u, v in b.graph.edges() if u in qs and v in qs and u != v})
    finally:
        subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", wt], capture_output=True)
    if len(fns) < MIN_FUNCS:
        return None
    random.Random(f"{SEED}:{c['merge']}").shuffle(fns)
    lines = DIGEST_LINES
    while True:
        text = "\n\n".join(f"`{f['qname']}` ({f['file']}):\n" + "\n".join(f["digest"][:lines]) +
                           (f"\n  ... ({len(f['digest']) - lines} more digest lines)" if len(f["digest"]) > lines else "")
                           for f in fns)
        prompt = INSTRUCTIONS.format(repo=c["repo"], title=c["title"], n=len(fns), digests=text)
        if count(prompt) <= PROMPT_TOKEN_CAP or lines <= 3:
            break
        lines -= 1
    return {**c, "id": f"{c['repo']}-plan-{c['merge'][:8]}", "shown": [f["qname"] for f in fns],
            "file_of": {f["qname"]: f["file"] for f in fns}, "edges": edges, "digest_lines": lines,
            "prompt": prompt, "prompt_tokens": count(prompt)}


# ------------------------------------------------------------------ answers and scores
def parse(raw: str) -> list[dict]:
    try:
        got = json.loads(raw)
        subs = got.get("subtasks", []) if isinstance(got, dict) else []
        return [s for s in subs if isinstance(s, dict)]
    except (ValueError, TypeError):
        return []


def _norm_path(p: str, files: list[str]) -> str | None:
    p = (p or "").strip().strip("`").lstrip("./")
    if p in files:
        return p
    hits = [f for f in files if f.endswith("/" + p) or p.endswith("/" + f) or f.rsplit("/", 1)[-1] == p.rsplit("/", 1)[-1]]
    return hits[0] if len(hits) == 1 else None


def kendall(order_a: list[str], rank_b: dict[str, int]) -> float | None:
    """Kendall's tau-b between a list order and a rank map, over the items both have."""
    from scipy.stats import kendalltau
    items = [x for x in order_a if x in rank_b]
    if len(items) < 3:
        return None
    t = kendalltau(list(range(len(items))), [rank_b[x] for x in items]).correlation
    return None if t != t else float(t)


def score_one(task: dict, raw: str) -> dict:
    files = task["files"]
    truth = {o["file"]: o["commit_index"] for o in task["file_order"]}
    first_commit = {f for f, i in truth.items() if i == min(truth.values())}
    subs = parse(raw)
    order, seen = [], set()
    for s in subs:
        for p in s.get("files", []) or []:
            f = _norm_path(p, files)
            if f and f not in seen:
                seen.add(f)
                order.append(f)
    # dependency order: files of callees no later than files of their callers (PRISM's index at the base)
    pos = {f: i for i, f in enumerate(order)}
    pairs = [(task["file_of"][v], task["file_of"][u]) for u, v in task["edges"]]  # (callee file, caller file)
    pairs = [(a, b) for a, b in pairs if a != b and a in pos and b in pos]
    dirs = lambda fs: {f.rsplit("/", 1)[0] if "/" in f else "" for f in fs}
    return {"answered": bool(subs), "subtasks": len(subs), "commits": task["commits"],
            "count_within_2x": bool(subs) and 0.5 <= len(subs) / task["commits"] <= 2.0,
            "first_file_hit": bool(order) and order[0] in first_commit,
            "first_file_hit_alphabetical": sorted(files)[0] in first_commit,
            "tau": kendall(order, truth), "tau_alphabetical": kendall(sorted(files), truth),
            "files_named": len(order), "files_total": len(files),
            "dependency_respected": (sum(pos[a] <= pos[b] for a, b in pairs) / len(pairs)) if pairs else None,
            "dependency_pairs": len(pairs),
            "single_directory_subtasks": (sum(len(dirs([_norm_path(p, files) for p in s.get("files", []) or []
                                                        if _norm_path(p, files)])) == 1 for s in subs) / len(subs)) if subs else None}


def summary(rows: dict, first_file_base: dict) -> dict:
    """The rubric's verdicts (README; primary since amendment 2: the first-file hit). Bootstrap 95% intervals over
    tasks, 10,000 resamples, seed 5501."""
    from harness.experiments.suite import stats
    ids = sorted(rows)
    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None
    hit = [float(rows[i]["first_file_hit"]) for i in ids]
    random_base = [first_file_base[i] for i in ids]
    alpha_hit = [float(rows[i]["first_file_hit_alphabetical"]) for i in ids]
    vs_random, vs_alpha = stats.bootstrap_diff(random_base, hit, ids), stats.bootstrap_diff(alpha_hit, hit, ids)
    tau = [rows[i]["tau"] or 0.0 for i in ids]                      # exploratory; no measurable order counts as 0
    alpha = [rows[i]["tau_alphabetical"] or 0.0 for i in ids]
    t_vs_0, t_vs_alpha = stats.bootstrap_diff([0.0] * len(ids), tau, ids), stats.bootstrap_diff(alpha, tau, ids)
    dep_ids = [i for i in ids if rows[i]["dependency_respected"] is not None]
    dep = stats.bootstrap_diff([0.5] * len(dep_ids), [rows[i]["dependency_respected"] for i in dep_ids], dep_ids) if dep_ids else None
    return {"tasks": len(ids), "answered": sum(rows[i]["answered"] for i in ids),
            "primary_first_file_hit": mean(hit), "random_base_rate": mean(random_base), "alphabetical_hit": mean(alpha_hit),
            "hit_minus_random": [vs_random["mean"], vs_random["low"], vs_random["high"]],
            "hit_minus_alphabetical": [vs_alpha["mean"], vs_alpha["low"], vs_alpha["high"]],
            "verdict_digests_support_planning": vs_random["low"] > 0 and vs_alpha["low"] > 0,
            "exploratory_tau_mean": mean(tau), "exploratory_tau_interval": [t_vs_0["low"], t_vs_0["high"]],
            "exploratory_tau_alphabetical_mean": mean(alpha),
            "exploratory_tau_minus_alphabetical": [t_vs_alpha["mean"], t_vs_alpha["low"], t_vs_alpha["high"]],
            "count_within_2x": mean([float(rows[i]["count_within_2x"]) for i in ids]),
            "dependency_respected_mean": mean([rows[i]["dependency_respected"] for i in dep_ids]),
            "dependency_interval_vs_half": [dep["low"], dep["high"]] if dep else None, "dependency_tasks": len(dep_ids),
            "single_directory_subtasks": mean([rows[i]["single_directory_subtasks"] for i in ids
                                               if rows[i]["single_directory_subtasks"] is not None]),
            "verdict_dependency_visible": bool(dep) and dep["low"] > 0}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("cmd", choices=("mine", "build", "generate", "score"))
    p.add_argument("args", nargs="*")
    p.add_argument("--base-url", default="http://127.0.0.1:11434")
    p.add_argument("--model", default="qwen2.5-coder:14b-instruct-q8_0")
    a = p.parse_args(argv)
    if a.cmd == "mine":
        root, out = a.args
        cands = []
        for d in sorted(Path(root).iterdir()):
            if (d / ".git").exists():
                cands += mine_repo(d.name, str(d))
        Path(out).write_text(json.dumps(cands, indent=1) + "\n")
        print(f"{len(cands)} candidates")
    elif a.cmd == "build":
        import hashlib
        from tokenizers import Tokenizer
        root, cands_path, out = a.args[:3]
        tok = Tokenizer.from_file(a.args[3] if len(a.args) > 3 else "/home/user/models/qwen2-tokenizer/tokenizer.json")
        count = lambda s: len(tok.encode(s).ids)
        cands = json.loads(Path(cands_path).read_text())
        tasks, used = [], set()
        for c in select(cands) + [c for c in cands if c not in select(cands)]:  # replacements in the same seeded order
            if len(tasks) >= N_TASKS or c["merge"] in used or sum(t["repo"] == c["repo"] for t in tasks) >= PER_REPO:
                continue
            t = build_one(c, str(Path(root) / c["repo"]), count)
            if t:
                tasks.append(t)
                used.add(c["merge"])
                print(f"  {t['id']}: {len(t['shown'])} functions, {t['prompt_tokens']} tokens, {len(t['edges'])} edges", flush=True)
        body = {"status": "frozen", "tasks": tasks,
                "tasks_sha256": hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest()}
        Path(out).write_text(json.dumps(body, indent=1) + "\n")
    elif a.cmd == "generate":
        from harness.experiments.change_history.run import ollama
        tasks = json.loads(Path(a.args[0]).read_text())["tasks"]
        out = Path(a.args[1]); out.mkdir(parents=True, exist_ok=True)
        path = out / "answers.json"
        answers = json.loads(path.read_text()) if path.exists() else {}
        for t in tasks:
            if t["id"] in answers:
                continue
            stats: dict = {}
            try:
                text, secs = ollama(a.base_url, a.model, t["prompt"], stats=stats, seed=42)
                answers[t["id"]] = {"raw": text, "seconds": round(secs, 1), "model": a.model, **stats}
            except Exception as exc:
                answers[t["id"]] = {"raw": "", "error": f"{type(exc).__name__}: {exc}", "model": a.model}
            path.write_text(json.dumps(answers, indent=1, sort_keys=True) + "\n")
            print(f"  {t['id']}: {answers[t['id']].get('seconds', '-')}s {answers[t['id']].get('done_reason', '')}", flush=True)
    else:
        tasks = json.loads(Path(a.args[0]).read_text())["tasks"]
        answers = json.loads((Path(a.args[1]) / "answers.json").read_text())
        rows = {t["id"]: score_one(t, answers.get(t["id"], {}).get("raw", "")) for t in tasks}
        base = {t["id"]: len({o["file"] for o in t["file_order"] if o["commit_index"] == min(x["commit_index"] for x in t["file_order"])})
                / len(t["files"]) for t in tasks}
        res = {"rows": rows, "summary": summary(rows, base)}
        (Path(a.args[1]) / "scores.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
        print(json.dumps(res["summary"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
