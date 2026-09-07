import requests


class GatewayTimeoutError(Exception):
    pass


class PaymentDeclinedError(Exception):
    pass


class Receipt:
    def __init__(self, receipt_id):
        self.id = receipt_id


class PaymentProcessor:
    def __init__(self, client):
        self.client = client

    def charge(self, customer_id, amount):
        if not self.client.is_healthy():
            raise GatewayTimeoutError("gateway down")
        charge_resp = requests.post("https://gateway/charge", json={"customer_id": customer_id, "amount": amount})
        if charge_resp.status_code != 200:
            raise PaymentDeclinedError("declined")
        return Receipt(charge_resp.json()["id"])
