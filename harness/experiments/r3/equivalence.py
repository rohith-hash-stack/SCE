"""R3-5 equivalence tests E1-E3 (Revision 1 as revised by Revision 2) on a real repository. No model.

    python -m harness.experiments.r3.equivalence CLONE SCRATCH [OUT.json] [--rev REV]

`--rev` (default HEAD) runs at another revision, e.g. an R3a task's parent.

The **live** side is one WorkingIndex + ToolLayer over a worktree the test edits, as the agent would.
The **cold** side, for every snapshot, is an independent fresh worktree holding that snapshot's bytes, with its own
WorkingIndex + ToolLayer, built from nothing. An answer passes when the live answer, with any R3-5 notice removed,
is byte-identical to the cold one.

Queries, per snapshot, on every index-backed tool:
- change context of the edited function, of a caller in another file, and of a function in an unedited file;
- blast radius of the edited function;
- digests of the three;
- the new-function context for a new function in the edited file;
- one `prism_ask` and one `related_code` query.

- **E1, clean edit:** a comment inside the edited function, plus a new helper function at the end of the file.
- **E2, broken edit, after E1:** an unbalanced line inside the function.
  - (a) answers, notice aside, equal the cold build at E1's bytes;
  - (b) the notice names the file, "edit 1" and E1's hash;
  - (c) the file on disk (what `read_file` returns) holds the broken bytes;
  - (d) an answer about a function in a clean file that references the gated file carries the notice;
  - (e) `related_code` serves the gated file's last parsable entries (no broken line) with the notice.
- **E3, clean, then broken, then clean,** on a fresh start:
  - each answer equals its snapshot's cold build;
  - the middle answers carry the notice for edit 1;
  - the last answers carry no notice and show edit 3.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

TOKENIZER = "/home/user/models/qwen2-tokenizer/tokenizer.json"
NOTICE = re.compile(r"^⚠️ File .*\n", re.M)


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout


class Side:
    """One worktree with its own WorkingIndex and ToolLayer."""

    def __init__(self, clone: str, scratch: str, tag: str, files: dict[str, bytes] | None = None, rev: str = "HEAD"):
        from harness.experiments.r3.tools import ToolLayer
        from harness.experiments.r3.working_index import WorkingIndex
        self.clone = clone
        self.work, self.shadow = os.path.join(scratch, f"{tag}-work"), os.path.join(scratch, f"{tag}-shadow")
        for p in (self.work, self.shadow):
            shutil.rmtree(p, ignore_errors=True)
        _git("-C", clone, "worktree", "prune")
        _git("-C", clone, "worktree", "add", "-q", "--detach", self.work, rev)
        for rel, data in (files or {}).items():
            with open(os.path.join(self.work, rel), "wb") as fh:
                fh.write(data)
        self.wi = WorkingIndex(self.work, self.shadow)
        self.layer = ToolLayer(self.wi, TOKENIZER)

    def write(self, rel: str, data: bytes) -> None:
        with open(os.path.join(self.work, rel), "wb") as fh:
            fh.write(data)

    def close(self) -> None:
        self.wi.close()
        subprocess.run(["git", "-C", self.clone, "worktree", "remove", "--force", self.work], capture_output=True)
        _git("-C", self.clone, "worktree", "prune")


def pick(side: Side) -> dict:
    """The edited file and function, a caller in another file, a function in an unedited file, and a clean-file
    callee that the edited file calls (for E2(d))."""
    b, root = side.wi.builder, side.wi.shadow
    rel = lambda q: os.path.relpath(b.symbol_table.get(q).file, root)
    fns = [q for q in sorted(b._def_nodes) if (s := b.symbol_table.get(q)) is not None
           and s.kind in ("function", "method") and not b._in_test_file(s.file) and s.line_range[1] - s.line_range[0] >= 3]
    callers = {q: sorted(u for u, _v, d in b.graph.in_edges(q, data=True) if d.get("relation", "CALLS") == "CALLS"
                         and u in b.symbol_table and not b._in_test_file(b.symbol_table.get(u).file))
               for q in fns}
    for target in sorted(fns, key=lambda q: (-len([c for c in callers[q] if rel(c) != rel(q)]), q)):
        other_file_callers = [c for c in callers[target] if rel(c) != rel(target)]
        if not other_file_callers:
            continue
        f = rel(target)
        callees = sorted(v for _u, v, d in b.graph.out_edges([q for q in fns if rel(q) == f], data=True)
                         if d.get("relation", "CALLS") == "CALLS" and v in callers and rel(v) != f)
        if not callees:
            continue
        unedited = next(q for q in fns if rel(q) not in (f, rel(other_file_callers[0])))
        return {"file": f, "target": target, "caller": other_file_callers[0], "unedited": unedited,
                "clean_callee": callees[0], "lang": "python" if f.endswith(".py") else "ts"}
    raise SystemExit("no suitable edited function (one with a caller in another file and a callee in another file)")


def answers(layer, p: dict) -> dict[str, str]:
    leaf = p["target"].rsplit(".", 1)[-1]
    qs = {
        "change_context:target": ("prism_change_context", {"symbol": p["target"]}),
        "change_context:caller": ("prism_change_context", {"symbol": p["caller"]}),
        "change_context:unedited": ("prism_change_context", {"symbol": p["unedited"]}),
        "change_context:clean_callee": ("prism_change_context", {"symbol": p["clean_callee"]}),
        "blast_radius:target": ("prism_blast_radius", {"symbol": p["target"]}),
        "digest": ("prism_digest", {"symbols": [p["target"], p["caller"], p["unedited"]]}),
        "new_function_context": ("prism_new_function_context", {"file": p["file"], "name": "r3_probe_new",
                                                                "signature": "r3_probe_new(value)",
                                                                "purpose": f"wrap {leaf}"}),
        "ask": ("prism_ask", {"question": f"where is {leaf} used"}),
        "related_code": ("related_code", {"query": leaf}),
    }
    return {k: layer.call(t, **a) for k, (t, a) in qs.items()}


def edits(src: bytes, p: dict, side: Side) -> tuple[bytes, bytes]:
    """(clean, broken) versions of the edited file, both made from `src`."""
    sym = side.wi.builder.symbol_table.get(p["target"])
    lines = src.decode("utf8").splitlines(keepends=True)
    lo = sym.line_range[0]                   # insert after the definition's first line
    body = lines[lo] if lo < len(lines) else lines[-1]
    ind = body[: len(body) - len(body.lstrip())] or "    "
    if p["lang"] == "python":
        comment, helper, bad = f"{ind}# r3 equivalence probe\n", "\n\ndef _r3_probe_helper():\n    return None\n", f"{ind}def (((\n"
    else:
        comment, helper, bad = f"{ind}// r3 equivalence probe\n", "\nexport function r3ProbeHelper() { return null }\n", f"{ind}function (((\n"
    clean = "".join(lines[:lo] + [comment] + lines[lo:]) + helper
    clines = clean.splitlines(keepends=True)
    broken = "".join(clines[:lo + 1] + [bad] + clines[lo + 1:])
    return clean.encode(), broken.encode()


def compare(live: dict, cold: dict) -> dict:
    out = {}
    for k in cold:
        stripped = NOTICE.sub("", live[k], count=0).lstrip("\n") if live[k].startswith("⚠️") else live[k]
        out[k] = stripped == cold[k]
    return out


def cold_answers(clone, scratch, p, files: dict[str, bytes], tag: str, rev: str = "HEAD") -> dict:
    side = Side(clone, scratch, tag, files, rev)
    try:
        return answers(side.layer, p)
    finally:
        side.close()


def run(clone: str, scratch: str, rev: str = "HEAD") -> dict:
    res: dict = {"repo": os.path.basename(clone), "head": _git("-C", clone, "rev-parse", rev).strip()}
    live = Side(clone, scratch, "live", rev=rev)
    try:
        p = pick(live)
        res["picked"] = p
        src = open(os.path.join(live.work, p["file"]), "rb").read()
        clean, broken = edits(src, p, live)
        sha12 = lambda d: hashlib.sha256(d).hexdigest()[:12]
        # E1
        live.write(p["file"], clean)
        a1 = answers(live.layer, p)
        c1 = cold_answers(clone, scratch, p, {p["file"]: clean}, "cold-e1", rev)
        e1 = compare(a1, c1)
        res["E1"] = {"pass": all(e1.values()) and not any(v.startswith("⚠️") for v in a1.values()), "answers": e1}
        # E2
        live.write(p["file"], broken)
        a2 = answers(live.layer, p)
        e2a = compare(a2, c1)                     # last parsable version = E1's bytes
        notice = f"⚠️ File {p['file']} was edited and does not currently parse. Content shown is from the last parsable version (edit 1, commit {sha12(clean)})."
        touched = [k for k, v in a2.items() if v.startswith("⚠️")]
        e2b = bool(touched) and all(notice in a2[k] for k in touched)
        e2c = open(os.path.join(live.work, p["file"]), "rb").read() == broken
        e2d = a2["change_context:clean_callee"].startswith("⚠️") and p["file"] in a2["change_context:clean_callee"].split("\n", 1)[0]
        bad_line = broken.decode().splitlines()[live.wi.builder.symbol_table.get(p["target"]).line_range[0] + 1].strip()
        e2e = a2["related_code"].startswith("⚠️") and bad_line not in a2["related_code"]
        res["E2"] = {"pass": all(e2a.values()) and e2b and e2c and e2d and e2e,
                     "a_answers_equal_last_parsable": e2a, "b_notice": e2b, "answers_with_notice": touched,
                     "c_read_file_broken": e2c, "d_clean_file_cross_ref_flagged": e2d, "e_related_code_last_good": e2e,
                     "gated": live.wi.gated_files()}
    finally:
        live.close()
    # E3, a fresh start: clean, broken, clean
    live = Side(clone, scratch, "live3", rev=rev)
    try:
        third = clean.replace(b"r3 equivalence probe", b"r3 equivalence probe, edit three")
        steps, cold = [("clean", clean), ("broken", broken), ("clean again", third)], {}
        out = []
        for i, (label, data) in enumerate(steps, 1):
            live.write(p["file"], data)
            a = answers(live.layer, p)
            shown = clean if label == "broken" else data
            key = sha12(shown)
            if key not in cold:
                cold[key] = cold_answers(clone, scratch, p, {p["file"]: shown}, f"cold-e3-{i}", rev)
            eq = compare(a, cold[key])
            has_notice = any(v.startswith("⚠️") for v in a.values())
            ok = all(eq.values()) and (has_notice == (label == "broken"))
            if label == "broken":
                ok = ok and all(f"(edit 1, commit {sha12(clean)})" in v for v in a.values() if v.startswith("⚠️"))
            if i == 3:
                ok = ok and live.wi.files[p["file"]].shown_edit == 3
            out.append({"step": i, "edit": label, "pass": ok, "notice": has_notice,
                        "answers_equal": all(eq.values()), "failed": [k for k, v in eq.items() if not v]})
        res["E3"] = {"pass": all(s["pass"] for s in out), "steps": out}
    finally:
        live.close()
    res["pass"] = res["E1"]["pass"] and res["E2"]["pass"] and res["E3"]["pass"]
    return res


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    rev = "HEAD"
    if "--rev" in args:
        i = args.index("--rev"); rev = args[i + 1]; del args[i:i + 2]
    res = run(os.path.abspath(args[0]), os.path.abspath(args[1]), rev)
    text = json.dumps(res, indent=1, default=str)
    if len(args) > 2:
        with open(args[2], "w") as fh:
            fh.write(text + "\n")
    print(text)
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
