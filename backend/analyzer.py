"""Business process analyzer (Phase 1 + 2 combined).

Combines an LLM pass (per-step AI recommendations) with the deterministic
ROI engine to produce a full business case: score, costs, mapping,
assumptions. This is the artifact an AI PM would put in front of leadership.
"""
from __future__ import annotations

import json

from . import config, roi
from .llm import get_llm, record_usage
from .models import Analysis, AutomationScore, CostBreakdown, ProcessInput, new_id


def _process_prompt(p: ProcessInput) -> str:
    lines = ["Analyze this business process for AI automation.", ""]
    for i, s in enumerate(p.steps, 1):
        lines.append(
            f"STEP|{s.name}|{s.minutes_per_item}|{1 if s.repetitive else 0}|"
            f"{1 if s.requires_judgment else 0}|{1 if s.touches_money else 0}|"
            f"{1 if s.touches_pii else 0}|{1 if s.structured_data else 0}"
        )
    lines.append("MOCK_TASK:{\"task\":\"analyze_process\"}")
    return "\n".join(lines)


def analyze_process(p: ProcessInput) -> tuple[Analysis, list]:
    llm = get_llm()
    resp = llm.chat(
        system=(
            "You are an AI automation consultant. For each process step decide: "
            "automate | assist | human_approval | keep. Be conservative where "
            "money or personal data is involved."
        ),
        user=_process_prompt(p),
    )
    usage = record_usage(llm, resp["model"], resp["tokens_in"], resp["tokens_out"], agent="process_analyzer")

    mapping = json.loads(resp["text"])["steps"]

    score: AutomationScore = roi.score_process(p)
    costs: CostBreakdown = roi.costs(p)

    assumptions = [
        f"Blended human rate: ${p.hourly_rate_usd}/h",
        f"AI token cost assumed at ${config.DEFAULT_COST_PER_TICKET_USD:.3f}/item until real usage data exists",
        f"Human effort retained at {int(roi.HUMAN_REMAINING_FRACTION * 100)}% of original minutes for exceptions and oversight",
        "Implementation estimate: base fee + per-automated-step integration effort",
    ]

    analysis = Analysis(
        process_id=new_id("proc"),
        name=p.name,
        created_at=iso_now(),
        score=score,
        costs=costs,
        ai_mapping=mapping,
        assumptions=assumptions,
    )
    return analysis, [usage]


def iso_now() -> str:
    from .models import iso_now as _f
    return _f()
