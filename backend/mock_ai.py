"""Deterministic heuristics that stand in for LLM reasoning in mock mode.

Each function maps to one prompt task the agents send through the LLM
gateway. They are rule-based so the whole platform runs with zero API keys
and is fully reproducible in tests and CI — while the prompt/response
contracts stay identical to the live path.
"""
from __future__ import annotations

import json
import re

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _ticket_section(user: str) -> tuple[str, str]:
    m = re.search(r"TICKET_SUBJECT:(.*?)\nTICKET_BODY:(.*?)(?:MOCK_TASK:|$)", user, re.S)
    if not m:
        return "", ""
    return m.group(1).strip(), m.group(2).strip()


def _amount(text: str) -> float:
    flat = text.replace(",", "")
    m = re.search(r"\$\s?(\d+(?:\.\d+)?)", flat)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s?(?:usd|dollars)", flat)
    return float(m.group(1)) if m else 0.0


# ---------------------------------------------------------------------------
# Intake — classification, priority, intent, entities
# ---------------------------------------------------------------------------
def classify_intake(user: str) -> dict:
    subject, body = _ticket_section(user)
    text = (subject + "\n" + body).lower()

    if any(w in text for w in ("refund", "charged", "charge", "invoice", "billing", "payment", "subscription")):
        category = "billing"
    elif any(w in text for w in ("password", "login", "log in", "sign in", "2fa", "two-factor", "locked", "account access")):
        category = "account"
    elif any(w in text for w in ("error", "not working", "broken", "crash", "bug", "fails", "down", "timeout")):
        category = "technical"
    elif any(w in text for w in ("pricing", "quote", "demo", "upgrade", "discount", "enterprise", "cost of")):
        category = "sales"
    else:
        category = "other"

    if any(w in text for w in ("urgent", "asap", "outage", "production down", "immediately", "emergency")):
        priority = "urgent"
    elif any(w in text for w in ("refund", "cancel", "broken", "cannot access", "locked out", "disappointed")):
        priority = "high"
    elif any(w in text for w in ("question", "when will", "how do i", "curious")):
        priority = "low"
    else:
        priority = "medium"

    if category == "billing":
        intent = "request_refund" if "refund" in text else ("report_duplicate_charge" if "twice" in text or "double" in text else "billing_inquiry")
    elif category == "account":
        intent = "reset_password" if "password" in text else ("locked_out" if "locked" in text else "account_access")
    elif category == "technical":
        intent = "report_outage" if ("outage" in text or "down" in text) else "technical_issue"
    elif category == "sales":
        intent = "pricing_inquiry"
    else:
        intent = "general_inquiry"

    entities = {
        "emails": EMAIL_RE.findall(subject + "\n" + body),
        "order_numbers": re.findall(r"#\s?(\d{3,})", subject + "\n" + body),
    }
    return {
        "category": category,
        "priority": priority,
        "intent": intent,
        "entities": entities,
        "requested_refund_usd": _amount(text),
        "confidence": 0.88 if category != "other" else 0.55,
    }


# ---------------------------------------------------------------------------
# Drafting — grounded customer reply per category
# ---------------------------------------------------------------------------
def _grab(user: str, key: str, end_pat: str) -> str:
    m = re.search(rf"{key}:\s*(.*?){end_pat}", user, re.S)
    return m.group(1).strip() if m else ""


def _first_name(email: str) -> str:
    local = email.split("@")[0].split(".")[0]
    return local.capitalize() if local else "there"


