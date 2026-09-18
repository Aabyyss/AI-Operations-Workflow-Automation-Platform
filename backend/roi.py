"""ROI / cost engine — the quantitative heart of the AI PM side.

Two models stacked:
1. Labor economics — what the manual process costs today vs. with AI.
2. Token economics — what the AI actually costs to run per item.

Every number traces to an input or an explicit assumption, which is what
separates a real business case from a demo.
"""
from __future__ import annotations

from . import config
from .models import AutomationScore, CostBreakdown, ProcessInput

# Implementation cost model (deliberately simple, explicitly an estimate):
# base platform fee + per-AI-touch integration effort.
IMPLEMENTATION_BASE_USD = 800.0
IMPLEMENTATION_PER_AUTOMATED_STEP_USD = 250.0

# With AI handling the automatable steps, humans spend a fraction of the
# original minutes on exceptions, reviews and final ownership.
HUMAN_REMAINING_FRACTION = 0.25


def automation_rate(p: ProcessInput) -> float:
    """Fraction of monthly volume the AI path can fully handle."""
    ai_minutes = sum(
        s.minutes_per_item
        for s in p.steps
        if not (s.touches_money or s.touches_pii) and s.repetitive
    )
    total = sum(s.minutes_per_item for s in p.steps) or 1.0
    return min(0.95, ai_minutes / total)


def score_process(p: ProcessInput) -> AutomationScore:
    volume_score = min(100.0, p.monthly_volume / 30)  # 3000/mo saturates
    repetitive_steps = [s for s in p.steps if s.repetitive]
    repetitiveness = 100.0 * len(repetitive_steps) / max(1, len(p.steps))
    structured = [s for s in p.steps if s.structured_data]
    structure = 100.0 * len(structured) / max(1, len(p.steps))
    judgment = 100.0 * sum(1 for s in p.steps if s.requires_judgment) / max(1, len(p.steps))
    risky = 100.0 * sum(1 for s in p.steps if s.touches_money or s.touches_pii) / max(1, len(p.steps))

    total = (
        0.30 * volume_score
        + 0.30 * repetitiveness
        + 0.20 * structure
        - 0.10 * judgment
        - 0.10 * risky
    )
    total = round(max(0.0, min(100.0, total)), 1)
    if total >= 65:
        verdict, rationale = "high_potential", "Strong candidate: high volume, repetitive and structured steps."
    elif total >= 40:
        verdict, rationale = "moderate", "Partial automation viable; prioritize the repetitive steps."
    else:
        verdict, rationale = "low_potential", "Human judgment dominates; consider assisting, not automating."
    return AutomationScore(
        total=total,
        volume_score=round(volume_score, 1),
        repetitiveness_score=round(repetitiveness, 1),
        structure_score=round(structure, 1),
        judgment_penalty=round(judgment, 1),
        risk_penalty=round(risky, 1),
        verdict=verdict,  # type: ignore[arg-type]
        rationale=rationale,
    )


def costs(p: ProcessInput, token_cost_per_item: float = config.DEFAULT_COST_PER_TICKET_USD) -> CostBreakdown:
    rate = automation_rate(p)
    total_minutes = sum(s.minutes_per_item for s in p.steps)
    current_monthly = p.monthly_volume * total_minutes / 60 * p.hourly_rate_usd

    ai_monthly = (
        p.monthly_volume * (total_minutes * HUMAN_REMAINING_FRACTION) / 60 * p.hourly_rate_usd
        + p.monthly_volume * token_cost_per_item
        + 50.0  # infra/hosting allowance
    )
    savings = max(0.0, current_monthly - ai_monthly)
    n_automated = sum(
        1 for s in p.steps
        if not (s.touches_money or s.touches_pii) and s.repetitive
    )
    implementation = IMPLEMENTATION_BASE_USD + n_automated * IMPLEMENTATION_PER_AUTOMATED_STEP_USD
    payback = implementation / savings if savings > 0 else float("inf")
    first_year_roi = (savings * 12 - implementation) / implementation * 100 if implementation else 0.0
    return CostBreakdown(
        current_monthly_cost_usd=round(current_monthly, 2),
        ai_monthly_cost_usd=round(ai_monthly, 2),
        monthly_savings_usd=round(savings, 2),
        implementation_cost_usd=round(implementation, 2),
        payback_months=round(payback, 2) if payback != float("inf") else -1,
        first_year_roi_pct=round(first_year_roi, 1),
        automation_rate_pct=round(rate * 100, 1),
    )


def token_cost_per_item(model: str, tokens_in: int, tokens_out: int, calls_per_item: int = 4) -> float:
    """Token economics for one item flowing through the pipeline."""
    from .llm import cost_of
    return cost_of(model, tokens_in * calls_per_item, tokens_out * calls_per_item)
