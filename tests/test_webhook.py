"""Tests for HMAC webhook signatures and the signed intake endpoint."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from backend import config  # noqa: E402
from backend.webhook import WebhookAuthError, sign, verify  # noqa: E402

SECRET = "whsec_test_123"


def _ticket_payload() -> bytes:
    return (b'{"customer_email":"c@x.example","subject":"Signed ticket",'
            b'"body":"Via signed webhook"}')


def test_verify_noop_when_secret_unset(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "")
    verify(_ticket_payload(), None)  # must not raise


def test_roundtrip(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", SECRET)
    header = sign(_ticket_payload(), SECRET, timestamp=int(time.time()))
    verify(_ticket_payload(), header)  # must not raise


def test_tampered_body_rejected(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", SECRET)
    header = sign(_ticket_payload(), SECRET, timestamp=int(time.time()))
    with pytest.raises(WebhookAuthError):
        verify(b'{"tampered": true}', header)


def test_wrong_secret_rejected(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", SECRET)
    header = sign(_ticket_payload(), "whsec_other", timestamp=int(time.time()))
    with pytest.raises(WebhookAuthError):
        verify(_ticket_payload(), header)


def test_replay_rejected(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", SECRET)
    old = int(time.time()) - 3600  # 1h old, skew is 300s
    header = sign(_ticket_payload(), SECRET, timestamp=old)
    with pytest.raises(WebhookAuthError):
        verify(_ticket_payload(), header)


def test_malformed_header_rejected(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", SECRET)
    for bad in ("sha256=", "not-a-signature", None):
        with pytest.raises(WebhookAuthError):
            verify(_ticket_payload(), bad)


# ---------------------------------------------------------------------------
# Endpoint-level
# ---------------------------------------------------------------------------

@pytest.fixture()
def signed_client(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", SECRET)
    from fastapi.testclient import TestClient
    from backend.main import app
    with TestClient(app) as c:
        yield c


def _headers(body: bytes) -> dict:
    return {"X-AIOPS-Signature": sign(body, SECRET)}


def test_endpoint_unsigned_rejected(signed_client):
    r = signed_client.post("/api/webhooks/tickets", content=_ticket_payload())
    assert r.status_code == 401
    assert "signature" in r.json()["detail"].lower()


def test_endpoint_signed_accepted(signed_client):
    body = _ticket_payload()
    r = signed_client.post("/api/webhooks/tickets", content=body,
                           headers=_headers(body))
    assert r.status_code == 200
    assert r.json()["disposition"] in ("auto_resolved", "auto_replied",
                                       "human_review")


def test_endpoint_unsigned_ok_when_secret_unset(client):
    r = client.post("/api/webhooks/tickets", content=_ticket_payload())
    assert r.status_code == 200
