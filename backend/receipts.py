"""Signed decision receipts — one verifiable artifact per pipeline decision.

The audit log answers "what happened" for the operator who already trusts
this deployment. A receipt answers it for someone who does not: a customer,
an auditor, a regulator, a vendor evaluation. They get a JSON file that can
be checked offline, years later, with nothing but Python — no server, no
database, no shared secret.

What is signed is the *decision*, not the whole run: disposition, risk score
with its reason, the gates that were in force at the time, what the pipeline
proposed to do, whether the quality gate passed, which policy documents the
answer was grounded in, the cost, and the mode. The customer reply is
represented by its sha256 and length, never its text — a signed artifact
that carries PII is a liability with a timestamp on it.

Receipts are also a chain: each one records the digest of the one before it
and its own index. That makes a deleted or reordered receipt visible to
anyone holding a later one, without a central log server.

    seed (32 bytes) → Ed25519 → signature over canonical(payload)
    payload_sha256  → what the next receipt points back at

The key lives in AIOPS_RECEIPT_KEY (hex) or, for a demo, is generated once
into data/receipt_signing_key.json. Rotating it invalidates old receipts
against the current public key, which is exactly why the public key travels
inside every receipt.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path
from typing import Any

from . import config, ed25519
from .integrations.audit import audit_log
from .models import PipelineResult, iso_now, new_id, redact_emails
from .store import storage

RECEIPT_VERSION = 1
SIGNATURE_ALG = "ed25519"

# The public key travels in every receipt; this id is a convenience for
# humans reading a table of receipts ("signed by 3d4017c3…").
_cache: dict[str, Any] = {"path": None, "seed": None}


# --------------------------------------------------------------------- the key
def key_path() -> Path:
    return Path(config.DATA_DIR) / "receipt_signing_key.json"


def signing_seed() -> bytes:
    """The Ed25519 seed: configured, or generated once and persisted."""
    configured = (config.RECEIPT_KEY or "").strip()
    if configured:
        try:
            seed = bytes.fromhex(configured)
        except ValueError as exc:
            raise ValueError("AIOPS_RECEIPT_KEY must be hex-encoded 32 bytes") from exc
        if len(seed) != ed25519.SEED_BYTES:
            raise ValueError("AIOPS_RECEIPT_KEY must be hex-encoded 32 bytes")
        return seed

    path = key_path()
    if _cache["path"] == str(path) and _cache["seed"]:
        return _cache["seed"]
    if path.exists():
        seed = bytes.fromhex(json.loads(path.read_text(encoding="utf-8"))["seed"])
    else:
        seed = secrets.token_bytes(ed25519.SEED_BYTES)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"seed": seed.hex(), "created_at": iso_now()},
                                   indent=1), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass  # Windows without POSIX modes: the file is per-user anyway
    _cache["path"] = str(path)
    _cache["seed"] = seed
    return seed


def public_key_hex() -> str:
    return ed25519.public_key(signing_seed()).hex()


def key_id() -> str:
    return public_key_hex()[:16]


# ------------------------------------------------------------------- canonical
def canonical_bytes(payload: dict) -> bytes:
    """One byte string for one payload — sorted keys, no whitespace.

    Both signing and verification go through this, so a receipt cannot
    verify in one implementation and fail in another because of key order.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------- payload
def gates_snapshot() -> dict:
    """The thresholds in force when this decision was made.

    A risk score of 0.31 means nothing without the limit it was compared
    against, and that limit is environment-tunable. Pinning it here is what
    lets an auditor replay the *rule*, not only read the outcome.
    """
    return {
        "auto_execute_max_risk": config.AUTO_EXECUTE_MAX_RISK,
        "block_min_risk": config.BLOCK_MIN_RISK,
        "monetary_approval_limit_usd": config.MONETARY_APPROVAL_LIMIT_USD,
        "min_auto_confidence": config.MIN_AUTO_CONFIDENCE,
        "min_retrieval_score": config.MIN_RETRIEVAL_SCORE,
    }


