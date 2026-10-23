"""Moved to `prism.summaries.lint` (the shipped summarizer's repair pass uses
it); kept as an alias so the harness and its tests import the same code."""
from prism.summaries.lint import *  # noqa: F401,F403
from prism.summaries.lint import lint, problems, rates  # noqa: F401
