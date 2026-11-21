"""Two-direction scope analysis of one R3a task (investigation only; nothing in the repository changes).

    python -B scope.py TASK_ID >> scope.jsonl

Indexes (static, no environment): P = the parent (old anchor), S = the start state (parent + start patch: the new
anchor). Downstream from P: the break check of the new anchor code, and a reverse BFS over caller edges. Upstream from
S: the new anchor's calls, resolved, and bound against each callee's signature. Ground truth: the developer's gold
functions placed relative to the anchor.
"""
import json, os, re, shutil, subprocess, sys, tempfile, time
sys.path.insert(0, "/home/user/SCE")
os.environ.setdefault("PRISM_DISABLE_CACHE", "1")
from prism.cli import build_pipeline
from prism.impact.dependencies import DependencyAnalyzer, _call_sites, CERTAIN
from prism.impact.breaks import BreakChecker, signature, bind, _arguments, _text, _lang, FAILS, MEANING, CHECK
from harness.experiments.change_history.mine import functions

B = "/tmp/claude-0/-home-user-SCE/7b6039cf-c7d6-5676-a565-1dea0566e0f3/scratchpad"
clones = dict(s.split("=", 1) for s in open(f"{B}/pilotgate.repos").read().split())
clones.update(dict(s.split("=", 1) for s in open(f"{B}/verify/repos").read().split()))
MAX_HOPS = 8
CALL = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(")
BUILTIN = set(dir(__builtins__)) | {"super", "print", "len", "isinstance", "require", "console", "Promise", "Object",
            "Array", "String", "Number", "Boolean", "Error", "TypeError", "Map", "Set", "JSON", "Math", "Date",
            "Symbol", "RegExp", "parseInt", "parseFloat", "setTimeout", "clearTimeout", "fetch", "BigInt", "Reflect",
            "Proxy", "WeakMap", "WeakSet", "Buffer", "structuredClone", "queueMicrotask", "encodeURIComponent",
            "decodeURIComponent", "if", "for", "while", "switch", "return", "catch", "function", "typeof", "await",
            "new", "elif", "and", "or", "not", "in", "assert", "lambda", "with", "yield", "del", "except"}


def git(*a, cwd=None, check=True):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, errors="replace", check=check)


def rel(b, work, q):
    s = b.symbol_table.get(q)
    return os.path.relpath(s.file, work) if s is not None else None


def prod(b, q):
    s = b.symbol_table.get(q)
    return s is not None and not b._in_test_file(s.file)


def locate(b, work, gold):
    """Graph qname for a gold `file::Qual.name`: exact, then without `#` (JS private), then the enclosing function
    (nested functions are part of their parent's symbol)."""
    f, q = gold.split("::", 1)
    names = [n for n in b.symbol_table.all_qualified_names()
             if (s := b.symbol_table.get(n)) is not None and os.path.relpath(s.file, work) == f]
    tries = [q, q.replace("#", "")]
    parts = q.replace("#", "").split(".")
    tries += [".".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)]
    for i, cand in enumerate(tries):
        for name in names:
            if name == cand or name.endswith("." + cand):
                return name if i < 2 else name + " (enclosing)"
    return None


def code_of(path_text, qual):
    fns = functions("x.py" if qual[0] == "py" else "x.ts", path_text)
    return fns


def anchor_code(work, t, anchor_q):
    """The anchor's code in the working tree (file-local qualified name = the graph name minus its module prefix)."""
    p = os.path.join(work, t["anchor_file"])
    fns = functions(t["anchor_file"], open(p, "rb").read())
    for k, v in fns.items():
        if anchor_q == k or anchor_q.endswith("." + k):
            best = v
            if anchor_q.endswith("." + k) and anchor_q.split(".")[-1] == k.split(".")[-1]:
                return v["code"]
    return None


def bfs(deps, anchor):
    hops, seen, frontier = {}, {anchor}, [anchor]
    for h in range(1, MAX_HOPS + 1):
        nxt = []
        for q in frontier:
            for c, _cert in deps._callers(q):
                if c not in seen and prod(deps.b, c):
                    seen.add(c)
                    nxt.append(c)
        if not nxt:
            break
        hops[h] = nxt
        frontier = nxt
    return hops


