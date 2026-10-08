"""Query-intent classifier for production routing: heuristic only, no model,
no labels. Reads only the query text.

Two grammatical principles (see reports/harness_m4/analysis/
production_routing_experiment.md, section 2):
  impact    = a conditional change to the seed, or a question whose object is
              the seed's dependents (who/which ... call/use/rely on X; usages of
              X; where is X used)            -> "blast_radius"
  mechanism = a question whose subject is the seed (how does X ..., what does X
              do/call, starting from X, X delegates ...), or a trace/debug/
              locate verb                    -> "localization"
Weighted cue sums B (impact) and L (mechanism): "unknown" when B + L < floor,
otherwise the side ahead by `margin`, else "mixed". Confidence = |B - L| / (B + L).
"""
from __future__ import annotations

import re

INTENTS = ("blast_radius", "localization", "mixed", "unknown")

SEED = r"`?[\w.$<>]+`?"
BLAST = [
    # conditional change / impact framing
    (r"\bblast[- ]radius\b", 3.0),
    (r"\bwhat (breaks|would break|will break|could break|might break|fails|would fail)\b", 3.0),
    (r"\b(breaks?|break) if\b", 3.0),
    (r"\bif " + SEED + r" (changes|is changed|is modified|is removed|is renamed|starts|stops)\b", 2.5),
    (r"\bif (we|i|you) (change|modify|rename|remove|delete|deprecate|alter)\b", 3.0),
    (r"\b(when|before) (we|i|you) (change|modify|rename|remove|delete|deprecate|upgrade)\b", 2.5),
    (r"\b(impact|ripple)\b", 2.0), (r"\baffected\b", 2.0), (r"\baffect(s|ing)?\b", 1.0),
    (r"\bsafe to (change|rename|remove|delete|modify|deprecate)\b", 3.0),
    (r"\b(need|needs) (to change|updating|to be updated)\b", 2.0), (r"\bdeprecat(e|ing|ion)\b", 1.5),
    (r"\bchang(e|es|ing) (what|how) " + SEED + r" returns\b", 2.5),
    (r"\b(return value|signature|contract) (shape|change)\b", 1.0),
    # dependents as the object of the question
    (r"\b(who|what|which (functions?|methods?|code|callers?|modules?|parts?|components?|services?|reports?))\b[^.?\n]{0,50}\b(calls?|uses?|depends?|depend on|relies|rely|reach(es)?|invokes?|imports?)\b", 3.0),
    (r"\b(callers?|call[- ]sites|consumers?|dependents?|usages?)\b", 2.0),
    (r"\b(all|every) (callers?|call[- ]sites|usages?|uses|references)\b", 1.5),
    (r"\b(is|are) " + SEED + r" used\b", 3.0), (r"\bused (across|by|in|throughout)\b", 2.0),
    (r"\bdepends? on " + SEED, 2.0), (r"\brel(y|ies) on\b", 2.0),
    (r"\b(code )?paths? (that )?reach\b", 2.5),
]
LOCAL = [
    # the seed as the subject: what it does / how it works
    (r"\bhow (does|do|is|are) " + SEED + r"\b", 2.5),
    (r"\bhow\b[^.?\n]{0,60}\b(generat|build|comput|pars|handl|serializ|compil|check|stream|wire|work|process|resolv|validat|dispatch|render|construct|hash|load|pick|decid)", 2.0),
    (r"\bwhat (does|do) " + SEED + r" (do|call|use|invoke|delegate|return|compute)\b", 3.0),
    (r"\b(identify|explain|describe) (what|how) (it|" + SEED + r") (does|works|is done)\b", 3.0),
    (r"\bwhat it does\b", 2.5), (r"\bstarting (from|at)\b", 2.0),
    (r"\bdelegat(es|ed|ing|e)\b", 2.0), (r"\bcalls? into\b", 1.5),
    (r"\btrace\b", 2.5), (r"\bwalk (me )?through\b", 2.5), (r"\bstep(s| by step)\b", 1.5),
    (r"\bexecution (path|flow|order)\b", 2.5), (r"\b(causal|call) (chain|order)\b", 2.0), (r"\bcontrol flow\b", 2.0),
    (r"\bwhere (does|do) " + SEED + r"\b", 2.5), (r"\bfind (where|the source|the cause)\b", 2.5),
    (r"\bwhy (does|is|do)\b", 2.0), (r"\bdebug(ging)?\b", 2.0), (r"\b(bug|root cause)\b", 1.5),
    (r"\bwhich helper\b", 2.5), (r"\b(inside|internally|under the hood)\b", 1.5),
    (r"\blocate\b", 2.0), (r"\blocali[sz]e\b", 2.5),
]


def _score(text: str, cues: list[tuple[str, float]]) -> tuple[float, list[str]]:
    t = text.lower()
    s, hits = 0.0, []
    for pat, w in cues:
        n = len(re.findall(pat, t))
        if n:
            hits.append(pat[:40])
            s += w * min(n, 2)
    return s, hits


def classify(query: str, margin: float = 2.0, floor: float = 2.0) -> tuple[str, float, dict]:
    """`(intent, confidence, evidence)` for one query; intent is one of `INTENTS`."""
    b, bh = _score(query, BLAST)
    l, lh = _score(query, LOCAL)
    total = b + l
    ev = {"blast": b, "local": l, "blast_hits": bh, "local_hits": lh}
    if total < floor:
        return "unknown", 0.0, ev
    if b >= l + margin:
        intent = "blast_radius"
    elif l >= b + margin:
        intent = "localization"
    else:
        intent = "mixed"
    return intent, round(abs(b - l) / total, 3), ev
