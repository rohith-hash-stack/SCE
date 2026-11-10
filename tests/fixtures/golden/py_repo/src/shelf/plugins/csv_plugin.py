"""A loader registered by decorator."""
from shelf.registry import register
from shelf.core.parse import parse


@register("csv")
def load_csv(text):
    """Load CSV text."""
    return parse(text)
