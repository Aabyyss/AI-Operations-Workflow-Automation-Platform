"""Central configuration for the AI Operations & Workflow Automation Platform.

Everything is intentionally explicit: an AI PM should know where every
number in an ROI estimate or routing decision comes from.
"""
from __future__ import annotations

import os
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    """Read an environment variable, treating set-but-empty as unset.

    `os.getenv`'s own default only covers the *missing* case. Under
    docker-compose every variable is passed as `${VAR:-}`, which expands to
    an explicitly empty string when the operator's .env has no value — so
    `int(_env("AIOPS_RATE_LIMIT", "60"))` raises ValueError at boot on
    `int("")`, and a string default like the chat model is silently
    replaced by "", which is then sent to the API as a model name.

    Empty (or whitespace-only) therefore means "not configured" everywhere
    below, which is what every one of these variables already means when it
    is absent.
    """
    value = os.getenv(name)
    return default if value is None or not value.strip() else value

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# The storage directory. Overridable because the offline receipt tooling
# (scripts/rotate_receipt_key.py, scripts/verify_receipt.py) runs in its own
# process and has to be pointed at a deployment's data dir explicitly.
DATA_DIR = Path(_env("AIOPS_DATA_DIR") or (PROJECT_ROOT / "data"))
KNOWLEDGE_DIR = PROJECT_ROOT / "knowledge_base"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Storage backend
#
# "json"     : zero-config JSON files under DATA_DIR (default — the demo,
#              tests and CI never need a database).
# "postgres" : the same Storage interface over Postgres/JSONB with a
#              pgvector-ready schema. Requires AIOPS_DATABASE_URL and the
#              optional driver: pip install -r requirements-postgres.txt
# ---------------------------------------------------------------------------
STORAGE_BACKEND = _env("AIOPS_STORAGE", "json").strip().lower()
DATABASE_URL = _env("AIOPS_DATABASE_URL", "")

# ---------------------------------------------------------------------------
# Runtime mode
#
# "mock"  : deterministic, zero-API-key, rule-based pipeline (default).
# "live"  : route every LLM call through the configured OpenAI-compatible
#           provider. The same pipeline and the same Pydantic contracts are
#           used either way; only the LLM gateway swaps.
# ---------------------------------------------------------------------------
MODE = _env("AIOPS_MODE", "mock").lower()
OPENAI_API_KEY = _env("OPENAI_API_KEY", "")
OPENAI_BASE_URL = _env("OPENAI_BASE_URL", "https://api.openai.com/v1")
CHAT_MODEL = _env("AIOPS_CHAT_MODEL", "gpt-4o-mini")

# Model routing: cheap model for classification-shaped work (intake,
# quality gates), stronger model where quality pays (drafting). Both
# default to CHAT_MODEL so routing is inert until configured.
LIGHT_MODEL = _env("AIOPS_LIGHT_MODEL", CHAT_MODEL)
HEAVY_MODEL = _env("AIOPS_HEAVY_MODEL", CHAT_MODEL)

# Monthly LLM budget in USD; 0 disables budget alerts entirely.
MONTHLY_BUDGET_USD = float(_env("AIOPS_MONTHLY_BUDGET_USD", "0"))
EMBED_MODEL = _env("AIOPS_EMBED_MODEL", "text-embedding-3-small")

# Public base URL n8n (or any external system) uses to reach this API.
# In docker-compose, n8n reaches the API by service name.
API_BASE_URL = _env("AIOPS_API_BASE_URL", "http://api:8000")

# Security: when set, every API request must present this key (X-API-Key or
# Authorization: Bearer). Unset = open API, the right default for local dev.
API_KEY = _env("AIOPS_API_KEY", "")
# Role-scoped keys below the master key: an approver may read everything and
# decide reviews; an admin may run admin mutations (prune, knowledge-corpus
# writes) but not approve refunds. Unset = role unused, master key stays the
# only path.
APPROVER_KEY = _env("AIOPS_APPROVER_KEY", "")
ADMIN_KEY = _env("AIOPS_ADMIN_KEY", "")
# Shadow mode (backend/shadow.py): when on, /api/tickets scores each ticket
# through the same intake, retrieval and risk gates but executes nothing — no
# draft, no email, no refund, no review, no run recorded. The evaluation
# question ("what would it have escalated?") with the blast radius of a log.
SHADOW_MODE = _env("AIOPS_SHADOW", "0").strip().lower() in ("1", "true", "yes", "on")

