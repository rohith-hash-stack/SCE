"""Summary lint: the noise that pollutes the BM25 summary field.

- axis_label:  an internal 4-axis label copied from the card - a multi-word
               label in plain form ("pure compute", "leaf utility"), a raw
               label token ("PURE_COMPUTE"), or the effect phrasing ("no
               effect", "[Effect: ...]"). Single-word labels ("query",
               "pipeline") are ordinary English and not flagged.
- filler:      template filler from the old "[Effect / Return / Throws]" slot
               ("no return", "returns nothing").
- identifier:  any code identifier - backticked, dotted, snake_case,
               camelCase or `name()`.
- callee_list: three or more of the card's own callee names.
"""
from __future__ import annotations

import re

from prism.semantics.bitmask import FeatureBit

_PREFIXES = ("SINK_", "FORM_", "OUTPUT_", "ROLE_", "PRIM_")
_LABELS = sorted({b.name.split("_", 1)[1] if b.name.startswith(_PREFIXES) else b.name for b in FeatureBit} | {"NONE"})
_MULTIWORD = [l.replace("_", " ").lower() for l in _LABELS if "_" in l]
_AXIS_RE = re.compile(  # plain-form multi-word labels and the effect phrasing, any case
    r"\b(" + "|".join(map(re.escape, _MULTIWORD)) + r")\b|\bno (side )?effects?\b|\[\s*effect", re.I)
_RAW_RE = re.compile(  # raw label tokens, case-sensitive: "TIME_IO", "ORCHESTRATOR" - not "query"
    r"\b(" + "|".join(re.escape(l) for l in _LABELS if len(l) > 3) + r")\b")
_FILLER_RE = re.compile(
    r"\bno return( value)?\b|\breturns? nothing\b|\bno output\b|\bwithout returning( any(thing| particular value| value)?)?\b",
    re.I)
_IDENT_RE = re.compile(  # product names such as Node.js / Next.js and numbers like 1.1 are not identifiers
    r"`[^`]+`|\b(?!\w+\.js\b)[A-Za-z_]\w*\.[A-Za-z_]\w*\b|\b[a-z0-9]+_[a-z0-9_]+\b|\b[a-z]+[A-Z]\w*\b|\b\w+\(\)")


def card_callees(card: str) -> set[str]:
    line = next((l for l in card.splitlines() if l.startswith("Direct Callees:")), "")
    names = re.findall(r"[\w$.]+", line.split(":", 1)[1].replace("unresolved calls on parameters/self", ""))
    return {n.rsplit(".", 1)[-1].lower() for n in names if len(n.rsplit(".", 1)[-1]) >= 3 and n != "none"}


def lint(summary: str, card: str) -> dict[str, bool]:
    words = set(re.findall(r"[a-z0-9_$]+", summary.lower()))
    return {
        "axis_label": bool(_AXIS_RE.search(summary) or _RAW_RE.search(summary)),
        "filler": bool(_FILLER_RE.search(summary)),
        "identifier": bool(_IDENT_RE.search(summary)),
        "callee_list": len(card_callees(card) & words) >= 3,
    }


def problems(summary: str, card: str) -> list[str]:
    """What a summary breaks, in words the repair prompt can quote back."""
    f = lint(summary, card)
    out = []
    if f["identifier"]:
        out.append("it contains code identifiers (" + ", ".join(dict.fromkeys(_IDENT_RE.findall(summary))) + ")")
    if f["callee_list"]:
        out.append("it lists the names of functions it calls")
    if f["axis_label"]:
        out.append("it uses internal analysis labels such as 'pure compute' or 'no effect'")
    if f["filler"]:
        out.append("it ends with filler such as 'no return'")
    return out


def rates(pairs) -> dict:
    """`pairs`: iterable of (summary, card). Share of summaries per flag, and any."""
    flags = [lint(s, c) for s, c in pairs]
    n = len(flags) or 1
    out = {k: round(sum(f[k] for f in flags) / n, 3) for k in ("axis_label", "filler", "identifier", "callee_list")}
    out["any"] = round(sum(any(f.values()) for f in flags) / n, 3)
    out["n"] = len(flags)
    return out
