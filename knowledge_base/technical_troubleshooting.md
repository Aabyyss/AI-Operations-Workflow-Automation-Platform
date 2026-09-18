# Technical Troubleshooting Guide

Acme Software Technical Support Guide (v2.4)

## API errors

- 401 errors: check that the API key is active and sent as a Bearer token.
- 429 errors: the account hit its rate limit; retry with exponential backoff.
- 5xx errors: check the status page first; if degraded, notify customers of
  the incident rather than troubleshooting client-side.

## Sync failures

Data sync failures are usually caused by expired OAuth tokens. Re-connect the
integration from Settings > Integrations. If sync remains broken, collect the
customer's workspace ID and escalate to engineering with the last sync
timestamp.

## Performance issues

Reports taking longer than 30 seconds should be checked against known
incidents. Ask customers to narrow date ranges; ranges over 12 months exceed
report limits by design.

## Escalation

Any issue reproducible on our status page infrastructure goes to engineering
via PagerDuty, not to the general support queue.
