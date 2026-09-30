"""Tests for model routing (light/heavy tiers)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402
from backend.llm import get_llm, model_for_tier  # noqa: E402


def test_tier_resolution_defaults_to_chat_model(monkeypatch):
    monkeypatch.setattr(config, "LIGHT_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(config, "HEAVY_MODEL", "gpt-4o")
    assert model_for_tier("light") == "gpt-4o-mini"
    assert model_for_tier("heavy") == "gpt-4o"
    assert model_for_tier("anything-else") == "gpt-4o-mini"


def test_routing_visible_in_usage_records(client):
    client.post("/api/tickets", json={
        "customer_email": "jordan.miles@northwind.example",
        "subject": "Charged twice for my subscription",
        "body": "Charged twice - $49.00 on the 3rd and again on the 5th.",
    })
    usage = client.get("/api/usage").json()
    by_agent = {u["agent"]: u["model"] for u in usage}
    assert by_agent["intake"] == "mock-light"    # classification-shaped work
    assert by_agent["quality"] == "mock-light"   # gate — cheap tier
    assert by_agent["draft"] == "mock-heavy"     # customer-facing prose


def test_routing_is_inert_until_configured():
    """Both tiers default to CHAT_MODEL — no surprise model changes."""
    assert config.LIGHT_MODEL == config.CHAT_MODEL
    assert config.HEAVY_MODEL == config.CHAT_MODEL
