"""The D1 trap, part 1: a module function named like an unrelated method."""

_VALUES = {}


def set_value(key, value):
    """Set a module-level value."""
    _VALUES[key] = value
