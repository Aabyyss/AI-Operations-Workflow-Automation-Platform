"""Shadow mode: the gates on live traffic, with the actions switched off.

The value of shadow mode is entirely in what it *does not* do. A "safe"
scoring path that quietly queues a refund, sends an email, creates a review
or writes a run record would be worse than useless: it would look safe and
move the numbers it exists to measure.

So these tests assert absence first — no actions, no review, no run, no PII
in the ledger, containment untouched — and only then the arithmetic: a
refund over the limit escalates, a 2FA removal escalates, a pricing
question does not, and the report counts the would-haves with their
triggers.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config, shadow  # noqa: E402
from backend.models import Ticket  # noqa: E402
from backend.store import storage  # noqa: E402


def _ticket(subject="Charged twice", body=None) -> Ticket:
    return Ticket(customer_email="jordan@example.com", subject=subject,
                  body=body or "You charged my card twice — $49.00 twice. Refund one.")


# ------------------------------------------------------------------ scoring
def test_an_escalating_ticket_is_scored_as_would_escalate():
    record = shadow.score(_ticket(
        subject="Refund for annual plan",
        body="I want a $1200 refund for my annual plan, cancel it."))
    assert record["would_escalate"] is True
    assert record["would_auto_resolve"] is False
    assert record["risk_score"] >= config.AUTO_EXECUTE_MAX_RISK
    assert any("limit" in t for t in record["triggers"])


def test_a_2fa_removal_is_scored_as_a_security_escalation():
    record = shadow.score(_ticket("Remove 2FA", "Please remove 2FA from my account."))
    assert record["would_escalate"] is True
    assert any("2FA" in t for t in record["triggers"])


def test_a_low_risk_pricing_question_would_not_escalate():
    record = shadow.score(_ticket("Pricing question",
                                  "What do the plans cost for a 20 person team?"))
    assert record["would_escalate"] is False
    assert record["would_auto_resolve"] is True
    assert record["proposed_actions"]


def test_the_record_keeps_the_decision_and_not_the_customer():
    record = shadow.score(_ticket())
    blob = str(record)
    assert record["ticket"]["id"]
    assert "jordan@example.com" not in blob
    assert "You charged my card twice" not in blob
    assert record["retrieval"], "a grounded decision names its documents"
    assert set(record["gates"]) >= {"auto_execute_max_risk", "block_min_risk",
                                    "monetary_approval_limit_usd"}


def test_scoring_has_no_side_effects():
    shadow.score(_ticket())
    shadow.score(_ticket("Remove 2FA", "remove 2FA from my account please"))
    assert storage.all("runs") == []
    assert storage.all("reviews") == []
    assert storage.all("tickets") == []
    assert storage.all("outbox") == []
    assert len(storage.all("shadow")) == 2


def test_scoring_never_creates_a_signed_receipt():
    """A receipt attests to a decision that was executed. This one was not."""
    shadow.score(_ticket())
    assert storage.all("receipts") == []


# ------------------------------------------------------------------- report
def test_the_report_counts_would_have_escalations_with_triggers():
    shadow.score(_ticket("Refund for annual plan",
                         "Refund my $1200 annual plan now, cancel it."))
    shadow.score(_ticket("Remove 2FA", "remove 2FA from my account"))
    shadow.score(_ticket("Pricing question", "What do plans cost for 20 people?"))
    report = shadow.report()

    assert report["count"] == 3
    assert report["would_have_escalated"] == 2
    assert report["would_have_auto_resolved"] == 1
    assert report["escalation_rate_pct"] == 66.7
    assert report["top_triggers"]
    assert sum(t["tickets"] for t in report["top_triggers"]) >= 2
    assert "2 of them" in report["sample_note"]
    assert report["actions_that_would_have_run"]


def test_the_report_says_what_it_did_not_do():
    shadow.score(_ticket())
    report = shadow.report()
    assert report["live_traffic_is_shadowed"] is False
    assert "never writes to the run ledger" in report["what_it_does_not_do"]
    assert report["gates"]["auto_execute_max_risk"] == config.AUTO_EXECUTE_MAX_RISK


def test_an_empty_report_is_not_a_zero_claim():
    report = shadow.report()
    assert report["count"] == 0
    assert report["escalation_rate_pct"] == 0.0
    assert report["sample_note"] == "No tickets scored yet."


def test_clearing_starts_a_fresh_window():
    shadow.score(_ticket())
    assert shadow.clear() == 1
    assert shadow.report()["count"] == 0


# --------------------------------------------------------------- live traffic
def test_shadow_mode_scores_tickets_without_acting(client, monkeypatch):
    monkeypatch.setattr(config, "SHADOW_MODE", True)
    resp = client.post("/api/tickets", json={
        "customer_email": "a@b.com", "subject": "Remove 2FA",
        "body": "Please remove 2FA from my account, I lost my phone."})
    assert resp.status_code == 200
    body = resp.json()
    assert body["disposition"] == "shadow_scored"
    assert body["actions_taken"] == []
    assert body["final_response"] is None
    assert body["decision"]["needs_human_approval"] is True
    assert body["receipt_id"] is None
    # Nothing executed, nothing queued, nothing counted as a run.
    assert storage.all("reviews") == []
    assert storage.all("runs") == []
    assert storage.all("outbox") == []
    assert len(storage.all("shadow")) == 1


def test_shadow_mode_leaves_the_answer_endpoint_alone(client, monkeypatch):
    monkeypatch.setattr(config, "SHADOW_MODE", True)
    client.post("/api/tickets", json={
        "customer_email": "a@b.com", "subject": "Charged twice",
        "body": "You charged my card twice for $49, please refund one."})
    summary = client.get("/api/analytics/summary").json()
    assert summary["tickets_processed"] == 0        # a shadow run is not a run
    assert summary["automation_rate_pct"] == 0.0
    assert client.get("/api/analytics/shadow").json()["count"] == 1


def test_shadow_off_is_the_normal_pipeline(client):
    resp = client.post("/api/tickets", json={
        "customer_email": "a@b.com", "subject": "Charged twice",
        "body": "You charged my card twice for $49, please refund one."})
    assert resp.json()["disposition"] != "shadow_scored"
    assert len(storage.all("runs")) == 1
    assert storage.all("shadow") == []


# ---------------------------------------------------------------- endpoints
def test_the_score_endpoint_records_without_acting(client):
    resp = client.post("/api/shadow/score", json={
        "customer_email": "a@b.com", "subject": "Refund for annual plan",
        "body": "Refund my $1200 annual plan."})
    assert resp.status_code == 200
    body = resp.json()
    assert body["would_escalate"] is True
    assert storage.all("runs") == [] and storage.all("reviews") == []


def test_the_report_endpoint_aggregates(client):
    for body in ("Refund my $1200 annual plan.",
                 "What do the plans cost for a 20 person team?"):
        client.post("/api/shadow/score", json={
            "customer_email": "a@b.com", "subject": "Pricing question", "body": body})
    report = client.get("/api/analytics/shadow").json()
    assert report["count"] == 2
    assert report["would_have_escalated"] == 1


def test_clearing_the_window_is_an_admin_mutation(client, monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "master")
    monkeypatch.setattr(config, "APPROVER_KEY", "approver")
    client.post("/api/shadow/score", headers={"X-API-Key": "master"}, json={
        "customer_email": "a@b.com", "subject": "Support", "body": "hello there"})
    denied = client.delete("/api/analytics/shadow", headers={"X-API-Key": "approver"})
    assert denied.status_code == 403
    allowed = client.delete("/api/analytics/shadow", headers={"X-API-Key": "master"})
    assert allowed.status_code == 200
    assert allowed.json()["cleared"] == 1
