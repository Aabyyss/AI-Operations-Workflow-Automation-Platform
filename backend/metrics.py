"""Prometheus exposition for the platform's own telemetry.

Renders the stored operational state as the plain-text format Prometheus
scrapes (version 0.0.4), so monitoring needs zero client libraries: point
a scrape job at /metrics and the pipeline's throughput, containment,
cost, latency and queue depth land in your dashboards like any other
service. Pure function over store contents — trivially testable.
"""
from __future__ import annotations

from .run_metrics import percentile

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

AUTO = {"auto_resolved", "auto_replied"}


def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _line(name: str, labels: dict[str, str] | None, value: float) -> str:
    if labels:
        inner = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items())
        return f"{name}{{{inner}}} {value}"
    return f"{name} {value}"


def render_prometheus(runs: list[dict], reviews: list[dict],
                      usage: list[dict], feedback: list[dict],
                      outbox: list[dict], knowledge_chunks: int) -> str:
    """All platform metrics in Prometheus text format."""
    lines: list[str] = []

    # -- throughput -------------------------------------------------------
    lines.append("# HELP aiops_runs_total Support pipeline runs by disposition.")
    lines.append("# TYPE aiops_runs_total counter")
    by_disp: dict[str, int] = {}
    for r in runs:
        d = r.get("disposition", "unknown")
        by_disp[d] = by_disp.get(d, 0) + 1
    for d, n in sorted(by_disp.items()):
        lines.append(_line("aiops_runs_total", {"disposition": d}, n))
    lines.append(_line("aiops_runs_total", {"disposition": "auto"}, float(sum(
        n for d, n in by_disp.items() if d in AUTO))))

    # -- latency ----------------------------------------------------------
    lat = [float(r["total_latency_ms"]) for r in runs
           if r.get("total_latency_ms") is not None]
    lines.append("# HELP aiops_pipeline_latency_ms Pipeline wall time in ms.")
    lines.append("# TYPE aiops_pipeline_latency_ms gauge")
    lines.append(_line("aiops_pipeline_latency_ms", {"quantile": "0.5"},
                       round(percentile(lat, 50), 3)))
    lines.append(_line("aiops_pipeline_latency_ms", {"quantile": "0.95"},
                       round(percentile(lat, 95), 3)))

    # -- cost -------------------------------------------------------------
    cost = sum(float(u.get("cost_usd", 0.0)) for u in usage)
    lines.append("# HELP aiops_llm_cost_usd_total Cumulative LLM spend in USD.")
    lines.append("# TYPE aiops_llm_cost_usd_total counter")
    lines.append(_line("aiops_llm_cost_usd_total", None, round(cost, 6)))

    # -- queues / governance ---------------------------------------------
    pending = sum(1 for r in reviews if r.get("status") == "pending")
    lines.append("# HELP aiops_reviews_pending Human-approval queue depth.")
    lines.append("# TYPE aiops_reviews_pending gauge")
    lines.append(_line("aiops_reviews_pending", None, pending))

    out_pending = sum(1 for o in outbox if o.get("status") == "pending")
    lines.append("# HELP aiops_outbox_pending Outbound actions awaiting delivery.")
    lines.append("# TYPE aiops_outbox_pending gauge")
    lines.append(_line("aiops_outbox_pending", None, out_pending))

    # -- feedback ---------------------------------------------------------
    lines.append("# HELP aiops_feedback_total Operator feedback by rating.")
    lines.append("# TYPE aiops_feedback_total counter")
    for rating in ("up", "down"):
        n = sum(1 for f in feedback if f.get("rating") == rating)
        lines.append(_line("aiops_feedback_total", {"rating": rating}, n))

    # -- knowledge base ---------------------------------------------------
    lines.append("# HELP aiops_knowledge_chunks Loaded RAG chunk count.")
    lines.append("# TYPE aiops_knowledge_chunks gauge")
    lines.append(_line("aiops_knowledge_chunks", None, knowledge_chunks))

    return "\n".join(lines) + "\n"
