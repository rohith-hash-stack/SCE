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

Extra checks (`extra=`, for the unvalidated candidates v3 and v3-name; v2 is
the locked, shipped lint and is unchanged):

- restates_name: at most MAX_NAME_ONLY_WORDS distinct content words beyond
                 the function's own name (and its class's), once generic
                 filler ("given", "value", "using") is set aside - "Matches a
                 method and path to a result." for `SmartRouter.match`;
- too_short:     fewer than MIN_WORDS words, the prompt's own floor.
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


#: Strict mode (v3): summaries adding this many content words or fewer beyond the
#: function's name restate it; summaries under MIN_WORDS words break the prompt's floor.
MAX_NAME_ONLY_WORDS = 2
MIN_WORDS = 8
FLAGS = ("axis_label", "filler", "identifier", "callee_list")
EXTRA_FLAGS = ("restates_name", "too_short")
#: Words that describe nothing on their own; not counted as content (tokenized
#: like the summary, so plural and verb forms match).
_GENERIC_TEXT = ("function method given specified provided value values data result results return returns based "
                 "option options new instance object param parameter parameters input output use using handle "
                 "handles current item items string strings type types certain specific according accordingly")


def card_name(card: str) -> str:
    line = next((l for l in card.splitlines() if l.startswith("Qualified Name:")), "")
    return line.split(":", 1)[1].strip() if ":" in line else ""


def content_words(summary: str, qualified_name: str) -> set[str]:
    """Distinct content words of `summary` that its function's own name (and,
    for a method, its class's name) and generic filler do not already say."""
    from prism.query.nlq import tokenize
    parts = qualified_name.split("#")[0].split(".")
    name = set(tokenize(parts[-1]))
    if len(parts) > 1 and parts[-2][:1].isupper():
        name |= set(tokenize(parts[-2]))
    generic = set(tokenize(_GENERIC_TEXT))
    return {w for w in tokenize(summary) if w not in name and w not in generic}


def card_callees(card: str) -> set[str]:
    line = next((l for l in card.splitlines() if l.startswith("Direct Callees:")), "")
    names = re.findall(r"[\w$.]+", line.split(":", 1)[1].replace("unresolved calls on parameters/self", ""))
    return {n.rsplit(".", 1)[-1].lower() for n in names if len(n.rsplit(".", 1)[-1]) >= 3 and n != "none"}


def lint(summary: str, card: str, extra: "tuple[str, ...]" = ()) -> dict[str, bool]:
    words = set(re.findall(r"[a-z0-9_$]+", summary.lower()))
    flags = {
        "axis_label": bool(_AXIS_RE.search(summary) or _RAW_RE.search(summary)),
        "filler": bool(_FILLER_RE.search(summary)),
        "identifier": bool(_IDENT_RE.search(summary)),
        "callee_list": len(card_callees(card) & words) >= 3,
    }
    if "restates_name" in extra:
        flags["restates_name"] = len(content_words(summary, card_name(card))) <= MAX_NAME_ONLY_WORDS
    if "too_short" in extra:
        flags["too_short"] = len(summary.split()) < MIN_WORDS
    return flags


def problems(summary: str, card: str, extra: "tuple[str, ...]" = ()) -> list[str]:
    """What a summary breaks, in words the repair prompt can quote back."""
    f = lint(summary, card, extra)
    out = []
    if f["identifier"]:
        out.append("it contains code identifiers (" + ", ".join(dict.fromkeys(_IDENT_RE.findall(summary))) + ")")
    if f["callee_list"]:
        out.append("it lists the names of functions it calls")
    if f["axis_label"]:
        out.append("it uses internal analysis labels such as 'pure compute' or 'no effect'")
    if f["filler"]:
        out.append("it ends with filler such as 'no return'")
    if f.get("restates_name"):
        out.append("it only restates the function's name; say what the function does with its inputs and how, "
                   "using the profile (what it checks, computes, changes, sends or returns)")
    if f.get("too_short"):
        out.append(f"it is shorter than {MIN_WORDS} words")
    return out


def rates(pairs, extra: "tuple[str, ...]" = ()) -> dict:
    """`pairs`: iterable of (summary, card). Share of summaries per flag, and any."""
    flags = [lint(s, c, extra) for s, c in pairs]
    n = len(flags) or 1
    out = {k: round(sum(f[k] for f in flags) / n, 3) for k in FLAGS + tuple(x for x in EXTRA_FLAGS if x in extra)}
    out["any"] = round(sum(any(f.values()) for f in flags) / n, 3)
    out["n"] = len(flags)
    return out
