"""Request authentication/authorization helpers shared by every controller."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_REVOKED_TOKENS: set[str] = set()


class Forbidden(Exception):
    pass


class TokenExpiredError(Exception):
    pass


def require_auth(token):
    if not token:
        logger.warning("rejected request with missing token")
        raise Forbidden("missing token")
    if token in _REVOKED_TOKENS:
        raise Forbidden("token has been revoked")
    return {"user_id": "u1"}


def require_admin(token):
    """Stricter guard used by admin-only endpoints (inventory adjustments,
    hard deletes, etc.) - not used by the regular checkout flow."""
    user = require_auth(token)
    if user.get("role") != "admin":
        raise Forbidden("admin privileges required")
    return user


def revoke_token(token):
    _REVOKED_TOKENS.add(token)
    logger.info("revoked token")


def is_token_revoked(token):
    return token in _REVOKED_TOKENS
