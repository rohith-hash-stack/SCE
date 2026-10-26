"""Summary grounding on set 6: summaries written from the function's own source (README.md).

`generate` writes the SRC and SRCC summaries of every function in each set-6 repository, resumably
(`<out>/<repo>/{src,srcc}.json`, checkpointed every CHECKPOINT functions). `score` ranks the frozen set-6
questions with BASE, V2 (the tracked locked index), SRC, SRCC and SRCC_PLUS and writes
`<out>/{results.json,REPORT.md,accuracy_sample.json}`.

Usage:
  python -m harness.experiments.summary_grounding.run generate APPS OUT [--base-urls URLS] [--threads N] [--model M]
                                                            [--arms src,srcc | long]
  python -m harness.experiments.summary_grounding.run score APPS OUT
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import statistics as st
import sys
import time
from pathlib import Path

from prism.summaries.cards import build_card, resources
from prism.summaries.index import MIN_COVERAGE, SUMMARY_WEIGHT, function_symbols, read_index, search_text
from prism.summaries.lint import lint
from prism.summaries.lint import problems
from prism.summaries.pipeline import BATCH, BODY_CAP, LOCKED_MODEL, REPAIR_ATTEMPTS, REPAIR_PROMPT, SUMMARY_PROMPT, Ollama, _clean, repair

ROOT = Path(__file__).resolve().parents[3]
V2_INDEXES = ROOT / "reports" / "summary_candidate_v5" / "set6"
ARMS = ("BASE", "V2", "SRC", "SRCC", "SRCC_PLUS")
GENERATED = ("src", "srcc")
#: Length control (README "Follow-up"): V2's prompts with only the sentence length changed.
_V2_LENGTH, _LONG_LENGTH = "8 to 14 words", "12 to 15 words"
LONG_PROMPT = SUMMARY_PROMPT.replace(_V2_LENGTH, _LONG_LENGTH)
LONG_REPAIR_PROMPT = REPAIR_PROMPT.replace(_V2_LENGTH, _LONG_LENGTH)
assert LONG_PROMPT != SUMMARY_PROMPT and LONG_REPAIR_PROMPT != REPAIR_PROMPT, "v2 prompts no longer state the length"
FACT_TYPES = ("condition", "exception", "state_change", "return", "external_call", "computation")
CHECKPOINT = 50
ALPHA, MAX_RECALL5_DROP = 0.05, 0.02

_RULES = (
    "Rules for the sentence:\n"
    "1. No code identifiers: no function, method, class, variable, module or file names, and no backticks, dots, "
    "underscores, camelCase or parentheses.\n"
    "2. Do not list the functions it calls; say in ordinary words what they achieve together.\n"
    "3. Do not simply restate the function's name; describe its behaviour.\n"
    "4. No internal analysis labels or filler, such as 'pure compute', 'no effect', 'leaf utility' or 'no return'.\n"
    "5. Only describe behaviour, protocols and data present in the profile and source; do not assume business domains.\n")

SRC_PROMPT = (
    "You are an expert software architect describing a function for a code search index.\n\n"
    "Profile:\n{card}\n\nSource (lines {start}-{end}):\n{body}\n\n"
    "Write one plain-English sentence of 8 to 14 words saying what this function itself does and how, in the words a "
    "developer would use when asking about this behaviour. Base it on the source: what it checks, computes, changes, "
    "sends, raises or returns.\n" + _RULES +
    "Respond with a JSON object {{\"summary\": \"<the sentence>\"}}.")

SRCC_PROMPT = (
    "You are an expert software architect describing a function for a code search index.\n\n"
    "Profile:\n{card}\nResources touched: {resources}\n\nSource (lines {start}-{end}):\n{body}\n\n"
    "Step 1. List the facts about THIS function's own code. Each fact has a type (one of condition, exception, "
    "state_change, return, external_call, computation), a short text, and the first and last line numbers of the "
    "source lines above that show it. Only facts visible in those lines. Do not describe what the functions it calls do "
    "inside themselves, and do not state what its callers do.\n"
    "Step 2. From those facts only, write one plain-English sentence of 8 to 14 words saying what the function does "
    "and how, in the words a developer would use when asking about this behaviour.\n" + _RULES +
    "Respond with a JSON object {{\"facts\": [{{\"type\": \"...\", \"text\": \"...\", \"lines\": [first, last]}}], "
    "\"summary\": \"<the sentence>\"}}.")


def numbered_body(lines: list[str], line_range: tuple[int, int], cap: int = BODY_CAP) -> tuple[str, int, bool]:
    """`(text, last line shown, truncated)`: the function's lines with their file line numbers, at most `cap`."""
    start, end = line_range
    shown_end = min(end, start + cap - 1)
    body = "\n".join(f"{n}: {lines[n - 1]}" for n in range(start, shown_end + 1) if n - 1 < len(lines))
    return body + (f"\n... ({end - shown_end} more lines)" if end > shown_end else ""), shown_end, end > shown_end