# Decision receipts (backend/receipts.py): hex-encoded 32-byte Ed25519 seed
# used to sign one receipt per pipeline decision. Unset = a key is generated
# once into DATA_DIR/receipt_signing_key.json (fine for a demo, not for a
# deployment that has to prove who signed).
RECEIPT_KEY = _env("AIOPS_RECEIPT_KEY", "")
# How long a receipt signing key may serve before health calls it due for
# rotation. Rotation already kept every old receipt verifiable; this is the
# number that turns "we should rotate" from memory into a report. 0 disables
# the warning (an externally managed key with its own schedule).
RECEIPT_KEY_MAX_AGE_DAYS = float(_env("AIOPS_RECEIPT_KEY_MAX_AGE_DAYS", "365"))
# Shared secret used to verify HMAC-SHA256 signatures on the n8n intake
# webhook. Unset = signatures are accepted but not enforced.
WEBHOOK_SECRET = _env("AIOPS_WEBHOOK_SECRET", "")

# Outbound webhook delivery (backend/outbound.py): where queued actions are
# POSTed as signed webhooks. Empty = delivery disabled, outbox stays a
# durable queue.
OUTBOX_URL = _env("AIOPS_OUTBOX_URL", "")
OUTBOX_SECRET = _env("AIOPS_OUTBOX_SECRET", "")
# Max age of a signed webhook payload (replay window).
WEBHOOK_MAX_SKEW_S = int(_env("AIOPS_WEBHOOK_MAX_SKEW_S", "300"))

# Rate limiting: requests per client IP per sliding window. 0 = disabled
# (default - a single-tenant deployment rarely needs it).
RATE_LIMIT = int(_env("AIOPS_RATE_LIMIT", "0"))
RATE_LIMIT_WINDOW_S = int(_env("AIOPS_RATE_LIMIT_WINDOW_S", "60"))

# Request logging: "plain" (uvicorn default) or "json" (structured,
# one JSON object per request on stdout - for shippers and SIEMs).
LOG_FORMAT = _env("AIOPS_LOG_FORMAT", "plain")
# When set (and LOG_FORMAT=json), each record is also appended to this file.
# stdout alone is not durable: `docker compose down` takes the container's
# stream with it, and a shipper pointed at a file is the common setup.
REQUEST_LOG_FILE = _env("AIOPS_REQUEST_LOG_FILE")

# ---------------------------------------------------------------------------
# Governance thresholds — the heart of the human-in-the-loop story.
# Env-overridable so ops can tune per deployment without a code change.
# ---------------------------------------------------------------------------
AUTO_EXECUTE_MAX_RISK = float(_env("AIOPS_AUTO_EXECUTE_MAX_RISK", "0.30"))  # <= this -> run without a human
BLOCK_MIN_RISK = float(_env("AIOPS_BLOCK_MIN_RISK", "0.85"))  # > this -> never auto-run, always review

# The ladder has two ways to fall over once both ends are knobs, so both are
# checked at import — a governance rule nobody can rely on is worse than no
# rule, and neither misconfiguration fails visibly on its own:
#
#   * block <= auto would block exactly the band the ladder promises to run,
#     silently inverting the documented "≤ 0.30 auto, > 0.85 block".
#   * block > 1.0 can never fire: risk is clamped to 1.0 before it is
#     compared, so the hard block would stand there armed and inert while
#     every receipt kept recording it as in force.
if BLOCK_MIN_RISK <= AUTO_EXECUTE_MAX_RISK:
    raise RuntimeError(
        "AIOPS_BLOCK_MIN_RISK must be greater than AIOPS_AUTO_EXECUTE_MAX_RISK "
        f"(got block={BLOCK_MIN_RISK}, auto={AUTO_EXECUTE_MAX_RISK}): between "
        "them they define which tickets run without a human, and a block at or "
        "below the auto threshold contradicts the ladder the governance "
        "documentation promises")
if BLOCK_MIN_RISK > 1.0:
    raise RuntimeError(
        f"AIOPS_BLOCK_MIN_RISK={BLOCK_MIN_RISK} can never be reached: risk is "
        "clamped to 1.0 before comparison, so the hard block would never fire "
        "— keep it at or below 1.0")

# Monetary limits: any action touching money above this always requires
# human approval regardless of classifier confidence.
MONETARY_APPROVAL_LIMIT_USD = float(_env("AIOPS_MONETARY_APPROVAL_LIMIT_USD", "500.0"))

# Minimum retrieval similarity for the Knowledge Agent to trust a document.
MIN_RETRIEVAL_SCORE = float(_env("AIOPS_MIN_RETRIEVAL_SCORE", "0.15"))

# Minimum blended confidence (decision + quality) to auto-execute.
MIN_AUTO_CONFIDENCE = float(_env("AIOPS_MIN_AUTO_CONFIDENCE", "0.75"))
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
