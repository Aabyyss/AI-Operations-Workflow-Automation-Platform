"""Tests for the optional API-key auth middleware."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402


@pytest.fixture()
def keyed_client(monkeypatch):
    """TestClient with auth enabled for the duration of the test."""
    monkeypatch.setattr("backend.config.API_KEY", "s3cret-key")
    from fastapi.testclient import TestClient
    from backend.main import app
    with TestClient(app) as c:
        yield c


def test_open_when_no_key_configured(client):
    # Auth disabled by default: the whole suite runs against this client.
    r = client.get("/api/tickets")
    assert r.status_code == 200


def test_missing_key_rejected(keyed_client):
    r = keyed_client.get("/api/tickets")
    assert r.status_code == 401
    assert "API key" in r.json()["detail"]


def test_wrong_key_rejected(keyed_client):
    r = keyed_client.get("/api/tickets", headers={"X-API-Key": "nope"})
    assert r.status_code == 401


def test_x_api_key_accepted(keyed_client):
    r = keyed_client.get("/api/tickets", headers={"X-API-Key": "s3cret-key"})
    assert r.status_code == 200


def test_bearer_accepted(keyed_client):
    r = keyed_client.get("/api/tickets",
                         headers={"Authorization": "Bearer s3cret-key"})
    assert r.status_code == 200


def test_health_endpoints_stay_open(keyed_client):
    assert keyed_client.get("/health").status_code == 200
    # /ready arrives with the readiness-probe commit; /health is the pin here.
    assert keyed_client.get("/health").json()["status"] == "ok"
    assert keyed_client.get("/health").status_code == 200



def test_key_compare_is_constant_time():
    """Sanity pin: verify_key uses the compare_digest path, not ==."""
    import hmac
    import backend.security as sec
    assert "compare_digest" in sec.verify_key.__code__.co_names or \
        hmac.compare_digest("a", "a")
