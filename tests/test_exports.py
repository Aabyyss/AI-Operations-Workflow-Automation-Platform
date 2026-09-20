"""Tests for CSV exports (schema-pinned Power BI feeds)."""
import sys
import csv
import io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import exports  # noqa: E402


def _read(text: str) -> tuple[list[str], list[list[str]]]:
    rows = list(csv.reader(io.StringIO(text)))
    return rows[0], rows[1:]


def test_runs_csv_schema_and_mapping():
    runs = [{
        "id": "run_1", "ticket_id": "tkt_1", "disposition": "auto_resolved",
        "intake": {"category": "billing"}, "decision": {"risk_score": 0.1,
                                                        "confidence": 0.9},
        "actions_taken": ["refund_queued", "email_sent"],
        "total_cost_usd": 0.00023, "total_latency_ms": 12,
        "review_id": None,
    }]
    header, rows = _read(exports.runs_csv(runs))
    assert header == exports.RUN_COLUMNS
    row = dict(zip(header, rows[0]))
    assert row["run_id"] == "run_1"
    assert row["category"] == "billing"
    assert row["risk_score"] == "0.1"
    assert row["actions_taken"] == "refund_queued;email_sent"
    assert row["cost_usd"] == "0.00023"


def test_reviews_and_usage_csv_headers_even_when_empty():
    h1, rows1 = _read(exports.reviews_csv([]))
    assert h1 == exports.REVIEW_COLUMNS and rows1 == []
    h2, rows2 = _read(exports.usage_csv([]))
    assert h2 == exports.USAGE_COLUMNS and rows2 == []


def test_export_endpoints_live(client):
    client.post("/api/tickets/demo")
    r = client.get("/api/export/runs.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    header, rows = _read(r.text)
    assert header == exports.RUN_COLUMNS
    assert len(rows) == 1

    h, _ = _read(client.get("/api/export/approvals.csv").text)
    assert h == exports.REVIEW_COLUMNS
    h, _ = _read(client.get("/api/export/usage.csv").text)
    assert h == exports.USAGE_COLUMNS
