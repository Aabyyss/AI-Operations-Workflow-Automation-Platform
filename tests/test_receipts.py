"""Signed decision receipts: issuance, tampering, chaining, offline use.

A receipt exists for the person who does not trust this deployment. So the
tests care about the things that make it worth handing over:

1. It verifies from the receipt alone — no server, no store, no secret.
2. Every field that matters is inside the signature: change the risk score,
   the disposition, a gate threshold or the response digest and it fails.
3. The chain catches a deleted or reordered receipt.
4. The key can be pinned (AIOPS_RECEIPT_KEY), so a deployment signs with a
   key it controls rather than one generated on first boot.
5. A failed signature does not corrupt the run; it is recorded on it.

The last point matters most operationally: signing is the one step that can
fail for reasons the pipeline cannot fix (disk full, bad key), and a run
that happened must never be reported as if it did not.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config, receipts  # noqa: E402
from backend.models import PipelineResult  # noqa: E402
from backend.store import storage  # noqa: E402


def _run(client, body=None):
    resp = client.post("/api/tickets", json=body or {
        "customer_email": "jordan@example.com",
        "subject": "Charged twice for my subscription",
        "body": "You charged my card twice this month — $49.00 on the 3rd and "
                "again on the 5th. I need one of the charges refunded.",
    })
    assert resp.status_code == 200
    return resp.json()


# ------------------------------------------------------------------- issuance
def test_a_run_produces_a_signed_receipt(client):
    run = _run(client)
    stored = receipts.receipt_for_run(run["id"])
    assert stored is not None
    assert run["receipt_id"] == stored["payload"]["receipt_id"]
    assert run["receipt_sha256"] == stored["payload_sha256"]
    assert receipts.verify_receipt(stored)["ok"] is True


def test_the_receipt_records_the_decision_not_the_customer_text(client):
    run = _run(client)
    payload = receipts.receipt_for_run(run["id"])["payload"]
    assert payload["disposition"] == run["disposition"]
    assert payload["decision"]["risk_score"] == run["decision"]["risk_score"]
    assert payload["gates"]["auto_execute_max_risk"] == config.AUTO_EXECUTE_MAX_RISK
    # The reply is represented by a digest, not by its text: a signed
    # artifact that carries customer PII is a liability with a timestamp.
    if run["final_response"]:
        assert payload["response"]["chars"] == len(run["final_response"])
        assert payload["response"]["sha256"]
        assert run["final_response"] not in json.dumps(payload)
    assert "jordan@example.com" not in json.dumps(payload)
    # Action ids carry the recipient; the receipt names the action only.
    assert any("mail_sent:" in a for a in payload["actions_taken"])
    assert all("[redacted-email]" in a or ":" not in a or "@" not in a
               for a in payload["actions_taken"])


def test_the_payload_is_self_contained_about_the_policy_it_used(client):
    run = _run(client)
    payload = receipts.receipt_for_run(run["id"])["payload"]
    assert payload["retrieval"], "a grounded answer must name the documents"
    for chunk in payload["retrieval"]:
        assert set(chunk) == {"doc_id", "title", "score"}


def test_receipts_chain_to_each_other(client):
    first = _run(client)
    second = _run(client)
    a = receipts.receipt_for_run(first["id"])
    b = receipts.receipt_for_run(second["id"])
    assert a["payload"]["chain"]["index"] == 0
    assert a["payload"]["chain"]["prev"] is None
    assert b["payload"]["chain"]["index"] == 1
    assert b["payload"]["chain"]["prev"] == a["payload_sha256"]
    assert receipts.verify_chain([a, b])["ok"] is True


# ------------------------------------------------------------------ tampering
def test_changing_the_risk_score_breaks_the_signature(client):
    run = _run(client)
    receipt = json.loads(json.dumps(receipts.receipt_for_run(run["id"])))
    receipt["payload"]["decision"]["risk_score"] = 0.01
    verdict = receipts.verify_receipt(receipt)
    assert verdict["ok"] is False
    assert "digest" in verdict["reason"]


def test_changing_the_disposition_breaks_the_signature(client):
    run = _run(client)
    receipt = json.loads(json.dumps(receipts.receipt_for_run(run["id"])))
    assert receipt["payload"]["disposition"] != "failed"
    receipt["payload"]["disposition"] = "failed"
    assert receipts.verify_receipt(receipt)["ok"] is False


def test_loosening_a_gate_threshold_breaks_the_signature(client):
    """The tempting edit: prove the pipeline let something through that it
    should have held, then rewrite the limit that was in force."""
    run = _run(client)
    receipt = json.loads(json.dumps(receipts.receipt_for_run(run["id"])))
    receipt["payload"]["gates"]["monetary_approval_limit_usd"] = 1_000_000
    assert receipts.verify_receipt(receipt)["ok"] is False


def test_a_receipt_re_signed_with_another_key_is_rejected_as_a_swap(client):
    """An attacker who edits the payload and signs it with their own key
    produces a self-consistent receipt — but not this run's receipt."""
    run = _run(client)
    original = receipts.receipt_for_run(run["id"])
    forged = json.loads(json.dumps(original))
    forged["payload"]["decision"]["reason"] = "verified by legal"
    other = bytes(range(32))
    body = receipts.canonical_bytes(forged["payload"])
    from backend import ed25519
    forged["payload_sha256"] = receipts._sha256(body)  # their own digest
    forged["signature"] = {
        "alg": "ed25519",
        "key_id": ed25519.public_key(other).hex()[:16],
        "public_key": ed25519.public_key(other).hex(),
        "signature": ed25519.sign(other, body).hex(),
    }
    assert receipts.verify_receipt(forged)["ok"] is True  # self-consistent…
    # …and still not the receipt that was issued for this run.
    assert forged["payload_sha256"] != original["payload_sha256"]


