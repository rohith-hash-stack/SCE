from app.auth import verify_session
from app.gateway import PaymentGateway
from app.repository import OrderRepository


class OrderService:
    def __init__(self, session, client):
        self.repo = OrderRepository(session)
        self.gateway = PaymentGateway(client)

    def checkout_order(self, token, order_id, items):
        order = {"order_id": order_id, "items": items, "status": "PENDING"}
        order["status"] = "PAID"
        self.repo.save(order)
        return order

    def cancel_order(self, order_id, amount):
        """Reference pattern for coordinating with the payment gateway and
        persisting the resulting order state - the template `refund_transaction`
        should follow."""
        refund_id = self.gateway.reverse_charge(order_id, amount)
        order = {"order_id": order_id, "status": "CANCELLED", "refund_id": refund_id}
        self.repo.save(order)
        return order

    def refund_transaction(self, order_id, amount):
        raise NotImplementedError("refund_transaction not yet implemented")
