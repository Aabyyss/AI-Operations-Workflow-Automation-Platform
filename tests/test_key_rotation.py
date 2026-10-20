"""Key rotation: the past stays verifiable, the retired secret is gone.

Rotating a signing key is easy; the part worth testing is what rotation
must *not* do. It must not invalidate the receipts already issued (they
are evidence, and evidence does not expire because the operator rotated),
and it must not leave the retired secret anywhere that could sign again.

So the tests are written from the auditor's side of the transaction:

1. A receipt signed before the rotation still verifies afterwards, and
   says so honestly — signed by a *retired* published key.
2. A receipt signed after the rotation verifies against the *active* key,
   and no key signs both.
3. The published history contains public keys only: it is the file that
   leaves the building.
4. Pinning is real: a self-consistent receipt signed by a key that was
   never published fails, with a reason that names the key.
5. The failure modes are loud — a history that disagrees with the seed
   file, a corrupt history, an environment-managed key — because each
   silent one produces receipts that look signed and pin to nothing.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config, ed25519, receipts  # noqa: E402


def _run(client, subject="Charged twice for my subscription"):
    resp = client.post("/api/tickets", json={
        "customer_email": "a@b.com", "subject": subject,
        "body": "You charged my card twice — $49.00 on the 3rd and again on the 5th.",
    })
    assert resp.status_code == 200
    return resp.json()


def _receipt(client, subject="Charged twice"):
    run = _run(client, subject)
    return receipts.receipt_for_run(run["id"])


# ------------------------------------------------------- what rotation does
def test_old_receipts_still_verify_after_a_rotation(client):
    before = _receipt(client, "before the rotation")
    old_public = before["signature"]["public_key"]

    result = receipts.rotate_key()
    assert result["active"]["public_key"] != old_public
    assert [e["public_key"] for e in result["retired"]] == [old_public]

    after = _receipt(client, "after the rotation")
    assert after["signature"]["public_key"] == result["active"]["public_key"]

    verdict_before = receipts.verify_receipt(
        before, trusted_keys=receipts.trusted_keys_from(receipts.key_history_document()))
    assert verdict_before["ok"] is True
    assert verdict_before["checks"]["key_status"] == "retired"

    verdict_after = receipts.verify_receipt(
        after, trusted_keys=receipts.trusted_keys_from(receipts.key_history_document()))
    assert verdict_after["ok"] is True
    assert verdict_after["checks"]["key_status"] == "active"


def test_the_retired_secret_is_gone_not_parked(client):
    """Rotation must be a rotation: the old half that could sign is
    dropped, not moved to a backup file that outlives the rotation."""
    old_seed = receipts.signing_seed()
    receipts.rotate_key()
    assert receipts.signing_seed() != old_seed
    on_disk = receipts.key_path().read_text(encoding="utf-8")
    assert old_seed.hex() not in on_disk
    # …and nothing else in the data dir remembers it either.
    for path in Path(config.DATA_DIR).glob("*.json"):
        assert old_seed.hex() not in path.read_text(encoding="utf-8"), path.name


def test_a_retired_key_cannot_produce_a_new_receipt(client):
    receipts.rotate_key()
    fresh = _receipt(client)
    assert fresh["signature"]["public_key"] == receipts.public_key_hex()
    assert fresh["signature"]["public_key"] != receipts.key_history()[0]["public_key"]


def test_rotation_keeps_every_key_in_order(client):
    first = _receipt(client)
    receipts.rotate_key()
    second = _receipt(client)
    receipts.rotate_key()
    third = _receipt(client)

    doc = receipts.key_history_document()
    assert [e["status"] for e in doc["keys"]] == ["retired", "retired", "active"]
    assert doc["active_key_id"] == third["signature"]["key_id"]
    assert [e["public_key"] for e in doc["keys"]] == [
        first["signature"]["public_key"],
        second["signature"]["public_key"],
        third["signature"]["public_key"]]

    trusted = receipts.trusted_keys_from(doc)
    for receipt in (first, second, third):
        assert receipts.verify_receipt(receipt, trusted_keys=trusted)["ok"] is True


def test_the_whole_stored_chain_pins_after_a_rotation(client):
    before = _run(client, "one")
    receipts.rotate_key()
    after = _run(client, "two")
    chain = receipts.verify_chain(
        receipts.all_receipts(),
        trusted_keys=receipts.trusted_keys_from(receipts.key_history_document()))
    assert chain["ok"] is True
    assert (receipts.receipt_for_run(before["id"])["signature"]["key_id"]
            != receipts.receipt_for_run(after["id"])["signature"]["key_id"])


def test_rotation_is_recorded_in_the_audit_log(client):
    receipts.rotate_key()
    events = [e for e in receipts.storage.all("audit")
              if e["event"] == "receipt_key_rotated"]
    assert events, "a rotation that nobody can see happened is a rotation nobody audited"
    assert events[-1]["payload"]["published_keys"] == 2
    assert len(events[-1]["payload"]["retired_key_ids"]) == 1


# ------------------------------------------------------- the published half
def test_the_published_history_carries_no_secret(client):
    receipts.rotate_key()
    doc_text = receipts.key_history_path().read_text(encoding="utf-8")
    assert "seed" not in doc_text
    doc = json.loads(doc_text)
    assert doc["alg"] == "ed25519"
    assert doc["keys"][-1]["retired_at"] is None
    assert all(e["retired_at"] for e in doc["keys"][:-1])


def test_the_history_is_append_only_across_restarts(client, monkeypatch):
    receipts.rotate_key()
    entries_before = receipts.key_history()
    # Simulate a process restart: the in-memory cache is empty, the files
    # are the only truth — rotation must not re-derive or re-create them.
    monkeypatch.setattr(receipts, "_cache", {"path": None, "seed": None})
    assert receipts.key_history() == entries_before


def test_a_corrupt_history_is_loud_not_recreated(client):
    """The one quiet failure worth fearing: a corrupt (or lost) history
    file silently re-created, dropping every retired key — after which
    nobody can verify the receipts those keys signed."""
    receipts.rotate_key()
    receipts.key_history_path().write_text("{not json", encoding="utf-8")
    receipts._cache.update(path=None, seed=None)
    with pytest.raises(ValueError):
        receipts.signing_seed()


def test_a_history_that_disagrees_with_the_seed_file_refuses_to_sign(client):
    run = _run(client)
    receipt = receipts.receipt_for_run(run["id"])
    foreign = ed25519.public_key(bytes(range(32))).hex()
    doc = json.loads(receipts.key_history_path().read_text(encoding="utf-8"))
    doc["keys"].append({"key_id": foreign[:16], "public_key": foreign,
                        "created_at": "2026-01-01T00:00:00Z", "retired_at": None})
    receipts.key_history_path().write_text(json.dumps(doc), encoding="utf-8")
    receipts._cache.update(path=None, seed=None)
    with pytest.raises(ValueError, match="unpublished"):
        receipts.signing_seed()


def test_a_missing_seed_file_is_not_replaced_by_a_fresh_key(client):
    receipts.rotate_key()
    receipts.key_path().unlink()
    receipts._cache.update(path=None, seed=None)
    with pytest.raises(ValueError, match="restore that key's seed"):
        receipts.signing_seed()


def test_an_env_managed_key_has_no_local_history_and_refuses_rotation(monkeypatch):
    seed = bytes(range(32))
    monkeypatch.setattr(config, "RECEIPT_KEY", seed.hex())
    receipts._cache.update(path=None, seed=None)
    history = receipts.key_history()
    assert [e["public_key"] for e in history] == [ed25519.public_key(seed).hex()]
    with pytest.raises(ValueError, match="AIOPS_RECEIPT_KEY"):
        receipts.rotate_key()


# ------------------------------------------------------------- the pinning
def test_pinning_accepts_every_published_key_and_rejects_the_rest(client):
    first = _receipt(client)
    receipts.rotate_key()
    second = _receipt(client)
    trusted = receipts.trusted_keys_from(receipts.key_history_document())
    assert receipts.verify_receipt(first, trusted_keys=trusted)["ok"] is True
    assert receipts.verify_receipt(second, trusted_keys=trusted)["ok"] is True

    # A self-consistent receipt signed by a key that was never published:
    # valid signature, unknown signer — exactly what pinning exists to catch.
    forged = json.loads(json.dumps(second))
    forged["payload"]["decision"]["reason"] = "verified by legal"
    other = bytes(range(32))
    body = receipts.canonical_bytes(forged["payload"])
    forged["payload_sha256"] = receipts._sha256(body)
    forged["signature"] = {"alg": "ed25519",
                           "key_id": ed25519.public_key(other).hex()[:16],
                           "public_key": ed25519.public_key(other).hex(),
                           "signature": ed25519.sign(other, body).hex()}
    unpinned = receipts.verify_receipt(forged)  # self-consistent…
    assert unpinned["ok"] is True
    assert unpinned["checks"]["key_status"] == "unpublished"
    pinned = receipts.verify_receipt(forged, trusted_keys=trusted)
    assert pinned["ok"] is False
    assert "never published that key" in pinned["reason"]


def test_pinning_tolerates_whatever_shape_the_history_arrived_in():
    pub = ed25519.public_key(bytes(range(32))).hex()
    for document in ([{"public_key": pub}],                      # bare list
                     {"keys": [{"public_key": pub}]},            # API document
                     {"keys": [{"key_id": pub[:16],              # registry file
                                "public_key": pub}]}):
        trusted = receipts.trusted_keys_from(document)
        assert list(trusted) == [pub[:16]]

    for broken in ([{"public_key": "zz"}], {"keys": []}, {"keys": "nope"}, 42):
        with pytest.raises(ValueError):
            receipts.trusted_keys_from(broken)


# ---------------------------------------------------------------- endpoints
def test_the_key_history_endpoint_serves_the_publishable_view(client):
    before = _receipt(client)
    receipts.rotate_key()
    body = client.get("/api/receipts/key-history").json()
    assert body["active_key_id"] != before["signature"]["key_id"]
    assert [e["status"] for e in body["keys"]] == ["retired", "active"]
    assert "seed" not in json.dumps(body)
    # The pinning endpoint consumes exactly what this one publishes.
    trusted = receipts.trusted_keys_from(body)
    assert receipts.verify_receipt(before, trusted_keys=trusted)["checks"]["key_status"] == "retired"


def test_public_key_points_at_the_history(client):
    body = client.get("/api/receipts/public-key").json()
    assert body["key_history"] == "/api/receipts/key-history"


def test_pin_verifies_over_http_against_the_published_history(client):
    before = _receipt(client, "one")
    receipts.rotate_key()
    after = _receipt(client, "two")
    pinned = client.get("/api/receipts/verify?pin=true").json()
    assert pinned["ok"] is True
    assert pinned["pinned"] is True
    assert pinned["checked"] == 2

    post = client.post("/api/receipts/verify",
                       json={"receipt": after, "pin": True}).json()
    assert post["ok"] is True
    assert post["checks"]["key_trusted"] is True
    assert post["checks"]["key_status"] == "active"

    # Without pinning, the same receipt reports provenance but cannot fail.
    loose = client.post("/api/receipts/verify", json=after).json()
    assert loose["ok"] is True
    assert loose["pinned"] is False
    assert before["payload"]["receipt_id"]  # still in the ledger, still verifiable


def test_rotation_over_http_is_an_admin_mutation(monkeypatch):
    monkeypatch.setattr("backend.config.API_KEY", "master-key")
    monkeypatch.setattr("backend.config.APPROVER_KEY", "approver-key")
    monkeypatch.setattr("backend.config.ADMIN_KEY", "admin-key")
    from fastapi.testclient import TestClient
    from backend.main import app
    with TestClient(app) as scoped:
        assert scoped.post("/api/receipts/rotate-key").status_code == 401
        assert scoped.post("/api/receipts/rotate-key",
                           headers={"X-API-Key": "approver-key"}).status_code == 403
        ok = scoped.post("/api/receipts/rotate-key",
                         headers={"X-API-Key": "master-key"})
        assert ok.status_code == 200
        assert ok.json()["active"]["retired_at"] is None
        again = scoped.post("/api/receipts/rotate-key",
                            headers={"X-API-Key": "admin-key"})
        assert again.status_code == 200
        assert len(again.json()["retired"]) == 2
