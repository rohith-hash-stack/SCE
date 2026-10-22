"""SUMMARY.md from the per-repo results of `purpose_search.run`.
Usage: python -m harness.experiments.purpose_search.summarize OUT_DIR"""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

METRICS = ("recall@5", "recall@20", "pool_recall", "mrr")
HELD_OUT = {"set 2": ("fastapi_template", "styleguide"), "set 3": ("express", "trpc")}
Q2E = ("fastapi", "django")


def render(results: dict[str, dict]) -> str:
    f = lambda x: "-" if x is None else f"{x:.3f}"
    L = ["# Purpose-enriched search", ""]
    models = sorted({(r["backend"], r["model"]) for r in results.values()})
    L += [f"Summarizer: {', '.join(f'{b}/{m}' for b, m in models)}. BASE is the shipped `NLQIndex`; +RES / +SUM "
          "rebuild its BM25 documents with the extra text appended; FIELD w=... scores BM25(code) + w * BM25(summary) "
          "with the summary as an independent field. Nothing else in the ranking changes. Pool = top-20 hits + "
          "resolved callees of the top-8.", ""]
    arms = list(next(iter(results.values()))["search"]) if results else []
    for label, repos in HELD_OUT.items():
        held = [results[r] for r in repos if r in results]
        if not held:
            continue
        n = sum(len(r["questions"]) for r in held)
        L += [f"## Held-out {label} ({n} questions: {', '.join(r['repo'] for r in held)})", "",
              "| arm | " + " | ".join(METRICS) + " |", "|---|" + "---|" * len(METRICS)]
        for arm in arms:
            rows = [q for r in held for q in r["search"][arm]["per_question"]]
            L.append(f"| {arm} | " + " | ".join(f(st.mean(q[m] for q in rows)) for m in METRICS) + " |")
        for r in held:
            L += ["", f"`{r['repo']}` ({len(r['questions'])} questions)", "",
                  "| arm | " + " | ".join(METRICS) + " |", "|---|" + "---|" * len(METRICS)]
            L += [f"| {arm} | " + " | ".join(f(r["search"][arm][m]) for m in METRICS) + " |" for arm in arms]
        L.append("")
    q2e = [results[r] for r in Q2E if r in results]
    if q2e:
        shown = [a for a in arms if a in ("BASE", "+SUM") or a.startswith("FIELD")]
        L += ["## Q2E unseeded behavioural questions (q09-q14)", "",
              f"| question | subject | subject rank {' / '.join(shown)} | pool recall {' / '.join(shown)} |",
              "|---|---|---|---|"]
        for r in q2e:
            per = {arm: {q["id"]: q for q in r["search"][arm]["per_question"]} for arm in arms}
            for q in r["questions"]:
                ranks = [per[a][q["id"]]["ranks"].get(q["subject"]) for a in shown]
                pools = [per[a][q["id"]]["pool_recall"] for a in shown]
                L.append(f"| {q['id']} | `{q['subject']}` | {' / '.join(str(x) if x else '-' for x in ranks)} | "
                         f"{' / '.join(f'{p:.2f}' for p in pools)} |")
        L += ["", "Full gold ranks per arm are in `<repo>.json` (`search.<arm>.per_question[].ranks`).", ""]
    L += ["## Direct metrics (summaries alone)", "",
          "| repo | functions summarized | identifiability (summary / docstring on its subset / chance) | "
          "query matching hit@1 / hit@5 / hit@20 / mrr |", "|---|---|---|---|"]
    for r in results.values():
        i, qm = r["identifiability"], r["query_matching"]
        ident = (f"{f(i.get('summary_acc'))} (n={i.get('sample')}) / {f(i.get('docstring_acc'))} "
                 f"vs {f(i.get('summary_acc_on_docstring_subset'))} (n={i.get('with_docstring')}) / 0.200") if i else "-"
        L.append(f"| {r['repo']} | {r['summaries']}/{r['cards']} ({r['scope']}) | {ident} | "
                 f"{f(qm.get('hit@1'))} / {f(qm.get('hit@5'))} / {f(qm.get('hit@20'))} / {f(qm.get('mrr'))} |")
    L += ["", "## Cost", "", "| repo | summaries | LLM calls (failed) | prompt / completion tokens | LLM seconds |",
          "|---|---|---|---|---|"]
    for r in results.values():
        u = r["llm_usage"]
        L.append(f"| {r['repo']} | {r['summaries']} | {u['calls']} ({u['failed']}) | {u['prompt_tokens']} / "
                 f"{u['completion_tokens']} | {r['synth_seconds']} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    out = Path((argv or sys.argv[1:])[0])
    results = {p.stem: json.loads(p.read_text()) for p in sorted(out.glob("*.json"))
               if not p.stem.startswith("summary_cache") and p.stem in sum(HELD_OUT.values(), ()) + Q2E}
    (out / "SUMMARY.md").write_text(render(results))
    print(render(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
