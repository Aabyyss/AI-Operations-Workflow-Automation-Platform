"""Tests for the agentic pipeline and its governance gates."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import pipeline, store  # noqa: E402
from backend.models import Ticket  # noqa: E402


def _ticket(subject, body, email="sam@customer.example"):
    return Ticket(customer_email=email, subject=subject, body=body)


def test_small_duplicate_refund_auto_resolves():
    r = pipeline.run_pipeline(_ticket(
        "Charged twice",
        "I've been charged twice for my subscription — $49.00. I need one refunded."))
    assert r.disposition == "auto_resolved"
    assert r.decision.can_auto_resolve is True
    assert r.decision.risk_score <= 0.30
    assert r.final_response and "refund" in r.final_response.lower()
    assert any(a.startswith("refund_queued") for a in r.actions_taken)
    assert r.total_cost_usd >= 0
    assert len(r.trace) >= 4  # intake, knowledge, decision, draft, quality, actions


def test_large_refund_requires_human_approval():
    r = pipeline.run_pipeline(_ticket(
        "Refund request for annual plan",
        "I cancelled but was charged $1,200.00 for the annual plan. I need a refund."))
    assert r.disposition == "human_review"
    assert r.decision.risk_score >= 0.30
    assert r.review_id is not None
    assert r.final_response is None  # nothing sent without approval


def test_unknown_topic_escalates():
    r = pipeline.run_pipeline(_ticket(
        "Question about onboarding",
        "Can you walk me through onboarding for a new team? Not covered in docs."))
    assert r.disposition == "human_review"
    assert r.decision.risk_score >= 0.30
    assert "no confident policy match" in r.decision.reason


def test_two_factor_request_escalates_on_security_grounds():
    r = pipeline.run_pipeline(_ticket(
        "Remove 2FA",
        "I lost my phone and need you to turn off 2FA on my account."))
    assert r.disposition == "human_review"
    assert any("2FA" in r.decision.reason for _ in [0])


def test_password_reset_auto_resolves():
    r = pipeline.run_pipeline(_ticket(
        "Can't sign in",
        "I forgot my password, can you help me reset it?"))
    assert r.disposition == "auto_resolved"
    assert any(a.startswith("email_sent") for a in r.actions_taken)


def test_tech_issue_auto_resolves_with_troubleshooting():
    r = pipeline.run_pipeline(_ticket(
        "API returns 429",
        "I keep getting 429 errors from the API all morning."))
    assert r.disposition == "auto_resolved"


def test_all_runs_and_usage_recorded():
    before = len(store.storage.all("runs"))
    pipeline.run_pipeline(_ticket("Pricing question", "How much is the growth plan per user?"))
    after = store.storage.all("runs")
    assert len(after) == before + 1
    assert after[-1]["intake"]["category"] in ("sales", "other")


def test_review_request_contains_proposed_response():
    r = pipeline.run_pipeline(_ticket(
        "Enterprise refund request",
        "We were charged $2,500.00 this month and want a full refund."))
    assert r.disposition == "human_review"
    reviews = store.storage.all("reviews")
    assert reviews and reviews[-1]["ticket_id"] == r.ticket_id
    assert reviews[-1]["proposed_response"]
