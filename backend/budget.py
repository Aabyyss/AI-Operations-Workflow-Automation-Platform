"""LLM budget guardrails — spend awareness as a first-class metric.

'How much will this cost?' has two answers: what you've spent this period
and what you're on track to spend. Both belong in front of whoever owns
the budget line *before* the invoice arrives, which is what this module
computes from the usage ledger.

Disabled by default (AIOPS_MONTHLY_BUDGET_USD=0): no budget configured,
no alert noise.
"""
from __future__ import annotations

from datetime import datetime, timezone


def budget_status(usage: list[dict], budget_usd: float,
                  now: datetime | None = None) -> dict:
    """Spend vs a monthly budget, with a naive linear projection.

    The projection assumes the current run rate continues for a 30-day
    month. That is deliberately simple — the alternative (calendar-aware
    curves) is precision theater at portfolio scale.
    """
    now = now or datetime.now(timezone.utc)
    spend = round(sum(float(u.get("cost_usd", 0.0)) for u in usage), 6)

    if budget_usd <= 0:
        return {
            "budget_usd": 0.0, "spent_usd": spend, "remaining_usd": None,
            "projected_month_usd": None, "utilization_pct": None,
            "alert": None, "enabled": False,
        }

    day = now.timetuple().tm_mday
    days_in_month = 30.0
    fraction_elapsed = day / days_in_month
    projected = spend / fraction_elapsed if fraction_elapsed > 0 else spend

    return {
        "budget_usd": budget_usd,
        "spent_usd": spend,
        "remaining_usd": round(budget_usd - spend, 6),
        "projected_month_usd": round(projected, 4),
        "utilization_pct": round(100.0 * spend / budget_usd, 1),
        "alert": bool(spend >= budget_usd or projected > budget_usd * 1.2),
        "enabled": True,
    }
