# Competitive Analysis — where this platform sits, and what to improve

**Read this first — what is and isn't claimed here.**
Figures below are *public list prices and vendor-published claims* gathered
from the sources in §8 on 2026-10-07; list prices move, so treat them as
magnitudes, not contracts. We have **not** run head-to-head benchmarks
against any vendor — nobody can reproduce another company's resolution rate
without their customer's traffic. So every line is one of two things: a
sourced public claim (cited), or a **documented difference in code you can
read in this repo** (`backend/`, `dashboard/`, `tests/`). Where a vendor
does not publish something, this document says so instead of guessing.

---

## 1. The honest one-liner

> Most of the market sells **a better answer** (an agent that resolves more
> tickets). This platform sells **the evidence that the answer was safe to
> give, what it cost, and who signed off** — with the same pipeline running
> offline in CI so the governance claims are testable, not rhetorical.

That is a narrower wedge than "we're an AI customer service agent", and it is
the wedge the code actually defends. The ROI engine, deterministic gates,
approval queue, cost ledger and audit trail are the product; the six agents
are the payload.

## 2. Scope — who we are actually up against

Four adjacent markets all show up in a buyer's shortlist, and they are *not*
substitutes for each other:

| Category | Players | What they own | What they don't |
|---|---|---|---|
| **A. Resolution agents** | Intercom Fin, Zendesk AI, Ada, Forethought, Decagon, Sierra, Zowie, Lorikeet, Gorgias | Conversation surface, deflection, seat-of-pants UX polish, huge integration catalogs | Business case *before* you buy; portable audit/eval you control; offline reproducibility |
| **B. Workflow automation** | n8n, Zapier, Make, Power Automate, UiPath, Automation Anywhere, Workato, Kognitos | Connections, triggers, RPA depth, orchestration at scale | Risk gating, human-approval semantics, per-decision cost accounting |
| **C. Agent observability / eval** | LangSmith, Langfuse, Arize Phoenix, Braintrust, W&B Weave | Tracing, datasets, eval rigor, annotation queues | The decision itself — they watch an agent, they don't gate it |
| **D. Enterprise AI-ops suites** | Salesforce Agentforce, ServiceNow, Moveworks / Kore.ai | Enterprise distribution, ITSM/CRM gravity, procurement trust | Time-to-value for a mid-market team with no platform team |

This platform deliberately straddles **A + B + C**: it runs the pipeline (A),
emits importable n8n graphs and feeds BI (B), and pins its own eval +
observability (C) — but it starts from **the business case**, which none of
the three lead with. That is the gap worth defending.

## 3. Pricing and business model (sourced)

| Product | Public model | Figure | Source |
|---|---|---|---|
| Intercom Fin | per outcome | **$0.99 / resolution** (billed once per conversation) | Intercom pricing, getmacha |
| Zendesk AI | seat + outcome | **$29 Essential / $85 Advanced / $132 Expert** per seat/mo; outcome tier reported at $1.20–$1.50 | Zendesk vs Fin, myaskai |
| Salesforce Agentforce | per conversation | **~$2.00 / conversation** | clonedesk |
| Sierra | enterprise quote only | no published price; third-party estimates **~$150k/yr + $50k–$200k setup** | getmacha, lorikeetcx |
| Decagon | quote; per-outcome or per-… | not published | decagon.ai |
| Lorikeet | published, annual | **$2,100/mo (Start), $5,100/mo (Scale)** paid annually | lorikeetcx |
| Forethought | enterprise quote | not published | decagon.ai |
| n8n | execution-based or self-host | **$20/mo (2,500 executions)**; **free self-hosted** | cipherprojects |
| Zapier | per task | **$19.99/mo (750 tasks)** | cipherprojects |
| UiPath / Workato / Automation Anywhere | enterprise quote | not published | kognitos, technologyadvice |
| This platform | self-hosted, no per-seat or per-resolution meter | **$0.00015 avg cost/ticket** measured in mock mode (`scripts/demo`), + your model spend in live mode | `backend/config.py::TOKEN_PRICING`, `scripts/demo` |

