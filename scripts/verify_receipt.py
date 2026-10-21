#!/usr/bin/env python
"""Verify signed decision receipts — offline, with nothing but Python.

    python -m scripts.verify_receipt receipt.json
    python -m scripts.verify_receipt receipts/*.json          # a chain
    python -m scripts.verify_receipt bundle.json --require-root --json
    python -m scripts.verify_receipt receipt.json --keys published_keys.json
    python -m scripts.verify_receipt exports/                   # a folder, in chain order

What this proves:
  * the payload is exactly what was signed (sha256 of its canonical form),
  * the signature is valid for the public key embedded in the receipt,
  * in a multi-file run, each receipt links to the digest of the one before.

What it cannot prove on its own, and says so rather than implying otherwise:
that the key belongs to the organisation you think it does. A receipt embeds
its own public key, so a receipt *anyone* signed verifies. `--keys` closes
that gap: pass the published key history (`GET /api/receipts/key-history`,
or data/receipt_keys.json straight from the deployment) and a receipt
signed by a key outside it fails, named. Without `--keys` the verdict still
reports the key's status — active, retired, unpublished — as information.

Exit codes: 0 all verified, 1 verification failed, 2 bad input.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.console import use_utf8  # noqa: E402
from backend import receipts  # noqa: E402


def _load(path: Path) -> list[dict]:
    """One receipt, or a bundle {"receipts": [...]} — both are accepted."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("receipts"), list):
        return data["receipts"]
    if isinstance(data, list):
        return data
    return [data]


def _chain_index(receipt: dict) -> int:
    index = ((receipt.get("payload") or {}).get("chain") or {}).get("index")
    return index if isinstance(index, int) else 10 ** 9


def _from_directory(folder: Path) -> list[dict]:
    """Every receipt in a folder, ordered by chain index — not by filename.

    Exported receipts are named after their run, so a shell glob orders them
    arbitrarily and a perfectly good chain then reports a broken link. Files
    named on the command line keep the caller's order on purpose: reordering
    them by hand is one of the tampering cases the verifier is supposed to
    catch.
    """
    collected: list[dict] = []
    for path in sorted(folder.glob("*.json")):
        collected.extend(_load(path))
    return sorted(collected, key=_chain_index)


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
    use_utf8()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path,
                    help="receipt JSON file(s) or a bundle; order is the chain order")
    ap.add_argument("--require-root", action="store_true",
                    help="fail unless the chain starts at index 0 (no missing history)")
    ap.add_argument("--keys", type=Path, metavar="PATH",
                    help="published key history (GET /api/receipts/key-history or "
                         "data/receipt_keys.json); a receipt signed by a key not "
                         "in it fails")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable result on stdout")
    args = ap.parse_args()

    trusted = None
    if args.keys:
        try:
            trusted = receipts.trusted_keys_from(
                json.loads(args.keys.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: cannot read key history {args.keys}: {exc}", file=sys.stderr)
            return 2
        except ValueError as exc:
            print(f"error: {args.keys} is not a usable key history: {exc}",
                  file=sys.stderr)
            return 2

    receipts_in: list[dict] = []
    directories = [p for p in args.files if p.is_dir()]
    for path in args.files:
        try:
            receipts_in.extend(_from_directory(path) if path.is_dir()
                               else _load(path))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: cannot read {path}: {exc}", file=sys.stderr)
            return 2
    if directories and not receipts_in:
        print(f"error: no receipt JSON files in {', '.join(str(d) for d in directories)}",
              file=sys.stderr)
        return 2

    if len(receipts_in) == 1 and not args.require_root:
        # One receipt is the common case: report its own verdict without
        # dragging in a chain question the caller did not ask.
        result = receipts.verify_receipt(receipts_in[0], trusted_keys=trusted)
        result.setdefault("checked", 1)
        result.setdefault("linkage_complete", (receipts_in[0].get("payload", {})
                                              .get("chain") or {}).get("index") == 0)
    else:
        result = receipts.verify_chain(receipts_in, require_root=args.require_root,
                                       trusted_keys=trusted)

    if args.json:
        print(json.dumps({"ok": result["ok"], **{k: v for k, v in result.items()
                                                 if k != "ok"}}, indent=1))
        return 0 if result["ok"] else 1

    if directories:
        print(f"read {len(receipts_in)} receipt(s) from "
              f"{', '.join(str(d) for d in directories)}, ordered by chain index")
    print(f"receipts checked: {result.get('checked', len(receipts_in))}")
    for receipt in receipts_in:
        print(_describe(receipt))
    print(f"chain: {'starts at index 0' if result.get('linkage_complete') else 'starts mid-ledger'}")
    if result.get("signed_by"):
        print(f"signed by: {result['signed_by']}")
    if len(result.get("keys_used") or []) > 1:
        print(f"keys: {len(result['keys_used'])} distinct signing keys in this "
              f"bundle, {result['rotations_spanned']} rotation(s) spanned")
    if trusted is not None:
        print(f"keys: pinned against {len(trusted)} published "
              f"{'key' if len(trusted) == 1 else 'keys'} "
              f"({', '.join(sorted(trusted))})")
    elif result.get("checks", {}).get("key_status"):
        print(f"key: {result['checks']['key_status']} (no --keys given, so this is "
              f"the receipt's own claim about its signer)")
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
