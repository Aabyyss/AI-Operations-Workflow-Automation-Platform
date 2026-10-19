"""Handoff labour, billed on both sides of the comparison.

The old model had an asymmetry that flattered every per-outcome vendor:
their *unresolved* tickets — the ones their meter never bills for — land on
the buyer's own agents, and that time was priced nowhere. Our escalations
were, as soon as you asked for them. So the comparison penalised the
pipeline for the humans it hands work to, and gave the vendor a free
handoff queue.

Now `include_human_review_cost` prices the same minutes on both sides:

    their cost = bill + (tickets they did not resolve) × cost-per-human-touch
    our cost   = measured decisions + infra + (measured escalations) × same

which is the only comparison a per-outcome vendor can be judged on fairly —
and the only one that can say, honestly, that AI resolution *saves* money
against a human queue when the per-outcome price is below the labour cost.

These tests pin the arithmetic, the fairness property, the backwards
compatible default, and the crossover's sign flip.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.cost_compare import compare_costs, handoff_per_ticket_usd  # noqa: E402
from backend.models import CostComparisonRequest  # noqa: E402


def _runs(n, cost=0.0004, escalated_every=None):
    out = []
    for i in range(n):
        disposition = "auto_resolved"
        if escalated_every and i % escalated_every == 0:
            disposition = "human_review"
        out.append({"id": f"run_{i}", "disposition": disposition,
                    "total_cost_usd": cost})
    return out


def _req(**kwargs):
    base = dict(monthly_volume=1000, resolution_rate_pct=50, plan_slugs=["intercom-fin"],
                include_human_review_cost=True, human_hourly_rate_usd=30.0,
                human_minutes_per_escalation=6.0)
    base.update(kwargs)
    return CostComparisonRequest(**base)


# ------------------------------------------------------------------ the rate
def test_one_handoff_costs_minutes_times_rate():
    assert handoff_per_ticket_usd(_req()) == 3.0      # 6 min at $30/h
    assert handoff_per_ticket_usd(_req(human_minutes_per_escalation=10.0,
                                       human_hourly_rate_usd=24.0)) == 4.0


def test_handoff_is_zero_when_the_flag_is_off():
    assert handoff_per_ticket_usd(_req(include_human_review_cost=False)) == 0.0


# ---------------------------------------------------------- both sides, same unit
def test_vendor_side_carries_the_tickets_it_does_not_resolve():
    req = _req(monthly_volume=1000, resolution_rate_pct=50)
    plan = compare_costs(_runs(30), req).plans[0]
    assert plan.monthly_usd == 495.0                  # 500 × $0.99, unchanged
    assert plan.handoff_labour_usd == 1500.0          # 500 unresolved × $3.00
    assert plan.fully_loaded_monthly_usd == 1995.0
    assert plan.components["handoff_labour_usd"] == 1500.0


def test_our_side_carries_the_escalations_it_measures():
    runs = _runs(30, escalated_every=3)               # one third escalated
    c = compare_costs(runs, _req())
    assert c.comparison_basis == "fully_loaded"
    assert c.escalations_per_month == pytest.approx(333.3, abs=0.1)
    # 10 of 30 runs escalated → 333.3 tickets/month × $3.00, not a rounded 1/3.
    assert c.our_handoff_labour_usd == round(c.escalations_per_month * 3.0, 2)
    assert c.our_handoff_labour_usd == 999.9
    assert c.our_monthly_usd == round(c.our_bill_usd + c.our_handoff_labour_usd, 2)


def test_both_sides_use_the_same_labour_rate():
    """The fairness property: one rate, applied to each side's own queue."""
    c = compare_costs(_runs(30, escalated_every=2), _req(human_hourly_rate_usd=48.0,
                                                         human_minutes_per_escalation=5.0))
    per_ticket = c.handoff_per_ticket_usd
    assert per_ticket == 4.0
    plan = c.plans[0]
    assert plan.handoff_labour_usd == pytest.approx(500 * per_ticket)
    assert c.our_handoff_labour_usd == pytest.approx(500 * per_ticket)


def test_a_per_outcome_plan_no_longer_wins_by_hiding_its_queue():
    """The case the old model got wrong: cheap meter, expensive handoff."""
    runs = _runs(30, escalated_every=4)               # 25% of ours escalate
    cheap_meter = dict(per_outcome_usd=0.02)
    bill_only = compare_costs(runs, _req(**cheap_meter, include_human_review_cost=False))
    loaded = compare_costs(runs, _req(**cheap_meter))
    assert bill_only.plans[0].verdict == "cheaper"    # $10 bill vs ~$50 of our cost
    # Fully loaded: 500 unresolved × $3 = $1,500, which swamps the cheap meter.
    assert loaded.plans[0].fully_loaded_monthly_usd == 1510.0
    assert loaded.plans[0].verdict == "more_expensive"


