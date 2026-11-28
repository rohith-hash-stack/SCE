"""Committed version; the working-tree overlay adds a required parameter (V2)."""


def price(amount, rate):
    """Amount with tax at `rate`."""
    return amount * (1 + rate)