Two things to read off that table honestly:

1. **The per-resolution meter is the market's gravity.** At $0.99/outcome and
   a 50% resolution rate on 10k conversations/month, that's ~$4,950/mo
   before seats (guptadeepak's own worked example). Our architecture has no
   meter to argue about — cost is a *measurement* it prints, not a billing
   unit — which is a real advantage for a buyer who has been burned by
   unpredictable AI spend (gurusup calls exactly this out: "costs are
   unpredictable at scale").
2. **Enterprise pricing is now opaque by design** (Sierra, Decagon,
   Forethought, UiPath, Workato). That's an opening for anyone shipping a
   system a solo engineer can stand up in an afternoon and *audit*.

## 4. Capability comparison

● shipped & tested in this repo · ◐ partial / adjacent · ○ not comparable
(not a knock — different product category)

| Capability | This platform | A. Resolution agents | B. Workflow automation | C. Observability |
|---|---|---|---|---|
| **Business case before build** (ROI, payback, automation score, per-assumption) | ● `POST /api/analyze` | ○ | ○ | ○ |
| RAG-grounded answers | ● `knowledge_base/` + ranking tests | ● their core | ◐ via nodes | ○ |
| **Deterministic** risk gates (money/security/weak-retrieval) — not model judgment | ● `agent_pipeline` gates, governance tests | ◐ mostly model-or-policy-config | ○ | ○ |
| Human approval queue | ● queue → decide → execute | ● handoff to human | ◐ "manual step" nodes | ◐ annotation queues |
| Approval-queue **SLA** (aging buckets, turnaround, escalation rate) | ● `/api/analytics/approvals` | ◐ seat-based reporting | ○ | ○ |
| Per-decision **cost ledger** (tokens in/out, $ per agent, per ticket) | ● `usage.json`, `/api/export/usage.csv` | ◐ outcome billing, not per-decision | ○ | ◐ trace-level cost |
| Audit trail with **correlation IDs** | ● `audit.json`, `integrations.audit` | ◐ admin logs | ◐ run history | ● traces |
| Quality-gate **precision + recall** | ● `/api/analytics/quality` | ◐ resolution rate only | ○ | ◐ needs labeled sets |
| Model routing by tier (light/heavy) | ● inert until env-set | ○ (abstracted) | ◐ in some nodes | ○ |
| Run **replay + diff** | ● `POST /api/runs/{id}/replay`, SSE stream | ○ | ◐ re-run workflow | ◐ replay a trace |
| Drift canary + budget guardrails | ● `/api/analytics/drift`, `/budget` | ○ | ○ | ◐ alerts |
| Retention/prune with dry-run | ● `POST /api/admin/prune` | ○ | ○ | ◐ |
| Role-scoped keys (operator/approver/admin) | ● `test_roles.py` | ● SSO/RBAC (deeper) | ● RBAC (deeper) | ● SSO |
| Knowledge corpus CRUD **over API** | ● `POST/DELETE /api/knowledge`, admin-gated | ◐ UI-only, vendor-hosted | ○ | ○ |
| Self-host / bring-your-own-model | ● mock mode default, `AIOPS_MODE=live` | ○ SaaS | ● n8n yes | ● Langfuse yes |
| **Runs offline in CI with zero keys** | ● 165 tests + eval gate | ○ | ○ | ○ |
| Postgres + pgvector swap behind one interface | ● `AIOPS_STORAGE=postgres` | ○ | ● | ○ |
| Importable workflow export (n8n graph) | ● `POST /api/workflows/design` | ○ | ● it's their runtime | ○ |
| Marketing site, brand, G2 reviews, SOC 2 | ○ nothing | ● | ● | ● |

**Read the matrix as a buyer would:** categories A, B and C each beat us
badly on a column or two that matters — A owns conversation surface and
polish, B owns connector breadth and RPA, C owns trace depth and annotation.
Nobody in the matrix except us has the **left column**: the business case, the
governance evidence, and the offline reproducibility in one artifact.

## 5. Where we win (concrete, not aspirational)

1. **Governance is code, and it's tested.** `refunds ≥ $500`, 2FA changes,
   weak retrieval, low confidence → human queue *regardless of what the model
   claims*. Competitors ship this as configuration; we ship it as assertions
   (`tests/` runs them in CI on 3.11–3.13). This is the strongest single
   differentiator and the one worth leading marketing with.
2. **The ROI engine answers "should we automate this?" before any agent runs.**
   No resolution agent answers that question at all — they assume you've
   already decided.
3. **Honest metrics.** The A/B report withholds conclusions below 5 samples
   per cohort; the quality panel reports precision *and* a recall proxy and
   says so. In a market where vendors quote best-case resolution rates
   (Intercom's own pricing page shows ~50%, case studies show higher), an
   instrument that refuses to over-claim is itself differentiated.
4. **Zero-cost, zero-key reproducibility.** `python -m scripts.demo` and the
   full test suite run offline in a container. Nobody else in the matrix can
   let a skeptical engineer grade them in five minutes.
5. **No meter.** Cost is measured per decision, not billed per resolution —
   direct answer to the "unpredictable AI spend" complaint the market keeps
   voicing. And the claim is checkable: `POST /api/analytics/cost-comparison`
   prices the published market plans at any volume against our measured cost,
   publishes the crossover rate for each, and lists the plans that beat us.
6. **Portability of the audit.** Evidence lives in your Postgres/JSON, not a
   vendor dashboard. Lorikeet is explicitly targeting "audit-trail depth for
   regulated businesses"; on the support side that is the closest
   philosophical competitor, and their entry point is $2,100/mo annual.

## 6. Where we lose (say it plainly)

| Gap | Reality in the market |
|---|---|
| No conversation surface | Fin/Zendesk/Ada own chat, email, voice today. We run over an API + operator dashboard. |
| No connector breadth | Zapier/n8n/Make/RPA suites have thousands of connectors; we have an outbox + n8n seam. |
| No trace depth | Langfuse/LangSmith/Arize do spans, datasets, annotation, cost-per-span far beyond `audit.json`. |
| No SOC 2 / SSO / RBAC depth | Three shared keys is not enterprise identity. |
| No brand, no references, no proof from anyone but us | Enterprise trust is bought with customers, not architecture. |
| Live-mode quality is unproven at volume | Our 8/8 routing eval is a labeled gate, not a traffic benchmark. |
| pgvector cutover not done | Schema is pgvector-ready; the retriever still ranks in-process. |

## 7. What we can improve — prioritized

Ordered by (impact on the wedge) ÷ (effort). Items 1–4 are the ones that
turn this from "impressive portfolio artifact" into "someone would pay for
this".

### P1 — Governance evidence, the wedge itself
1. **Signed decision receipts.** One durable, verifiable, exportable record
   per decision: inputs hash, policy version, gate that fired, model + prompt
   hash, tokens, approver identity, outcome. Now it's audit entries; make it
   a **single artifact a compliance reviewer can read** and verify offline
   (`GET /api/runs/{id}/receipt`). Leads Lorikeet's transparency pitch with
   something you can hand to an auditor.
2. **Policy-as-code with a version and a diff.** Gates live in code today —
   make the policy a named, versioned artifact (`knowledge_base/policy.yaml`)
   so a *rejected* decision can cite `policy v3 §2.1`, and a change to the
   money threshold shows up in the audit trail. This is what makes the
   governance claim auditable over time, not just at HEAD.
3. **Shadow mode / traffic replay.** Score live traffic against the gates
   without acting, and report "would have escalated 28.6% of this week's
   tickets". This is *the* sales motion of the category (see Sierra's
   "trust" positioning) and our replay endpoint is already half of it.
4. **Extend the labeled eval set to 50+ tickets with a CI recall floor.**
   One public leaderboard-style number from a reproducible harness is worth
   more than any copy. Keep it honest: publish the misses.

### P2 — Close the credibility gaps that don't need a platform team
5. **OpenTelemetry export alongside `/metrics`.** Traces into whatever the
   buyer already runs — cheap, and it removes "yet another dashboard" as an
   objection.
6. **pgvector retrieval cutover** (the documented one-file extension point in
   `docs/DECISIONS.md`), behind `AIOPS_RETRIEVAL=pgvector`, with the existing
   ranking tests pinned for both backends.
7. **Outbound n8n executor** (milestone 12): poll the outbox, execute against
   real Gmail/Slack/HubSpot, replay-safe dry-run. Turns the seam into a
   delivery story.
8. **SSO + per-key audit** (`who changed the corpus, when`) — the thinnest
   credible enterprise-identity story before anyone asks for SOC 2.
9. **Cost-per-resolution comparison calculator** — ✅ **shipped**:
   `POST /api/analytics/cost-comparison` models every published list price
   ($0.99/outcome, $2.00/conversation, seat tiers, flat platform fees) at the
   buyer's volume against this platform's **measured** cost per decision. For
   each plan it computes the resolution rate above which that plan overtakes
   the pipeline (`breakeven_resolution_rate_pct`) — 3.4% for Fin, 1.7% for
   Agentforce at 1,500 tickets — which is the number a buyer should argue
   about, and it names the plans that come out cheaper than us. Rendered as a
   dashboard panel with its assumptions and caveats inline. This is the single
   highest-leverage piece of collateral against the per-outcome pricing model.

### P3 — Polish that the screenshot showed missing
10. **A UI pass, not a UI fix.** The audit overflow was a symptom of rendering
    raw payloads into a fixed-width card. Design rule going forward: *every
    panel must degrade gracefully at 1024px and at 1920px, and no panel may
    scroll a full JSON blob horizontally.* See §8.
11. **Empty/loading/error states** for every panel. A dashboard that shows
    "—" with no explanation loses a demo in the first ten seconds.
12. **Accessibility**: focus rings, `aria-live` on the audit stream, keyboard
    navigation for the approval queue, colour never the only signal (the
    tone classes must pair with a glyph/label).
13. **Density mode** — operators watching 12 events at a time want a compact
    table view; executives want the KPI strip. Same data, two densities.

## 8. The screenshot defect, and what it taught us

The attached screenshot showed the **Recent Audit Events** panel with raw
JSON payloads (`{"ticket_id":"tkt_642ab783e0","actions":[...`) running off
the right edge of the card — text clipped mid-glyph, no ellipsis, no way to
read the rest. Three separate defects were behind it:

| Defect | Cause | Fix (shipped, `ffb33f3`) |
|---|---|---|
| Payload text overflowed the card | No `overflow-wrap`; a single long unbroken JSON token has no break opportunity | `td,.card,.pill{overflow-wrap:anywhere}` |
| Truncation sliced mid-character | `String.slice(0,90)` cuts anywhere | `truncate(v,n)` appends a real ellipsis |
| Payload unreadable even when it fit | Whole JSON blob dumped as one string | `fmtPayload()` renders `key value · key value`, full JSON in the `title` tooltip |

A fourth bug fell out of the same audit: `renderCosts` was emitting an
`<tr>` with 2 cells under 4 headers plus an empty `colspan=2` row — the cost
table was structurally invalid. Now aggregated correctly per agent with
`colspan=4`.

**Verified, not assumed:** the fix was checked on the running dashboard, not
just in source — the audit card measured `scrollWidth === clientWidth === 582`
(no overflow) with 12 events rendered, and the inline script passes
`acorn --ecma2022 --module` (parses clean).

### Improvement backlog from the comparison
- **Audit panel → structured trace view.** Filter by event, by correlation ID,
  by ticket; expand to the full payload in a side drawer instead of a tooltip.
  (Directly answers category C's trace depth with our own data.)
- **KPI strip honesty badges.** Every headline number carries its sample size
  and window inline. We refuse to over-claim in the API; don't undermine that
  in the UI.
- **Approval queue as a first-class surface** with keyboard approve/reject,
  SLA countdown, and the policy clause that triggered it.
- **Responsive layout**: single-column below 1100px, sidebar collapse, KPI
  grid 4→2→1.
- **Copy pass**: replace internal terms (`readyz`, `outbox_id`) with operator
  language on the dashboard, keep the raw names in tooltips.

## 9. Sources (accessed 2026-10-07)

- Intercom — *AI customer service agent pricing comparison* (market page lists
  Zendesk outcome pricing at $1.20–$1.50 vs Fin's $0.99):
  <https://www.intercom.com/learning-center/ai-customer-service-agent-pricing-comparison>
- Intercom — *Pricing* ("$0.99 per Fin outcome"; "we're seeing a 50% resolution
  rate with Fin"): <https://www.intercom.com/pricing>
- Zendesk — *Zendesk vs Fin* (outcome pricing; seat tiers $29/$85/$132):
  <https://www.zendesk.com/why-zendesk/zendesk-vs-fin/>
- MyAskAI — *Zendesk AI vs Intercom Fin 2026* ($115/agent Enterprise plan,
  $0.99/resolution): <https://myaskai.com/blog/zendesk-ai-intercom-ai-comparison-2026>
- getmacha — *Intercom Fin cost 2026* ($0.99/outcome, billed once per
  conversation, runs on 4 other help desks):
  <https://www.getmacha.com/blog/intercom-fin-ai-explained>
- getmacha — *Sierra AI review 2026* (no published pricing; ~$150k/yr +
  $50k–$200k setup estimates): <https://www.getmacha.com/blog/sierra-ai-complete-guide>
- Lorikeet — *8 best Sierra alternatives 2026* (Lorikeet $2,100/mo Start,
  $5,100/mo Scale, paid annually; "audit-trail depth" positioning):
  <https://www.lorikeetcx.ai/articles/sierra-alternatives-2026>
- clonedesk — *AI support agent pricing 2026* (Agentforce ~$2.00/conversation,
  Zendesk ~$50/agent/mo): <https://clonedesk.ai/blog/ai-support-agent-pricing>
- guptadeepak — *Top 5 AI customer service tools 2026* (worked example:
  10k conversations, 50% resolution → ~$4,950/mo in Fin costs):
  <https://guptadeepak.com/tools/top-5-ai-customer-service-tools-2026/>
- gurusup — *Zendesk vs Intercom 2026* ("more autonomous but costs are
  unpredictable at scale"): <https://gurusup.com/blog/zendesk-vs-intercom>
- Decagon — *Best AI customer service software* (Forethought: mid-market,
  enterprise-quoted): <https://decagon.ai/blog/ai-customer-service>
- Decagon — *Decagon vs Sierra* (Sierra per-outcome, Decagon flexible):
  <https://decagon.ai/vs/sierra>
- Sierra — *Product overview* (outcome-based pricing model):
  <https://sierra.ai/product>
- Cipher Projects — *n8n vs Zapier 2026* (n8n $20/mo 2,500 executions or free
  self-hosted; Zapier $19.99/mo 750 tasks):
  <https://www.cipherprojects.com/blog/posts/n8n-vs-zapier-automation-tool-comparison/>
- Kognitos — *UiPath alternatives 2026* (Workato, n8n, Make, Zapier,
  Relevance AI as evaluated alternatives):
  <https://www.kognitos.com/blog/best-uipath-alternatives-generative-ai-automation-2026/>
- TechnologyAdvice — *Best AI workflow automation tools 2026*:
  <https://technologyadvice.com/blog/information-technology/best-ai-workflow-automation-tools/>
- Puffersoft — *n8n vs Zapier vs Make 2026* ("at scale, n8n can cost 5–8× less
  than Zapier"): <https://puffersoft.com/n8n-vs-zapier-vs-make-automation-2026/>
- Digital Applied — *Agent observability 2026 (LangSmith, Langfuse, Arize)*:
  <https://www.digitalapplied.com/blog/agent-observability-platforms-langsmith-langfuse-arize-2026>
- Vellum — *Best AI agent observability tools 2026*:
  <https://www.vellum.ai/blog/best-ai-agent-observability-tools>
- MLflow — *Top 5 agent observability tools*:
  <https://mlflow.org/top-5-agent-observability-tools/>
