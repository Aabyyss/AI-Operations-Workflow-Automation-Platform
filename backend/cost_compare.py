"""Cost comparison — what a support AI costs at *your* volume.

The market prices AI support two ways: per resolution (Intercom Fin at
$0.99/outcome, Agentforce at ~$2.00/conversation) and per seat (Zendesk AI
tiers), sometimes both. Both meters scale with *the vendor's success*. This
platform has no meter at all — it measures the token cost of every decision
it makes and publishes it.

That inversion is the point of this module, and it is also its trap. A
comparison is only worth reading if it is willing to lose: so this module
models the market plans from their published list prices, computes our side
from the **run ledger** rather than an estimate, and says plainly when a
plan comes out cheaper.

Two honesty rules are inherited from the rest of the platform:

1. **Sample floor.** Below `MIN_DECISIONS` recorded decisions the measured
   cost is labelled *indicative*, not representative.
2. **Mock mode is not real money.** In the default offline mode the token
   cost is deterministic but synthetic. The report says so rather than
   implying anyone measured a real invoice.

Prices are a snapshot (`PRICE_SNAPSHOT`) with sources attached, because list
prices move and a business case that cites nothing is a brochure.
"""
from __future__ import annotations

import copy
from typing import Any

from . import config
from .models import CostComparison, CostComparisonRequest, PlanCost

# Below this many recorded decisions the measured cost-per-decision is
# reported as indicative rather than representative.
MIN_DECISIONS = 20

# Public list prices captured for docs/COMPETITIVE_ANALYSIS.md. Every entry
# carries its source so a reader can check it and a buyer can override it.
PRICE_SNAPSHOT = "2026-10-07"

MARKET_PLANS: tuple[dict[str, Any], ...] = (
    {
        "slug": "intercom-fin",
        "vendor": "Intercom Fin",
        "model": "per_outcome",
        "per_outcome_usd": 0.99,
        "seat_price_usd": 0.0,
        "flat_monthly_usd": 0.0,
        "source": "https://www.intercom.com/pricing",
        "note": "Billed once per conversation outcome, however many messages it takes.",
    },
    {
        "slug": "zendesk-ai",
        "vendor": "Zendesk AI",
        "model": "seat_plus_outcome",
        "per_outcome_usd": 1.35,
        "seat_price_usd": 85.0,
        "flat_monthly_usd": 0.0,
        "source": "https://www.zendesk.com/why-zendesk/zendesk-vs-fin/",
        "note": ("Advanced seat tier ($29 Essential / $85 Advanced / $132 Expert); "
                 "outcome component reported at $1.20–$1.50, modelled at the midpoint."),
    },
    {
        "slug": "agentforce",
        "vendor": "Salesforce Agentforce",
        "model": "per_outcome",
        "per_outcome_usd": 2.00,
        "seat_price_usd": 0.0,
        "flat_monthly_usd": 0.0,
        "source": "https://clonedesk.ai/blog/ai-support-agent-pricing",
        "note": "Per-conversation pricing.",
    },
    {
        "slug": "lorikeet",
        "vendor": "Lorikeet",
        "model": "flat_platform",
        "per_outcome_usd": 0.0,
        "seat_price_usd": 0.0,
        "flat_monthly_usd": 2100.0,
        "source": "https://www.lorikeetcx.ai/articles/sierra-alternatives-2026",
        "note": "Publishes pricing: $2,100/mo (Start) paid annually; $5,100/mo (Scale).",
    },
    {
        "slug": "enterprise-quote",
        "vendor": "Sierra / Decagon / Forethought",
        "model": "quote_only",
        "per_outcome_usd": None,
        "seat_price_usd": None,
        "flat_monthly_usd": None,
        "source": "https://www.getmacha.com/blog/sierra-ai-complete-guide",
        "note": ("No published list price — enterprise contracts only (third-party "
                 "estimates put Sierra near $150k/yr plus $50k–$200k setup). Supply a "
                 "quoted rate to model it."),
    },
)

_PLAN_BY_SLUG = {p["slug"]: p for p in MARKET_PLANS}


def list_market_plans() -> list[dict[str, Any]]:
    """The preset catalog, safe to hand to a UI."""
    return copy.deepcopy(list(MARKET_PLANS))


