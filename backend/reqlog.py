"""Structured request logging — the feed log shippers and SIEMs want.

Off by default: uvicorn's access log covers local dev. Set
AIOPS_LOG_FORMAT=json and every request emits one machine-parseable JSON
line to stdout: request id (propagated from X-Request-ID when a caller
sends one), method, path, status, duration, client.

Set AIOPS_REQUEST_LOG_FILE too and the same line is appended to that file.
stdout alone is not durable — the container's stream leaves with the
container — and a shipper pointed at a file is the common setup.

A log sink is never allowed to fail a request: if the file cannot be
opened or written, the record still goes to stdout, one warning says why,
and the middleware carries on.

Registered last so it *wraps* the auth and rate-limit middlewares — a 401
or 429 is an http_request log line too, which is exactly what you want
when investigating an incident.
"""
from __future__ import annotations

import json
import time
import uuid

from fastapi import FastAPI, Request

from . import config
from .models import iso_now
from .ratelimit import client_ip

NOISY_PATHS = {"/health", "/ready"}

# A file sink that failed once says so once, then stops: a request path is
# not where a repeating warning belongs, and the failure is usually sticky.
_file_warning_shown = False


def _emit(record: dict) -> None:
    line = json.dumps(record, separators=(",", ":"))
    print(line, flush=True)
    path = config.REQUEST_LOG_FILE
    if not path:
        return
    global _file_warning_shown
    try:
        # Append, one open per record: cheap at this request rate, and it
        # keeps working when the file is rotated or truncated underneath us.
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        if not _file_warning_shown:
            _file_warning_shown = True
            print(f"request log file unavailable ({exc}); continuing with "
                  f"stdout only", flush=True)


def install_request_logging(app: FastAPI) -> None:
    """Attach the logger. Inert unless AIOPS_LOG_FORMAT=json."""

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        start = time.perf_counter()
        response = await call_next(request)
        if config.LOG_FORMAT == "json" and request.url.path not in NOISY_PATHS:
            _emit({
                "ts": iso_now(),
                "event": "http_request",
                "request_id": rid,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                "client": client_ip(request),
            })
        response.headers["X-Request-ID"] = rid
        return response
