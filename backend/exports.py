"""CSV exports — the flat files Power BI actually ingests.

Power BI desktop wants stable, typed, tabular feeds; the JSON analytics
endpoints are for humans and n8n. Every export is a pure function from
stored records to CSV text, so the schema is pinned by tests and the
refresh in Power BI is just a scheduled GET.
"""
from __future__ import annotations

import csv
import io

RUN_COLUMNS = ["run_id", "ticket_id", "disposition", "risk_score",
               "confidence", "category", "cost_usd", "latency_ms",
               "actions_taken", "error", "review_id"]

REVIEW_COLUMNS = ["review_id", "ticket_id", "created_at", "status",
                  "risk_score", "reason", "reviewed_by", "reviewed_at"]

USAGE_COLUMNS = ["ts", "ticket_id", "agent", "model", "tokens_in",
                 "tokens_out", "cost_usd", "mode"]


def _rows(records: list[dict], columns: list[str], mappers: dict[str, object]) -> list[list]:
    rows = []
    for rec in records:
        row = []
        for col in columns:
            mapper = mappers.get(col)
            row.append(mapper(rec) if mapper else rec.get(col, ""))
        rows.append(row)
    return rows


def _to_csv(columns: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows(rows)
    return buf.getvalue()


def runs_csv(runs: list[dict]) -> str:
    mappers = {
        "category": lambda r: ((r.get("intake") or {}).get("category") or ""),
        "risk_score": lambda r: ((r.get("decision") or {}).get("risk_score", "")),
        "confidence": lambda r: ((r.get("decision") or {}).get("confidence", "")),
        "actions_taken": lambda r: ";".join(r.get("actions_taken") or []),
        "latency_ms": lambda r: r.get("total_latency_ms", ""),
        "cost_usd": lambda r: r.get("total_cost_usd", ""),
        "run_id": lambda r: r.get("id", ""),
    }
    return _to_csv(RUN_COLUMNS, _rows(runs, RUN_COLUMNS, mappers))


def reviews_csv(reviews: list[dict]) -> str:
    return _to_csv(REVIEW_COLUMNS, _rows(reviews, REVIEW_COLUMNS, {}))


def usage_csv(usage: list[dict]) -> str:
    return _to_csv(USAGE_COLUMNS, _rows(usage, USAGE_COLUMNS, {}))
