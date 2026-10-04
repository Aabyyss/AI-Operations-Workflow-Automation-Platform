"""Optional API-key authentication for the FastAPI surface.

Off by default: local dev, the demo, CI, and the dashboard need zero config.
When AIOPS_API_KEY is set, every request must carry a matching key — except
health endpoints (k8s/liveness probes and uptime checks must never need
secrets) — via either ``X-API-Key`` or ``Authorization: Bearer <key>``.

Comparison is constant-time (hmac.compare_digest): a naive ``==`` lets an
attimeter byte-probe the key one character at a time.
"""
from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from . import config

# Routes that must stay reachable without a key (liveness/readiness probes).
OPEN_PATHS = {"/health", "/ready"}

_REALM = "api-key"


def _constant_time_eq(presented: str, expected: str) -> bool:
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def role_of(presented_key: str | None) -> str | None:
    """Resolve a presented key to its role: operator, approver, admin, or None.

    The master key (AIOPS_API_KEY) is the operator role — full access.
    AIOPS_APPROVER_KEY and AIOPS_ADMIN_KEY grant narrower, delegable roles
    (narrower on purpose: an approver cannot mint approvals, an admin
    cannot approve refunds). Empty string in config means unconfigured,
    never a match.
    """
    if not presented_key or not config.API_KEY:
        return None
    if _constant_time_eq(presented_key, config.API_KEY):
        return "operator"
    if config.ADMIN_KEY and _constant_time_eq(presented_key, config.ADMIN_KEY):
        return "admin"
    if config.APPROVER_KEY and _constant_time_eq(presented_key, config.APPROVER_KEY):
        return "approver"
    return None


def verify_key(presented: str | None) -> bool:
    """Constant-time check of a presented key against any configured role key."""
    if not config.API_KEY:
        return True  # auth disabled
    return role_of(presented) is not None


def _presented_key(request: Request) -> str | None:
    header = request.headers.get("x-api-key")
    if header:
        return header
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def install_auth(app: FastAPI) -> None:
    """Attach the auth gate. No-op unless AIOPS_API_KEY is configured."""

    @app.middleware("http")
    async def api_key_gate(request: Request, call_next: Callable[[Request], Awaitable]):
        if config.API_KEY and request.url.path not in OPEN_PATHS:
            if not verify_key(_presented_key(request)):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing API key"},
                    headers={"WWW-Authenticate": f"Bearer realm={_REALM}"},
                )
        return await call_next(request)


# ---------------------------------------------------------------------------
# Route dependencies — scope narrowing below the authentication gate.
# ---------------------------------------------------------------------------

def _scope_factory(*allowed: str) -> Callable[[Request], str]:
    def dependency(request: Request) -> str:
        if not config.API_KEY:
            return "open"  # auth disabled deployment — every route stays usable
        role = role_of(_presented_key(request))
        if role not in allowed:
            raise HTTPException(403, f"requires role: {' or '.join(allowed)}")
        return role
    return dependency


require_operator = _scope_factory("operator")
require_approver = _scope_factory("operator", "approver")
require_admin = _scope_factory("operator", "admin")
