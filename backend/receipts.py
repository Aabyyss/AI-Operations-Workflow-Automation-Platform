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
into data/receipt_signing_key.json. Rotation (rotate_key, or `python -m
scripts.rotate_receipt_key`) drops the old *secret* — nothing can be signed
with it again — and keeps the old *public* keys in the published history
(data/receipt_keys.json), so every receipt ever issued still verifies and
says whether its key is the active one or a retired one. That history is
what `--keys` pins against: without it a receipt is only self-consistent,
with it a receipt signed by a key this deployment never published fails.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
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

KEY_HISTORY_VERSION = 1


# --------------------------------------------------------------------- the key
def key_path() -> Path:
    """The *signing* half: the active seed. Secret — and rotation drops the
    old one, so a retired key cannot sign anything ever again."""
    return Path(config.DATA_DIR) / "receipt_signing_key.json"


def key_history_path() -> Path:
    """The *published* half: public keys only, oldest first, active last.

    Deliberately a different file from the seed: this one is meant to leave
    the deployment (served over HTTP, dropped next to a receipt file), so it
    must never contain a secret.
    """
    return Path(config.DATA_DIR) / "receipt_keys.json"


def _entry(seed: bytes, *, created_at: str | None,
           retired_at: str | None = None) -> dict:
    pub = ed25519.public_key(seed).hex()
    return {"key_id": pub[:16], "public_key": pub,
            "created_at": created_at, "retired_at": retired_at}


def _read_history_file() -> list[dict]:
    """Entries from disk; [] when there is no history yet.

    A corrupt history raises rather than returning []: silently re-creating
    it would drop every retired key, which is the one thing that must never
    happen quietly — old receipts would stop verifying.
    """
    path = key_history_path()
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    keys = doc.get("keys") if isinstance(doc, dict) else doc
    if not isinstance(keys, list):
        raise ValueError(f"{path.name} does not contain a key history")
    return keys


def _write_history(entries: list[dict]) -> None:
    path = key_history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"version": KEY_HISTORY_VERSION, "alg": SIGNATURE_ALG, "keys": entries}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    tmp.replace(path)


def _write_seed(seed: bytes, created_at: str | None) -> None:
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"seed": seed.hex(), "created_at": created_at},
                              indent=1), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass  # Windows without POSIX modes: the file is per-user anyway
    tmp.replace(path)


def published_history() -> list[dict] | None:
    """The history this deployment has already published — or None.

    Never creates anything: verification is a read path, and the auditor's
    copy of this code must not conjure a key file into existence while they
    are checking a receipt they were handed.
    """
    configured = (config.RECEIPT_KEY or "").strip()
    if configured:
        try:
            seed = bytes.fromhex(configured)
        except ValueError:
            return None
        if len(seed) != ed25519.SEED_BYTES:
            return None
        return [_entry(seed, created_at=None)]
    try:
        entries = _read_history_file()
        if entries:
            return entries
        path = key_path()
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            seed = bytes.fromhex(raw["seed"])
            return [_entry(seed, created_at=raw.get("created_at"))]
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    return None


def key_history() -> list[dict]:
    """Every key this deployment has signed with, oldest first.

    Unlike published_history this *will* create the first entry (a fresh
    deployment has nothing published yet), which is what rotation and the
    publish endpoints need.
    """
    existing = published_history()
    if existing is not None:
        return existing
    seed = signing_seed()
    return [_entry(seed, created_at=iso_now())]


def key_history_document() -> dict:
    """The publishable view: what `GET /api/receipts/key-history` serves and
    what an auditor drops in front of the verifier as `--keys`."""
    entries = [dict(e, status="retired" if e.get("retired_at") else "active")
               for e in key_history()]
    return {"version": KEY_HISTORY_VERSION, "alg": SIGNATURE_ALG,
            "active_key_id": entries[-1]["key_id"] if entries else None,
            "keys": entries}


