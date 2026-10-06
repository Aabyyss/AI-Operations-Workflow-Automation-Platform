"""Tests for the buyer-facing one-pager.

A shareable document is an artifact with a blast radius: it leaves the
building, it gets forwarded, and nobody re-derives it on the other end. So
these tests pin three things:

1. **No drift.** The page the CLI writes and the page the endpoint serves are
   byte-identical, because both go through `render_one_pager`. A buyer
   pressing Ctrl+P and an engineer calling the API must see the same numbers.
2. **No injection.** The page is assembled from strings; a hostile vendor
   name must not become markup.
3. **No flattery.** The honesty sections are asserted to be present — the
   sample floor, the plans that beat us, the quote-required plan that must
   never render as $0.00.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.cost_compare import compare_costs  # noqa: E402
from backend.models import CostComparisonRequest  # noqa: E402
from html import escape  # noqa: E402

from backend.one_pager import PRODUCT, REPO_URL, _usd, render_one_pager  # noqa: E402
from backend.store import storage  # noqa: E402


def _comparison(n_runs=30, **kwargs):
    runs = [{"id": f"run_{i}", "disposition": "auto_resolved",
             "total_cost_usd": 0.0004} for i in range(n_runs)]
    req = CostComparisonRequest(monthly_volume=1500, resolution_rate_pct=50,
                                seats=3, **kwargs)
    return compare_costs(runs, req)


# ------------------------------------------------------------------ document

def test_output_is_a_complete_standalone_document():
    html = render_one_pager(_comparison())
    assert html.startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")
    # The product name is escaped in markup (& -> &amp;), which is correct HTML.
    assert "<title>" in html
    assert escape(PRODUCT) in html
    # Self-contained: nothing fetched from the network at render time.
    for external in ("<link", "<script", "<img", 'src="http'):
        assert external not in html, external


def test_source_links_are_present_for_every_plan():
    c = _comparison()
    html = render_one_pager(c)
    for p in c.plans:
        assert p.source in html, p.slug


def test_hostile_vendor_text_cannot_become_markup():
    c = _comparison()
    evil = '<script>alert("x")</script>'
    c.plans[0] = c.plans[0].model_copy(update={"vendor": evil, "note": evil, "source": evil})
    html = render_one_pager(c)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert 'href="&lt;script&gt;' in html


# --------------------------------------------------------------- the numbers

def test_crossover_callout_leads_with_the_lowest_rate():
    # Agentforce at $2.00/outcome crosses over earliest of the presets.
    html = render_one_pager(_comparison())
    assert "If Salesforce Agentforce resolves more than" in html
    assert "Also modelled:" in html
    assert "Intercom Fin 3.4%" in html


def test_quote_only_plan_never_renders_as_zero_dollars():
    c = _comparison()
    quote = next(p for p in c.plans if p.slug == "enterprise-quote")
    assert quote.monthly_usd is None
    html = render_one_pager(c)
    assert "quote required" in html
    # No money cell may render as a price of zero; the quote row shows em dashes.
    assert "$0.00</td>" not in html
    assert '<td class="num">—</td>' in html


def test_per_ticket_matches_the_dashboard_rounding():
    assert _usd(0.495) == "$0.50"      # not $0.49 (binary-float rounding)
    assert _usd(0.00018) == "$0.00018"
    assert _usd(0) == "$0"
    assert _usd(None) == "—"


def test_our_side_states_the_measurement_basis():
    html = render_one_pager(_comparison(n_runs=30))
    assert "measured" in html
    assert "30</b> recorded decision(s)" in html
    assert "mock" in html


# --------------------------------------------------------- honesty sections

def test_sample_floor_caveat_is_rendered_when_below_floor():
    html = render_one_pager(_comparison(n_runs=3))
    assert "indicative" in html
    assert "floor" in html


def test_we_publish_the_plans_that_beat_us():
    c = _comparison(per_outcome_usd=0.01)  # cheat the market rate down
    assert c.plans_that_beat_us
    html = render_one_pager(c)
    assert "Where this comparison says we lose" in html
    for slug in c.plans_that_beat_us:
        vendor = next(p.vendor for p in c.plans if p.slug == slug)
        assert vendor in html


def test_winning_is_not_reported_as_a_quality_claim():
    html = render_one_pager(_comparison())
    assert "not a claim about" in html
    assert "per-decision pricing charges you for the work" in html


def test_verdict_labels_stay_terse_enough_for_the_column_layout():
    """A long verdict label widens that column and starves the plan column,
    whose source notes then wrap into ribbons — measured at 247px vs 156px
    before this was pinned."""
    html = render_one_pager(_comparison())
    labels = re.findall(r'<td class="verdict"><span class="[^"]*">([^<]+)</span></td>', html)
    assert labels, "verdict cells must carry the verdict class"
    assert all(len(x) <= 24 for x in labels), labels


def test_plan_column_is_allowed_to_dominate():
    # The first column is explicitly width-capped in CSS so the notes fit.
    assert "td:first-child,th:first-child{width:42%}" in render_one_pager(_comparison())


def test_page_invites_verification():
    html = render_one_pager(_comparison())
    assert REPO_URL in html
    assert "python -m scripts.demo" in html


# ----------------------------------------------------------------- endpoint

def test_endpoint_serves_html_and_matches_the_cli_renderer(client):
    r = client.get("/api/analytics/cost-comparison/one-pager.html",
                   params={"monthly_volume": 1500, "resolution_rate_pct": 50, "seats": 3})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    # Same renderer as scripts.cost_onepager, over the same ledger: the
    # endpoint reads this deployment's runs, so the expectation is built from
    # `storage.all("runs")` rather than a synthetic list. No drift between
    # the two paths for identical inputs.
    req = CostComparisonRequest(monthly_volume=1500, resolution_rate_pct=50, seats=3)
    assert r.text == render_one_pager(compare_costs(storage.all("runs"), req))


def test_endpoint_validates_its_inputs(client):
    assert client.get("/api/analytics/cost-comparison/one-pager.html",
                      params={"monthly_volume": 0}).status_code == 422
    assert client.get("/api/analytics/cost-comparison/one-pager.html",
                      params={"resolution_rate_pct": 150}).status_code == 422


def test_endpoint_works_on_a_fresh_install(client):
    """No runs recorded: the page must render and say so, not 500."""
    r = client.get("/api/analytics/cost-comparison/one-pager.html")
    assert r.status_code == 200
    assert "assumed default" in r.text or "default" in r.text