def build_payload(result: PipelineResult, *, receipt_id: str, issued_at: str,
                  index: int, prev: str | None) -> dict:
    decision = result.decision
    quality = result.quality
    response = result.final_response
    return {
        "receipt_version": RECEIPT_VERSION,
        "receipt_id": receipt_id,
        "issued_at": issued_at,
        "run_id": result.id,
        "ticket_id": result.ticket_id,
        "disposition": result.disposition.value,
        "decision": {
            "risk_score": decision.risk_score if decision else None,
            "confidence": decision.confidence if decision else None,
            "can_auto_resolve": decision.can_auto_resolve if decision else None,
            "needs_human_approval": decision.needs_human_approval if decision else None,
            "reason": decision.reason if decision else None,
            "proposed_actions": decision.proposed_actions if decision else [],
        },
        "quality": {
            "passed": quality.passed,
            "grounded": quality.grounded,
            "tone_ok": quality.tone_ok,
            "pii_leak": quality.pii_leak,
            "issues": quality.issues,
        } if quality else None,
        "response": {
            "sha256": _sha256(response.encode("utf-8")),
            "chars": len(response),
            "grounded_in": result.draft.grounded_in if result.draft else [],
        } if response else None,
        "retrieval": [
            {"doc_id": c.doc_id, "title": c.title, "score": c.score}
            for c in (result.knowledge.chunks if result.knowledge else [])
        ],
        "cost_usd": result.total_cost_usd,
        # Action ids carry the recipient ("mail_sent:a@b.com"). The receipt
        # says what was done, not to whom: it is a document that leaves the
        # building, so every address is redacted before signing.
        "actions_taken": [redact_emails(a) for a in result.actions_taken],
        "mode": config.MODE,
        "gates": gates_snapshot(),
        "chain": {"index": index, "prev": prev},
    }


def sign_payload(payload: dict) -> dict:
    """Wrap a payload in its signature block. The signature is over the
    canonical payload bytes; the digest is what the next receipt chains to."""
    body = canonical_bytes(payload)
    signature = ed25519.sign(signing_seed(), body)
    return {
        "payload": payload,
        "payload_sha256": _sha256(body),
        "signature": {
            "alg": SIGNATURE_ALG,
            "key_id": key_id(),
            "public_key": public_key_hex(),
            "signature": signature.hex(),
        },
    }


# ---------------------------------------------------------------------- issuing
def issue(result: PipelineResult) -> dict:
    """Create, chain, store and audit the receipt for one finished run."""
    existing = storage.all("receipts")
    index = (existing[-1]["payload"]["chain"]["index"] + 1) if existing else 0
    prev = existing[-1]["payload_sha256"] if existing else None
    payload = build_payload(result, receipt_id=new_id("rcpt"), issued_at=iso_now(),
                            index=index, prev=prev)
    receipt = sign_payload(payload)
    storage.append("receipts", receipt)
    audit_log("receipt_issued", {"receipt_id": payload["receipt_id"],
                                 "run_id": result.id, "index": index,
                                 "sha256": receipt["payload_sha256"]})
    return receipt


def receipt_for_run(run_id: str) -> dict | None:
    for receipt in reversed(storage.all("receipts")):
        if receipt.get("payload", {}).get("run_id") == run_id:
            return receipt
    return None


