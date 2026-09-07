class PaymentGateway:
    """The payment provider integration. `charge` and `reverse_charge` are
    the only two real methods - there is no `refund` or `process_refund`
    method anywhere in this codebase."""

    def __init__(self, client):
        self.client = client

    def charge(self, order_id, amount):
        return f"receipt-{order_id}"

    def reverse_charge(self, order_id, amount):
        return f"refund-{order_id}"
