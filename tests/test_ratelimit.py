"""Tests for the opt-in sliding-window rate limiter."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from backend import config  # noqa: E402
from backend.ratelimit import SlidingWindow  # noqa: E402


def test_window_math():
    w = SlidingWindow()
    allowed = sum(w.allow(3, 60)[0] for _ in range(5))
    assert allowed == 3  # 4th and 5th hit the cap


def test_window_slides():
    w = SlidingWindow()
    for _ in range(3):
        w.allow(3, 60)
    assert w.allow(3, 60)[0] is False
    # Age every hit out of the window.
    w.hits.extend([])  # no-op clarity
    w.hits.clear()
    w.hits.append(0.0)  # ancient timestamp
    allowed, _ = w.allow(3, 60)
    assert allowed is True


def test_retry_after_is_positive():
    w = SlidingWindow()
    for _ in range(2):
        w.allow(2, 60)
    allowed, retry_after = w.allow(2, 60)
    assert allowed is False
    assert retry_after > 0


@pytest.fixture()
def limited_client(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT", 2)
    monkeypatch.setattr(config, "RATE_LIMIT_WINDOW_S", 60)
    from fastapi.testclient import TestClient
    from backend.main import app
    with TestClient(app) as c:
        yield c


def test_off_by_default(client):
    """The whole suite runs against this client — 50+ requests, zero 429s."""
    assert all(client.get("/api/tickets").status_code == 200 for _ in range(30))


def test_429_with_retry_after(limited_client):
    assert limited_client.get("/api/tickets").status_code == 200
    assert limited_client.get("/api/tickets").status_code == 200
    r = limited_client.get("/api/tickets")
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1


def test_health_exempt(limited_client):
    for _ in range(5):
        assert limited_client.get("/health").status_code == 200
