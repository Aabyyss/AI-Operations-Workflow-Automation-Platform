"""Buyer-facing one-pager, rendered from a real cost comparison.

A prospect should be able to read this without anyone in the room, which
means it has to survive three tests:

1. **It has to be generated, not written.** Every figure comes from
   `cost_compare.compare_costs`, so the document cannot disagree with the
   API a buyer might call themselves.
2. **It has to be honest about its weak points** — the sample floor, the
   mock-mode caveat, the plans that beat us, the fact that list prices move.
   A one-pager that only shows winning numbers is the thing this project
   exists to argue against.
3. **It has to print.** Light palette, no dark mode, no external CSS, JS or
   images, page-break rules inside the tables. Open it and hit Ctrl+P.

Output is a single self-contained HTML file: no build step, no CDN, safe to
email as an attachment or drop in a repo.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from html import escape

from .cost_compare import PRICE_SNAPSHOT
from .models import CostComparison

PRODUCT = "AI Operations & Workflow Automation Platform"
REPO_URL = "https://github.com/Aabyyss/AI-Operations-Workflow-Automation-Platform"

_CSS = """
:root{--ink:#111827;--muted:#5b6573;--line:#d7dce4;--soft:#f6f8fb;
      --good:#0f7a4d;--warn:#8a5a00;--accent:#2c4bd8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:#fff;color:var(--ink);max-width:820px;margin:0 auto;padding:40px 28px 56px;
     font:15px/1.55 "Segoe UI",system-ui,-apple-system,sans-serif}
.eyebrow{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
h1{font-size:27px;line-height:1.25;margin:6px 0 8px;font-weight:650}
h2{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);
   margin:28px 0 8px;font-weight:600}
.lede{color:var(--muted);font-size:14.5px}
.inputs{margin-top:18px;padding:12px 14px;background:var(--soft);
        border:1px solid var(--line);border-radius:10px;font-size:14px}
