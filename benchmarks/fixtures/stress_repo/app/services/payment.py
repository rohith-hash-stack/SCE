"""Payment gateway integration.

`charge` is the method on the checkout path: it calls out to the payment
provider over HTTP and publishes a `payment.completed` event on success.
`refund` and the retry/idempotency helpers below round out a realistic
payment service but are not part of a single order's happy-path checkout.
"""
from __future__ import annotations

import logging
import time

import kafka
import requests

logger = logging.getLogger(__name__)

GATEWAY_BASE_URL = "https://gateway.internal/api"
MAX_RETRIES = 3


class PaymentDeclinedError(Exception):
    pass


class PaymentGatewayTimeoutError(Exception):
    pass


class PaymentGateway:
    def __init__(self, client, producer):
        self.client = client
        self.producer = producer

    def charge(self, customer_id, amount):
        response = requests.post(
            f"{GATEWAY_BASE_URL}/charge",
            json={"customer_id": customer_id, "amount": amount},
        )
        if response.status_code != 200:
            logger.warning("charge declined for customer %s: %s", customer_id, response.status_code)
            raise PaymentDeclinedError("declined")
        receipt_id = response.json()["receipt_id"]
        self.producer.publish("payment.completed", {"customer_id": customer_id, "amount": amount})
        logger.info("charged customer %s amount=%.2f receipt=%s", customer_id, amount, receipt_id)
        return receipt_id

    def charge_with_retry(self, customer_id, amount, max_retries=MAX_RETRIES):
        """Retries a charge on transient gateway timeouts - defensive
        wrapper used by batch/offline billing jobs, not interactive
        checkout (which surfaces the error to the customer immediately)."""
        last_error = None
        for attempt in range(max_retries):
            try:
                return self.charge(customer_id, amount)
            except PaymentGatewayTimeoutError as exc:
                last_error = exc
                time.sleep(2**attempt)
        raise last_error

    def refund(self, receipt_id, amount):
        response = requests.post(f"{GATEWAY_BASE_URL}/refund", json={"receipt_id": receipt_id, "amount": amount})
        if response.status_code != 200:
            raise PaymentDeclinedError("refund declined")
        self.producer.publish("payment.refunded", {"receipt_id": receipt_id, "amount": amount})
        return response.json()["refund_id"]

    def verify_gateway_health(self):
        """Health-check hook used by the ops dashboard."""
        response = requests.get(f"{GATEWAY_BASE_URL}/health")
        return response.status_code == 200
