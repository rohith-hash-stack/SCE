"""Loading and validating the evaluation set.

`dataset/questions.json` holds only what a system under test may see (query
text, corpus, and - for seeded controls only - the inputs the existing
benchmark already hands Arm 5). `dataset/gold.json` holds the annotations and
is read only by the scorer. `validate` checks the schema, the category
distribution, query/gold separation, and (given indexed builders) that every
gold symbol exists and every recorded call site matches the source.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

DATASET_DIR = Path(__file__).resolve().parent / "dataset"
CATEGORIES = {"seeded_control": 8, "unseeded_behavioral": 6, "compound": 4, "ambiguous": 3, "unanswerable": 3}
EXPECTED_BEHAVIOR = {"answerable": "answer", "ambiguous": "clarify", "unanswerable": "abstain"}
REQUIREMENT_KINDS = {"behavior", "impact", "tests", "concurrency", "absence"}


def load_questions(path: Path | None = None) -> list[dict]:
    return json.loads((path or DATASET_DIR / "questions.json").read_text())["questions"]


def load_gold(path: Path | None = None) -> dict[str, dict]:
    return json.loads((path or DATASET_DIR / "gold.json").read_text())["gold"]


def location_symbol(builder, location: str) -> str | None:
    """`file:line` (corpus-relative) -> the indexed function/method defined
    on that line, for gold items identified by location (nested functions
    that share a name)."""
    rel, _, line = location.partition(":")
    hits = [s.qualified_name for s in builder.symbol_table
            if s.kind in ("function", "method") and s.file.endswith("/" + rel) and s.line_range[0] == int(line)]
    return hits[0] if len(hits) == 1 else None


def resolve_endpoint(builder, name: str) -> str | None:
    if name.startswith("@"):
        return location_symbol(builder, name[1:])
    return name if name in builder.symbol_table else None


def required_symbols(req: dict, builder=None) -> list[str]:
    syms = list(req.get("required_symbols", []))
    for item in req.get("required_symbols_by_location", []):
        syms.append(location_symbol(builder, item["location"]) if builder is not None else "@" + item["location"])
    return syms


def validate(questions: list[dict], gold: dict[str, dict], builders: dict | None = None) -> list[str]:
    """Problems found; empty means valid. `builders` maps corpus -> an indexed
    ConcreteGraphBuilder for the symbol/call-site checks (skipped if None)."""
    problems: list[str] = []
    ids = [q["id"] for q in questions]
    if len(set(ids)) != len(ids):
        problems.append("duplicate question ids")
    if set(ids) != set(gold):
        problems.append(f"question/gold id mismatch: {sorted(set(ids) ^ set(gold))}")
    counts = Counter(q["category"] for q in questions)
    if dict(counts) != CATEGORIES:
        problems.append(f"category counts {dict(counts)} != {CATEGORIES}")
    for q in questions:
        g = gold.get(q["id"], {})
        if set(q) - {"id", "corpus", "category", "query", "inputs"}:
            problems.append(f"{q['id']}: unexpected question fields {sorted(set(q) - {'id', 'corpus', 'category', 'query', 'inputs'})}")
        if q["inputs"] and q["category"] != "seeded_control":
            problems.append(f"{q['id']}: only seeded controls may carry inputs")
        if EXPECTED_BEHAVIOR.get(g.get("answerability")) != g.get("expected_behavior"):
            problems.append(f"{q['id']}: answerability/expected_behavior mismatch")
        if g.get("answerability") == "answerable" and not g.get("requirements"):
            problems.append(f"{q['id']}: answerable question without requirements")
        if q["category"] == "compound" and len(g.get("requirements", [])) < 2:
            problems.append(f"{q['id']}: compound question needs >= 2 requirements")
        if g.get("answerability") == "ambiguous" and not (g.get("ambiguity", {}).get("interpretations") and g["ambiguity"].get("resolving_information")):
            problems.append(f"{q['id']}: ambiguous question needs interpretations and resolving_information")
        if g.get("answerability") == "unanswerable" and not g.get("unanswerable_reason"):
            problems.append(f"{q['id']}: unanswerable question needs a reason")
        for req in g.get("requirements", []):
            if req.get("kind") not in REQUIREMENT_KINDS:
                problems.append(f"{q['id']}/{req.get('id')}: unknown kind {req.get('kind')}")
            if req.get("kind") == "absence":
                if req.get("required_symbols") or not req.get("absence_evidence"):
                    problems.append(f"{q['id']}/{req['id']}: absence requirement must have no symbols and cite its search")
            elif not (req.get("required_symbols") or req.get("required_symbols_by_location")):
                problems.append(f"{q['id']}/{req['id']}: requirement without required symbols")
        # leakage: outside seeded controls, the query must not name a gold symbol
        if q["category"] != "seeded_control":
            for req in g.get("requirements", []):
                for sym in [*req.get("required_symbols", []), req.get("subject") or ""]:
                    leaf = sym.rsplit(".", 1)[-1]
                    if sym and (sym in q["query"] or (len(leaf) > 3 and not leaf.startswith("__")
                                                      and re.search(rf"\b{re.escape(leaf)}\b", q["query"]))):
                        if not (q["category"] == "unanswerable" or leaf in {"get", "set", "add"}):
                            problems.append(f"{q['id']}: query names gold symbol {sym}")
        if builders is None or q["corpus"] not in builders:
            continue
        b = builders[q["corpus"]]
        for req in g.get("requirements", []):
            for sym in required_symbols(req, b):
                if sym is None or sym not in b.symbol_table:
                    problems.append(f"{q['id']}/{req['id']}: required symbol not indexed: {sym}")
            for e in req.get("relationship_paths", []):
                for end in (e["from"], e["to"]):
                    if resolve_endpoint(b, end) is None:
                        problems.append(f"{q['id']}/{req['id']}: edge endpoint not indexed: {end}")
                problems.extend(f"{q['id']}/{req['id']}: {p}" for p in _check_call_site(b, e))
        for interp in g.get("ambiguity", {}).get("interpretations", []):
            for sym in interp.get("symbols", []):
                if sym not in b.symbol_table:
                    problems.append(f"{q['id']}: interpretation symbol not indexed: {sym}")
    return problems


def _check_call_site(builder, e: dict) -> list[str]:
    rel, _, line = e["call_site"].partition(":")
    files = {s.file for s in builder.symbol_table if s.file.endswith("/" + rel)}
    if len(files) != 1:
        return [f"call site file not found: {rel}"]
    lines = Path(next(iter(files))).read_text(encoding="utf-8").splitlines()
    text = lines[int(line) - 1].strip() if int(line) <= len(lines) else ""
    if e["source_text"].strip() not in text:
        return [f"call site {e['call_site']} text {text!r} does not contain {e['source_text']!r}"]
    caller = resolve_endpoint(builder, e["from"])
    info = builder.symbol_table.get(caller) if caller else None
    if info is not None and not (info.line_range[0] <= int(line) <= info.line_range[1]):
        return [f"call site {e['call_site']} is outside {caller} lines {info.line_range}"]
    return []