def valid_facts(facts, start: int, end: int) -> tuple[list[dict], int]:
    """Facts with a known type, text, and a line span inside [start, end]; plus how many were dropped."""
    kept, dropped = [], 0
    for f in facts if isinstance(facts, list) else []:
        try:
            a, b = (int(x) for x in f["lines"])
            ok = f.get("type") in FACT_TYPES and str(f.get("text") or "").strip() and start <= a <= b <= end
        except (KeyError, TypeError, ValueError):
            ok = False
        if ok:
            kept.append({"type": f["type"], "text": _clean(f["text"]), "lines": [a, b]})
        else:
            dropped += 1
    return kept, dropped


def corpus(apps: Path, repo: str, spec: dict):
    from prism.cli import build_pipeline
    from prism.graph.entry_points import find_entry_points
    from prism.query.nlq import NLQIndex
    root = str(apps / repo / spec["root"])
    if not os.path.isdir(root):
        raise SystemExit(f"{repo}: no checkout at {root}")
    builder = build_pipeline(root, use_cache=False)[0]
    base = NLQIndex(builder, root)
    fns = function_symbols(base.pool)
    if not fns:
        raise SystemExit(f"{repo}: no functions indexed under {root}")
    return root, builder, base, fns, find_entry_points(builder)


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(sorted(data.items())), indent=0) + "\n")
    os.replace(tmp, path)


def long_batch(llm, cards: dict[str, str], batch: list[str]) -> dict[str, str]:
    """V2's `pipeline.summarize_batch` with LONG_PROMPT: one reply for the batch, a card it left out retried alone."""
    got = llm.json(LONG_PROMPT.format(n=len(batch), cards="\n---\n".join(cards[q] for q in batch)),
                   max_out=60 * len(batch) + 200) or {}
    out = {q: _clean(got[q]) for q in batch if isinstance(got, dict) and got.get(q)}
    for q in batch:
        if q not in out:
            one = llm.json(LONG_PROMPT.format(n=1, cards=cards[q]), max_out=200) or {}
            v = one.get(q) or (next(iter(one.values())) if isinstance(one, dict) and len(one) == 1 else None)
            if v:
                out[q] = _clean(v)
    return out


def long_repair(llm, summary: str, card: str) -> str:
    """V2's `pipeline.repair` with LONG_REPAIR_PROMPT."""
    best, best_n = summary, sum(lint(summary, card).values())
    for _ in range(REPAIR_ATTEMPTS):
        if not best_n:
            break
        got = llm.json(LONG_REPAIR_PROMPT.format(problems="; ".join(problems(best, card)), summary=best, card=card),
                       max_out=200) or {}
        cand = _clean(got.get("summary") or "")
        n = sum(lint(cand, card).values()) if cand else best_n
        if cand and n < best_n:
            best, best_n = cand, n
    return best


