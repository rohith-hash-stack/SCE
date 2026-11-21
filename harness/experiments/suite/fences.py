"""Remove a Markdown fence that wraps a whole answer, and nothing else (scoring only; applied to every version alike).

    strip_outer_fence("```python\\ndef f(): ...\\n```")  ->  "def f(): ..."

- Only a fence that opens the text (optionally with a language tag) is removed, with its closing fence when the
  text ends with one. An opening fence whose answer was cut off has no closing fence; the opening line still goes.
- Fences anywhere else are left alone: inside a string literal (a Markdown renderer's tests) they are code. An
  answer split into several fenced blocks keeps its inner fences and still fails to parse: a format failure, not
  something to repair.
"""
from __future__ import annotations

import re

_OPEN = re.compile(r"\A[ \t]*```[\w+.-]*[ \t]*\n")
_CLOSE = re.compile(r"\n[ \t]*```[ \t]*\Z")


def strip_outer_fence(text: str) -> str:
    stripped = text.strip("\n")
    opened = _OPEN.match(stripped)
    if not opened:
        return text
    body = stripped[opened.end():]
    closed = _CLOSE.search(body)
    return body[:closed.start()] if closed else body
