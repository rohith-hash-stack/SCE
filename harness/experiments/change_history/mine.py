"""Mine change-propagation tasks from git history.

A task is a real commit in which a developer changed one function (the anchor) and, in the same commit,
changed functions that call it. Given the anchor's change, the AI must name the callers that must change
and write their new code; the commit itself is the answer key.

    python -m harness.experiments.change_history.mine OUT NAME=CLONE [NAME=CLONE ...] [--scan N]

Pass 1 (cheap, per commit): non-merge commits touching 1-6 non-test source files and at most 200 changed
lines. Parse each touched file before and after (tree-sitter), find the functions whose code changed
(comments and whitespace ignored), and keep the commit when a changed function's old code calls another
changed function by name.

Pass 2 (per candidate): index the parent revision with PRISM and keep the anchor when at least one of its
production callers in the index was changed in the commit. The task records the anchor's old and new
code, the callers that changed (old and new code: the answer key), the callers that did not change, and
the tier:

    easy    one caller changed, in the anchor's own file
    medium  one or two callers changed, at least one in another file
    hard    three or more callers changed, or changed callers in two or more other files
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from prism.parser.lang_config import CLASS_NODE_TYPES, FUNCTION_NODE_TYPES
from prism.parser.tree_sitter_loader import parse_source

SOURCE_EXT = (".py", ".js", ".ts", ".mjs", ".cjs")
TEST_PARTS = re.compile(r"(^|/)(tests?|__tests__|spec|testing|examples?|docs?|benchmarks?)/|(^|/)test_[^/]*$|"
                        r"_test\.py$|\.(spec|test)\.[jt]s$")
MAX_FILES = 6
MAX_LINES = 200
MAX_ANCHOR_LINES = 120


def git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True,
                          errors="replace").stdout


def _normalized(code: str, lang: str) -> str:
    """Code without comments, docstrings and whitespace differences (what counts as a change)."""
    if lang == "python":
        code = re.sub(r'(?s)("""|\'\'\').*?\1', "", code)
        code = re.sub(r"#[^\n]*", "", code)
    else:
        code = re.sub(r"(?s)/\*.*?\*/", "", code)
        code = re.sub(r"(?m)//[^\n]*$", "", code)
    return re.sub(r"\s+", " ", code).strip()


def functions(path: str, source: bytes) -> dict[str, dict]:
    """`{name: {code, start, end}}` for the functions of one file; name = enclosing classes + own name."""
    parsed = parse_source(path, source)
    if parsed is None or parsed.root_node is None:
        return {}
    lang = parsed.language_id
    fn_types = FUNCTION_NODE_TYPES.get(lang, set()) | {"function_expression", "arrow_function"}
    cls_types = CLASS_NODE_TYPES.get(lang, set())
    out: dict[str, dict] = {}

    def name_of(node) -> str | None:
        name = node.child_by_field_name("name")
        if name is not None:
            return source[name.start_byte:name.end_byte].decode("utf8", "replace")
        parent = node.parent
        if parent is not None and parent.type in ("variable_declarator", "assignment_expression", "pair"):
            target = parent.child_by_field_name("name") or parent.child_by_field_name("left") or \
                parent.child_by_field_name("key")
            if target is not None:
                return source[target.start_byte:target.end_byte].decode("utf8", "replace").split(".")[-1]
        return None

    def walk(node, scope: tuple[str, ...]) -> None:
        for child in node.named_children:
            if child.type in cls_types:
                n = child.child_by_field_name("name")
                label = source[n.start_byte:n.end_byte].decode("utf8", "replace") if n is not None else "?"
                walk(child, scope + (label,))
            elif child.type in fn_types:
                name = name_of(child)
                if name:
                    key = ".".join(scope + (name,))
                    if key not in out:
                        out[key] = {"code": source[child.start_byte:child.end_byte].decode("utf8", "replace"),
                                    "start": child.start_point[0] + 1, "end": child.end_point[0] + 1}
                walk(child, scope + ((name,) if name else ()))
            else:
                walk(child, scope)
    walk(parsed.root_node, ())
    return out


