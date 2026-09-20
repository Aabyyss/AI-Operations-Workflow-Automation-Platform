"""Shared Pydantic contracts for the whole platform.

If an AI PM can't describe a system's data model precisely, the system
can't be governed. Every agent hand-off in this repo is a typed object.
"""
from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Enums — controlled vocabularies an enterprise would govern
# ---------------------------------------------------------------------------
class TicketCategory(str, Enum):
    BILLING = "billing"
    TECHNICAL = "technical"
    ACCOUNT = "account"
    SALES = "sales"
    OTHER = "other"


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class Disposition(str, Enum):
    """What the pipeline decided to do with a ticket."""
    AUTO_RESOLVED = "auto_resolved"        # AI resolved end-to-end
    AUTO_REPLIED = "auto_replied"          # draft sent after low-risk approval path
    HUMAN_REVIEW = "human_review"          # waiting for a human
    APPROVED_EXECUTED = "approved_executed"  # human approved, then executed
    REJECTED = "rejected"                  # human rejected the AI plan
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Business process (Side A — the AI PM input)
# ---------------------------------------------------------------------------
class ProcessStep(BaseModel):
    name: str
    description: str = ""
    minutes_per_item: float = Field(gt=0, description="Human minutes per item")
    repetitive: bool = True
    requires_judgment: bool = False
    touches_money: bool = False
    touches_pii: bool = False
    structured_data: bool = True


class ProcessInput(BaseModel):
    name: str
    description: str = ""
    department: str = "operations"
    monthly_volume: int = Field(default=1000, gt=0)
    hourly_rate_usd: float = Field(default=25.0, gt=0)
    steps: list[ProcessStep]
    error_rate_pct: float = Field(default=2.0, ge=0, description="Current human error rate")


# ---------------------------------------------------------------------------
# ROI engine outputs
# ---------------------------------------------------------------------------
class CostBreakdown(BaseModel):
    current_monthly_cost_usd: float
    ai_monthly_cost_usd: float
    monthly_savings_usd: float
    implementation_cost_usd: float
    payback_months: float
    first_year_roi_pct: float
    automation_rate_pct: float


class AutomationScore(BaseModel):
    total: float = Field(ge=0, le=100)
    volume_score: float
    repetitiveness_score: float
    structure_score: float
    judgment_penalty: float
    risk_penalty: float
    verdict: Literal["high_potential", "moderate", "low_potential"]
    rationale: str


class Analysis(BaseModel):
    process_id: str
    name: str
    created_at: str
    score: AutomationScore
    costs: CostBreakdown
    ai_mapping: list[dict[str, Any]] = Field(
        description="Per-step recommendation: keep / assist / automate / human-approval"
    )
    assumptions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Ticket pipeline (Side B — the integration reality)
# ---------------------------------------------------------------------------
class Ticket(BaseModel):
    id: str = Field(default_factory=lambda: new_id("tkt"))
    created_at: str = Field(default_factory=iso_now)
    customer_email: str
    subject: str
    body: str
    channel: Literal["email", "web", "slack"] = "email"


class IntakeResult(BaseModel):
    category: TicketCategory
    priority: Priority
    intent: str
    entities: dict[str, Any] = Field(default_factory=dict)
    requested_refund_usd: float = 0.0
    confidence: float = Field(ge=0, le=1)


class RetrievedChunk(BaseModel):
    doc_id: str
    title: str
    text: str
    score: float


class KnowledgeResult(BaseModel):
    chunks: list[RetrievedChunk] = Field(default_factory=list)
    policy_summary: str = ""
    sufficient: bool = False
    confidence: float = Field(default=0.0, ge=0, le=1)


class DecisionResult(BaseModel):
    can_auto_resolve: bool
    needs_human_approval: bool
    reason: str
    risk_score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    proposed_actions: list[str] = Field(default_factory=list)


class DraftResponse(BaseModel):
    text: str
    grounded_in: list[str] = Field(default_factory=list, description="doc ids used")


class QualityResult(BaseModel):
    passed: bool
    grounded: bool
    tone_ok: bool
    pii_leak: bool
    issues: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class AgentTrace(BaseModel):
    """One hop in the pipeline — kept for auditability and monitoring."""
    agent: str
    started_at: float = Field(default_factory=time.time)
    duration_ms: int = 0
    input_summary: str = ""
    output_summary: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    mode: Literal["mock", "live"] = "mock"


class PipelineResult(BaseModel):
    id: str = Field(default_factory=lambda: new_id("run"))
    ticket_id: str
    disposition: Disposition
    intake: IntakeResult | None = None
    knowledge: KnowledgeResult | None = None
    decision: DecisionResult | None = None
    draft: DraftResponse | None = None
    quality: QualityResult | None = None
    final_response: str | None = None
    actions_taken: list[str] = Field(default_factory=list)
    review_id: str | None = None
    trace: list[AgentTrace] = Field(default_factory=list)
    total_cost_usd: float = 0.0
    total_latency_ms: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# Human-in-the-loop
# ---------------------------------------------------------------------------
class ReviewRequest(BaseModel):
    id: str = Field(default_factory=lambda: new_id("rev"))
    ticket_id: str
    created_at: str = Field(default_factory=iso_now)
    risk_score: float
    reason: str
    proposed_response: str
    proposed_actions: list[str] = Field(default_factory=list)
    status: Literal["pending", "approved", "rejected"] = "pending"
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    decision_note: str | None = None


class ReviewDecision(BaseModel):
    reviewer: str = "human_operator"
    note: str | None = None


class WorkflowDesignRequest(BaseModel):
    """Ask the workflow designer to emit an n8n import for a stored analysis."""
    process_id: str
    workflow_name: str | None = None


# ---------------------------------------------------------------------------
# Cost / monitoring
# ---------------------------------------------------------------------------
class UsageRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("use"))
    ts: str = Field(default_factory=iso_now)
    ticket_id: str | None = None
    agent: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    mode: Literal["mock", "live"] = "mock"


def redact_emails(text: str) -> str:
    """Cheap PII scrub used by the quality gate."""
    return re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[redacted-email]", text)
