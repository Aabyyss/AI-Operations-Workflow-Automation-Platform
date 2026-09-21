"""HMAC-SHA256 webhook signature verification — the n8n intake bridge.

When AIOPS_WEBHOOK_SECRET is set, the n8n workflow signs each POST body
with HMAC-SHA256 and sends ``X-AIOPS-Signature: sha256=<hex>``. The API
recomputes the digest over the raw bytes and compares in constant time.

Replay protection: a timestamp is mixed into the signed payload and must
fall within AIOPS_WEBHOOK_MAX_SKEW_S of now (default 300 s) — a captured
request is worthless after that window.

Unset secret = signatures accepted but not enforced, matching the auth
middleware's "secure when configured" philosophy.
"""
from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import Request

from . import config

_SCHEME = "sha256="


class WebhookAuthError(Exception):
    """Raised when a webhook signature fails verification."""


def sign(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """Produce an X-AIOPS-Signature header value (mirrors the n8n side)."""
    ts = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(secret.encode("utf-8"), digestmod=hashlib.sha256)
    mac.update(f"{ts}.".encode("utf-8"))
    mac.update(payload)
    return f"{_SCHEME}{ts}.{mac.hexdigest()}"


def verify(payload: bytes, header: str | None, secret: str | None = None) -> None:
    """Raise WebhookAuthError unless the signature checks out."""
    secret = secret if secret is not None else config.WEBHOOK_SECRET
    if not secret:
        return  # enforcement disabled
    if not header or not header.startswith(_SCHEME):
        raise WebhookAuthError("missing or malformed signature header")

    try:
        ts_raw, mac_hex = header[len(_SCHEME):].split(".", 1)
        ts = int(ts_raw)
    except ValueError as exc:
        raise WebhookAuthError("malformed signature") from exc

    skew = getattr(config, "WEBHOOK_MAX_SKEW_S", 300)
    if abs(time.time() - ts) > skew:
        raise WebhookAuthError("signature timestamp outside allowed skew")

    mac = hmac.new(secret.encode("utf-8"), digestmod=hashlib.sha256)
    mac.update(f"{ts}.".encode("utf-8"))
    mac.update(payload)
    if not hmac.compare_digest(mac.hexdigest(), mac_hex):
        raise WebhookAuthError("signature mismatch")


async def read_signed_body(request: Request) -> bytes:
    """Read the raw request body and enforce the signature header."""
    body = await request.body()
    verify(body, request.headers.get("x-aiops-signature"))
    return body