def _lang(path: str) -> str:
    return "python" if path.endswith(".py") else "js"


def changed_functions(repo: str, commit: str, files: list[str]) -> dict[tuple[str, str], dict]:
    """`{(file, name): {old, new, start, end}}` for functions present before and after whose code changed."""
    out = {}
    for f in files:
        try:
            old = git(repo, "show", f"{commit}^:{f}").encode()
            new = git(repo, "show", f"{commit}:{f}").encode()
        except subprocess.CalledProcessError:
            continue  # added or deleted file
        before, after = functions(f, old), functions(f, new)
        for name in before.keys() & after.keys():
            a, b = before[name], after[name]
            if _normalized(a["code"], _lang(f)) != _normalized(b["code"], _lang(f)):
                out[(f, name)] = {"old": a["code"], "new": b["code"], "start": a["start"], "end": a["end"]}
    return out


def pass1(repo: str, scan: int, forced: bool = False) -> list[dict]:
    log = git(repo, "log", "--no-merges", f"-n{scan}", "--format=%H%x00%s", "--numstat")
    candidates, current = [], None
    for line in log.splitlines() + [""]:
        if "\x00" in line:
            if current:
                candidates.append(current)
            sha, subject = line.split("\x00", 1)
            current = {"commit": sha, "subject": subject, "files": [], "lines": 0}
        elif line.strip() and current is not None:
            parts = line.split("\t")
            if len(parts) == 3:
                added, removed, path = parts
                if path.endswith(SOURCE_EXT) and not TEST_PARTS.search(path) and not path.endswith(".d.ts"):
                    current["files"].append(path)
                    current["lines"] += int(added) if added.isdigit() else 0
                    current["lines"] += int(removed) if removed.isdigit() else 0
    out = []
    for c in candidates:
        if not 1 <= len(c["files"]) <= MAX_FILES or c["lines"] > MAX_LINES:
            continue
        changed = changed_functions(repo, c["commit"], c["files"])
        if len(changed) < 2:
            continue
        leaves = {k: k[1].rsplit(".", 1)[-1] for k in changed}
        pairs = []
        for anchor, leaf in leaves.items():
            if anchor[1].rsplit(".", 1)[-1].startswith("__") or len(changed[anchor]["old"].splitlines()) > MAX_ANCHOR_LINES:
                continue
            callers = [k for k in changed if k != anchor and re.search(rf"\b{re.escape(leaf)}\s*\(", changed[k]["old"])]
            if forced:
                from harness.experiments.change_history.select import forced_reasons
                if not forced_reasons({"anchor_old": changed[anchor]["old"], "anchor_new": changed[anchor]["new"]}):
                    continue  # nothing in the anchor's change forces its callers to change
            if callers:
                pairs.append((anchor, callers))
        if pairs:
            out.append({**c, "changed": changed, "pairs": pairs})
    return out


def _index_at(repo: str, commit: str, root: str):
    from prism.cli import build_pipeline
    work = tempfile.mkdtemp(prefix="hist_", dir=root)
    subprocess.run(["git", "-C", repo, "worktree", "add", "-q", "--detach", work, commit], check=True,
                   capture_output=True)
    builder = build_pipeline(work, use_cache=False)[0]
    return work, builder


def _qname_for(builder, work: str, file: str, start: int) -> str | None:
    path = os.path.join(work, file)
    best = None
    for q in builder._def_nodes:
        sym = builder.symbol_table.get(q)
        if sym is not None and sym.file == path and sym.kind in ("function", "method") and sym.line_range[0] == start:
            best = q
    return best


def tier(anchor_file: str, changed_callers: list[dict]) -> str:
    other_files = {c["file"] for c in changed_callers if c["file"] != anchor_file}
    if len(changed_callers) >= 3 or len(other_files) >= 2:
        return "hard"
    if other_files:
        return "medium"
    return "easy"


