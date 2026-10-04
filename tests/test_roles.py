"""Tests for role-scoped API keys: operator > admin/approver, no overlap."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402


@pytest.fixture()
def scoped_client(monkeypatch):
    monkeypatch.setattr("backend.config.API_KEY", "master-key")
    monkeypatch.setattr("backend.config.APPROVER_KEY", "approver-key")
    monkeypatch.setattr("backend.config.ADMIN_KEY", "admin-key")
    from fastapi.testclient import TestClient
    from backend.main import app
    with TestClient(app) as c:
        yield c


def _key(client, key):
    return {"X-API-Key": key}


def test_master_key_still_full_access(scoped_client):
    assert scoped_client.get("/api/tickets", headers=_key(scoped_client, "master-key")).status_code == 200


def test_missing_key_rejected_as_before(scoped_client):
    assert scoped_client.get("/api/tickets").status_code == 401


def test_unknown_key_rejected(scoped_client):
    r = scoped_client.get("/api/tickets", headers=_key(scoped_client, "wrong"))
    assert r.status_code == 401


def test_approver_reads_everything(scoped_client):
    for path in ("/api/tickets", "/api/runs", "/api/reviews", "/api/analytics/summary"):
        r = scoped_client.get(path, headers=_key(scoped_client, "approver-key"))
        assert r.status_code == 200, path


def test_approver_decides_review(scoped_client):
    """An approver key can do the one thing it exists for: decide a review."""
    t = {"customer_email": "a@b.com", "subject": "Charged twice",
         "body": "Refund $600 for the duplicate charge on my card."}
    scoped_client.post("/api/tickets", json=t, headers=_key(scoped_client, "master-key"))
    review = next(r for r in scoped_client.get("/api/reviews", headers=_key(scoped_client, "master-key")).json()
                  if r["status"] == "pending")
    r = scoped_client.post(f"/api/reviews/{review['id']}/decision",
                           json={"reviewer": "amy", "note": "APPROVE"},
                           headers=_key(scoped_client, "approver-key"))
    assert r.status_code == 200


def test_admin_cannot_approve_refunds(scoped_client):
    """Role separation on purpose: an admin key cannot mint approvals."""
    t = {"customer_email": "a@b.com", "subject": "Charged twice",
         "body": "Refund $600 for the duplicate charge on my card."}
    scoped_client.post("/api/tickets", json=t, headers=_key(scoped_client, "master-key"))
    review = next(r for r in scoped_client.get("/api/reviews", headers=_key(scoped_client, "master-key")).json()
                  if r["status"] == "pending")
    r = scoped_client.post(f"/api/reviews/{review['id']}/decision",
                           json={"reviewer": "root", "note": "APPROVE"},
                           headers=_key(scoped_client, "admin-key"))
    assert r.status_code == 403
    assert "requires role" in r.json()["detail"]


def test_approver_cannot_prune(scoped_client):
    r = scoped_client.post("/api/admin/prune", headers=_key(scoped_client, "approver-key"))
    assert r.status_code == 403


def test_admin_runs_prune_dry_run(scoped_client):
    r = scoped_client.post("/api/admin/prune", headers=_key(scoped_client, "admin-key"))
    assert r.status_code == 200
    assert r.json()["mode"] == "dry-run"


def test_unscoped_write_endpoints_accept_any_valid_key(scoped_client):
    """Deliberate v1 posture: role dependencies guard the sensitive routes;
    the wide-open write surface (tickets) stays usable for automation keys."""
    t = {"customer_email": "a@b.com", "subject": "Charged twice",
         "body": "You charged my card twice for $49, please refund one."}
    r = scoped_client.post("/api/tickets", json=t,
                           headers=_key(scoped_client, "approver-key"))
    assert r.status_code == 200


def test_role_keys_ignored_when_master_unset(monkeypatch, client):
    """No master key = auth disabled entirely; scoped keys grant nothing extra."""
    monkeypatch.setattr("backend.config.API_KEY", "")
    monkeypatch.setattr("backend.config.APPROVER_KEY", "approver-key")
    from backend.security import role_of
    assert role_of("approver-key") is None
