# System Design — AI Operations & Workflow Automation Platform

> One system, two questions. **"Should we automate this?"** (AI Product Management)
> and **"How do we run it safely in production?"** (AI Integration).
> This document explains how the code answers both.

---

## 1. System context

```text
                ┌──────────────────────────────┐
                │        Operator / PM         │
                │  (dashboard at GET /)        │
                └──────┬───────────────▲───────┘
                       │ describe process,      │ business case, run traces,
                       │ submit tickets,        │ approvals queue, analytics
                       │ approve/reject         │
                       ▼                        │
   n8n / email ──►  ┌──────────────────────────┴──────┐
   ticket intake    │           FastAPI app            │
   webhook          │  backend/main.py                 │
                    └───┬──────────┬───────────┬──────┘
                        │          │           │
          ┌─────────────▼──┐  ┌────▼─────┐  ┌──▼──────────────┐
          │ Analyzer+ROI   │  │ 6-agent  │  │ Analytics       │
          │ (business side)│  │ pipeline │  │ (Power BI feed) │
          └────────────────┘  └────┬─────┘  └─────────────────┘
                                   │
                    ┌──────────────┼───────────────┐
                    ▼              ▼               ▼
                 RAG store    LLM gateway    Integrations
              (knowledge_base/) (mock/live)  (CRM, email, billing, Slack)
                                     │
                              audit log (JSONL)
```

Everything on the left of the LLM gateway is deterministic and testable without
API keys; the gateway is the only swappable "AI" boundary.

## 2. Module map

| Module | Responsibility | Depends on |
|---|---|---|
| `backend/models.py` | Every Pydantic contract; the shared vocabulary of hand-offs | — |
| `backend/config.py` | Paths, thresholds, env switches (`AIOPS_MODE`, `OPENAI_API_KEY`) | — |
| `backend/store.py` | JSON persistence behind a swappable interface (Postgres later) | models |
| `backend/llm.py` | One interface, two implementations: `MockLLM`, `OpenAILLM`; token accounting | config |
| `backend/mock_ai.py` | Deterministic heuristics so the whole system runs offline | models |
| `backend/roi.py` | Labor economics + token economics; every figure traces to an input | config, models |
| `backend/analyzer.py` | LLM step-mapping + ROI → full business case artifact | roi, llm, models |
| `backend/rag.py` | Markdown → heading-aware chunks → TF-IDF (or live embeddings) → ranked hits | config |
| `backend/pipeline.py` | The 6 agents + deterministic risk gates — **source of truth for governance** | rag, llm, integrations |
| `backend/graph.py` | LangGraph orchestration wrapping the same agents | pipeline |
| `backend/integrations/` | CRM / email / billing / Slack adapters + JSONL audit log | models, store |
| `backend/main.py` | FastAPI surface + dashboard serving | everything |

Design rule: **the graph is a wrapper, not a second brain.** Governance lives in
`pipeline.py`; LangGraph only sequences it. Replacing the orchestrator must never
change a risk decision.

## 3. The agent pipeline

```text
intake ──► knowledge ──► decide ──► draft ──► quality ──► act
 (classify)  (RAG top-k)  (risk)    (grounded) (gates)     │
                                   ▲                      │
                                   │        ┌─────────────┴───────────┐
                                   └── revise│ low risk → execute      │
                                             │ high risk → human queue │
                                             └─────────────────────────┘
```

| Agent | Input → Output | Notes |
|---|---|---|
| Intake | free text → `Intent`, category, priority, amount | amount extraction drives risk |
| Knowledge | query → top-k `KnowledgeHit` with scores | retrieval score reused as risk signal |
| Decide | signals → `Disposition`: `auto_execute / assist / human_approval` | **pure code, no model** |
| Draft | context → reply, grounded in retrieved chunks | refuses without hits |
| Quality | reply + context → pass/revise | policy-grounding, personalization, amount consistency |
| Act | approved reply → integrations | idempotent-ish via outbox + audit |

## 4. Governance: risk gates

The decision agent runs **before** any model output is trusted, on the ticket's
own facts:

| Signal | Threshold | Disposition |
|---|---|---|
| Money involved | ≥ $500 | `human_approval` |
| Security action (2FA, password, MFA) | always | `human_approval` |
| Retrieval score | < 0.25 | `human_approval` (nothing authoritative to ground on) |
| Retrieval score | 0.25–0.45 | `assist` (draft, human sends) |
| Intake confidence | < 0.55 | `human_approval` |
| Everything else low-risk | — | `auto_execute` |

Why deterministic? An LLM "judging its own confidence" is the failure mode this
system exists to prevent. The gates are code, unit-tested, and cannot be talked
around by a prompt.

## 5. RAG design

- **Corpus**: `knowledge_base/*.md`, committed alongside code — policies version
  with the system that enforces them.
- **Chunking**: heading-aware (each `##` section becomes a chunk; long sections
  split on 700-char windows with overlap). Preserves semantic boundaries vs
  fixed-size chopping.
- **Retrieval**: TF-IDF + cosine offline; same `Retriever` interface accepts live
  embeddings when `AIOPS_MODE=live`. Score normalization is identical either way
  so risk thresholds don't shift between modes.
- **Grounding contract**: the draft agent may only cite retrieved chunks; the
  quality gate rejects replies containing dollar figures not present in the
  retrieved context.

## 6. ROI model (Side A)

```text
current_monthly_cost  = Σ_steps (volume × minutes × hourly_rate / 60)
ai_minutes_per_item   = Σ_steps minutes×(repetitive ? (1−ai_capability) : (1−automatable_share))
ai_monthly_cost       = ai_labor + token_cost(volume, tokens_per_item, model_rate)
monthly_savings       = current − ai  −  ai_monthly_cost
first_year_roi        = (12×savings − implementation_cost) / implementation_cost
```

`token_cost` uses per-model in/out rates from config; the analyzer emits every
assumption alongside the numbers, so a reviewer can contest inputs, not magic.

## 7. API surface

| Endpoint | Purpose |
|---|---|
| `POST /api/analyze` | Process description → business case (score, costs, mapping) |
| `POST /api/tickets` | Inbound ticket → full run trace (disposition, actions, cost, latency) |
| `GET /api/tickets/{id}` | One run, full agent trace |
| `GET /api/runs` | All runs — BI feed |
| `GET /api/reviews` / `POST /api/reviews/{id}/decision` | Human approval queue + approve/reject |
| `GET /api/analytics/summary` | Automation rate, cost by agent, disposition mix |

## 8. Storage & evolution path

- Now: JSON files under `data/` behind `store.py` interfaces — zero-infra dev,
  trivially inspectable, diffable in tests.
- Next: Postgres + pgvector behind the same interface; the rest of the system
  doesn't change because nothing touches persistence directly.
- Outbox pattern: actions land in an outbox first (audit + replay), executors are
  separate — the n8n outbound poller in the roadmap consumes it.

## 9. Failure modes we explicitly designed against

| Failure | Mitigation |
|---|---|
| Model overconfident on a $5,000 refund | Money/security gates ignore model confidence |
| Hallucinated policy quote | Quality gate rejects ungrounded figures; weak retrieval forces human |
| Silent bad actions | Outbox + JSONL audit log before execution |
| Cost blow-up unnoticed | Per-run token/cost recording, surfaced in analytics + dashboard |
| Vendor API down mid-run | Integrations are thin adapters; outbox retains work for replay |
| "Works on my machine" | Mock mode = full pipeline in CI with no keys (GitHub Actions proves it) |
