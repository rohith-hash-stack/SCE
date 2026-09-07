"""Billing and event-notification dependencies used by the order service."""
from __future__ import annotations

import kafka
import requests


class RefundDeclinedError(Exception):
    pass


class BillingService:
    def __init__(self, client):
        self.client = client

    def refund(self, order_id, amount):
        """Issues a refund through the payment provider and returns the
        refund id. The one and only refund method - callers must not
        invent an alternative name or signature."""
        response = requests.post("https://gateway.internal/refund", json={"order_id": order_id, "amount": amount})
        if response.status_code != 200:
            raise RefundDeclinedError(order_id)
        return response.json()["refund_id"]


class EventBus:
    def __init__(self, producer):
        self.producer = producer

    def publish(self, topic, payload):
        """Publishes an event to the given topic. The one and only
        publish method - callers must not invent an alternative name."""
        self.producer.publish(topic, payload)
