# Contributing

Thanks for looking at this project. It is a portfolio system, but it is built
like a production one — so contributions follow production discipline.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env      # optional; mock mode needs nothing
python -m pytest          # must be green before anything else
```

## Ground rules

1. **Contracts first.** Data shapes live in `backend/models.py`. Change the
   contract, then the implementations — never the other way around.
2. **Governance is code.** Risk gates in `backend/pipeline.py` are
   deterministic on purpose. PRs that make routing depend on model
   self-reported confidence will be rejected — argue it in an ADR first.
3. **Every behavioral change ships with a test** that fails without the change.
   The governance tests (money limit, security escalation, weak retrieval) are
   load-bearing: do not weaken them to make a change pass.
4. **Audit everything new.** Any new outbound action goes through the outbox +
   audit log like the existing integrations — no side effects without a record.
5. **Mock mode stays complete.** If your change only works with `AIOPS_MODE=live`,
   the pipeline has grown a hole. Mock and live must produce the same contract.

## Commits

Small, single-purpose, imperative subject lines (`roi: add payback months`,
not `misc fixes`). Docs-only changes are fine — this repo treats writing as
part of the system.

## What's wanted most

See the backlog in [docs/project-plan.md](docs/project-plan.md): Postgres +
pgvector behind `Storage`, the outbound n8n executor, the evaluation harness.

## Reporting issues

Include: mode (`mock`/`live`), the run trace from `GET /api/runs` (or the
ticket ID), and expected vs actual disposition. Governance surprises get
triaged first — they are the point of this system.