def draft_reply(user: str) -> dict:
    category = _grab(user, "CATEGORY", r"\n")
    email = _grab(user, "CUSTOMER_EMAIL", r"\n")
    try:
        amount = float(_grab(user, "REFUND_USD", r"\n") or 0)
    except ValueError:
        amount = 0.0
    policy = _grab(user, "POLICY_SNIPPET", r"\nCOMPOSE").strip()
    snippet = (policy[:320] + "…") if len(policy) > 320 else policy
    name = _first_name(email)

    if category == "billing" and amount > 0:
        text = (
            f"Hi {name},\n\n"
            "Thanks for reaching out, and I'm sorry for the billing trouble. "
            "I reviewed your account against our refund policy:\n\n"
            f"\"{snippet}\"\n\n"
            f"Based on that policy, a refund of ${amount:,.2f} has been queued to your "
            "original payment method and should appear within 5-7 business days. "
            "I've logged this on your account for our records.\n\n"
            "If anything looks off after that window, reply to this email and a human "
            "teammate will pick it up right away.\n\nBest regards,\nAcme Support Team"
        )
    elif category == "billing":
        text = (
            f"Hi {name},\n\nThanks for your message about billing. Here is the relevant "
            f"part of our policy:\n\n\"{snippet}\"\n\nIf that doesn't cover your case, "
            "reply with a few details and we'll take a closer look.\n\n"
            "Best regards,\nAcme Support Team"
        )
    elif category == "account":
        text = (
            f"Hi {name},\n\nHappy to help you get back into your account. Here is our "
            "recommended procedure:\n\n"
            f"\"{snippet}\"\n\n"
            "For security we can't change credentials manually from this channel, but "
            "the steps above resolve most cases in about two minutes.\n\n"
            "Best regards,\nAcme Support Team"
        )
    elif category == "technical":
        text = (
            f"Hi {name},\n\nSorry you're hitting this. Our troubleshooting guide "
            "suggests the following for issues like yours:\n\n"
            f"\"{snippet}\"\n\n"
            "If the problem persists after these steps, reply with the exact error "
            "message and we'll escalate to our engineering team.\n\n"
            "Best regards,\nAcme Support Team"
        )
    elif category == "sales":
        text = (
            f"Hi {name},\n\nGreat to hear from you. Here's a quick summary of our "
            f"plans:\n\n\"{snippet}\"\n\n"
            "Happy to set up a call or a trial — just reply with what works for you.\n\n"
            "Best regards,\nAcme Sales Team"
        )
    else:
        text = (
            f"Hi {name},\n\nThanks for reaching out. We want to make sure this gets to "
            "the right specialist, so a human teammate will review your message and "
            "follow up shortly.\n\nBest regards,\nAcme Support Team"
        )
    return {"text": text}


# ---------------------------------------------------------------------------
# Quality gate — groundedness, tone, PII
# ---------------------------------------------------------------------------
def quality_check(user: str) -> dict:
    draft = _grab(user, "DRAFT_TEXT", r"\nGROUNDING_OK")
    grounded = _grab(user, "GROUNDING_OK", r"(?:\n|MOCK_TASK)").lower() == "yes"

    pii_leak = bool(EMAIL_RE.search(draft))
    tone_ok = ("Hi " in draft or "Hello " in draft) and "!!!" not in draft
    issues: list[str] = []
    if not grounded:
        issues.append("draft is not grounded in retrieved policy")
    if pii_leak:
        issues.append("raw email address leaked into draft")
    if not tone_ok:
        issues.append("tone check failed")
    passed = grounded and not pii_leak and tone_ok
    return {
        "passed": passed,
        "grounded": grounded,
        "tone_ok": tone_ok,
        "pii_leak": pii_leak,
        "issues": issues,
        "confidence": 0.92 if passed else 0.45,
    }


# ---------------------------------------------------------------------------
# Process analyzer — per-step AI recommendation
# ---------------------------------------------------------------------------
def analyze_process_notes(user: str) -> dict:
    steps = []
    for line in user.splitlines():
        if not line.startswith("STEP|"):
            continue
        parts = line.split("|")
        if len(parts) < 8:
            continue
        _, name, minutes, rep, judg, money, pii, struct = parts[:8]
        minutes = float(minutes)
        rep, judg, money, pii, struct = (x == "1" for x in (rep, judg, money, pii, struct))
        if money:
            rec, why = "human_approval", "Touches money — keep a human sign-off."
        elif pii:
            rec, why = "assist", "PII involved — AI can process with audit trail and human oversight."
        elif rep and struct:
            rec, why = "automate", "Repetitive and structured — highest ROI to automate."
        elif judg:
            rec, why = "assist", "Requires judgment — AI drafts, human approves."
        else:
            rec, why = "keep", "Low automation leverage as designed."
        steps.append({"step": name, "minutes": minutes, "recommendation": rec, "rationale": why})
    return {"steps": steps}