def _money(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


def _unit(v: float) -> float:
    """Per-ticket figures run well below a cent — keep enough precision."""
    return round(v, 5)


def measured_cost_per_decision(runs: list[dict]) -> dict[str, Any]:
    """Mean token cost of one pipeline decision, from the run ledger.

    Every run is one decision — auto-resolved, escalated, approved or
    failed — so the mean over `total_cost_usd` is the honest "cost per
    decision". A per-outcome vendor only bills you for the ones it
    resolved; we pay for all of them, including the escalations. That is
    modelled as such rather than hidden.
    """
    costs = []
    for r in runs:
        try:
            costs.append(float(r.get("total_cost_usd") or 0.0))
        except (TypeError, ValueError):
            continue
    decisions = len(costs)
    if decisions:
        per_decision = sum(costs) / decisions
        basis = "measured" if decisions >= MIN_DECISIONS else "indicative"
    else:
        per_decision = config.DEFAULT_COST_PER_TICKET_USD
        basis = "default"
    return {
        "cost_per_decision_usd": _unit(per_decision),
        "decisions": decisions,
        "basis": basis,
        "sample_sufficient": decisions >= MIN_DECISIONS,
    }


def measured_escalation_rate(runs: list[dict]) -> float | None:
    """Share of decisions that touched a human, from the run ledger.

    Anything that did not auto-resolve involved a human at some point,
    whether they approved it, rejected it or are still sitting on it.
    """
    if not runs:
        return None
    auto = sum(1 for r in runs if r.get("disposition") == "auto_resolved")
    return round(1.0 - auto / len(runs), 4)


def _breakeven(our_monthly: float, volume: int, per_outcome: float,
               fixed_monthly: float) -> tuple[float | None, str]:
    """Resolution rate above which a per-outcome plan costs more than us.

    The vendor's bill rises with the share of volume it resolves; ours does
    not. So there is exactly one crossover point, and it is the number a
    buyer should argue about.
    """
    if per_outcome <= 0:
        return None, "no per-outcome meter — this plan's cost does not scale with how much it resolves"
    rate = 100.0 * (our_monthly - fixed_monthly) / (volume * per_outcome)
    if rate < 0:
        return None, ("even at 0% billed resolutions this plan's fixed cost already "
                      "exceeds the pipeline's measured cost")
    if rate > 100:
        return None, ("even at 100% billed resolutions this plan stays below the "
                      "pipeline's measured cost at this volume")
    return round(rate, 1), (f"above {round(rate, 1)}% billed resolutions this plan "
                            f"costs more than running the pipeline")


def _price_plan(plan: dict[str, Any], req: CostComparisonRequest,
                our_monthly: float) -> PlanCost:
    per_outcome = plan["per_outcome_usd"]
    seat_price = plan["seat_price_usd"]
    flat = plan["flat_monthly_usd"]

    # Buyer-supplied overrides win over the snapshot, so a real quote can be
    # modelled without editing this file.
    if req.per_outcome_usd is not None:
        per_outcome = req.per_outcome_usd
    if req.seat_price_usd is not None:
        seat_price = req.seat_price_usd
    if req.flat_monthly_usd is not None:
        flat = req.flat_monthly_usd

    if plan["model"] == "quote_only" and per_outcome is None and flat is None:
        return PlanCost(
            slug=plan["slug"], vendor=plan["vendor"], model="quote_only",
            source=plan["source"], note=plan["note"],
            verdict="quote_required",
            breakeven_note="no published list price to model — supply a quoted rate",
        )

    per_outcome = per_outcome or 0.0
    seat_price = seat_price or 0.0
    flat = flat or 0.0

    billed_outcomes = req.monthly_volume * req.resolution_rate_pct / 100.0
    outcome_cost = billed_outcomes * per_outcome
    seat_cost = req.seats * seat_price
    monthly = outcome_cost + seat_cost + flat

    fixed_monthly = seat_cost + flat
    breakeven, breakeven_note = _breakeven(our_monthly, req.monthly_volume,
                                           per_outcome, fixed_monthly)
    delta = monthly - our_monthly

    return PlanCost(
        slug=plan["slug"],
        vendor=plan["vendor"],
        model=plan["model"],
        source=plan["source"],
        note=plan["note"],
        monthly_usd=_money(monthly),
        annual_usd=_money(monthly * 12),
        cost_per_ticket_usd=_unit(monthly / req.monthly_volume),
        components={
            "outcome_usd": _money(outcome_cost) or 0.0,
            "seats_usd": _money(seat_cost) or 0.0,
            "platform_usd": _money(flat) or 0.0,
        },
        breakeven_resolution_rate_pct=breakeven,
        breakeven_note=breakeven_note,
        monthly_delta_usd=_money(delta),
        verdict="more_expensive" if monthly > our_monthly else "cheaper",
    )


def compare_costs(runs: list[dict], req: CostComparisonRequest) -> CostComparison:
    """Model every selected market plan against our measured cost.

    `runs` is the pipeline ledger; nothing here reads the network, and no
    vendor number is invented — presets carry their sources, overrides come
    from the caller.
    """
    measured = measured_cost_per_decision(runs)
    per_decision = measured["cost_per_decision_usd"]

    escalation_rate = measured_escalation_rate(runs)
    escalations_per_month = (req.monthly_volume * escalation_rate) if escalation_rate else 0.0

    outcomes_side = req.monthly_volume * per_decision
    human_side = 0.0
    if req.include_human_review_cost and escalation_rate:
        human_side = (escalations_per_month
                      * req.human_minutes_per_escalation / 60.0
                      * req.human_hourly_rate_usd)
    our_monthly = outcomes_side + req.platform_monthly_usd + human_side

    slugs = req.plan_slugs or [p["slug"] for p in MARKET_PLANS]
    plans = [_price_plan(_PLAN_BY_SLUG[s], req, our_monthly)
             for s in slugs if s in _PLAN_BY_SLUG]

    priced = [p for p in plans if p.monthly_usd is not None]
    cheapest = min(priced, key=lambda p: p.monthly_usd) if priced else None   # type: ignore[arg-type,return-value]
    # Two distinct counts, named so neither can be misread: the plans that
    # cost less than us, and how many we came out cheaper than. Quote-only
    # plans are in neither — they have no price to compare.
    beat_us = [p.slug for p in priced if p.monthly_usd < our_monthly]
    beaten = len(priced) - len(beat_us)

    assumptions = [
        f"Vendor side: {req.monthly_volume:,} tickets/month × "
        f"{req.resolution_rate_pct}% billed resolutions × the plan's per-outcome rate, "
        f"plus {req.seats} seats and any platform fee.",
        f"Our side: {req.monthly_volume:,} tickets/month × ${per_decision} measured "
        f"cost per decision (every ticket, not only the resolved ones) "
        f"+ ${req.platform_monthly_usd} infra allowance.",
        "A per-outcome 'resolution' and a pipeline 'decision' are not guaranteed to be "
        "the same unit; we charge ourselves per decision, which is the conservative "
        "framing for us.",
        f"Market prices are a list-price snapshot taken {PRICE_SNAPSHOT}.",
    ]
    if req.include_human_review_cost and escalation_rate:
        assumptions.append(
            f"Human review included on our side: {escalations_per_month:.0f} escalations/month "
            f"at {req.human_minutes_per_escalation} min × ${req.human_hourly_rate_usd}/h "
            f"(measured escalation rate {escalation_rate * 100:.1f}%).")
    elif escalation_rate:
        assumptions.append(
            f"Human review time is excluded on both sides; our measured escalation rate is "
            f"{escalation_rate * 100:.1f}%, so set include_human_review_cost to price it.")

    caveats = []
    if not measured["sample_sufficient"]:
        caveats.append(
            f"Cost per decision is {'assumed' if measured['basis'] == 'default' else 'indicative'}: "
            f"{measured['decisions']} recorded decision(s), floor is {MIN_DECISIONS}. "
            f"Run more tickets in this deployment before quoting the figure.")
    if config.MODE != "live":
        caveats.append(
            "This deployment is in MOCK mode: token cost is deterministic and "
            "comparable run-to-run, but it is priced from synthetic token counts, not "
            "real vendor spend. Set AIOPS_MODE=live + OPENAI_API_KEY to compare real cost.")
    caveats.append(
        "Neither side includes integration, migration or training effort, and enterprise "
        "contracts are usually quoted with volume commitments the list price hides.")
    if not req.include_human_review_cost:
        caveats.append(
            "Handoff labour is excluded: the vendor's unresolved tickets and our escalated "
            "ones both cost human minutes that this model does not bill to either side.")
    if beat_us:
        caveats.append(
            "Plans that come out cheaper here are listed as such: "
            f"{', '.join(beat_us)}. A comparison that only reports wins is marketing.")

    return CostComparison(
        inputs={
            "monthly_volume": req.monthly_volume,
            "resolution_rate_pct": req.resolution_rate_pct,
            "seats": req.seats,
            "include_human_review_cost": req.include_human_review_cost,
            "human_hourly_rate_usd": req.human_hourly_rate_usd,
            "human_minutes_per_escalation": req.human_minutes_per_escalation,
            "platform_monthly_usd": req.platform_monthly_usd,
        },
        measured_cost_per_decision_usd=per_decision,
        measured_from_decisions=measured["decisions"],
        measurement_basis=measured["basis"],
        sample_sufficient=measured["sample_sufficient"],
        min_sample_for_measured=MIN_DECISIONS,
        mode=config.MODE,
        escalations_per_month=round(escalations_per_month, 1),
        our_monthly_usd=_money(our_monthly) or 0.0,
        our_annual_usd=_money(our_monthly * 12) or 0.0,
        our_cost_per_ticket_usd=_unit(our_monthly / req.monthly_volume),
        our_components={
            "decisions_usd": _money(outcomes_side) or 0.0,
            "platform_usd": _money(req.platform_monthly_usd) or 0.0,
            "human_review_usd": _money(human_side) or 0.0,
        },
        plans=plans,
        cheapest_plan_slug=cheapest.slug if cheapest else None,
        plans_beaten=beaten,
        plans_that_beat_us=beat_us,
        assumptions=assumptions,
        caveats=caveats,
    )
