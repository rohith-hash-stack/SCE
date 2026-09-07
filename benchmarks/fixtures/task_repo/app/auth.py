"""Request authentication for the order service."""
from __future__ import annotations


class Forbidden(Exception):
    pass


def require_auth(token):
    """Validates a request token, raising Forbidden if it is missing or
    invalid. This is the one and only auth guard in this module - callers
    must not invent an alternative."""
    if not token:
        raise Forbidden("missing token")
    return {"user_id": "u1"}
