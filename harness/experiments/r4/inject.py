"""Arm 3 of the 3-arm re-baseline (INJECTED): PRISM's context for the task, retrieved by the harness before the
agent starts and prepended to the task message (R1/R2's "upfront context" inside the agent loop).

**What is retrieved:** only information the agent itself is given is used to choose the query. Nothing comes from the
answer key.
- **delete:** the target the prompt names (`must_be_absent`, `path::Qual.name`), as PRISM's dotted symbol (the path
  without its extension, `/` -> `.`, then the qualified name), through `prism_change_context`. If PRISM cannot resolve
  that symbol (an error), `prism_ask` with the target's name and file instead.
- **feature:** the feature's description from the prompt (the text between "Implement the following feature." and
  "Tests for the feature must pass"), first 400 characters, through `prism_ask`.

The same `ToolLayer` call the PRISM arm's tools make, so the same 4,000-token cap and refresh notices apply.

**The arm otherwise is BASELINE's:** the same tools (shared tools and `related_code`; no PRISM tools), the same system
prompt; only the task message starts with the block below.
"""
from __future__ import annotations

import os
import re

HEADER = "Context from the code index (PRISM), retrieved for this task before you started:"
SEPARATOR = "\n\n---\n\n"


def dotted(target: str) -> str:
    path, _, qual = target.partition("::")
    return ".".join(os.path.splitext(path)[0].split("/") + ([qual] if qual else []))


def feature_question(prompt: str) -> str:
    m = re.search(r"Implement the following feature\.\s*(.*?)\s*(?:Tests for the feature must pass|$)", prompt, re.S)
    text = (m.group(1) if m else prompt).strip()
    return re.sub(r"\s+", " ", text)[:400]


def injected_context(task: dict, layer) -> tuple[str, dict]:
    """(the block to prepend, a record of what was retrieved)."""
    if task.get("family") == "delete" and task.get("must_be_absent"):
        sym = dotted(task["must_be_absent"])
        out = layer.call("prism_change_context", symbol=sym)
        info = {"tool": "prism_change_context", "query": sym}
        if out.lstrip().startswith("error"):
            f, _, q = task["must_be_absent"].partition("::")
            question = f"{q} in {f}"
            info.update(fallback_from=out[:200], tool="prism_ask", query=question)
            out = layer.call("prism_ask", question=question)
    else:
        question = feature_question(task.get("prompt", ""))
        out = layer.call("prism_ask", question=question)
        info = {"tool": "prism_ask", "query": question}
    info["error"] = out.lstrip().startswith("error")
    info["chars"] = len(out)
    return f"{HEADER}\n\n{out}{SEPARATOR}", info
