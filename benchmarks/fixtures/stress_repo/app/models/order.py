"""Order domain model.

Represents a customer order as it moves through the checkout pipeline:
created, stock-reserved, priced, paid, and (occasionally) refunded or
cancelled. This module is intentionally the "richest" file in the stress
fixture - lots of state, lots of methods, only some of which are ever on
the `process_order` call path - to exercise how much a whole-file dump
wastes versus a variable-resolution slice of just the relevant method.
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DEFAULT_CURRENCY = "USD"


@dataclass
class LineItem:
    sku: str
    price: float
    quantity: int = 1


class Order:
    def __init__(self, order_id, items):
        self.order_id = order_id
        self.items = items
        self.status = "PENDING"
        self.created_at = datetime.datetime.utcnow()
        self.history = []
        self.receipt_id = None
        self.currency = DEFAULT_CURRENCY

    def mark_paid(self, receipt_id):
        self.status = "PAID"
        self.receipt_id = receipt_id
        self.history.append(("PAID", datetime.datetime.utcnow()))
        logger.info("order %s marked paid with receipt %s", self.order_id, receipt_id)

    def mark_cancelled(self, reason):
        """Cancel the order and record why, for later customer support review."""
        if self.status == "PAID":
            raise ValueError("cannot cancel an order that has already been paid")
        self.status = "CANCELLED"
        self.cancellation_reason = reason
        self.history.append(("CANCELLED", datetime.datetime.utcnow()))
        logger.warning("order %s cancelled: %s", self.order_id, reason)

    def mark_refunded(self, refund_id, amount):
        """Record a refund against a previously paid order."""
        if self.status != "PAID":
            raise ValueError("cannot refund an order that was never paid")
        self.status = "REFUNDED"
        self.refund_id = refund_id
        self.refunded_amount = amount
        self.history.append(("REFUNDED", datetime.datetime.utcnow()))
        logger.info("order %s refunded %s via %s", self.order_id, amount, refund_id)

    def total(self):
        return sum(item.price * item.quantity for item in self.items)

    def item_count(self):
        return sum(item.quantity for item in self.items)

    def is_paid(self):
        return self.status == "PAID"

    def is_cancelled(self):
        return self.status == "CANCELLED"

    def audit_trail(self):
        """Human-readable audit trail for support tooling; not part of the
        checkout path, but the kind of ancillary method every order/entity
        class accumulates over time.
        """
        lines = [f"Order {self.order_id} ({self.currency} {self.total():.2f})"]
        for event, timestamp in self.history:
            lines.append(f"  {timestamp.isoformat()}: {event}")
        return "\n".join(lines)

    def to_summary_dict(self):
        return {
            "order_id": self.order_id,
            "status": self.status,
            "total": self.total(),
            "item_count": self.item_count(),
            "currency": self.currency,
        }


@dataclass
class OrderSearchFilters:
    """Filter criteria for admin order search screens - unrelated to the
    checkout path itself, but a realistic neighbor in the same module.
    """

    status: str | None = None
    min_total: float | None = None
    max_total: float | None = None
    created_after: datetime.datetime | None = None
    tags: list[str] = field(default_factory=list)
