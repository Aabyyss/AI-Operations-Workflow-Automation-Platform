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

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import config

# Routes that must stay reachable without a key (liveness/readiness probes).
OPEN_PATHS = {"/health", "/ready"}

_REALM = "api-key"


def verify_key(presented: str | None) -> bool:
    """Constant-time check of a presented key against the configured one."""
    expected = config.API_KEY
    if not expected:
        return True  # auth disabled
    if not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


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
    async def api_key_gate(request: Request, call_next):
        if config.API_KEY and request.url.path not in OPEN_PATHS:
            if not verify_key(_presented_key(request)):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing API key"},
                    headers={"WWW-Authenticate": f"Bearer realm={_REALM}"},
                )
        return await call_next(request)