def signing_seed() -> bytes:
    """The Ed25519 seed of the *active* key: configured, or generated once
    and persisted — and always consistent with the published history.

    The consistency check is the point of the pair of files: signing with a
    key that was never published would produce receipts that fail every
    pinning check, which is worse than refusing to sign.
    """
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

    history = _read_history_file()
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        seed = bytes.fromhex(raw["seed"])
        created_at = raw.get("created_at")
    elif history:
        raise ValueError(
            f"{path.name} is missing but {key_history_path().name} records active "
            f"key {history[-1].get('key_id')} — restore that key's seed file "
            f"before signing (refusing to sign with a fresh unpublished key)")
    else:
        seed = secrets.token_bytes(ed25519.SEED_BYTES)
        created_at = iso_now()
        _write_seed(seed, created_at)

    pub = ed25519.public_key(seed).hex()
    if history and history[-1].get("public_key") != pub:
        raise ValueError(
            f"{key_history_path().name} says the active key is "
            f"{str(history[-1].get('key_id'))}… but {path.name} holds the seed for "
            f"{pub[:16]}… — restore the matching seed file or republish the "
            f"history (refusing to sign with an unpublished key)")
    if not history:
        _write_history([_entry(seed, created_at=created_at)])

    _cache["path"] = str(path)
    _cache["seed"] = seed
    return seed


def key_age_days(now: datetime | None = None) -> float | None:
    """How long the active key has been serving, or None when that is unknowable.

    None covers the two honest answers: an `AIOPS_RECEIPT_KEY` this process was
    handed (no creation date to read) and a deployment that has never signed
    (no history yet). Neither is zero — reporting 0 would say "freshly rotated"
    about a key nobody can date.

    Reads the *published* history, which never creates a file: this is called
    from /api/health, where writing a key would be a side effect of asking.
    """
    try:
        entries = published_history()
    except OSError:
        return None
    if not entries or not entries[-1].get("created_at"):
        return None
    created = _parse_ts(entries[-1]["created_at"])
    if created is None:
        return None
    moment = now or datetime.now(timezone.utc)
    return round((moment - created).total_seconds() / 86400.0, 2)


def _parse_ts(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def public_key_hex() -> str:
    return ed25519.public_key(signing_seed()).hex()


def key_id() -> str:
    return public_key_hex()[:16]


def rotate_key() -> dict:
    """Retire the active key; start signing with a new one.

    What is dropped is the old *secret* — after this returns, nothing can
    be signed with the retired key, which is what makes rotation a rotation
    rather than an addition. What is kept is its *public* half, in the
    published history, so every receipt it ever signed still verifies and
    is reported as signed by a retired key.

    Both files are written to temp files and moved into place, so a crash
    mid-rotation cannot leave the seed file and the history disagreeing
    about who is active (signing_seed refuses to run in that state).
    """
    if (config.RECEIPT_KEY or "").strip():
        raise ValueError(
            "AIOPS_RECEIPT_KEY is set — this deployment's key comes from the "
            "environment; unset it to rotate the file-based key")
    signing_seed()  # ensures both files exist and agree
    history = key_history()
    now = iso_now()

    # Everything currently active becomes retired, in place (order is kept:
    # the history is append-only, oldest first).
    entries = [dict(e, retired_at=e.get("retired_at") or now) for e in history]
    new_seed = secrets.token_bytes(ed25519.SEED_BYTES)
    entries.append(_entry(new_seed, created_at=now))

    _write_history(entries)
    _write_seed(new_seed, now)
    _cache["path"] = None
    _cache["seed"] = None

    result = {"rotated_at": now,
              "active": entries[-1],
              "retired": [e for e in entries if e.get("retired_at")],
              "history": {"version": KEY_HISTORY_VERSION, "alg": SIGNATURE_ALG,
                          "keys": [dict(e, status="retired" if e.get("retired_at")
                                        else "active") for e in entries]}}
    audit_log("receipt_key_rotated", {
        "active_key_id": entries[-1]["key_id"],
        "retired_key_ids": [e["key_id"] for e in result["retired"]],
        "published_keys": len(entries)})
    return result


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
def trusted_keys_from(document: dict | list) -> dict:
    """The pinning map, built from a published history.

    Accepts the document the API serves ({"keys": [...]}), the raw registry
    file, or a bare list of entries — an auditor should not have to know
    which shape they were handed. Raises ValueError on anything that is not
    a usable history; callers turn that into "bad input", not "failed".
    """
    entries = document.get("keys") if isinstance(document, dict) else document
    if not isinstance(entries, list):
        raise ValueError("key history must be a list of keys (or {\"keys\": [...]})")
    trusted: dict[str, dict] = {}
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict) or not entry.get("public_key"):
            raise ValueError(f"key history entry {i} has no public_key")
        try:
            public = bytes.fromhex(str(entry["public_key"]))
        except ValueError as exc:
            raise ValueError(f"key history entry {i} public_key is not hex") from exc
        if len(public) != ed25519.SEED_BYTES:
            raise ValueError(f"key history entry {i} public_key is not 32 bytes")
        normalised = dict(entry)
        normalised.setdefault("key_id", public.hex()[:16])
        trusted[normalised["key_id"]] = normalised
    if not trusted:
        raise ValueError("key history contains no keys")
    return trusted


