"""Opt-in rate limiting — per client IP, sliding window.

Off by default (AIOPS_RATE_LIMIT=0): a single-tenant deployment behind n8n
doesn't need it, and a wrong default here would 429 the demo. When enabled,
each client IP gets RATE_LIMIT requests per RATE_LIMIT_WINDOW_S; health
endpoints are exempt so probes can't be locked out.

The sliding window uses a deque of monotonic timestamps per IP and prunes
expired entries on every hit — O(window), no background sweeper needed.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import config

OPEN_PATHS = {"/health", "/ready"}


class SlidingWindow:
    """One IP's hit timestamps (monotonic seconds)."""

    def __init__(self) -> None:
        self.hits: deque[float] = deque()

    def allow(self, limit: int, window_s: float) -> tuple[bool, float]:
        """Return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        while self.hits and now - self.hits[0] >= window_s:
            self.hits.popleft()
        if len(self.hits) >= limit:
            retry_after = window_s - (now - self.hits[0])
            return False, max(0.0, round(retry_after, 1))
        self.hits.append(now)
        return True, 0.0


class RateLimiter:
    def __init__(self) -> None:
        self._windows: dict[str, SlidingWindow] = defaultdict(SlidingWindow)

    def allow(self, ip: str) -> tuple[bool, float]:
        return self._windows[ip].allow(config.RATE_LIMIT, config.RATE_LIMIT_WINDOW_S)


def client_ip(request: Request) -> str:
    """Best-effort client identity. Honors X-Forwarded-For when present
    (deployments behind a reverse proxy); falls back to the socket peer."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def install_rate_limit(app: FastAPI) -> None:
    """Attach limiting. No-op unless AIOPS_RATE_LIMIT > 0."""

    limiter = RateLimiter()

    @app.middleware("http")
    async def rate_limit_gate(request: Request, call_next):
        if config.RATE_LIMIT > 0 and request.url.path not in OPEN_PATHS:
            allowed, retry_after = limiter.allow(client_ip(request))
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded"},
                    headers={"Retry-After": str(int(retry_after) + 1),
                             "X-RateLimit-Limit": str(config.RATE_LIMIT)},
                )
        return await call_next(request)
