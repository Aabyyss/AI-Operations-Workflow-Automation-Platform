"""Central configuration for the AI Operations & Workflow Automation Platform.

Everything is intentionally explicit: an AI PM should know where every
number in an ROI estimate or routing decision comes from.
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
KNOWLEDGE_DIR = PROJECT_ROOT / "knowledge_base"
DATA_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Runtime mode
#
# "mock"  : deterministic, zero-API-key, rule-based pipeline (default).
# "live"  : route every LLM call through the configured OpenAI-compatible
#           provider. The same pipeline and the same Pydantic contracts are
#           used either way; only the LLM gateway swaps.
# ---------------------------------------------------------------------------
MODE = os.getenv("AIOPS_MODE", "mock").lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
CHAT_MODEL = os.getenv("AIOPS_CHAT_MODEL", "gpt-4o-mini")
EMBED_MODEL = os.getenv("AIOPS_EMBED_MODEL", "text-embedding-3-small")

# Public base URL n8n (or any external system) uses to reach this API.
# In docker-compose, n8n reaches the API by service name.
API_BASE_URL = os.getenv("AIOPS_API_BASE_URL", "http://api:8000")

# ---------------------------------------------------------------------------
# Governance thresholds — the heart of the human-in-the-loop story.
# Env-overridable so ops can tune per deployment without a code change.
# ---------------------------------------------------------------------------
AUTO_EXECUTE_MAX_RISK = float(os.getenv("AIOPS_AUTO_EXECUTE_MAX_RISK", "0.30"))  # <= this -> run without a human
BLOCK_MIN_RISK = 0.85            # > this -> never auto-run, always review

# Monetary limits: any action touching money above this always requires
# human approval regardless of classifier confidence.
MONETARY_APPROVAL_LIMIT_USD = float(os.getenv("AIOPS_MONETARY_APPROVAL_LIMIT_USD", "500.0"))

# Minimum retrieval similarity for the Knowledge Agent to trust a document.
MIN_RETRIEVAL_SCORE = float(os.getenv("AIOPS_MIN_RETRIEVAL_SCORE", "0.15"))

# Minimum blended confidence (decision + quality) to auto-execute.
MIN_AUTO_CONFIDENCE = float(os.getenv("AIOPS_MIN_AUTO_CONFIDENCE", "0.75"))
TOKEN_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "text-embedding-3-small": (0.02, 0.0),
}

# Default blended cost per resolved ticket used when no real usage data
# exists yet (estimate until monitoring fills in actuals).
DEFAULT_COST_PER_TICKET_USD = 0.012

# ---------------------------------------------------------------------------
# Labor economics used by the ROI engine (defaults, overridable per process)
# ---------------------------------------------------------------------------
DEFAULT_HOURLY_RATE_USD = 25.0
DEFAULT_MONTHLY_VOLUME = 1000
DEFAULT_MINUTES_PER_ITEM = 6.0


def is_live() -> bool:
    """True when configured for real LLM calls."""
    return MODE == "live" and bool(OPENAI_API_KEY)
