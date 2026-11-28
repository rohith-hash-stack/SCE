"""Layer 1 (pre-flight scope check) for a gated R4 candidate of any family, against the developer's commit.

    python -B scope_family.py FAMILY COMMIT >> family_scope.jsonl

Indexes (static): P = the parent, S = the commit. Per family:
- the anchor: delete -> the removed function; move -> the moved function (old path); feature / N -> the new functions;
  tests -> the target the gate picked; refactor -> the changed functions.
- downstream (delete, move, refactor): callers of the anchor at P by hop (production code); the commit's changed
  functions placed on that map; hop-1 callers the commit left untouched.
- upstream (all but tests): in the commit's new or changed production functions at S, calls whose target is unresolved
  (a bare name defined nowhere, not imported, not a builtin, not a local) and certain calls whose arguments do not bind
  to an internal callee's signature.
Classification: upstream-broken (any unresolved call or certain arity mismatch) > cascading (a changed caller beyond 2
hops) > bounded+clean. "Beyond callers" (changed functions that are neither callers nor the anchor) is recorded, not
classified.
"""
import builtins, json, os, re, shutil, subprocess, sys, tempfile, time
sys.path.insert(0, "/home/user/SCE")
from prism.cli import build_pipeline
from prism.impact.dependencies import DependencyAnalyzer, _call_sites
from prism.impact.breaks import signature, bind, _arguments, _text, _lang, FAILS
from harness.experiments.change_history.mine import functions, _normalized

B = "/tmp/claude-0/-home-user-SCE/7b6039cf-c7d6-5676-a565-1dea0566e0f3/scratchpad"
CL = dict(s.split("=", 1) for s in (open(f"{B}/verify/repos").read() + " " + open(f"{B}/r4mine/nrepos/specs.txt").read()
                                    + " " + open(f"{B}/r4mine/nrepos/specs2.txt").read()).split())
CAND = "/home/user/SCE/harness/experiments/r4/candidates"
GATE = f"{B}/r4gate"
JS_GLOBALS = {"Promise", "Object", "Array", "String", "Number", "Boolean", "Error", "TypeError", "RangeError", "Map",
              "Set", "WeakMap", "WeakSet", "JSON", "Math", "Date", "Symbol", "RegExp", "parseInt", "parseFloat",
              "setTimeout", "clearTimeout", "setInterval", "clearInterval", "fetch", "BigInt", "Reflect", "Proxy",
              "Buffer", "structuredClone", "queueMicrotask", "encodeURIComponent", "decodeURIComponent", "require",
              "console", "process", "URL", "URLSearchParams", "Headers", "Request", "Response", "AbortController",
              "TextEncoder", "TextDecoder", "isNaN", "isFinite", "Intl", "globalThis", "Uint8Array", "ArrayBuffer",
              "describe", "it", "test", "expect", "Function", "eval", "alert", "atob", "btoa", "Blob", "ReadableStream",
              "WritableStream", "TransformStream", "FormData", "Event", "EventTarget", "AggregateError", "SyntaxError",
              "DOMException", "performance", "crypto", "SharedArrayBuffer", "DataView", "Float64Array", "Int32Array"}
PY_BUILTINS = set(dir(builtins))
MAX_HOPS = 8


def git(*a, cwd=None, check=True):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, errors="replace", check=check)


def prod(b, q):
    s = b.symbol_table.get(q)
    return s is not None and not b._in_test_file(s.file)


def locate(b, work, gold):
    f, q = gold.split("::", 1)
    names = [n for n in b.symbol_table.all_qualified_names()
             if (s := b.symbol_table.get(n)) is not None and os.path.relpath(s.file, work) == f]
    parts = q.replace("#", "").split(".")
    for i, cand in enumerate([q, q.replace("#", "")] + [".".join(parts[:k]) for k in range(len(parts) - 1, 0, -1)]):
        for n in names:
            if n == cand or n.endswith("." + cand):
                return n
    return None


def changed_functions(work, parent, commit, files):
    out = set()
    for f in files:
        o = git("show", f"{parent}:{f}", cwd=work, check=False)
        n = git("show", f"{commit}:{f}", cwd=work, check=False)
        fo = functions(f, o.stdout.encode()) if o.returncode == 0 else {}
        fn = functions(f, n.stdout.encode()) if n.returncode == 0 else {}
        lang = "python" if f.endswith(".py") else "js"
        for q in set(fo) | set(fn):
            if _normalized(fo.get(q, {}).get("code", ""), lang) != _normalized(fn.get(q, {}).get("code", ""), lang):
                out.add(f"{f}::{q}")
    return out


def bfs(deps, anchor):
    hops, seen, frontier = {}, {anchor}, [anchor]
    for h in range(1, MAX_HOPS + 1):
        nxt = []
        for q in frontier:
            for c, _ in deps._callers(q):
                if c not in seen and prod(deps.b, c):
                    seen.add(c); nxt.append(c)
        if not nxt:
            break
        hops[h] = nxt; frontier = nxt
    return hops


