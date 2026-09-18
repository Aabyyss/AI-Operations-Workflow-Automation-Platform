# Governance & Evaluation

The platform's core thesis: **AI quality and safety are product features you
design, measure, and enforce — not prompts you hope about.**

## Risk model

`DecisionAgent` computes risk deterministically (auditable, testable):

| Signal                                   | Risk contribution        |
|------------------------------------------|--------------------------|
| Refund ≥ $500 (configurable)             | +0.60                    |
| Refund < $500 (within policy)            | +0.10                    |
| 2FA / security-controlled action         | +0.55                    |
| No confident policy match (retrieval)    | +0.45                    |
| Intake confidence < 0.60                 | +0.25                    |
| Retrieval confidence < 0.30              | +0.20                    |
| Technical issue                          | +0.10                    |
| Account guidance                         | +0.05                    |
| Urgent priority                          | +0.05                    |

Routing thresholds (from `backend/config.py`):

```text
risk ≤ 0.30  → auto-execute (after the quality gate)
risk > 0.30  → human approval queue
risk > 0.85  → hard block (never auto-run, always review)
```

Quality gate (before any auto-execution): grounded in retrieved policy,
professional tone, no PII leakage. Failure → escalation, never a bad send.

## Human-in-the-loop

Every escalation creates a `ReviewRequest` with the full AI proposal:
drafted response, intended actions, risk score, and the *reason* in plain
language. Humans approve (actions execute) or reject (nothing sent). The
review decision itself is audit-logged with reviewer identity.

## Audit trail

Append-only (`/api/audit`): every classification, risk decision, escalation,
approval, executed action, and pipeline error, with timestamps and payloads.
This is the artifact you show in a compliance review — and the training set
for future evaluation.

## Evaluation approach

**Online:** the approval queue doubles as ground truth. Approved responses
that humans edit = quality-gate misses; rejection rate by category = risk
model calibration data. `/api/analytics/summary` tracks the split.

**Offline:** the 25-test suite pins the governance behavior — money limits,
security gates, retrieval floors, PII leaks. Any change to thresholds shows
up as a red test before it shows up as an incident.

**Cost:** every LLM call records tokens + USD by agent (`/api/usage`), so
unit economics (cost per resolved ticket) is a query, not a guess. In mock
mode costs are deterministic estimates; in live mode they are real.
