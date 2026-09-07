from app.exceptions import PayloadValidationError
from app.validation import validate_payload


def _prepare_payload(payload):
    return validate_payload(payload)


def charge_customer(payload, amount):
    """Simulates a payment-gateway outage under a specific edge condition
    (a negative amount) by raising a plain RuntimeError - unrelated to
    payload validation."""
    if amount < 0:
        raise RuntimeError("gateway offline")
    return {"charged": amount}


def process_payload(payload):
    try:
        amount = _prepare_payload(payload)
        charge_customer(payload, amount)
        return {"status": "ok", "amount": amount}
    except Exception:
        return {"error": "invalid payload"}
