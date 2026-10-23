"""Query routing for `prism.ask`: what kind of query is this?

    classify("Router.include_router")                    -> "identifier"
    classify("where is user_id checked before saving?")  -> "mixed"
    classify("where are notifications saved?")           -> "natural_language"

Deterministic, no model. A query is *natural language* when it reads as a
sentence - four or more words, a question word in its first two words, or
a trailing `?`. A token is *code-shaped* when it has a `.`, `_`, `:`, `#`
or `/`, ends in `()`, or is camelCase/PascalCase. Then:

- identifier: not a sentence and has a code-shaped token, or is at most two
  plain words ("redirect", "send file") - a name lookup;
- mixed: a sentence with a code-shaped token in it;
- natural_language: everything else.

The route decides only the exact-name pin (`NLQIndex.search`): for
identifier and mixed queries, symbols whose qualified name the query's
names denote (`prism.query.locate.qualified_name_matches`) are placed
first. The summary weight does not depend on the route.

Measured on the frozen question sets: of 164 natural-language questions
(held-out sets 2-4 and Q2E's behavioural questions) 156 route
natural_language and 8 mixed, none identifier; all 104 exact-name lookups
built from set 4's gold names route identifier.
"""
from __future__ import annotations

import re

ROUTES = ("identifier", "mixed", "natural_language")
QUESTION_WORDS = frozenset({"how", "where", "what", "when", "why", "which", "who", "does", "do", "is", "are", "can", "should"})
CODE_SHAPE = re.compile(r"[._:#/]|\(\)|^[a-z]+[A-Z]\w*$|^[A-Z][a-z0-9]+[A-Z]\w*$|^[a-z0-9]+_[a-z0-9_]+$")
_QUOTES = "`'\",;"


def _tokens(query: str) -> list[str]:
    return query.strip().rstrip("?").split()


def code_tokens(query: str) -> list[str]:
    """The query's code-shaped tokens, quotes stripped."""
    return [t.strip(_QUOTES) for t in _tokens(query) if CODE_SHAPE.search(t.strip(_QUOTES))]


def classify(query: str) -> str:
    toks = _tokens(query)
    words = [t.lower().strip(",.;:?") for t in toks]
    sentence = len(toks) >= 4 or bool(QUESTION_WORDS & set(words[:2])) or query.strip().endswith("?")
    code = code_tokens(query)
    if not sentence and (code or len(toks) <= 2):
        return "identifier"
    return "mixed" if code else "natural_language"


def name_candidates(query: str, route: str) -> list[str]:
    """Names to look up for the exact-name pin, in query order: the
    code-shaped tokens normalised to dotted form (`A::b`, `A#b` -> `A.b`;
    a trailing `()` and trailing punctuation dropped); for an identifier
    query with none, the query itself when it is a single word."""
    if route == "natural_language":
        return []
    raw = code_tokens(query)
    if not raw and route == "identifier":
        toks = _tokens(query)
        raw = toks if len(toks) == 1 else []
    out = []
    for t in raw:
        name = re.sub(r"\(\)$", "", t.rstrip(".,;:")).replace("::", ".").replace("#", ".")
        if name and "/" not in name and re.fullmatch(r"[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*", name) and name not in out:
            out.append(name)
    return out
