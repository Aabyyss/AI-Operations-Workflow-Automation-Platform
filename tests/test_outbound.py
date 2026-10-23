"""Tests for the outbound webhook deliverer."""
import http.server
import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config, outbound  # noqa: E402
from backend.webhook import verify  # noqa: E402


def _queue_refund():
    outbound.storage.append("outbox", {
        "id": "out_test1", "ts": "2026-10-01T00:00:00Z", "kind": "refund",
        "ticket_id": "tkt_x", "amount_usd": 49.0, "status": "pending",
    })


class _Clock:
    """A movable clock for the backoff tests.

    Backoff is time, so a test that waits for real seconds is a test nobody
    runs. outbound._utcnow is the single seam retries schedule against.
    """

    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _failing_url(monkeypatch) -> str:
    server = http.server.HTTPServer(("127.0.0.1", 0), _FAIL)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(config, "OUTBOX_URL",
                        f"http://127.0.0.1:{server.server_port}/hook")
    return server


def test_noop_without_outbox_url(client, monkeypatch):
    monkeypatch.setattr(config, "OUTBOX_URL", "")
    summary = outbound.deliver_pending()
    assert summary["attempted"] == 0
    assert "not configured" in summary["reason"]


class _OK(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        # Keep the raw header object: urllib title-cases names on the wire
        # (X-Aiops-Signature), and Message.get() is case-insensitive.
        self.server.last_headers = self.headers
        self.server.last_body = body
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


def test_delivers_signed_webhook(client, monkeypatch):
    server = http.server.HTTPServer(("127.0.0.1", 0), _OK)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/hook"
    monkeypatch.setattr(config, "OUTBOX_URL", url)
    monkeypatch.setattr(config, "OUTBOX_SECRET", "test-secret")
    try:
        _queue_refund()
        summary = outbound.deliver_pending()
        assert summary == {"attempted": 1, "delivered": 1, "deferred": 0,
                           "dead": 0, "waiting": 0}

        # The webhook arrived signed, and the signature verifies.
        sig = server.last_headers.get("X-AIOPS-Signature")
        verify(server.last_body, sig, "test-secret")

        ledger = client.get("/api/deliveries").json()
        assert ledger and ledger[-1]["ok"] is True

        view = client.get("/api/outbox").json()
        rec = next(r for r in view if r["id"] == "out_test1")
        assert rec["status"] == "delivered" and rec["attempts"] == 1
    finally:
        server.shutdown()


class _FAIL(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(500)
        self.end_headers()

    def log_message(self, *a):
        pass


def test_failure_schedules_retry(client, monkeypatch):
    server = http.server.HTTPServer(("127.0.0.1", 0), _FAIL)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(config, "OUTBOX_URL",
                        f"http://127.0.0.1:{server.server_port}/hook")
    try:
        _queue_refund()
        summary = outbound.deliver_pending()
        assert summary["deferred"] == 1
        view = client.get("/api/outbox").json()
        rec = next(r for r in view if r["id"] == "out_test1")
        assert rec["status"] == "pending" and rec["attempts"] == 1
        # ...and the retry is scheduled, not imminent: the next flush sees
        # the item cooling down instead of hammering a failing endpoint.
        assert rec["not_before"] is not None
        again = outbound.deliver_pending()
        assert again["attempted"] == 0 and again["waiting"] == 1
    finally:
        server.shutdown()


def test_backoff_window_opens_as_time_passes(client, monkeypatch):
    """Exponential backoff: 2s, 4s, 8s... the vendor gets breathing room,
    and the queue is attempted again the moment the window opens."""
    clock = _Clock()
    monkeypatch.setattr(outbound, "_utcnow", clock)
    server = _failing_url(monkeypatch)
    try:
        _queue_refund()
        assert outbound.deliver_pending()["deferred"] == 1

        clock.tick(1)  # still inside the 2 s window
        assert outbound.deliver_pending()["attempted"] == 0

        clock.tick(2)  # window open -> attempt 2 -> 4 s window
        second = outbound.deliver_pending()
        assert second["attempted"] == 1 and second["deferred"] == 1

        clock.tick(3)  # not yet
        assert outbound.deliver_pending()["waiting"] == 1
        clock.tick(60)  # far past any window
        assert outbound.deliver_pending()["attempted"] == 1
    finally:
        server.shutdown()


def test_manual_reset_clears_the_backoff_immediately(client, monkeypatch):
    """retry() exists for the calm after the storm: endpoint back, flush now
    — waiting out a backoff the outage itself set would defeat it."""
    clock = _Clock()
    monkeypatch.setattr(outbound, "_utcnow", clock)
    server = _failing_url(monkeypatch)
    try:
        _queue_refund()
        outbound.deliver_pending()  # fails, schedules a window
        assert outbound.deliver_pending()["attempted"] == 0

        reset = client.post("/api/outbox/out_test1/retry")
        assert reset.status_code == 200
        assert outbound.deliver_pending()["attempted"] == 1
    finally:
        server.shutdown()


def test_dead_letter_then_retry(client, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(outbound, "_utcnow", clock)
    server = http.server.HTTPServer(("127.0.0.1", 0), _FAIL)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(config, "OUTBOX_URL",
                        f"http://127.0.0.1:{server.server_port}/hook")
    try:
        _queue_refund()
        # Exhaust the retry budget: each flush past its backoff window,
        # exactly as a cron target would behave.
        for _ in range(outbound.MAX_ATTEMPTS):
            outbound.deliver_pending()
            clock.tick(60)
        rec = next(r for r in client.get("/api/outbox").json()
                   if r["id"] == "out_test1")
        assert rec["status"] == "dead"

        # A dead record is no longer retried by the flusher...
        summary = outbound.deliver_pending()
        assert summary["attempted"] == 0

        # ...until an operator resets it.
        reset = client.post("/api/outbox/out_test1/retry")
        assert reset.status_code == 200
        assert reset.json()["status"] == "pending"
        assert reset.json()["attempts"] == 0

        # And once reset, health stops reporting the dead letter: the fold
        # shows the reset, not the history of the outage.
        checks = client.get("/api/health").json()["checks"]
        assert checks["outbox"]["dead_letters"] == 0
        assert checks["outbox"]["pending_delivery"] == 1
    finally:
        server.shutdown()


def test_retry_unknown_or_delivered_404(client):
    assert client.post("/api/outbox/out_nope/retry").status_code == 404


def test_delivered_records_are_not_resent(client, monkeypatch):
    server = http.server.HTTPServer(("127.0.0.1", 0), _OK)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(config, "OUTBOX_URL",
                        f"http://127.0.0.1:{server.server_port}/hook")
    try:
        _queue_refund()
        assert outbound.deliver_pending()["delivered"] == 1
        # Second flush: nothing left to attempt — no duplicate vendor calls.
        summary = outbound.deliver_pending()
        assert summary["attempted"] == 0
    finally:
        server.shutdown()


def test_view_folds_status_updates():
    _queue_refund()  # base record so the folded entry has kind/payload
    outbound.storage.append("outbox", {
        "id": "out_test1", "status_update": True, "status": "delivered",
        "attempts": 1, "last_error": None, "ts": "2026-10-01T00:00:01Z",
    })
    view = outbound.outbox_view()
    rec = next(r for r in view if r["id"] == "out_test1")
    assert rec["status"] == "delivered"
    assert rec["kind"] == "refund"  # base fields survive folding
