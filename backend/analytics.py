"""SLA metrics for the human-approval queue.

Human-in-the-loop is only governable if the queue itself is measured:
how fast work is waiting, how fast humans turn it around, and what
share of tickets need a human at all. These are the numbers an ops
manager charts weekly — the same metrics Power BI gets via the API.
"""
from __future__ import annotations

from datetime import datetime

# Aging buckets for pending reviews, in minutes.
BUCKETS = [(15, "under_15m"), (60, "15m_to_1h"), (240, "1h_to_4h"),
           (float("inf"), "over_4h")]


def _minutes_between(start: str, end: datetime) -> float | None:
    try:
        created = datetime.fromisoformat(start)
        if created.tzinfo is None:
            from datetime import timezone
            created = created.replace(tzinfo=timezone.utc)
        return (end - created).total_seconds() / 60.0
    except (ValueError, TypeError):
        return None


def _median(values: list[float]) -> float:
    values = sorted(values)
    n = len(values)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2.0


def approval_sla_metrics(reviews: list[dict], runs_count: int,
                         now: datetime) -> dict:
    """Compute queue-aging, turnaround, and escalation-rate metrics.

    `reviews` are stored ReviewRequest dicts; `now` is injected so the
    metrics are deterministic and testable.
    """
    aging = {label: 0 for _, label in BUCKETS}
    oldest_pending_minutes = 0.0
    turnarounds: list[float] = []
    pending = approved = rejected = 0

    for r in reviews:
        status = r.get("status", "pending")
        if status == "pending":
            pending += 1
            age = _minutes_between(r.get("created_at", ""), now)
            if age is None:
                continue
            oldest_pending_minutes = max(oldest_pending_minutes, age)
            for threshold, label in BUCKETS:
                if age < threshold:
                    aging[label] += 1
                    break
        else:
            if status == "approved":
                approved += 1
            else:
                rejected += 1
            if r.get("reviewed_at"):
                # turnaround = created -> reviewed
                try:
                    created = datetime.fromisoformat(r["created_at"])
                    reviewed = datetime.fromisoformat(r["reviewed_at"])
                    if created.tzinfo is None:
                        from datetime import timezone
                        created = created.replace(tzinfo=timezone.utc)
                    if reviewed.tzinfo is None:
                        from datetime import timezone
                        reviewed = reviewed.replace(tzinfo=timezone.utc)
                    turnarounds.append((reviewed - created).total_seconds() / 60.0)
                except (ValueError, TypeError, KeyError):
                    pass

    decided = len(turnarounds)
    total_reviews = len(reviews)
    escalation_rate = round(100.0 * total_reviews / max(1, runs_count), 1)

    return {
        "pending": pending,
        "approved": approved,
        "rejected": rejected,
        "aging_buckets": aging,
        "oldest_pending_minutes": round(oldest_pending_minutes, 1),
        "turnaround_minutes": {
            "mean": round(sum(turnarounds) / decided, 1) if decided else 0.0,
            "median": round(_median(turnarounds), 1) if decided else 0.0,
            "max": round(max(turnarounds), 1) if decided else 0.0,
            "sample_size": decided,
        },
        "escalation_rate_pct": escalation_rate,
        "tickets_processed": runs_count,
    }
