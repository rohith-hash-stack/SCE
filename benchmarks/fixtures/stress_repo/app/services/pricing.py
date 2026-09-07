"""Pricing and discount calculations.

`calculate_total` is the one method on the checkout path; the discount and
loyalty-tier helpers below are realistic surrounding functionality that a
pricing engine accumulates, none of it needed for a single order's basic
tax calculation.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

LOYALTY_DISCOUNT_RATES = {"bronze": 0.0, "silver": 0.02, "gold": 0.05, "platinum": 0.10}


class InvalidDiscountError(Exception):
    pass


class PricingEngine:
    def calculate_total(self, order, tax_rate):
        subtotal = order.total()
        tax = subtotal * tax_rate
        total = subtotal + tax
        logger.debug("calculated total for order %s: subtotal=%.2f tax=%.2f", order.order_id, subtotal, tax)
        return total

    def apply_discount_code(self, order, code, percent_off):
        """Applies a promotional discount code to an order's subtotal."""
        if not (0 < percent_off <= 100):
            raise InvalidDiscountError(f"invalid discount percentage: {percent_off}")
        subtotal = order.total()
        discount = subtotal * (percent_off / 100.0)
        logger.info("applied discount code %s (-%.2f) to order %s", code, discount, order.order_id)
        return subtotal - discount

    def apply_loyalty_discount(self, order, tier):
        rate = LOYALTY_DISCOUNT_RATES.get(tier)
        if rate is None:
            raise InvalidDiscountError(f"unknown loyalty tier: {tier}")
        subtotal = order.total()
        return subtotal * (1 - rate)

    def estimate_shipping(self, order, region):
        """Rough shipping estimate based on item count and region - a
        placeholder used by the storefront UI, not the checkout backend."""
        base_rate = 4.99 if region == "domestic" else 19.99
        return base_rate + (0.5 * order.item_count())

    def calculate_total_with_shipping(self, order, tax_rate, region):
        return self.calculate_total(order, tax_rate) + self.estimate_shipping(order, region)
