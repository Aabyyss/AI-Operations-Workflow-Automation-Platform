"""Offline quality evaluation for the support pipeline.

Runs a labeled ticket set through the real pipeline in an isolated data
directory (never touches ./data) and reports routing accuracy, escalation
recall, cost per ticket, and latency.

Why: model, prompt, or gate changes must re-prove themselves against expected
behavior before shipping — the same way a regression suite pins code, this
pins governance. Exit code 1 on any miss, so CI can gate on it.

Run:  python -m scripts.eval_quality
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402
from backend.models import Ticket  # noqa: E402

# ---------------------------------------------------------------------------
# Labeled set: expected = the disposition governance *should* produce.
# Derived from the pinned behaviors in tests/test_pipeline.py — the label
# source is the same governance rules the suite enforces.
# ---------------------------------------------------------------------------
EVAL_SET: list[dict] = [
    {
        "id": "small-duplicate-refund",
        "subject": "Charged twice",
        "body": "I've been charged twice for my subscription — $49.00. "
                "I need one refunded.",
        "expected": "auto_resolved",
        "reason_label": "low-risk monetary action under the approval limit",
    },
    {
        "id": "password-reset",
        "subject": "Can't sign in",
        "body": "I forgot my password, can you help me reset it?",
        "expected": "auto_resolved",
        "reason_label": "standard self-service runbook action",
    },
    {
        "id": "api-429",
        "subject": "API returns 429",
        "body": "I keep getting 429 errors from the API all morning.",
        "expected": "auto_resolved",
        "reason_label": "troubleshooting runbook in the knowledge base",
    },
    {
        "id": "pricing-question",
        "subject": "Pricing question",
        "body": "How much does the growth plan cost per user? "
                "Do you offer nonprofit discounts?",
        "expected": "auto_resolved",
        "reason_label": "informational, policy-backed, no action side effects",
    },
    {
        "id": "large-refund",
        "subject": "Refund request for annual plan",
        "body": "I cancelled but was charged $1,200.00 for the annual plan. "
                "I need a refund.",
        "expected": "human_review",
        "reason_label": "monetary action above the approval limit",
    },
    {
        "id": "enterprise-refund",
        "subject": "Enterprise refund request",
        "body": "We were charged $2,500.00 this month and want a full refund.",
        "expected": "human_review",
        "reason_label": "monetary action above the approval limit",
    },
    {
        "id": "remove-2fa",
        "subject": "Remove 2FA",
        "body": "I lost my phone and need you to turn off 2FA on my account.",
        "expected": "human_review",
        "reason_label": "security-sensitive account change",
    },
    {
        "id": "unknown-onboarding",
        "subject": "Question about onboarding",
        "body": "Can you walk me through onboarding for a new team? "
                "Not covered in docs.",
        "expected": "human_review",
        "reason_label": "no confident policy match to ground a response on",
    },
]


_ORIGINALS: dict = {}


def _isolate_data_dir() -> None:
    """Point config.DATA_DIR at a fresh temp dir and rebuild storage.

    eval harness must be side-effect free: no writes into ./data, ever.
    """
    tmp = Path(tempfile.mkdtemp(prefix="aiops-eval-"))
    config.DATA_DIR = tmp
    from backend import store
    _ORIGINALS.update(storage=store.storage, append=store.append, all=store.all)
    store.storage = store.Storage()          # rebind the singleton
    import backend.store as store_mod
    store_mod.append = store.storage.append  # keep module helpers aligned
    store_mod.all = store.storage.all


def _restore_data_dir() -> None:
    """Restore pre-eval store bindings — the harness must stay side-effect
    free even when main() runs inside another process's test suite."""
    if not _ORIGINALS:
        return
    from backend import store
    store.storage = _ORIGINALS["storage"]
    store.append = _ORIGINALS["append"]
    store.all = _ORIGINALS["all"]


def run_eval() -> tuple[int, int, list[dict]]:
    from backend import pipeline

    results: list[dict] = []
    for case in EVAL_SET:
        ticket = Ticket(customer_email="eval@customer.example",
                        subject=case["subject"], body=case["body"])
        run = pipeline.run_pipeline(ticket)
        got = run.disposition.value if hasattr(run.disposition, "value") \
            else str(run.disposition)
        results.append({
            "id": case["id"],
            "expected": case["expected"],
            "got": got,
            "match": got == case["expected"],
            "risk": run.decision.risk_score if run.decision else None,
            "cost_usd": run.total_cost_usd,
            "latency_ms": run.total_latency_ms,
        })
    correct = sum(1 for r in results if r["match"])
    return correct, len(results), results


def report(correct: int, total: int, results: list[dict]) -> None:
    width = max(len(r["id"]) for r in results) + 2
    print("=" * 72)
    print("QUALITY EVAL — routing accuracy vs governance-labeled set")
    print("=" * 72)

    print(f"\n{'CASE'.ljust(width)}{'EXPECTED'.ljust(16)}{'GOT'.ljust(16)}RESULT")
    for r in results:
        mark = "OK " if r["match"] else "MISS"
        print(f"{r['id'].ljust(width)}{r['expected'].ljust(16)}"
              f"{r['got'].ljust(16)}{mark}")

    accuracy = correct / total * 100
    expected_esc = [r for r in results if r["expected"] == "human_review"]
    recalled = sum(1 for r in expected_esc if r["match"])
    esc_recall = recalled / len(expected_esc) * 100 if expected_esc else 100.0
    avg_cost = sum(r["cost_usd"] for r in results) / total
    avg_lat = sum(r["latency_ms"] for r in results) / total

    print(f"\nRouting accuracy      : {correct}/{total} ({accuracy:.0f}%)")
    print(f"Escalation recall     : {recalled}/{len(expected_esc)} "
          f"({esc_recall:.0f}%)  <- high-risk cases that were caught")
    print(f"Avg cost per ticket   : ${avg_cost:.5f}")
    print(f"Avg latency per ticket: {avg_lat:.0f}ms")

    misses = [r for r in results if not r["match"]]
    if misses:
        print("\nMISSES (expected vs got):")
        for r in misses:
            case = next(c for c in EVAL_SET if c["id"] == r["id"])
            print(f"  - {r['id']}: {case['reason_label']} -> "
                  f"expected {r['expected']}, got {r['got']}")


def main() -> int:
    _isolate_data_dir()
    try:
        correct, total, results = run_eval()
        report(correct, total, results)
    finally:
        _restore_data_dir()
        shutil.rmtree(config.DATA_DIR, ignore_errors=True)  # no temp litter
    ok = correct == total
    print(f"\n{'PASS' if ok else 'FAIL'}: {'all cases match governance labels' if ok else 'routing diverged from labels — do not ship'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