def _key_status(public_hex: str,
                entries: list[dict] | None) -> tuple[str, dict | None]:
    """(status, entry) for a signing key against a history: active, retired
    or unpublished. No history at all is "unpublished", never a pass."""
    if not entries:
        return "unpublished", None
    for entry in entries:
        if ((str(entry.get("public_key") or "").lower() == public_hex)
                or entry.get("key_id") == public_hex[:16]):
            return ("retired" if entry.get("retired_at") else "active"), entry
    return "unpublished", None


def _local_history_safe() -> list[dict] | None:
    """The published history, best-effort: a verification must never raise
    (or create files) because the local key state is unreadable."""
    try:
        return published_history()
    except OSError:
        return None


def verify_receipt(receipt: dict, *,
                   trusted_keys: dict[str, dict] | None = None) -> dict:
    """Check one receipt offline. Returns a verdict, never raises on bad input.

    `checks` is deliberately granular: "invalid" is not a useful thing to
    tell an auditor who is holding a receipt they believe is genuine.

    `trusted_keys` (see trusted_keys_from) is what turns "this file is
    self-consistent" into "this file was signed by a key that deployment
    published": with it, a signature by any other key fails. Without it the
    key's provenance is still reported — active, retired, unpublished — but
    cannot fail the verdict, because a receipt dropped on its own carries
    no history with it.
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

    public_hex = public.hex()
    checks["key_id_matches"] = block.get("key_id") == public_hex[:16]

    history = (trusted_keys.values() if trusted_keys is not None
               else _local_history_safe())
    status, _entry_matched = _key_status(public_hex, list(history) if history else None)
    checks["key_trusted"] = trusted_keys is not None
    checks["key_status"] = status  # active | retired | unpublished
    if trusted_keys is not None and status == "unpublished":
        return {"ok": False,
                "reason": f"signed by key {public_hex[:16]}…, which is not in the "
                          f"published key history — this deployment never "
                          f"published that key",
                "checks": checks}

    checks["run_id"] = payload.get("run_id")
    checks["disposition"] = payload.get("disposition")
    checks["signed_by"] = public_hex
    provenance = {"active": "the active published key",
                  "retired": "a retired published key — rotated out, still in "
                             "the key history, so this receipt still verifies",
                  "unpublished": "the embedded key (no published history to "
                                 "compare it against)"}[status]
    return {"ok": True,
            "reason": f"payload is intact and signed by {provenance}",
            "checks": checks}


def verify_chain(receipts: list[dict], *, require_root: bool = False,
                 trusted_keys: dict[str, dict] | None = None) -> dict:
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
        verdict = verify_receipt(receipt, trusted_keys=trusted_keys)
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
