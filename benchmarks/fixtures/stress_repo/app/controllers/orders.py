"""HTTP-facing order controller.

`process_order` is the single entrypoint benchmarked here: it authenticates
the request, reserves stock, prices the order, charges the customer, and
persists the result. `cancel_order` and the two route handlers below round
out a realistic controller surface without being part of that call chain.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from app.controllers.auth import require_admin, require_auth
from app.models.order import Order
from app.repositories.order_repository import OrderRepository
from app.services.inventory import InventoryService
from app.services.payment import PaymentGateway
from app.services.pricing import PricingEngine

logger = logging.getLogger(__name__)

router = APIRouter()


class OrderController:
    def __init__(self, session, payment_client, event_producer, tax_rate):
        self.repo = OrderRepository(session)
        self.inventory = InventoryService(session)
        self.payments = PaymentGateway(payment_client, event_producer)
        self.pricing = PricingEngine()
        self.tax_rate = tax_rate

    def process_order(self, token, order_id, items):
        require_auth(token)
        order = Order(order_id, items)
        self.inventory.reserve_stock(items)
        total = self.pricing.calculate_total(order, self.tax_rate)
        receipt_id = self.payments.charge(order_id, total)
        order.mark_paid(receipt_id)
        self.repo.save(order)
        logger.info("processed order %s for total %.2f", order_id, total)
        return order

    def cancel_order(self, token, order_id, reason):
        """Cancels an existing, unpaid order. A separate control-flow path
        from `process_order`, sharing the repository but not the payment
        or inventory reservation steps."""
        require_auth(token)
        order = self.repo.find_by_id(order_id)
        order.mark_cancelled(reason)
        self.repo.save(order)
        return order

    def force_delete_order(self, token, order_id):
        """Admin-only hard delete, gated behind the stricter admin guard."""
        require_admin(token)
        return self.repo.delete(order_id)


@router.post("/orders")
def create_order_endpoint(controller, token, order_id, items):
    return controller.process_order(token, order_id, items)


@router.delete("/orders/{order_id}")
def cancel_order_endpoint(controller, token, order_id, reason):
    return controller.cancel_order(token, order_id, reason)