def pass2(name: str, repo: str, cand: dict, scratch: str, budget: int) -> list[dict]:
    from prism.impact.context import ChangeContextBuilder, render
    commit = cand["commit"]
    parent = git(repo, "rev-parse", f"{commit}^").strip()
    work, builder = _index_at(repo, parent, scratch)
    tasks = []
    try:
        cb = ChangeContextBuilder(builder, work)
        changed_q = {}
        for (f, fname), info in cand["changed"].items():
            q = _qname_for(builder, work, f, info["start"])
            if q:
                changed_q[q] = (f, fname, info)
        for (af, aname), _callers in cand["pairs"]:
            anchor = next((q for q, v in changed_q.items() if v[0] == af and v[1] == aname), None)
            if anchor is None:
                continue
            graph_callers = {u for u, _v, d in builder.graph.in_edges(anchor, data=True)
                             if d.get("relation", "CALLS") in ("CALLS", "INSTANTIATES") and not u.startswith("<")}
            prod = {q for q in graph_callers if (s := builder.symbol_table.get(q)) and not builder._in_test_file(s.file)}
            changed_callers = sorted(prod & changed_q.keys())
            if not changed_callers:
                continue
            info = changed_q[anchor][2]
            ctx = cb.build(anchor, budget_tokens=budget, include_tests=False)
            file_text = (Path(work) / af).read_text(errors="replace")
            tasks.append({
                "repo": name, "commit": commit, "parent": parent, "subject": cand["subject"],
                "anchor": anchor, "anchor_file": af, "anchor_lines": [info["start"], info["end"]],
                "anchor_old": info["old"], "anchor_new": info["new"],
                "anchor_file_text": file_text,
                "changed_callers": [{"qname": q, "file": changed_q[q][0], "name": changed_q[q][1],
                                     "old": changed_q[q][2]["old"], "new": changed_q[q][2]["new"]}
                                    for q in changed_callers],
                "unchanged_callers": sorted(prod - set(changed_callers)),
                "prism_context": render(ctx), "prism_tokens": ctx.token_count,
            })
            tasks[-1]["tier"] = tier(af, tasks[-1]["changed_callers"])
    finally:
        subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", work], capture_output=True)
    return tasks


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("out")
    p.add_argument("repos", nargs="+")
    p.add_argument("--scan", type=int, default=1500, help="newest non-merge commits scanned per repository")
    p.add_argument("--budget", type=int, default=6000, help="token budget of the PRISM change context")
    p.add_argument("--max-candidates", type=int, default=40, help="pass-2 candidates per repository")
    p.add_argument("--forced", action="store_true", help="pass 1 keeps only anchors whose change forces callers")
    args = p.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    scratch = tempfile.mkdtemp(prefix="mine_", dir=out)
    # resumable: the tasks found so far and the commits already indexed are kept across restarts
    tasks = json.loads((out / "candidates.json").read_text()) if (out / "candidates.json").exists() else []
    done_path = out / "processed.json"
    done = set(json.loads(done_path.read_text())) if done_path.exists() else set()
    for spec in args.repos:
        name, _, repo = spec.partition("=")
        cands = pass1(repo, args.scan, args.forced)
        print(f"{name}: {len(cands)} pass-1 candidates ({len(done)} already processed)", flush=True)
        for cand in cands[: args.max_candidates]:
            if cand["commit"] in done:
                continue
            try:
                found = pass2(name, repo, cand, scratch, args.budget)
            except Exception as exc:  # one bad revision must not stop the mining
                print(f"  {cand['commit'][:10]}: skipped ({type(exc).__name__}: {exc})", flush=True)
                done.add(cand["commit"])
                continue
            for t in found:
                print(f"  {t['commit'][:10]} {t['tier']:6} {t['anchor']} -> {len(t['changed_callers'])} changed, "
                      f"{len(t['unchanged_callers'])} unchanged callers", flush=True)
            tasks.extend(found)
            (out / "candidates.json").write_text(json.dumps(tasks, indent=1) + "\n")  # saved as it goes
            done.add(cand["commit"])
            done_path.write_text(json.dumps(sorted(done)) + "\n")
    (out / "candidates.json").write_text(json.dumps(tasks, indent=1) + "\n")
    print(f"{len(tasks)} tasks -> {out / 'candidates.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
