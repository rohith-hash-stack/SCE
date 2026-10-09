"""Lexical subject-discovery measurement (M3 floor): the probe measurement,
re-run through `lexical.py`, in the recorded fixture's exact shape so the two
can be compared field by field. The query is the question text itself."""
from __future__ import annotations

import json
from pathlib import Path

from harness.experiments.query_to_evidence.nlq.lexical import VARIANTS, LexicalIndex, tokenize

RECORDED = Path(__file__).resolve().parent / "fixtures" / "lexical_recorded.json"
#: The k values the report measured; expansion was run for q09-q24 only.
TOP_K, EXPANSION_K, EXPANSION_FROM = 10, (10, 20, 50), "q09"
#: Provenance of the recording: the corpus roots the probes indexed (their
#: `SymbolInfo.file` paths, absolute, are part of every recorded document).
#: Reproduction tokenizes paths under these roots, so it does not depend on
#: where the corpora are checked out now.
RECORDED_CORPUS_ROOTS = {"fastapi": "/home/user/SCE/.benchmarks/corpora/fastapi",
                         "django": "/home/user/SCE/.benchmarks/corpora/django"}


def load_recorded() -> dict:
    return json.loads(RECORDED.read_text())


def recorded_index(builder, repo_root: str, corpus: str) -> LexicalIndex:
    """The index whose documents are token-for-token those of the recording,
    wherever `repo_root` is."""
    return LexicalIndex(builder, repo_root, path_tokens_root=RECORDED_CORPUS_ROOTS[corpus])


def gold_targets(g: dict) -> tuple[list[str], list[list[str]]]:
    """Requirement subjects (sorted, unique) and the in-repo symbol groups of
    each ambiguity interpretation (empty groups - external readings - dropped)."""
    targets = sorted({r["subject"] for r in g.get("requirements", []) if r.get("subject")})
    interps = [i["symbols"] for i in (g.get("ambiguity") or {}).get("interpretations", []) if i["symbols"]]
    return targets, interps


def measure_question(ix: LexicalIndex, q: dict, g: dict) -> dict:
    targets, interps = gold_targets(g)
    row = {"targets": targets, "query_tokens": sorted(set(tokenize(q["query"])))}
    for v in VARIANTS:
        order = ix.ranked(q["query"], v)
        pos = {n: r + 1 for r, n in enumerate(order)}
        row[v] = {"rank": {t: pos.get(t) for t in targets}, "top10": order[:TOP_K],
                  "interp_in_top20": [any(pos.get(s, 1e9) <= 20 for s in grp) for grp in interps],
                  "interp_best_rank": [min((pos.get(s) for s in grp if pos.get(s)), default=None) for grp in interps]}
    if q["id"] >= EXPANSION_FROM:
        row["expansion"] = {}
        for v in VARIANTS:
            for k in EXPANSION_K:
                exp = ix.expanded(q["query"], v, k)
                row["expansion"][f"{v}@{k}"] = {"size": len(exp), "targets_found": {t: t in exp for t in targets},
                                                "interps_found": [any(s in exp for s in grp) for grp in interps]}
    return row


def measure(indexes: dict, questions: list[dict], gold: dict) -> dict:
    """`indexes`: corpus -> LexicalIndex. Questions of other corpora are skipped."""
    out = {"corpus": {c: {"ranked_pool": len(ix.pool)} for c, ix in sorted(indexes.items())}, "queries": {}}
    for q in questions:
        if q["corpus"] in indexes:
            out["queries"][q["id"]] = measure_question(indexes[q["corpus"]], q, gold[q["id"]])
    return out


def diff_against_recorded(measured: dict, recorded: dict | None = None) -> list[str]:
    """Every field that differs from the recorded probe output (empty = reproduced)."""
    recorded = recorded or load_recorded()
    problems = []
    for c, v in measured["corpus"].items():
        if v["ranked_pool"] != recorded["corpus"][c]["ranked_pool"]:
            problems.append(f"{c}: pool {v['ranked_pool']} != recorded {recorded['corpus'][c]['ranked_pool']}")
    for qid, row in measured["queries"].items():
        rec = recorded["queries"][qid]
        for key in sorted(set(row) | set(rec)):
            if row.get(key) != rec.get(key):
                problems.append(f"{qid}.{key}: measured {row.get(key)!r} != recorded {rec.get(key)!r}")
    return problems


def subject_recall(measured: dict, qids, k: int, mode: str) -> dict:
    """Questions (and subjects) whose gold subject is found. mode = "rank"
    (rank <= k in either variant) or "expanded" (in either variant's expanded
    set at k). Denominators are the questions/subjects given, never inferred."""
    qids = list(qids)
    q_hit, s_hit, s_den = 0, 0, 0
    for qid in qids:
        row = measured["queries"][qid]
        found = []
        for t in row["targets"]:
            if mode == "rank":
                f = any((row[v]["rank"][t] or 10 ** 9) <= k for v in VARIANTS)
            else:
                f = any(row["expansion"][f"{v}@{k}"]["targets_found"][t] for v in VARIANTS)
            found.append(f)
        s_hit += sum(found)
        s_den += len(found)
        q_hit += any(found)
    return {"questions": [q_hit, len(qids)], "subjects": [s_hit, s_den]}
