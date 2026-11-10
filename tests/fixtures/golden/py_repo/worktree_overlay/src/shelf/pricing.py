"""Working-tree version: `currency` is a new required parameter, and checkout.py is not updated."""


def price(amount, rate, currency):
    """Amount with tax at `rate`, in `currency`."""
    return f"{amount * (1 + rate):.2f} {currency}"
