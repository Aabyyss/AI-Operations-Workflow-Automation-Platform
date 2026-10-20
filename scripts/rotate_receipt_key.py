#!/usr/bin/env python
"""Rotate the decision-receipt signing key, keeping the past verifiable.

    python -m scripts.rotate_receipt_key
    python -m scripts.rotate_receipt_key --json
    python -m scripts.rotate_receipt_key --history-out published_keys.json

What this does:
  * retires the active key — its public half stays in the published
    history (data/receipt_keys.json), so every receipt it signed still
    verifies and reports itself as signed by a *retired* published key;
  * drops the retired *secret*: nothing can be signed with it again,
    which is what makes this a rotation rather than an addition;
  * signs everything after this point with a fresh key.

What it does not do: touch the receipts, or the chain. Every one of them
still verifies; a verifier pinning against the published history sees
"retired" instead of "active", which is the truth.

Refuses to run when AIOPS_RECEIPT_KEY is set: that key belongs to the
environment, and rotating the file would not change what signs.

Exit codes: 0 rotated, 2 refused (env-managed key, unreadable state).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import receipts  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true",
                    help="machine-readable result on stdout")
    ap.add_argument("--history-out", type=Path, metavar="PATH",
                    help="also write the publishable key history to this file "
                         "(what an auditor passes to verify_receipt --keys)")
    args = ap.parse_args()

    before = receipts.public_key_hex()
    try:
        result = receipts.rotate_key()
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    history = result["history"]
    if args.history_out:
        args.history_out.write_text(json.dumps(history, indent=1), encoding="utf-8")

    if args.json:
        print(json.dumps({"ok": True, "previous_key": before,
                          "active_key": result["active"]["public_key"],
                          "retired_keys": [e["key_id"] for e in result["retired"]],
                          "published_keys": len(history["keys"]),
                          "history_out": str(args.history_out) if args.history_out else None},
                         indent=1))
        return 0

    print(f"rotated at {result['rotated_at']}")
    print(f"  retired: {before[:16]}… ({len(result['retired'])} key(s) in history now)")
    print(f"  active:  {result['active']['public_key'][:16]}…")
    print(f"  published history: {receipts.key_history_path()}")
    print("receipts signed before this point still verify; their key status is now 'retired'.")
    if args.history_out:
        print(f"  history copy written: {args.history_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
