"""v1.1+ Agent Surface: API-key authentication & rate limiting for the
`prism.slice`/`prism.explain` MCP tools.

**Stated honestly up front**: this server's only transport `prism mcp`
actually launches is `stdio` (see `prism.mcp.server.run_server`) - a
subprocess talking newline-framed JSON-RPC over stdin/stdout, with no
HTTP request and therefore no `Authorization` *header* at all. The
`Context` object the MCP SDK injects into a tool handler does expose
`.headers` (populated when a future deployment runs this same server
over `sse`/`streamable-http` instead), so `AuthGuard.check` reads it
there first - but the two tools this module guards also accept a plain
`api_key: str | None` **tool argument** as the transport-agnostic
fallback that actually works today, over the same stdio transport this
repository's own test harness (`mcp.client.Client(server)`, in-process)
and `prism mcp` both use. Both paths run through the exact same
validation; nothing here treats one as more trusted than the other.

Rate limiting (60 requests/minute per key) only ever applies once
`PRISM_MCP_API_KEYS` is actually configured - an unauthenticated server
(the documented default: `prism mcp` with no key configuration at all)
has no per-caller identity to key a limiter on, and rate-limiting the
single anonymous "caller" would just be an arbitrary global call cap
with no real abuse-prevention meaning.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque

from mcp.shared.exceptions import MCPError

#: Comma-separated list of valid API keys - unset/empty means
#: authentication (and therefore rate limiting, see the module docstring)
#: is disabled entirely, the documented default for a locally-run,
#: single-user `prism mcp` process.
API_KEYS_ENV_VAR = "PRISM_MCP_API_KEYS"

RATE_LIMIT_PER_MINUTE = 60
_WINDOW_SECONDS = 60.0


def _configured_keys() -> frozenset[str]:
    """Read fresh from the environment on every call (never cached at
    import time) - a test (or an operator via `prism mcp`'s own restart)
    changing `PRISM_MCP_API_KEYS` takes effect immediately, the same
    "no stale module-level snapshot of mutable config" property
    `prism.mcp.cache.GraphCache.sandbox_root` already keeps for
    `PRISM_MCP_DEFAULT_REPO`.
    """
    raw = os.environ.get(API_KEYS_ENV_VAR, "")
    return frozenset(k.strip() for k in raw.split(",") if k.strip())


def auth_required() -> bool:
    return bool(_configured_keys())


def _resolve_candidate(header_value: str | None, api_key_arg: str | None) -> str | None:
    """The header value wins when the transport actually supplied one
    (only ever true under `sse`/`streamable-http`); otherwise the tool
    argument, the path this codebase's own stdio transport and in-process
    test client both actually exercise."""
    if header_value:
        prefix = "Bearer "
        return header_value[len(prefix):] if header_value.startswith(prefix) else header_value
    return api_key_arg


def check_authorized(header_value: str | None, api_key_arg: str | None) -> None:
    """Raises `MCPError(code=401, ...)` if `PRISM_MCP_API_KEYS` is
    configured and neither the `Authorization: Bearer <key>` header value
    (when the transport provides one) nor the `api_key` tool argument
    names a configured key. A no-op when authentication is disabled.
    """
    keys = _configured_keys()
    if not keys:
        return
    candidate = _resolve_candidate(header_value, api_key_arg)
    if candidate is None or candidate not in keys:
        raise MCPError(code=401, message="unauthorized: a valid API key is required (Authorization: Bearer <key>, or the api_key argument)")


class RateLimiter:
    """A real sliding-window limiter - `RATE_LIMIT_PER_MINUTE` requests
    per key per rolling 60-second window, not a naive fixed-bucket reset
    (which would let a caller burst 2x the nominal rate across a window
    boundary). Thread-safe: `prism.mcp.server`'s tools can run
    concurrently (Item 14, second post-implementation audit - this
    codebase's own MCP server is already built to be called from multiple
    concurrent agent sessions against the same process).
    """

    def __init__(self, limit_per_minute: int = RATE_LIMIT_PER_MINUTE, window_seconds: float = _WINDOW_SECONDS) -> None:
        self._limit = limit_per_minute
        self._window = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str) -> None:
        """Raises `MCPError(code=429, ...)` if `key` has already made
        `limit_per_minute` requests within the trailing `window_seconds`;
        otherwise records this call and returns."""
        now = time.monotonic()
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            cutoff = now - self._window
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._limit:
                raise MCPError(code=429, message=f"rate limit exceeded: {self._limit} requests/minute per key")
            bucket.append(now)

    def reset(self) -> None:
        """Test-only convenience - clears every key's history."""
        with self._lock:
            self._hits.clear()


#: One process-lifetime limiter, mirroring `prism.mcp.server`'s own
#: module-level `GraphCache` singleton - rate limiting is meaningless
#: reset per call.
rate_limiter = RateLimiter()


def enforce(header_value: str | None, api_key_arg: str | None) -> None:
    """The one call site `prism.mcp.server`'s new tools use: validates
    the credential (401 if invalid/missing while keys are configured),
    then checks the rate limit for whichever key was actually used (429
    if exceeded) - a no-op end to end when authentication is disabled.
    """
    check_authorized(header_value, api_key_arg)
    if auth_required():
        rate_limiter.check(_resolve_candidate(header_value, api_key_arg))