def upstream(S, work, quals):
    """Unresolved calls and certain arity mismatches in these functions (graph-based: a bare external target)."""
    unresolved, wrong = [], []
    for q in quals:
        node = S.def_node(q); sym = S.symbol_table.get(q)
        if node is None or sym is None or not prod(S, q):
            continue
        parsed = S.parsed_file(sym.file)
        py = sym.file.endswith((".py", ".pyi"))
        for _a, t, d in S.graph.out_edges(q, data=True):
            if d.get("relation", "CALLS") not in ("CALLS", "INSTANTIATES") or t.startswith("<"):
                continue
            if S.graph.nodes[t].get("external"):
                if "." not in t and t not in (PY_BUILTINS if py else JS_GLOBALS) and t not in S.symbol_table:
                    unresolved.append({"in": q, "name": t})
                continue
            if d.get("kind") == "TENTATIVE_CALL" or t.rsplit(".", 1)[-1] in PY_BUILTINS | JS_GLOBALS:
                continue
            tn, ts = S.def_node(t), S.symbol_table.get(t)
            if tn is None or ts is None:
                continue
            sig = signature(_text(tn, S.parsed_file(ts.file).source), _lang(ts.language_id))
            if sig is None:
                continue
            for call in _call_sites(S, node, parsed, t):
                pos, kw, star, dstar = _arguments(call, parsed.source)
                probs = [p for sev, p in bind(sig, sig, pos, kw, star, dstar, _lang(ts.language_id)) if sev == FAILS]
                if probs:
                    wrong.append({"in": q, "callee": t, "problem": probs[0][:90]})
    return unresolved, wrong


def main(fam, commit):
    cands = {c["commit"]: c for c in json.load(open(f"{CAND}/candidates_{'refactor_sig' if fam == 'refactor' else fam}.json"))}
    c = cands[commit]
    gate = next((json.loads(l) for l in open(f"{GATE}/{fam}/gate_{fam}.jsonl") if json.loads(l)["commit"] == commit), {})
    clone = CL[c["repo"]]
    scratch = tempfile.mkdtemp(prefix="sfam_", dir=f"{B}/scope"); work = os.path.join(scratch, "w")
    git("-C", clone, "worktree", "add", "-q", "--detach", work, c["parent"])
    rec = {"family": fam, "repo": c["repo"], "commit": commit}
    t0 = time.time()
    try:
        files = [f for f in git("diff", "--name-only", "--no-renames", c["parent"], commit, cwd=work).stdout.split() if f]
        src = [f for f in files if f.endswith((".py", ".js", ".ts", ".mjs", ".cjs", ".tsx", ".mts"))]
        changed = changed_functions(work, c["parent"], commit, src)
        P, _ = build_pipeline(work)
        dP = DependencyAnalyzer(P, repo_root=work)
        anchor = None
        if fam == "delete":
            anchor = c["anchor"]
        elif fam == "move":
            anchor = c["from"]
        elif fam == "tests":
            anchor = gate.get("target")
        rec["anchor"] = anchor
        hops, hop_of = {}, {}
        if anchor and fam in ("delete", "move"):
            aq = locate(P, work, anchor)
            rec["anchor_graph"] = aq
            if aq:
                hops = bfs(dP, aq)
                hop_of = {q: h for h, qs in hops.items() for q in qs}
        rec["graph_hops"] = {h: len(v) for h, v in hops.items()}
        placed, beyond = [], []
        changed_graph = set()
        for g in sorted(changed):
            gq = locate(P, work, g)
            if gq and gq in hop_of:
                placed.append(hop_of[gq]); changed_graph.add(gq)
            elif not (anchor and g == anchor):
                beyond.append(g)
        rec["max_changed_caller_hop"] = max(placed) if placed else 0
        rec["hop1_callers_left"] = sorted(q for q in hops.get(1, []) if q not in changed_graph)[:10]
        rec["beyond_callers"] = len(beyond)
        # upstream: the commit's state
        git("checkout", "-q", "-f", "--detach", commit, cwd=work)
        S, _ = build_pipeline(work)
        quals = []
        names = set(S.symbol_table.all_qualified_names())
        for g in changed:
            q = locate(S, work, g)
            if q:
                quals.append(q)
        unresolved, wrong = upstream(S, work, sorted(set(quals))) if fam != "tests" else ([], [])
        rec["unresolved"] = unresolved[:10]; rec["wrong_arity"] = wrong[:10]
        if unresolved or wrong:
            rec["layer1"] = "upstream-broken"
        elif rec["max_changed_caller_hop"] > 2:
            rec["layer1"] = "cascading"
        else:
            rec["layer1"] = "bounded+clean"
        if fam == "tests":
            rec["layer1"] = "bounded+clean (n/a: tests only)"
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        rec["seconds"] = round(time.time() - t0)
        git("-C", clone, "worktree", "remove", "--force", work, check=False)
        shutil.rmtree(scratch, ignore_errors=True)
    return rec


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], sys.argv[2])), flush=True)