def test_the_default_still_reports_the_vendor_bill():
    """Old behaviour, unchanged: without the flag, monthly_usd is the meter."""
    c = compare_costs(_runs(30), CostComparisonRequest(monthly_volume=1000,
                                                       plan_slugs=["intercom-fin"]))
    assert c.comparison_basis == "bill"
    assert c.our_handoff_labour_usd == 0.0
    assert c.plans[0].handoff_labour_usd == 0.0
    assert c.plans[0].fully_loaded_monthly_usd == c.plans[0].monthly_usd


# --------------------------------------------------------------- the crossover
def test_breakeven_flips_sign_when_labour_costs_more_than_the_meter():
    """$0.99 per outcome vs $3.00 per human touch: resolving *saves* money.

    So the plan's cost falls as it resolves more, and the crossover stops
    being a ceiling and becomes a floor — below it, the plan is dearer.
    """
    c = compare_costs(_runs(30, escalated_every=4),
                      _req(per_outcome_usd=0.50, platform_monthly_usd=50.0))
    fin = c.plans[0]
    # At 50% resolved the plan costs $250 + 500×$3 = $2,000 against our ~$800,
    # and only beats us once it resolves nearly everything.
    assert fin.breakeven_resolution_rate_pct == pytest.approx(86.0, abs=0.1)
    assert "below 86.0% billed resolutions" in fin.breakeven_note
    assert fin.verdict == "more_expensive"


def test_labour_at_the_same_price_as_the_meter_has_no_crossover():
    """p == h: each resolution moves cost between meter and queue 1:1."""
    c = compare_costs(_runs(30), _req(per_outcome_usd=3.0))
    fin = c.plans[0]
    assert fin.breakeven_resolution_rate_pct is None
    assert "no crossover" in fin.breakeven_note


def test_free_plan_with_zero_handoff_keeps_the_old_note():
    c = compare_costs(_runs(30), CostComparisonRequest(
        monthly_volume=1000, plan_slugs=["lorikeet"]))
    assert "does not scale with how much it resolves" in c.plans[0].breakeven_note


# ------------------------------------------------------------ reporting honesty
def test_assumptions_state_both_queues_and_the_same_rate():
    c = compare_costs(_runs(30, escalated_every=4), _req())
    joined = " ".join(c.assumptions)
    assert "Handoff labour is billed on both sides" in joined
    assert "$3.0 per ticket reaching a human" in joined
    assert "500 of the vendor's tickets/month" in joined


def test_excluding_labour_keeps_the_old_caveat():
    c = compare_costs(_runs(30), _req(include_human_review_cost=False))
    assert any("Handoff labour is excluded" in x for x in c.caveats)
    assert c.comparison_basis == "bill"


def test_a_full_resolution_rate_says_what_it_assumes():
    c = compare_costs(_runs(30), _req(resolution_rate_pct=100))
    assert any("no unresolved tickets to hand back" in x for x in c.caveats)


def test_counts_and_cheapest_follow_the_fully_loaded_figures():
    runs = _runs(30, escalated_every=4)
    c = compare_costs(runs, _req(plan_slugs=["intercom-fin", "agentforce", "lorikeet"]))
    by_slug = {p.slug: p for p in c.plans}
    assert c.cheapest_plan_slug == min(
        by_slug, key=lambda s: by_slug[s].fully_loaded_monthly_usd)
    for p in c.plans:
        assert (p.slug in c.plans_that_beat_us) == (
            p.fully_loaded_monthly_usd < c.our_monthly_usd)


def test_endpoint_accepts_the_flag_and_returns_both_sides(client):
    for _ in range(3):
        # 2FA removal is security-controlled, so it escalates and gives the
        # our-side handoff a measured rate to bill.
        client.post("/api/tickets", json={
            "customer_email": "a@b.com", "subject": "Remove 2FA",
            "body": "Please remove 2FA from my account, I lost my phone."})
    body = client.post("/api/analytics/cost-comparison", json={
        "monthly_volume": 1000, "plan_slugs": ["intercom-fin"],
        "include_human_review_cost": True, "human_hourly_rate_usd": 30}).json()
    assert body["comparison_basis"] == "fully_loaded"
    assert body["our_handoff_labour_usd"] > 0
    plan = body["plans"][0]
    assert plan["handoff_labour_usd"] == 1500.0
    assert plan["fully_loaded_monthly_usd"] == plan["monthly_usd"] + 1500.0
