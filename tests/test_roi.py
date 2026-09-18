"""Tests for the ROI/cost engine and process analyzer."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import roi  # noqa: E402
from backend.analyzer import analyze_process  # noqa: E402
from backend.models import ProcessInput, ProcessStep  # noqa: E402


def _process(**kw):
    base = dict(
        name="Ticket Handling",
        monthly_volume=1500,
        hourly_rate_usd=25.0,
        steps=[
            ProcessStep(name="Read", minutes_per_item=1.5, repetitive=True, touches_pii=True),
            ProcessStep(name="Categorize", minutes_per_item=1.0, repetitive=True),
            ProcessStep(name="Search KB", minutes_per_item=2.5, repetitive=True, requires_judgment=True),
            ProcessStep(name="Draft response", minutes_per_item=4.0, repetitive=True, requires_judgment=True),
            ProcessStep(name="Refund/CRM", minutes_per_item=2.0, repetitive=True,
                        touches_money=True, touches_pii=True),
        ],
    )
    base.update(kw)
    return ProcessInput(**base)


def test_current_cost_matches_labor_math():
    p = _process()
    costs = roi.costs(p)
    total_minutes = 11.0
    expected = 1500 * total_minutes / 60 * 25.0
    assert costs.current_monthly_cost_usd == round(expected, 2)


def test_ai_cost_is_lower_and_savings_positive():
    costs = roi.costs(_process())
    assert costs.ai_monthly_cost_usd < costs.current_monthly_cost_usd
    assert costs.monthly_savings_usd > 0
    assert costs.payback_months > 0
    assert costs.first_year_roi_pct > 0


def test_money_touching_steps_capped_from_full_automation():
    p = _process()
    rate = roi.automation_rate(p)
    assert rate < 1.0  # money+judgment steps are never fully automated
    costs = roi.costs(p)
    assert costs.automation_rate_pct == round(rate * 100, 1)


def test_high_volume_repetitive_process_scores_high():
    score = roi.score_process(_process(monthly_volume=3000))
    assert score.total >= 65
    assert score.verdict == "high_potential"


def test_low_volume_judgment_process_scores_lower():
    score = roi.score_process(_process(monthly_volume=50))
    assert score.total < score.volume_score + 60
    assert score.verdict in ("moderate", "low_potential")


def test_analyzer_produces_mapping_and_assumptions():
    analysis, usage = analyze_process(_process())
    recs = {m["step"]: m["recommendation"] for m in analysis.ai_mapping}
    assert recs["Refund/CRM"] == "human_approval"
    assert any(r in ("automate", "assist") for r in recs.values())
    assert len(analysis.assumptions) >= 3
    assert usage and usage[0].agent == "process_analyzer"
    assert usage[0].mode == "mock"
