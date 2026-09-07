from app.orders import OrderService


class FakeSession:
    def add(self, order):
        pass

    def commit(self):
        pass


class RecordingGateway:
    """Only implements the two real gateway methods. Calling anything else
    (e.g. a hallucinated `.refund(...)`) raises AttributeError, failing the
    test - a real, functional hallucination check via execution."""

    def __init__(self):
        self.reverse_charge_calls = []

    def charge(self, order_id, amount):
        return "receipt1"

    def reverse_charge(self, order_id, amount):
        self.reverse_charge_calls.append((order_id, amount))
        return "refund-123"


def test_refund_transaction_calls_real_gateway_method_with_exact_args():
    service = OrderService(session=FakeSession(), client=None)
    recording_gateway = RecordingGateway()
    service.gateway = recording_gateway  # substitute post-construction for observability

    result = service.refund_transaction("o1", 42.5)

    assert recording_gateway.reverse_charge_calls == [("o1", 42.5)]
    assert result is not None