def main(tid):
    t = json.load(open(f"/home/user/SCE/reports/r3/r3a/tasks/{tid}.json"))
    clone = clones[t["repo"]]
    scratch = tempfile.mkdtemp(prefix="scope_", dir=f"{B}/scope")
    work = os.path.join(scratch, "w")
    git("-C", clone, "worktree", "add", "-q", "--detach", work, t["parent"])
    rec = {"task": tid, "repo": t["repo"], "anchor": t["anchor"], "stratum": t["stratum"],
           "changed_callers": t.get("changed_callers")}
    try:
        anchor = t["anchor"]
        # the new anchor code (start patch applied, read, then undone so P indexes the parent)
        git("apply", "--whitespace=nowarn", "-", cwd=work, check=True) if False else None
        subprocess.run(["git", "apply", "--whitespace=nowarn", "-"], cwd=work, input=t["start_patch"], text=True,
                       check=True, capture_output=True)
        new_code = anchor_code(work, t, anchor)
        git("checkout", "--", ".", cwd=work)
        t0 = time.time(); P, _ = build_pipeline(work); rec["t_index_parent"] = round(time.time() - t0, 2)
        if anchor not in P.symbol_table:
            rec["error"] = "anchor not in the parent index"; return rec
        dP = DependencyAnalyzer(P, repo_root=work)
        old_code = _text(P.def_node(anchor), P.parsed_file(P.symbol_table.get(anchor).file).source)
        # ---------------- downstream
        t0 = time.time()
        breaks = BreakChecker(dP).check(anchor, new_code) if new_code else []
        rec["t_breakcheck"] = round(time.time() - t0, 3)
        t0 = time.time(); hops = bfs(dP, anchor); rec["t_bfs"] = round(time.time() - t0, 3)
        hop_of = {q: h for h, qs in hops.items() for q in qs}
        rec["graph_hops"] = {h: len(qs) for h, qs in hops.items()}
        rec["graph_files_per_hop"] = {h: len({rel(P, work, q) for q in qs}) for h, qs in hops.items()}
        rec["direct_callers_prod"] = len(hops.get(1, []))
        rec["breaks"] = [{"caller": b.caller, "file": b.file, "severity": b.severity, "problem": b.problem[:120],
                          "certainty": b.certainty} for b in breaks]
        rec["breaking_callers"] = sorted({b.caller for b in breaks if b.severity in (FAILS, MEANING)})
        rec["check_callers"] = sorted({b.caller for b in breaks if b.severity == CHECK})
        # ---------------- upstream (old anchor's callees from P)
        old_callees = {v for _a, v, d in P.graph.out_edges(anchor, data=True)
                       if d.get("relation", "CALLS") in ("CALLS", "INSTANTIATES") and not v.startswith("<")}
        # S: the start state
        subprocess.run(["git", "apply", "--whitespace=nowarn", "-"], cwd=work, input=t["start_patch"], text=True,
                       check=True, capture_output=True)
        t0 = time.time(); S, _ = build_pipeline(work); rec["t_index_start"] = round(time.time() - t0, 2)
        dS = DependencyAnalyzer(S, repo_root=work)
        t0 = time.time()
        new_callees = {}
        for _a, v, d in S.graph.out_edges(anchor, data=True):
            if d.get("relation", "CALLS") in ("CALLS", "INSTANTIATES") and not v.startswith("<"):
                new_callees[v] = "possible" if d.get("kind") == "TENTATIVE_CALL" else "certain"
        repo_leaves = {q.rsplit(".", 1)[-1] for q in S.symbol_table.all_qualified_names()}
        anchor_file_text = open(os.path.join(work, t["anchor_file"]), encoding="utf8", errors="replace").read()
        called = {m.group(1) for m in CALL.finditer(re.sub(r"(?s)(\"\"\".*?\"\"\"|'''.*?'''|//[^\n]*|#[^\n]*)", "", new_code or ""))}
        resolved_leaves = {q.rsplit(".", 1)[-1] for q in new_callees}
        anchor_leaf = anchor.rsplit(".", 1)[-1]
        undefined = sorted(n for n in called - resolved_leaves - BUILTIN - {anchor_leaf}
                           if n not in repo_leaves and not re.search(rf"\bimport\b[^\n]*\b{re.escape(n)}\b|\b{re.escape(n)}\s*=|def {re.escape(n)}\b|function {re.escape(n)}\b|\bconst {re.escape(n)}\b", anchor_file_text))
        # arity: each resolved certain callee's call sites in the new anchor, bound against the callee's signature
        wrong = []
        node, parsed = S.def_node(anchor), S.parsed_file(S.symbol_table.get(anchor).file)
        for v, cert in new_callees.items():
            vn = S.def_node(v); vs = S.symbol_table.get(v)
            if vn is None or vs is None:
                continue
            lang = _lang(vs.language_id)
            sig = signature(_text(vn, S.parsed_file(vs.file).source), lang)
            if sig is None:
                continue
            for call in _call_sites(S, node, parsed, v):
                pos, kw, star, dstar = _arguments(call, parsed.source)
                if sig.is_method and pos and _text(call, parsed.source).split("(")[0].count(".") == 0:
                    pass
                probs = [p for sev, p in bind(sig, sig, pos, kw, star, dstar, lang) if sev == FAILS]
                if probs:
                    wrong.append({"callee": v, "certainty": cert, "call": _text(call, parsed.source)[:80], "problem": probs[0][:100]})
        rec["t_upstream"] = round(time.time() - t0, 3)
        dropped = sorted(old_callees - set(new_callees))
        dead = [q for q in dropped if prod(S, q) and not [c for c, _ in dS._callers(q) if c != anchor]]
        rec["upstream"] = {"callees_new": len(new_callees), "callees_possible": sum(1 for c in new_callees.values() if c == "possible"),
                           "undefined_calls": undefined, "wrong_arity": wrong, "dropped_callees": dropped[:20],
                           "dropped_now_uncalled": dead[:20]}
        # ---------------- ground truth: where the developer's changes are
        commit_syms = {}
        gold = []
        for g in t["gold_functions"]:
            f, q = g.split("::", 1)
            gq = locate(P, work, g)
            enclosing = bool(gq and gq.endswith(" (enclosing)"))
            gq = gq.replace(" (enclosing)", "") if gq else gq
            kind = None
            if gq is None:
                kind = "new function (not at the parent)"
            elif gq == anchor:
                kind = "anchor"
            elif gq in hop_of:
                kind = f"caller hop {hop_of[gq]}"
            elif gq in old_callees or gq in new_callees:
                kind = "callee of the anchor"
            elif f == t["anchor_file"]:
                kind = "same file, not caller/callee"
            else:
                kind = "unrelated by the call graph"
            gold.append({"gold": g, "graph": gq, "relation": kind, "via_enclosing": enclosing,
                         "test_file": bool(gq and not prod(P, gq))})
        rec["gold"] = gold
        # did the developer define names the new anchor calls? (anchor incomplete at the start)
        after = set()
        for f in t["gold_files"]:
            r = git("show", f"{t['commit']}:{f}", cwd=work, check=False)
            if r.returncode == 0:
                after |= {k.rsplit(".", 1)[-1] for k in functions(f, r.stdout.encode())}
        rec["undefined_defined_by_commit"] = sorted(set(undefined) & after)
        rec["wrong_arity_callee_changed_by_commit"] = sorted({w["callee"] for w in wrong} &
                                                             {x["graph"] for x in gold if x["graph"]})
        rec["gold_callee_changed"] = [x["gold"] for x in gold if x["relation"] == "callee of the anchor"]
    except Exception as exc:
        import traceback
        rec["error"] = f"{type(exc).__name__}: {exc}"[:400]; rec["tb"] = traceback.format_exc()[-800:]
    finally:
        git("-C", clone, "worktree", "remove", "--force", work, check=False)
        shutil.rmtree(scratch, ignore_errors=True)
    return rec


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1])), flush=True)
