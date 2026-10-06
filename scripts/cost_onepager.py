"""Generate a buyer-facing cost one-pager from this deployment's own data.

Usage:
    python -m scripts.cost_onepager [--out marketing/cost-one-pager.html]
                                    [--volume 1500] [--rate 50] [--seats 0]
                                    [--platform 50] [--human-cost]
                                    [--human-rate 25] [--human-minutes 6]

Why a script instead of a hand-written document: the figures come from
`cost_compare.compare_costs` reading the real run ledger, so the page cannot
disagree with the API a prospect might call themselves. Change a number,
regenerate, done — no copy to keep in sync.

The output is self-contained HTML (no CDN, no JS, no images). Open it and
Ctrl+P for a clean PDF, or attach the file directly.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.cost_compare import compare_costs  # noqa: E402
from backend.models import CostComparisonRequest  # noqa: E402
from backend.one_pager import render_one_pager  # noqa: E402
from backend.store import storage  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "marketing" / "cost-one-pager.html"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help="output .html path (default: marketing/cost-one-pager.html)")
    ap.add_argument("--volume", type=int, default=1500, help="conversations per month")
    ap.add_argument("--rate", type=float, default=50.0,
                    help="share of volume the vendor's meter bills for (%%)")
    ap.add_argument("--seats", type=int, default=0, help="seats, for seat-based plans")
    ap.add_argument("--platform", type=float, default=50.0,
                    help="our monthly infra allowance in USD")
    ap.add_argument("--human-cost", action="store_true",
                    help="also price the human minutes our escalations consume")
    ap.add_argument("--human-rate", type=float, default=25.0, help="USD/hour for review time")
    ap.add_argument("--human-minutes", type=float, default=6.0,
                    help="minutes per escalated ticket")
    ap.add_argument("--quiet", action="store_true", help="do not print the summary")
    args = ap.parse_args()

    comparison = compare_costs(storage.all("runs"), CostComparisonRequest(
        monthly_volume=args.volume,
        resolution_rate_pct=args.rate,
        seats=args.seats,
        platform_monthly_usd=args.platform,
        include_human_review_cost=args.human_cost,
        human_hourly_rate_usd=args.human_rate,
        human_minutes_per_escalation=args.human_minutes,
    ))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_one_pager(comparison), encoding="utf-8")

    if not args.quiet:
        print(f"one-pager written: {out}")
        print(f"  our side      ${comparison.our_monthly_usd:,.2f}/month "
              f"(${comparison.our_cost_per_ticket_usd} per ticket)")
        print(f"  measured from {comparison.measured_from_decisions} decision(s) "
              f"— basis: {comparison.measurement_basis}")
        for p in comparison.plans:
            price = "quote required" if p.monthly_usd is None else f"${p.monthly_usd:,.2f}/month"
            cross = ("n/a" if p.breakeven_resolution_rate_pct is None
                     else f"costs more above {p.breakeven_resolution_rate_pct}%")
            print(f"  {p.slug:<18} {price:<18} {cross}")
        if comparison.plans_that_beat_us:
            print(f"  cheaper than us: {', '.join(comparison.plans_that_beat_us)}")
        if not comparison.sample_sufficient:
            print(f"  NB: {comparison.measured_from_decisions} decisions is below the "
                  f"{comparison.min_sample_for_measured}-sample floor — the page says so.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
