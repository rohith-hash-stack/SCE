"""33 structured prompt archetypes covering distinct developer/user
interaction styles, each targeting a real symbol in a large indexed
repository (see `archetypes.py`'s module docstring). Consumed by
`benchmarks/large_repo_prompt_matrix.py`.
"""
from __future__ import annotations

from benchmarks.prompt_taxonomy.archetypes import ARCHETYPES, ARCHETYPES_BY_ID, ARCHETYPES_BY_SLUG
from benchmarks.prompt_taxonomy.spec import DEFAULT_SYSTEM_PROMPT, PromptArchetype

__all__ = [
    "ARCHETYPES",
    "ARCHETYPES_BY_ID",
    "ARCHETYPES_BY_SLUG",
    "DEFAULT_SYSTEM_PROMPT",
    "PromptArchetype",
]
