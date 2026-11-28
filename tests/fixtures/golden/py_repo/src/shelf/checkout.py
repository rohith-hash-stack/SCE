"""An unchanged caller of pricing.price."""
from shelf.pricing import price


def total(amount):
    return price(amount, 0.2)
