from app.exceptions import PayloadValidationError


def validate_payload(payload):
    """The only validation error type raised anywhere in this codebase."""
    if "amount" not in payload:
        raise PayloadValidationError("missing amount")
    return payload["amount"]
