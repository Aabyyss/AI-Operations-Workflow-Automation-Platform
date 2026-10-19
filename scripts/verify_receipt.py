#!/usr/bin/env python
"""Verify signed decision receipts — offline, with nothing but Python.

    python -m scripts.verify_receipt receipt.json
    python -m scripts.verify_receipt receipts/*.json          # a chain
    python -m scripts.verify_receipt bundle.json --require-root --json

What this proves:
  * the payload is exactly what was signed (sha256 of its canonical form),
  * the signature is valid for the public key embedded in the receipt,
  * in a multi-file run, each receipt links to the digest of the one before.

What it cannot prove, and says so rather than implying otherwise: that the
key belongs to the organisation you think it does. That is what pinning is
for — compare `public_key` against the value published at
`GET /api/receipts/public-key` through a channel you already trust.

Exit codes: 0 all verified, 1 verification failed, 2 bad input.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import receipts  # noqa: E402


def _load(path: Path) -> list[dict]:
    """One receipt, or a bundle {"receipts": [...]} — both are accepted."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("receipts"), list):
        return data["receipts"]
    if isinstance(data, list):
        return data
    return [data]


def _describe(receipt: dict) -> str:
    payload = receipt.get("payload") or {}
    decision = payload.get("decision") or {}
    chain = payload.get("chain") or {}
    return (f"  index {chain.get('index')}  "
            f"{payload.get('receipt_id', '?')}  "
            f"{payload.get('disposition', '?')}  "
            f"risk {decision.get('risk_score')}  "
            f"{payload.get('run_id', '?')}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path,
                    help="receipt JSON file(s) or a bundle; order is the chain order")
    ap.add_argument("--require-root", action="store_true",
                    help="fail unless the chain starts at index 0 (no missing history)")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable result on stdout")
    args = ap.parse_args()

    receipts_in: list[dict] = []
    for path in args.files:
        try:
            receipts_in.extend(_load(path))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: cannot read {path}: {exc}", file=sys.stderr)
            return 2

    if len(receipts_in) == 1 and not args.require_root:
        # One receipt is the common case: report its own verdict without
        # dragging in a chain question the caller did not ask.
        result = receipts.verify_receipt(receipts_in[0])
        result.setdefault("checked", 1)
        result.setdefault("linkage_complete", (receipts_in[0].get("payload", {})
                                              .get("chain") or {}).get("index") == 0)
    else:
        result = receipts.verify_chain(receipts_in, require_root=args.require_root)

    if args.json:
        print(json.dumps({"ok": result["ok"], **{k: v for k, v in result.items()
                                                 if k != "ok"}}, indent=1))
        return 0 if result["ok"] else 1

    print(f"receipts checked: {result.get('checked', len(receipts_in))}")
    for receipt in receipts_in:
        print(_describe(receipt))
    print(f"chain: {'starts at index 0' if result.get('linkage_complete') else 'starts mid-ledger'}")
    if result.get("signed_by"):
        print(f"signed by: {result['signed_by']}")
    if result["ok"]:
        print(f"OK: {result.get('reason', 'every receipt verified and linked')}")
        notes = result.get("note")
        if notes:
            print(f"note: {notes}")
        return 0
    reason = result.get("reason") or "; ".join(
        p["problem"] for p in result.get("problems") or []) or "verification failed"
    print(f"FAILED: {reason}", file=sys.stderr)
    for problem in result.get("problems") or []:
        print(f"  index {problem.get('index')}: {problem.get('problem')}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
