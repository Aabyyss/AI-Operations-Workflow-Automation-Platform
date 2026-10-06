"""Tests for the market-vs-measured cost comparison.

Two things are being pinned here, and the second matters more than the
first:

1. The arithmetic — per-outcome and seat pricing, the crossover point, and
   the quote-only case that must not be silently priced at zero.
2. The **honesty rules** — the sample floor, the mock-mode caveat, the
   citation on every preset, and the fact that the report names the plans
   that beat us. A comparison model that always returns "we win" is a
   brochure with a function signature, so the losing cases are asserted
   rather than left to chance.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402
from backend.cost_compare import (MARKET_PLANS, MIN_DECISIONS, PRICE_SNAPSHOT,  # noqa: E402
                                  compare_costs, list_market_plans,
                                  measured_cost_per_decision,
                                  measured_escalation_rate)
from backend.models import CostComparisonRequest  # noqa: E402


def _runs(n, cost=0.0004, escalated_every=None):
    """n recorded decisions; every `escalated_every`-th one touched a human."""
    out = []
    for i in range(n):
        disposition = "auto_resolved"
        if escalated_every and i % escalated_every == 0:
            disposition = "human_review"
        out.append({"id": f"run_{i}", "disposition": disposition,
                    "total_cost_usd": cost})
    return out


# --------------------------------------------------------------- measurement

def test_no_runs_falls_back_to_the_documented_default():
    m = measured_cost_per_decision([])
    assert m["decisions"] == 0
    assert m["basis"] == "default"
    assert m["sample_sufficient"] is False
    assert m["cost_per_decision_usd"] == config.DEFAULT_COST_PER_TICKET_USD


def test_measured_basis_needs_the_sample_floor():
    below = measured_cost_per_decision(_runs(MIN_DECISIONS - 1))
    at = measured_cost_per_decision(_runs(MIN_DECISIONS))
    assert below["basis"] == "indicative"
    assert below["sample_sufficient"] is False
    assert at["basis"] == "measured"
    assert at["sample_sufficient"] is True
    assert at["cost_per_decision_usd"] == 0.0004


def test_unparseable_costs_are_skipped_not_zeroed():
    runs = _runs(3) + [{"id": "bad", "disposition": "auto_resolved",
                        "total_cost_usd": "not-a-number"},
                       {"id": "none", "disposition": "auto_resolved"}]
    m = measured_cost_per_decision(runs)
    # 4 usable records (3 priced + 1 missing-but-defaulted), the junk one dropped.
    assert m["decisions"] == 4
    assert m["cost_per_decision_usd"] == 0.0003


def test_escalation_rate_is_measured_and_none_without_runs():
    assert measured_escalation_rate([]) is None
    # Every 2nd of 10 runs escalated -> 50%.
    assert measured_escalation_rate(_runs(10, escalated_every=2)) == 0.5


# ------------------------------------------------------------------ pricing

def test_per_outcome_plan_math():
    req = CostComparisonRequest(monthly_volume=1000, resolution_rate_pct=50,
                                plan_slugs=["intercom-fin"])
    r = compare_costs(_runs(30), req)
    fin = r.plans[0]
    # 1000 tickets x 50% billed outcomes x $0.99
    assert fin.monthly_usd == 495.0
    assert fin.annual_usd == 5940.0
    assert fin.components["outcome_usd"] == 495.0
    assert fin.components["seats_usd"] == 0.0
    # Ours: 1000 x $0.0004 + $50 infra
    assert r.our_monthly_usd == 50.4
    assert r.our_cost_per_ticket_usd == 0.0504
    assert fin.verdict == "more_expensive"
    assert fin.monthly_delta_usd == round(495.0 - 50.4, 2)
    assert r.plans_that_beat_us == []
    assert r.plans_beaten == 1


def test_seat_plus_outcome_plan_math():
    req = CostComparisonRequest(monthly_volume=1000, resolution_rate_pct=50,
                                seats=3, plan_slugs=["zendesk-ai"])
    z = compare_costs(_runs(30), req).plans[0]
    assert z.components["outcome_usd"] == 675.0          # 500 x $1.35
    assert z.components["seats_usd"] == 255.0            # 3 x $85
    assert z.monthly_usd == 930.0


def test_flat_platform_plan_does_not_scale_with_resolution():
    low = compare_costs(_runs(30), CostComparisonRequest(
        monthly_volume=1000, resolution_rate_pct=10, plan_slugs=["lorikeet"])).plans[0]
    high = compare_costs(_runs(30), CostComparisonRequest(
        monthly_volume=1000, resolution_rate_pct=90, plan_slugs=["lorikeet"])).plans[0]
    assert low.monthly_usd == high.monthly_usd == 2100.0


def test_zero_resolution_still_costs_seats_and_platform():
    """The uncomfortable case for the vendor: 0% resolved, bill still arrives."""
    z = compare_costs(_runs(30), CostComparisonRequest(
        monthly_volume=1000, resolution_rate_pct=0, seats=3,
        plan_slugs=["zendesk-ai"])).plans[0]
    assert z.monthly_usd == 255.0
    assert z.components["outcome_usd"] == 0.0


# --------------------------------------------------------------- crossover

def test_breakeven_resolution_rate_is_the_crossover():
    r = compare_costs(_runs(30), CostComparisonRequest(
        monthly_volume=1000, resolution_rate_pct=50, plan_slugs=["intercom-fin"]))
    fin = r.plans[0]
    # our 50.40 / (1000 x 0.99) -> ~5.09%
    assert fin.breakeven_resolution_rate_pct == pytest.approx(5.09, abs=0.01)
    assert "above 5.1% billed resolutions" in fin.breakeven_note
    # Below the crossover the plan is cheaper, above it the plan is dearer.
    cheap = compare_costs(_runs(30), CostComparisonRequest(
        monthly_volume=1000, resolution_rate_pct=1, plan_slugs=["intercom-fin"]))
    assert cheap.plans[0].verdict == "cheaper"


def test_breakeven_is_none_when_the_plan_wins_at_every_rate():
    r = compare_costs(_runs(30), CostComparisonRequest(
        monthly_volume=100, resolution_rate_pct=50, platform_monthly_usd=500,
        plan_slugs=["intercom-fin"]))
    fin = r.plans[0]
    assert fin.breakeven_resolution_rate_pct is None
    assert "even at 100% billed resolutions" in fin.breakeven_note


def test_breakeven_is_none_when_fixed_cost_already_exceeds_ours():
    r = compare_costs(_runs(30), CostComparisonRequest(
        monthly_volume=1000, resolution_rate_pct=50, seats=100,
        plan_slugs=["zendesk-ai"]))
    z = r.plans[0]
    assert z.breakeven_resolution_rate_pct is None
    assert "even at 0% billed resolutions" in z.breakeven_note


# ------------------------------------------------------- losing gracefully

def test_quote_only_plan_is_not_silently_priced_at_zero():
    q = compare_costs(_runs(30), CostComparisonRequest(
        monthly_volume=1000, plan_slugs=["enterprise-quote"])).plans[0]
    assert q.monthly_usd is None
    assert q.verdict == "quote_required"
    assert "no published list price" in q.breakeven_note


def test_plans_that_beat_us_are_named_and_not_hidden():
    # Force a cheap market rate so the pipeline loses outright.
    r = compare_costs(_runs(30), CostComparisonRequest(
        monthly_volume=1000, resolution_rate_pct=50,
        per_outcome_usd=0.01, plan_slugs=["intercom-fin"]))
    assert r.plans[0].verdict == "cheaper"
    assert r.plans_that_beat_us == ["intercom-fin"]
    assert r.plans_beaten == 0  # the inverse count must not also claim a win
    assert r.cheapest_plan_slug == "intercom-fin"
    assert any("comparison that only reports wins is marketing" in c
               for c in r.caveats)


def test_cheapest_plan_ignores_unpriced_quotes():
    r = compare_costs(_runs(30), CostComparisonRequest(
        monthly_volume=1000, resolution_rate_pct=50,
        plan_slugs=["enterprise-quote", "agentforce"]))
    assert r.cheapest_plan_slug == "agentforce"
    # We beat Agentforce at $2.00/conversation; the unpriced quote is in
    # neither count because it has no number to compare.
    assert r.plans_beaten == 1
    assert r.plans_that_beat_us == []


def test_win_and_loss_counts_are_inverses_over_priced_plans():
    r = compare_costs(_runs(30), CostComparisonRequest(
        monthly_volume=1000, resolution_rate_pct=50,
        plan_slugs=["intercom-fin", "agentforce", "enterprise-quote"]))
    priced = [p for p in r.plans if p.monthly_usd is not None]
    assert r.plans_beaten + len(r.plans_that_beat_us) == len(priced)
    assert "enterprise-quote" not in r.plans_that_beat_us
    assert r.plans_beaten == 2  # $495 and $1000 both lose to our ~$50


def test_buyer_override_beats_the_snapshot():
    r = compare_costs(_runs(30), CostComparisonRequest(
        monthly_volume=1000, resolution_rate_pct=50,
        per_outcome_usd=1.00, plan_slugs=["intercom-fin"]))
    assert r.plans[0].monthly_usd == 500.0  # not the 0.99 snapshot
    assert PRICE_SNAPSHOT in r.assumptions[-1]


# ------------------------------------------------------------- honesty rules

def test_every_preset_cites_a_source():
    for p in MARKET_PLANS:
        assert p["source"].startswith("http"), p["slug"]
        assert p["note"], p["slug"]


def test_list_market_plans_is_a_copy():
    plans = list_market_plans()
    plans[0]["per_outcome_usd"] = 999.0
    assert MARKET_PLANS[0]["per_outcome_usd"] != 999.0


def test_sample_floor_is_reported_as_a_caveat():
    r = compare_costs(_runs(3), CostComparisonRequest(monthly_volume=1000))
    assert r.sample_sufficient is False
    assert r.measurement_basis == "indicative"
    assert r.min_sample_for_measured == MIN_DECISIONS
    assert any(str(MIN_DECISIONS) in c and "floor" in c for c in r.caveats)


def test_mock_mode_says_it_is_not_real_spend(monkeypatch):
    monkeypatch.setattr(config, "MODE", "mock")
    r = compare_costs(_runs(30), CostComparisonRequest(monthly_volume=1000))
    assert r.mode == "mock"
    assert any("MOCK mode" in c and "not real vendor spend" in c for c in r.caveats)


def test_human_review_cost_is_optional_and_uses_measured_escalation():
    runs = _runs(30, escalated_every=3)  # ~1/3 escalated
    off = compare_costs(runs, CostComparisonRequest(
        monthly_volume=1000, plan_slugs=["intercom-fin"]))
    on = compare_costs(runs, CostComparisonRequest(
        monthly_volume=1000, plan_slugs=["intercom-fin"],
        include_human_review_cost=True, human_hourly_rate_usd=30.0,
        human_minutes_per_escalation=6.0))
    assert off.our_components["human_review_usd"] == 0.0
    assert on.our_components["human_review_usd"] > 0
    assert on.our_monthly_usd > off.our_monthly_usd
    assert on.escalations_per_month == pytest.approx(off.escalations_per_month)
    assert any("Human review included" in a for a in on.assumptions)
    assert any("Handoff labour is excluded" in c for c in off.caveats)


def test_zero_state_comparison_does_not_crash(client):
    """Fresh install: no runs at all must 200, not 500."""
    r = client.post("/api/analytics/cost-comparison", json={"monthly_volume": 1000})
    assert r.status_code == 200
    body = r.json()
    assert body["measurement_basis"] == "default"
    assert body["our_monthly_usd"] > 0
    assert len(body["plans"]) == len(MARKET_PLANS)


# ---------------------------------------------------------------- endpoints

def test_market_plans_endpoint_publishes_snapshot_and_sources(client):
    body = client.get("/api/analytics/market-plans").json()
    assert body["price_snapshot"] == PRICE_SNAPSHOT
    slugs = {p["slug"] for p in body["plans"]}
    assert {"intercom-fin", "zendesk-ai", "agentforce", "lorikeet",
            "enterprise-quote"} <= slugs
    assert all(p["source"] for p in body["plans"])


def test_cost_comparison_endpoint_uses_real_measured_runs(client):
    for _ in range(3):
        client.post("/api/tickets", json={
            "customer_email": "a@b.com", "subject": "Charged twice",
            "body": "You charged my card twice for $49, please refund one."})
    body = client.post("/api/analytics/cost-comparison",
                       json={"monthly_volume": 1000}).json()
    assert body["measured_from_decisions"] == 3
    assert body["measured_cost_per_decision_usd"] > 0
    assert body["inputs"]["monthly_volume"] == 1000


def test_cost_comparison_is_audited(client):
    client.post("/api/analytics/cost-comparison",
                json={"monthly_volume": 2000, "resolution_rate_pct": 60})
    events = [e["event"] for e in client.get("/api/audit").json()]
    assert "cost_comparison_modelled" in events
    entry = next(e for e in client.get("/api/audit").json()
                 if e["event"] == "cost_comparison_modelled")
    assert entry["payload"]["monthly_volume"] == 2000
    assert "plans_modelled" in entry["payload"]


def test_cost_comparison_rejects_impossible_inputs(client):
    assert client.post("/api/analytics/cost-comparison",
                       json={"monthly_volume": 0}).status_code == 422
    assert client.post("/api/analytics/cost-comparison",
                       json={"resolution_rate_pct": 150}).status_code == 422
    assert client.post("/api/analytics/cost-comparison",
                       json={"monthly_volume": 100, "seats": -1}).status_code == 422
