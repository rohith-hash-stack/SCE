import logging

logger = logging.getLogger(__name__)


def compute_total(items, tax_rate):
    subtotal = 0
    for item in items:
        subtotal = subtotal + item.price
    logger.debug("computed subtotal")
    tax = subtotal * tax_rate
    if tax_rate < 0:
        raise ValueError("tax rate cannot be negative")
    total = subtotal + tax
    return round(total, 2)
