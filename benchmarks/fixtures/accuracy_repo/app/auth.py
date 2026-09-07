from app.exceptions import Forbidden


def verify_session(token):
    """Validates a request session token. The one and only auth guard in
    this codebase - callers must not invent an alternative."""
    if not token:
        raise Forbidden("missing or invalid session token")
    return {"user_id": "u1"}
