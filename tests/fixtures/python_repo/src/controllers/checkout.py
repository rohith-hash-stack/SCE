from fastapi import APIRouter

from src.auth.jwt import verify_session
from src.services.billing import PaymentProcessor
from src.repositories.orders import OrderRepository

app = APIRouter()


class CheckoutController:
    def __init__(self, http_client, db_session):
        self.auth_service = verify_session
        self.payment_processor = PaymentProcessor(http_client)
        self.order_repo = OrderRepository(db_session)

    def process_checkout(self, user_id, cart_id, token):
        user = verify_session(token)
        receipt = self.payment_processor.charge(user_id, cart_id)
        self.order_repo.mark_paid(cart_id, receipt.id)
        return receipt


@app.post("/checkout")
def checkout_endpoint(controller, user_id, cart_id, token):
    return controller.process_checkout(user_id, cart_id, token)