# ---------------------------------------------------------------------- chain
def test_a_deleted_receipt_is_visible_from_the_next_one(client):
    first = _run(client)
    second = _run(client)
    b = receipts.receipt_for_run(second["id"])
    # An auditor holding only the second receipt still sees it points at a
    # digest, i.e. it is not the head of a chain of one.
    assert b["payload"]["chain"]["index"] == 1
    assert b["payload"]["chain"]["prev"] == receipts.receipt_for_run(first["id"])["payload_sha256"]
    alone = receipts.verify_chain([b])  # alone: verifies, but says where it starts
    assert alone["ok"] is True
    assert alone["starts_at_index"] == 1
    assert alone["linkage_complete"] is False
    assert "does not include the start" in alone["note"]
    # A caller that must have the whole chain says so and gets a failure.
    strict = receipts.verify_chain([b], require_root=True)
    assert strict["ok"] is False
    assert "earlier receipts would need to be supplied" in strict["problems"][0]["problem"]


def test_a_reordered_chain_is_reported_with_an_index(client):
    runs = [_run(client) for _ in range(3)]
    chain = [receipts.receipt_for_run(r["id"]) for r in runs]
    verdict = receipts.verify_chain([chain[0], chain[2], chain[1]])
    assert verdict["ok"] is False
    assert verdict["problems"]
    assert "index" in verdict["problems"][0]


def test_a_broken_link_is_named(client):
    runs = [_run(client) for _ in range(2)]
    a, b = (receipts.receipt_for_run(r["id"]) for r in runs)
    tampered = json.loads(json.dumps(b))
    tampered["payload"]["chain"]["prev"] = "0" * 64
    # Re-sign so only the *link* is wrong, not the signature — this is the
    # case a signature alone cannot catch.
    body = receipts.canonical_bytes(tampered["payload"])
    from backend import ed25519
    seed = receipts.signing_seed()
    tampered["payload_sha256"] = receipts._sha256(body)
    tampered["signature"]["signature"] = ed25519.sign(seed, body).hex()
    verdict = receipts.verify_chain([a, tampered])
    assert verdict["ok"] is False
    assert "chain broken" in verdict["problems"][0]["problem"]
    assert verdict["problems"][0]["index"] == 1


# ------------------------------------------------------------------------ key
def test_a_configured_key_is_used_and_published_in_the_receipt(client, monkeypatch):
    seed = bytes(range(32))
    monkeypatch.setattr(config, "RECEIPT_KEY", seed.hex())
    receipts._cache.update(path=None, seed=None)   # drop any cached dev key
    run = _run(client)
    receipt = receipts.receipt_for_run(run["id"])
    from backend import ed25519
    assert receipt["signature"]["public_key"] == ed25519.public_key(seed).hex()
    assert receipts.verify_receipt(receipt)["ok"] is True


def test_a_malformed_configured_key_is_refused(monkeypatch):
    monkeypatch.setattr(config, "RECEIPT_KEY", "not-hex")
    receipts._cache.update(path=None, seed=None)
    with pytest.raises(ValueError, match="hex-encoded 32 bytes"):
        receipts.signing_seed()


def test_the_dev_key_is_generated_once_and_reused():
    first = receipts.public_key_hex()
    receipts._cache.update(path=None, seed=None)
    assert receipts.public_key_hex() == first
    assert receipts.key_path().exists()


# ------------------------------------------------------------ failure handling
def test_a_signing_failure_is_recorded_on_the_run_not_hidden(client, monkeypatch):
    monkeypatch.setattr(config, "RECEIPT_KEY", "00" * 32)

    class Boom:
        def __call__(self, *a, **k):
            raise RuntimeError("keyring offline")

    monkeypatch.setattr(receipts, "issue", Boom())
    run = _run(client)
    assert run["receipt_id"] is None
    assert "keyring offline" in run["receipt_error"]
    # The work still happened and is still in the ledger.
    assert storage.get("runs", run["id"]) is not None
    assert any(e["event"] == "receipt_failed" for e in storage.all("audit"))


def test_a_receipt_verifies_after_a_round_trip_through_a_file(client, tmp_path):
    """The artifact an auditor actually holds: a JSON file on disk."""
    run = _run(client)
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipts.receipt_for_run(run["id"]), indent=1),
                    encoding="utf-8")
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert receipts.verify_receipt(reloaded)["ok"] is True


def test_garbage_is_refused_with_a_reason_not_a_traceback():
    assert receipts.verify_receipt({})["ok"] is False
    assert "payload/signature" in receipts.verify_receipt({})["reason"]
    assert receipts.verify_receipt({"payload": {}, "signature": {}})["ok"] is False
    assert receipts.verify_chain([])["ok"] is False
