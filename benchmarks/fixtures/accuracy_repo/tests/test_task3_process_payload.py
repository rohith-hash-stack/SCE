import pytest

from app.payloads import process_payload


def test_process_payload_handles_validation_error_gracefully():
    result = process_payload({})
    assert result == {"error": "invalid payload"}


def test_process_payload_happy_path_unaffected():
    result = process_payload({"amount": 10})
    assert result == {"status": "ok", "amount": 10}


def test_process_payload_does_not_swallow_unrelated_errors():
    """A payment-gateway outage (RuntimeError, simulated by a negative
    amount) must propagate, not get silently reported as 'invalid payload' -
    the actual defect in the current implementation's `except Exception:`."""
    with pytest.raises(RuntimeError):
        process_payload({"amount": -1})
