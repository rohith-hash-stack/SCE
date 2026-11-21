"""Loader registration by decorator."""

REGISTRY = {}


def register(name):
    """Register the decorated loader under `name`."""
    def wrap(fn):
        REGISTRY[name] = fn
        return fn
    return wrap


def loader(name):
    """The loader registered under `name`."""
    return REGISTRY[name]
