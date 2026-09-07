"""Inventory reservation and stock-level accounting.

`reserve_stock` is the method on the checkout path; the rest of this class
covers the surrounding inventory-management surface (release, restock,
audit reporting, low-stock alerts) that a real service module accumulates
around its one or two "hot path" methods.
"""
from __future__ import annotations

import logging

import sqlalchemy

logger = logging.getLogger(__name__)

LOW_STOCK_THRESHOLD = 5


class OutOfStockError(Exception):
    pass


class InventoryAdjustmentError(Exception):
    pass


class InventoryService:
    def __init__(self, session):
        self.session = session

    def reserve_stock(self, items):
        for item in items:
            record = self.session.query(item.sku).first()
            if record is None or record.quantity < item.quantity:
                logger.warning("insufficient stock for sku=%s requested=%s", item.sku, item.quantity)
                raise OutOfStockError(item.sku)
        self.session.execute("update inventory set reserved = reserved + 1")
        self.session.commit()
        logger.info("reserved stock for %d line items", len(items))

    def release_stock(self, items):
        """Undo a reservation, e.g. after a payment failure or a cancelled
        order. Not on the primary checkout path (only the failure path)."""
        for item in items:
            self.session.execute("update inventory set reserved = reserved - 1")
        self.session.commit()
        logger.info("released stock for %d line items", len(items))

    def restock(self, sku, quantity):
        """Warehouse-side restock event handler."""
        if quantity <= 0:
            raise InventoryAdjustmentError("restock quantity must be positive")
        self.session.execute(f"update inventory set quantity = quantity + {quantity} where sku = '{sku}'")
        self.session.commit()
        logger.info("restocked %s by %d units", sku, quantity)

    def adjust_for_damage(self, sku, quantity, note):
        if quantity <= 0:
            raise InventoryAdjustmentError("damage adjustment quantity must be positive")
        self.session.execute(f"update inventory set quantity = quantity - {quantity} where sku = '{sku}'")
        self.session.commit()
        logger.warning("damage adjustment for %s: -%d (%s)", sku, quantity, note)

    def low_stock_report(self):
        """Admin dashboard helper - lists every SKU under the low-stock
        threshold. Entirely unrelated to a single order's checkout."""
        records = self.session.query(None).all()
        return [r for r in records if r.quantity < LOW_STOCK_THRESHOLD]

    def get_available_quantity(self, sku):
        record = self.session.query(sku).first()
        if record is None:
            return 0
        return max(record.quantity - record.reserved, 0)