.inputs b{font-weight:600}
.hero{margin-top:22px;display:flex;gap:16px;flex-wrap:wrap}
.hero .box{flex:1 1 220px;border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.hero .big{font-size:30px;font-weight:700;letter-spacing:-.01em}
.hero .sub{color:var(--muted);font-size:13px;margin-top:4px}
.hero .box.ours{border-color:var(--accent);background:#f4f6ff}
table{width:100%;border-collapse:collapse;font-size:14px;margin-top:6px}
th{text-align:left;font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;
   color:var(--muted);border-bottom:1px solid var(--line);padding:7px 8px}
td{padding:8px;border-bottom:1px solid #eceff4;vertical-align:top}
tr.ours td{background:#f4f6ff;font-weight:600}
td.num,th.num{text-align:right;white-space:nowrap}
/* The plan column carries the long source notes, so it must stay dominant.
   Verdicts are deliberately terse — a long label here starves that column
   and turns the notes into ribbons. */
td:first-child,th:first-child{width:42%}
td.verdict{white-space:nowrap}
.good{color:var(--good);font-weight:600}
.warn{color:var(--warn);font-weight:600}
.note{color:var(--muted);font-size:12.5px}
ul{margin:6px 0 0 18px}
li{margin:4px 0;font-size:14px}
.callout{margin-top:10px;padding:14px 16px;border-left:3px solid var(--accent);
         background:var(--soft);border-radius:0 10px 10px 0}
.callout.honest{border-left-color:var(--warn)}
footer{margin-top:34px;padding-top:14px;border-top:1px solid var(--line);
       color:var(--muted);font-size:12.5px}
code{background:var(--soft);border:1px solid var(--line);border-radius:5px;
     padding:1px 5px;font:12px/1.5 ui-monospace,Consolas,monospace}
a{color:var(--accent)}
@media print{
  body{padding:0;max-width:none}
  @page{margin:16mm}
  tr,li,.callout,.hero .box{page-break-inside:avoid}
  h1,h2{page-break-after:avoid}
}
"""


def _e(v: object) -> str:
    return escape(str(v if v is not None else ""), quote=True)


def _money(v: float | None) -> str:
    return "—" if v is None else f"${v:,.2f}"


def _usd(v: float | None) -> str:
    """Sub-cent unit costs: dollars for money, five decimals below a cent.

    Rounds half-up on the decimal text rather than via binary floats, so a
    per-ticket figure renders identically here and in the dashboard (0.495
    is $0.50 in both, instead of $0.49 in Python and $0.50 in JS).
    """
    if v is None:
        return "—"
    if v == 0:
        return "$0"
    d = Decimal(str(v))
    unit = Decimal("0.01") if abs(d) >= Decimal("0.01") else Decimal("0.00001")
    return "$" + str(d.quantize(unit, rounding=ROUND_HALF_UP))


def _plan_rows(c: CostComparison) -> str:
    rows = []
    for p in c.plans:
        quoted = p.monthly_usd is None
        if quoted:
            verdict = '<span class="note">quote required</span>'
        elif p.verdict == "more_expensive":
            verdict = '<span class="good">pipeline cheaper</span>'
        else:
            verdict = '<span class="warn">plan cheaper</span>'
        crossover = (f"<b>{p.breakeven_resolution_rate_pct}%</b>"
                     if p.breakeven_resolution_rate_pct is not None
                     else '<span class="note">n/a</span>')
        rows.append(
            f"<tr><td><a href=\"{_e(p.source)}\">{_e(p.vendor)}</a>"
            f"<div class=\"note\">{_e(p.note)}</div></td>"
            f"<td class=\"num\">{_money(p.monthly_usd)}</td>"
            f"<td class=\"num\">{_money(p.annual_usd)}</td>"
            f"<td class=\"num\">{_usd(p.cost_per_ticket_usd)}</td>"
            f"<td class=\"num\">{crossover}</td>"
            f"<td class=\"verdict\">{verdict}</td></tr>")
    rows.append(
        f"<tr class=\"ours\"><td>{_e(PRODUCT)}<div class=\"note\">self-hosted · pays per "
        f"decision, not per resolution</div></td>"
        f"<td class=\"num\">{_money(c.our_monthly_usd)}</td>"
        f"<td class=\"num\">{_money(c.our_annual_usd)}</td>"
        f"<td class=\"num\">{_usd(c.our_cost_per_ticket_usd)}</td>"
        f"<td class=\"num\"><span class=\"note\">flat</span></td>"
        f"<td class=\"verdict\"><span class=\"good\">no meter</span></td></tr>")
    return "\n".join(rows)


def _crossover_callout(c: CostComparison) -> str:
    """The single most useful number on the page, stated once and plainly."""
    priced = [p for p in c.plans
              if p.breakeven_resolution_rate_pct is not None and p.monthly_usd is not None]
    if not priced:
        return ""
    lead = min(priced, key=lambda p: p.breakeven_resolution_rate_pct)  # type: ignore[arg-type,return-value]
    others = [p for p in priced if p.slug != lead.slug]
    tail = ""
    if others:
        parts = ", ".join(f"{_e(p.vendor)} {p.breakeven_resolution_rate_pct}%"
                          for p in sorted(others, key=lambda x: x.breakeven_resolution_rate_pct))  # type: ignore[arg-type]
        tail = f"<br><span class=\"note\">Also modelled: {parts}.</span>"
    return (f"<div class=\"callout\"><b>If {_e(lead.vendor)} resolves more than "
            f"{lead.breakeven_resolution_rate_pct}% of these conversations, "
            f"you are paying more than running it in-house.</b>{tail}<br>"
            f"<span class=\"note\">{_e(lead.breakeven_note)}.</span></div>")


def _lose_section(c: CostComparison) -> str:
    sold = c.plans_beaten
    priced = len([p for p in c.plans if p.monthly_usd is not None])
    if c.plans_that_beat_us:
        return (f"<div class=\"callout honest\"><b>Where this comparison says we lose.</b> "
                f"At these inputs {len(c.plans_that_beat_us)} of {priced} modelled plans are "
                f"cheaper than the pipeline: {_e(', '.join(c.plans_that_beat_us))}. "
                f"We publish the plan, its price and its source rather than leaving it out — "
                f"a comparison that only reports wins is marketing.</div>")
    return (f"<div class=\"callout honest\"><b>Where this comparison says we lose.</b> "
            f"At these inputs the pipeline comes out cheaper than all {priced} priced plans. "
            f"That is not a claim about the vendors' products, and it is not a claim about "
            f"quality — it is what the meters do: per-outcome pricing charges you for the "
            f"vendor's success, per-decision pricing charges you for the work. Change the "
            f"volume or the billed-resolution share above and this can flip. "
            f"The crossover rate is where it flips.</div>")


def render_one_pager(c: CostComparison, *, tagline: str = "",
                     repo_url: str = REPO_URL) -> str:
    """Render a self-contained, print-ready one-pager for one comparison."""
    inputs = c.inputs
    human = ("human review time included" if inputs.get("include_human_review_cost")
             else "human review time excluded")
    basis_class = "good" if c.measurement_basis == "measured" else "warn"
    caveats = "".join(f"<li>{_e(x)}</li>" for x in c.caveats)
    assumptions = "".join(f"<li>{_e(x)}</li>" for x in c.assumptions)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI support cost at your volume — {_e(PRODUCT)}</title>
<style>{_CSS}</style>
</head>
<body>
<header>
  <div class="eyebrow">{_e(PRODUCT)}</div>
  <h1>What AI support costs at your volume</h1>
  <p class="lede">{_e(tagline) if tagline else
      "Modelled against published list prices — against what a running system measured."}</p>
</header>

<div class="inputs">
  Your numbers: <b>{inputs['monthly_volume']:,}</b> conversations/month ·
  <b>{inputs['resolution_rate_pct']:g}%</b> resolved by the vendor's meter ·
  <b>{inputs['seats']}</b> seats · {human} ·
  ${inputs['platform_monthly_usd']:,.2f}/month platform allowance
</div>

<div class="hero">
  <div class="box ours">
    <div class="eyebrow">This pipeline</div>
    <div class="big">{_money(c.our_monthly_usd)}<span class="note">/month</span></div>
    <div class="sub">{_usd(c.our_cost_per_ticket_usd)} per ticket · {_money(c.our_annual_usd)}/year
      · every ticket, resolved or not</div>
  </div>
  <div class="box">
    <div class="eyebrow">Cheapest modelled plan</div>
    <div class="big">{_money(next((p.monthly_usd for p in c.plans
        if p.slug == c.cheapest_plan_slug), None))}<span class="note">/month</span></div>
    <div class="sub">{_e(next((p.vendor for p in c.plans if p.slug == c.cheapest_plan_slug),
        "—"))}<span class="note"> · list price as of {_e(PRICE_SNAPSHOT)}</span></div>
  </div>
</div>

<h2>The number to argue about</h2>
{_crossover_callout(c)}

<h2>Plan by plan</h2>
<table>
  <thead><tr><th>Plan (published price, linked)</th><th class="num">$/month</th>
    <th class="num">$/year</th><th class="num">$/ticket</th>
    <th class="num">Costs more above</th><th>Verdict</th></tr></thead>
  <tbody>
{_plan_rows(c)}
  </tbody>
</table>

{_lose_section(c)}

<h2>How the pipeline's number was produced</h2>
<ul>
  <li><span class="{basis_class}">{_e(c.measurement_basis)}</span> — from
      <b>{c.measured_from_decisions}</b> recorded decision(s) in this deployment's run ledger
      ({_usd(c.measured_cost_per_decision_usd)} per decision), in
      <b>{_e(c.mode)}</b> mode. Floor for calling it representative:
      {c.min_sample_for_measured} decisions.</li>
  <li>Every ticket is one decision, including the {c.escalations_per_month:,.0f}/month that go
      to a human — that is the honest cost of the work, not only the cost of the wins.</li>
  <li>Risk gates are deterministic and tested: refunds ≥ a configured limit, security-controlled
      actions, weak retrieval and low confidence always reach a human queue. If the pipeline
      cannot substantiate a decision, it does not make it.</li>
</ul>

<h2>Assumptions</h2>
<ul>{assumptions}</ul>

<h2>Caveats — the parts that would otherwise flatter us</h2>
<ul>{caveats}</ul>

<h2>Check it yourself</h2>
<ul>
  <li>The whole system runs offline with no API keys:
      <code>pip install -r requirements.txt</code>, <code>python -m scripts.demo</code>.</li>
  <li>Change any number above and regenerate — this page is produced by
      <code>POST /api/analytics/cost-comparison</code>, not written by hand.</li>
  <li>Source, tests and docs: <a href="{_e(repo_url)}">{_e(repo_url)}</a></li>
</ul>

<footer>
  Generated from a live deployment on {_e(PRICE_SNAPSHOT)}; list prices are a snapshot with
  sources linked above and move when vendors change them. Figures are modelled, not billed —
  re-derive them before making a decision.
</footer>
</body>
</html>
"""