def generate_long(llm, builder, entries, fns, path: Path, threads: int, log, label: str) -> dict:
    """The LONG arm: V2's card (no body) and batch procedure, longer sentences (README "Follow-up")."""
    done = _load(path)
    cards = {s.qualified_name: c["card"] for s in fns if s.qualified_name not in done
             and (c := build_card(builder, {}, entries, s.qualified_name, axes=False))}
    todo = list(cards)
    log(f"{label}: {len(fns)} functions, {len(done)} done, {len(todo)} to write")
    t0 = time.time()
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    with cf.ThreadPoolExecutor(threads) as ex:
        for i, got in enumerate(ex.map(lambda b: long_batch(llm, cards, b), batches), 1):
            for q, s in got.items():
                flagged = any(lint(s, cards[q]).values())
                done[q] = {"summary": long_repair(llm, s, cards[q]) if flagged else s, "repaired": flagged}
            if i % max(1, CHECKPOINT // BATCH) == 0:
                _save(path, done)
                log(f"{label}: {len(done)}/{len(fns)} ({time.time() - t0:.0f}s)")
    _save(path, done)
    return {"functions": len(fns), "written": len(done), "seconds": round(time.time() - t0, 1)}


def generate_repo(llm, apps: Path, repo: str, spec: dict, out: Path, threads: int, log=print,
                  arms: "tuple[str, ...]" = GENERATED) -> dict:
    root, builder, base, fns, entries = corpus(apps, repo, spec)
    out.mkdir(parents=True, exist_ok=True)
    stats = {}
    for arm in arms:
        path = out / f"{arm}.json"
        if arm == "long":
            stats[arm] = generate_long(llm, builder, entries, fns, path, threads, log, f"{repo}/{arm}")
            continue
        done = _load(path)
        todo = [s for s in fns if s.qualified_name not in done]
        log(f"{repo}/{arm}: {len(fns)} functions, {len(done)} done, {len(todo)} to write")

        def one(sym, arm=arm):
            q = sym.qualified_name
            c = build_card(builder, {}, entries, q, axes=False)
            if c is None:
                return q, None
            body, shown_end, cut = numbered_body(base._source(sym.file), sym.line_range)
            start = sym.line_range[0]
            if arm == "src":
                got = llm.json(SRC_PROMPT.format(card=c["card"], start=start, end=shown_end, body=body), max_out=200) or {}
                entry = {}
            else:
                res = ", ".join(resources(builder, q)) or "none"
                got = llm.json(SRCC_PROMPT.format(card=c["card"], resources=res, start=start, end=shown_end, body=body),
                               max_out=700) or {}
                facts, dropped = valid_facts(got.get("facts"), start, shown_end)
                entry = {"facts": facts, "facts_dropped": dropped}
            summary = _clean(got.get("summary") or "")
            if not summary:
                return q, None
            flagged = any(lint(summary, c["card"]).values())
            if flagged:
                summary = repair(llm, summary, c["card"])
            return q, {**entry, "summary": summary, "repaired": flagged, "truncated": cut}

        t0, n = time.time(), 0
        with cf.ThreadPoolExecutor(threads) as ex:
            for q, e in ex.map(one, todo):
                if e is not None:
                    done[q] = e
                n += 1
                if n % CHECKPOINT == 0:
                    _save(path, done)
                    log(f"{repo}/{arm}: {len(done)}/{len(fns)} ({time.time() - t0:.0f}s)")
        _save(path, done)
        stats[arm] = {"functions": len(fns), "written": len(done), "seconds": round(time.time() - t0, 1)}
    return stats


def frozen_set6() -> dict:
    from harness.experiments.heldout_retrieval.set6 import score as s6
    return s6.frozen_dataset()


def score(apps: Path, out: Path, allow_partial: bool = False) -> dict:
    """`allow_partial`: score even when a generated arm covers < MIN_COVERAGE of a repository's functions
    (a local check of the scoring path before generation; never for the reported result)."""
    from harness.experiments.heldout_retrieval.set5.score import _best, _score, compare
    from prism.query.nlq import NLQIndex
    ds = frozen_set6()
    # Length-control mode (README "Follow-up"): every repository has the LONG arm.
    length = all((out / repo / "long.json").exists() for repo in ds["repos"])
    generated = GENERATED + (("long",) if length else ())
    rows = {a: {} for a in ARMS + (("LONG",) if length else ())}
    coverage, gen, texts_all, words = {}, {}, {}, {}
    for repo, spec in ds["repos"].items():
        root, builder, base, fns, _entries = corpus(apps, repo, spec)
        names = {s.qualified_name for s in fns}
        v2 = read_index(V2_INDEXES / repo / "v2.json")
        arms = {"V2": {q: search_text(e, v2.get("pipeline") or {}) for q, e in v2["functions"].items()}}
        for arm in generated:
            data = _load(out / repo / f"{arm}.json")
            arms[arm.upper()] = {q: e["summary"] for q, e in data.items()}
            gen[f"{repo}/{arm}"] = {
                "written": len(data), "repaired": sum(bool(e.get("repaired")) for e in data.values()),
                "truncated": sum(bool(e.get("truncated")) for e in data.values()),
                "mean_words": round(st.mean(len(e["summary"].split()) for e in data.values()), 1) if data else None,
                **({"facts": sum(len(e.get("facts", [])) for e in data.values()),
                    "facts_dropped": sum(e.get("facts_dropped", 0) for e in data.values())} if arm == "srcc" else {})}
            if arm == "srcc":
                arms["SRCC_PLUS"] = {q: e["summary"] + " " + " ".join(f["text"] for f in e.get("facts", []))
                                     for q, e in data.items()}
        idx = {"BASE": base}
        for arm, texts in arms.items():
            coverage[f"{repo}/{arm}"] = round(len(names & set(texts)) / max(1, len(names)), 3)
            if coverage[f"{repo}/{arm}"] < MIN_COVERAGE and not allow_partial:
                raise SystemExit(f"{repo}/{arm}: coverage {coverage[f'{repo}/{arm}']} < {MIN_COVERAGE}")
            idx[arm] = NLQIndex(builder, root, summaries=texts, summary_weight=SUMMARY_WEIGHT)
        texts_all[repo] = arms
        for arm in ("V2", "SRC", "SRCC") + (("LONG",) if length else ()):
            words.setdefault(arm, []).extend(len(arms[arm][q].split()) for q in names if arms[arm].get(q))
        for qn in (q for q in ds["questions"] if q["repo"] == repo):
            for arm, index in idx.items():
                rows[arm][qn["id"]] = {**_score(index.ranked(qn["question"]), qn["gold"]), "repo": repo}
    contrasts = {}
    pairs = (("SRC", "V2"), ("SRCC", "V2"), ("SRC", "BASE"), ("SRCC", "BASE"), ("SRCC", "SRC"),
             ("SRCC_PLUS", "V2"), ("V2", "BASE"))
    if length:
        pairs = (("SRC", "LONG"), ("LONG", "V2"), ("SRCC", "LONG"), ("LONG", "BASE")) + pairs
    for a, b in pairs:
        c = compare(rows, a, b)
        c["beats"] = c["better"] > c["worse"] and c["sign_p"] < ALPHA and c["recall5_delta"] >= -MAX_RECALL5_DROP - 1e-12
        contrasts[f"{a} vs {b}"] = c
    metrics = {a: {m: round(st.mean(r[m] for r in rows[a].values()), 3) for m in ("recall@5", "recall@20", "mrr")}
               for a in rows}
    by_repo = {a: {repo: round(st.mean(r["recall@5"] for r in rows[a].values() if r["repo"] == repo), 3)
                   for repo in ds["repos"]} for a in rows}
    # Diagnostic: did the gold function's own summary change from V2? Split SRC/SRCC vs V2 by that.
    split = {}
    for arm in ("SRC", "SRCC"):
        groups = {"gold summary changed": [], "gold summary unchanged or missing": []}
        for qn in ds["questions"]:
            t = texts_all[qn["repo"]]
            changed = any(t[arm].get(g) and t[arm].get(g) != t["V2"].get(g) for g in qn["gold"])
            groups["gold summary changed" if changed else "gold summary unchanged or missing"].append(qn["id"])
        split[arm] = {g: {"n": len(ids), "better": sum(_best(rows[arm][i]) < _best(rows["V2"][i]) for i in ids),
                          "worse": sum(_best(rows[arm][i]) > _best(rows["V2"][i]) for i in ids)} for g, ids in groups.items()}
    v2b = contrasts["V2 vs BASE"]
    valid = v2b["better"] == 37 and v2b["worse"] == 20 and metrics["V2"]["recall@5"] == 0.758 and metrics["BASE"]["recall@5"] == 0.7
    mean_words = {a: round(st.mean(v), 1) for a, v in words.items() if v}
    result = {"valid": valid, "metrics": metrics, "recall@5_by_repo": by_repo, "contrasts": contrasts,
              "gold_change_split": split, "coverage": coverage, "generation": gen, "mean_words": mean_words,
              "per_question": rows}
    if length:
        matched = mean_words["LONG"] >= mean_words["SRC"] - 1.0
        result["length_control"] = {
            "length_matched": matched, "src_mean_words": mean_words["SRC"], "long_mean_words": mean_words["LONG"],
            "verdict": ("length not matched (inconclusive)" if not matched else
                        "code adds value beyond length" if contrasts["SRC vs LONG"]["beats"] else
                        "no evidence that code adds value beyond length")}
    out.mkdir(parents=True, exist_ok=True)
    suffix = "_length" if length else ""
    (out / f"results{suffix}.json").write_text(json.dumps(result, indent=1) + "\n")
    if not length:
        _accuracy_sample(out, texts_all, apps, ds)
    (out / f"REPORT{suffix}.md").write_text(render(result) + "\n")
    print(render(result))
    return result


#: The 60 functions of the summary accuracy audit (stratified sample over set 6, chosen from source features only).
ACCURACY_SAMPLE = json.loads((Path(__file__).resolve().parent / "accuracy_sample_functions.json").read_text())


def _accuracy_sample(out: Path, texts_all: dict, apps: Path, ds: dict) -> None:
    rows = []
    for repo, q in ACCURACY_SAMPLE:
        srcc = _load(out / repo / "srcc.json").get(q, {})
        rows.append({"repo": repo, "qname": q, "V2": texts_all[repo]["V2"].get(q), "SRC": texts_all[repo]["SRC"].get(q),
                     "SRCC": texts_all[repo]["SRCC"].get(q), "SRCC_facts": srcc.get("facts")})
    (out / "accuracy_sample.json").write_text(json.dumps(rows, indent=1) + "\n")


def render(r: dict) -> str:
    L = ["# Summary grounding on set 6 (development comparison; README.md)", ""]
    if "length_control" in r:
        lc = r["length_control"]
        L += [f"**Length control (pre-registered, README 'Follow-up'): {lc['verdict'].upper()}** "
              f"(mean words: SRC {lc['src_mean_words']}, LONG {lc['long_mean_words']}; matched: {lc['length_matched']})", ""]
    L += [f"**Validity check (V2 vs BASE reproduces the recorded set-6 result): {'PASS' if r['valid'] else 'FAIL - results not interpretable'}**", "",
          "| arm | recall@5 | recall@20 | MRR | mean words |", "|---|---|---|---|---|"]
    L += [f"| {a} | {m['recall@5']} | {m['recall@20']} | {m['mrr']} | {r.get('mean_words', {}).get(a, '-')} |"
          for a, m in r["metrics"].items()]
    L += ["", "| contrast | better / worse / ties | sign p | recall@5 delta | MRR delta | beats (pre-registered) |",
          "|---|---|---|---|---|---|"]
    L += [f"| {k} | {c['better']} / {c['worse']} / {c['ties']} | {c['sign_p']} | {c['recall5_delta']:+.3f} | "
          f"{c['mrr_delta']:+.3f} | {'yes' if c['beats'] else 'no'} |" for k, c in r["contrasts"].items()]
    repos = list(next(iter(r["recall@5_by_repo"].values())))
    L += ["", "Recall@5 by repository:", "", "| arm | " + " | ".join(repos) + " |", "|---|" + "---|" * len(repos)]
    L += [f"| {a} | " + " | ".join(str(v[x]) for x in repos) + " |" for a, v in r["recall@5_by_repo"].items()]
    L += ["", "Diagnostic - rank vs V2, split by whether the gold function's own summary changed:", "",
          "| arm | group | n | better | worse |", "|---|---|---|---|---|"]
    L += [f"| {a} | {g} | {v['n']} | {v['better']} | {v['worse']} |" for a, s in r["gold_change_split"].items() for g, v in s.items()]
    L += ["", "Generation:", "", "```", json.dumps(r["generation"], indent=1), "```", "",
          f"Coverage (functions with a summary): {json.dumps(r['coverage'])}"]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("apps", type=Path)
    g.add_argument("out", type=Path)
    g.add_argument("--base-urls", default="http://127.0.0.1:11434")
    g.add_argument("--threads", type=int, default=2)
    g.add_argument("--model", default=LOCKED_MODEL)
    g.add_argument("--repos", default="")
    g.add_argument("--arms", default=",".join(GENERATED), help="summary arms to write: src, srcc, long")
    s = sub.add_parser("score")
    s.add_argument("apps", type=Path)
    s.add_argument("out", type=Path)
    s.add_argument("--allow-partial", action="store_true",
                   help="score although a generated arm covers < 95%% of functions (scoring-path check only)")
    args = ap.parse_args(argv)
    if args.cmd == "generate":
        ds = frozen_set6()
        llm = Ollama(args.model, [u for u in args.base_urls.split(",") if u])
        want = [r for r in args.repos.split(",") if r] or list(ds["repos"])
        arms = tuple(a for a in args.arms.split(",") if a)
        if not arms or set(arms) - set(GENERATED + ("long",)):
            raise SystemExit(f"--arms must name src, srcc and/or long, got {args.arms!r}")
        stats = {r: generate_repo(llm, args.apps, r, ds["repos"][r], args.out / r, args.threads, arms=arms) for r in want}
        name = "generation.json" if arms == GENERATED else f"generation_{'_'.join(arms)}.json"
        (args.out / name).write_text(json.dumps({"model": args.model, "arms": arms, "stats": stats, "llm": llm.usage}, indent=1) + "\n")
        print(json.dumps(stats, indent=1))
        return 0
    res = score(args.apps, args.out, allow_partial=args.allow_partial)
    return 0 if res["valid"] else 2


if __name__ == "__main__":
    sys.exit(main())
