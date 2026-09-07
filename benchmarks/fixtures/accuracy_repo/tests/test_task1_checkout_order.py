import pytest

from app.orders import OrderService


class FakeSession:
    def add(self, order):
        pass

    def commit(self):
        pass


def test_checkout_order_requires_auth():
    service = OrderService(session=FakeSession(), client=None)
    with pytest.raises(PermissionError):
        service.checkout_order(token=None, order_id="o1", items=["item1"])


def test_checkout_order_succeeds_with_valid_token():
    service = OrderService(session=FakeSession(), client=None)
    result = service.checkout_order(token="valid-token", order_id="o1", items=["item1"])
    assert result["order_id"] == "o1"
    assert result["status"] == "PAID"
