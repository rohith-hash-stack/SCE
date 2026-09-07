"""Order service.

`process_order` below has a known production defect (see Task 1 in
`benchmarks/tasks.py`): it writes to the database without an auth check.
`process_return` is the correct sibling method, used as the dependency-usage
template for Task 2's feature-extension prompt.
"""
from __future__ import annotations

from app.auth import require_auth
from app.billing import BillingService, EventBus
from app.repository import OrderRepository


class OrderService:
    def __init__(self, session, payment_client, producer):
        self.repo = OrderRepository(session)
        self.billing = BillingService(payment_client)
        self.events = EventBus(producer)

    def process_order(self, token, order_id, items):
        order = {"order_id": order_id, "items": items, "status": "PENDING"}
        order["status"] = "PAID"
        self.repo.save(order)
        return order

    def process_return(self, token, order_id, amount):
        require_auth(token)
        refund_id = self.billing.refund(order_id, amount)
        self.events.publish("order.returned", {"order_id": order_id, "refund_id": refund_id})
        return refund_id