# ----------------------------------------------------------------- verification
def verify_receipt(receipt: dict) -> dict:
    """Check one receipt offline. Returns a verdict, never raises on bad input.

    `checks` is deliberately granular: "invalid" is not a useful thing to
    tell an auditor who is holding a receipt they believe is genuine.
    """
    checks: dict[str, Any] = {}
    if not isinstance(receipt, dict):
        return {"ok": False, "reason": "receipt is not a JSON object", "checks": checks}

    payload = receipt.get("payload")
    block = receipt.get("signature") or {}
    if not isinstance(payload, dict) or not isinstance(block, dict):
        return {"ok": False, "reason": "receipt has no payload/signature block",
                "checks": checks}

    try:
        body = canonical_bytes(payload)
    except (TypeError, ValueError) as exc:
        return {"ok": False, "reason": f"payload is not canonicalisable: {exc}",
                "checks": checks}

    actual = _sha256(body)
    checks["payload_sha256_matches"] = actual == receipt.get("payload_sha256")
    checks["payload_sha256"] = actual
    if not checks["payload_sha256_matches"]:
        return {"ok": False,
                "reason": "payload digest does not match payload_sha256 — the "
                          "payload was altered after signing",
                "checks": checks}

    if payload.get("receipt_version") != RECEIPT_VERSION:
        checks["receipt_version_supported"] = False
        return {"ok": False,
                "reason": f"receipt_version {payload.get('receipt_version')!r} is not "
                          f"supported by this verifier (expects {RECEIPT_VERSION})",
                "checks": checks}
    checks["receipt_version_supported"] = True

    if block.get("alg") != SIGNATURE_ALG:
        return {"ok": False, "reason": f"unsupported signature alg {block.get('alg')!r}",
                "checks": checks}
    try:
        public = bytes.fromhex(block.get("public_key", ""))
        signature = bytes.fromhex(block.get("signature", ""))
    except (TypeError, ValueError):
        return {"ok": False, "reason": "signature block is not valid hex",
                "checks": checks}

    checks["signature_valid"] = ed25519.verify(public, body, signature)
    if not checks["signature_valid"]:
        return {"ok": False,
                "reason": "signature does not verify for the embedded public key",
                "checks": checks}

    checks["key_id_matches"] = block.get("key_id") == public.hex()[:16]
    checks["run_id"] = payload.get("run_id")
    checks["disposition"] = payload.get("disposition")
    checks["signed_by"] = public.hex()
    return {"ok": True,
            "reason": "payload is intact and signed by the embedded key",
            "checks": checks}


def verify_chain(receipts: list[dict], *, require_root: bool = False) -> dict:
    """Check a run of receipts: each signature, and each link to the previous.

    A received bundle often starts mid-ledger (someone exported the last ten
    decisions). Its signatures and internal links can still be verified; what
    cannot be proven is that nothing came before it. That distinction is
    reported as `linkage_complete` instead of being blurred into "ok", and
    `require_root=True` turns it into a failure for callers who must have the
    whole chain.

    Returns where the first break is instead of only that one exists — an
    auditor needs the index, not a boolean.
    """
    if not receipts:
        return {"ok": False, "reason": "no receipts supplied", "checked": 0}

    problems: list[dict] = []
    prev_sha = None
    for i, receipt in enumerate(receipts):
        verdict = verify_receipt(receipt)
        if not verdict["ok"]:
            problems.append({"index": i, "problem": verdict["reason"]})
            continue
        payload = receipt["payload"]
        chain = payload.get("chain") or {}
        expected_prev = prev_sha
        # The first receipt in a bundle has no predecessor to compare with —
        # whether that is acceptable is `linkage_complete`, not a broken link.
        if i > 0 and chain.get("prev") != expected_prev:
            problems.append({
                "index": i,
                "problem": ("chain broken: prev does not match the previous receipt's "
                            f"digest ({str(chain.get('prev'))[:12]}… vs "
                            f"{str(expected_prev)[:12]}…)"),
            })
        if i and chain.get("index") != (receipts[i - 1].get("payload", {})
                                        .get("chain", {}).get("index", -99) + 1):
            problems.append({"index": i, "problem": "chain index is not sequential"})
        prev_sha = receipt["payload_sha256"]

    starts_at = (receipts[0].get("payload", {}).get("chain") or {}).get("index")
    linkage_complete = starts_at == 0
    if not linkage_complete and require_root:
        problems.append({"index": 0,
                         "problem": f"chain starts at index {starts_at}, not 0 — "
                                    f"earlier receipts would need to be supplied"})

    return {
        "ok": not problems,
        "checked": len(receipts),
        "problems": problems,
        "linkage_complete": linkage_complete,
        "starts_at_index": starts_at,
        "head_sha256": receipts[-1].get("payload_sha256"),
        "signed_by": receipts[-1].get("signature", {}).get("public_key"),
        "note": ("every signature verified" if not problems else
                 "see problems") + ("; this bundle does not include the start of "
                 "the chain, so deletion before it cannot be ruled out"
                 if not linkage_complete else ""),
    }


def all_receipts() -> list[dict]:
    return storage.all("receipts")
