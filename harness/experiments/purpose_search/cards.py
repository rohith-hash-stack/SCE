"""Moved to `prism.summaries.cards` (the shipped summarizer uses it); kept as
an alias so the harness and its tests import the same code."""
from prism.summaries.cards import *  # noqa: F401,F403
from prism.summaries.cards import CAP, CAP_UNRESOLVED, SIG_CAP, build_card, docstring, resources  # noqa: F401
