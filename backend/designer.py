"""Workflow designer — the bridge from Side A to Side B.

An Analysis answers "should we automate this?". The designer answers
"what does the automation actually look like?" by emitting an n8n
workflow JSON that imports cleanly into n8n (File → Import from File).

Generated structure, mirroring the governance model:

  1. Intake chain    : webhook → POST /api/tickets (the agentic pipeline)
  2. Escalation chain: IF disposition == human_review → Slack ping;
                       approvals flow back via POST /api/reviews/{id}/decision
  3. Monitoring chain: schedule trigger → GET /api/analytics/summary and
                       GET /api/reviews?status=pending
  4. Sticky note     : the per-step AI mapping, so a human reviewer sees
                       what was automated and why.

The generation is deterministic — no LLM in the loop — because an
importable integration artifact must be reproducible and reviewable.
"""
from __future__ import annotations

from typing import Any

from .models import WorkflowDesignRequest, iso_now, new_id

HTTP = "n8n-nodes-base.httpRequest"
STICKY = "n8n-nodes-base.stickyNote"
WEBHOOK = "n8n-nodes-base.webhook"
IF_NODE = "n8n-nodes-base.if"
SLACK = "n8n-nodes-base.slack"
RESPOND = "n8n-nodes-base.respondToWebhook"
SCHEDULE = "n8n-nodes-base.scheduleTrigger"

DEFAULT_BASE_URL = "http://api:8000"  # docker-compose service name for n8n


def _node(name: str, ntype: str, params: dict[str, Any], x: int, y: int,
          version: float = 1) -> dict[str, Any]:
    return {"parameters": params, "id": new_id("node").replace("node_", ""),
            "name": name, "type": ntype, "typeVersion": version,
            "position": [x, y]}


def _chain(names: list[str]) -> dict[str, Any]:
    """Sequential main connections: a → b → c."""
    out: dict[str, Any] = {}
    for a, b in zip(names, names[1:]):
        out[a] = {"main": [[{"node": b, "type": "main", "index": 0}]]}
    return out


def _mapping_note(analysis: dict[str, Any]) -> str:
    lines = [f"AI mapping — {analysis.get('name', 'process')} "
             f"(automation score {analysis.get('score', {}).get('total', '?')}/100)", ""]
    for m in analysis.get("ai_mapping", []):
        lines.append(f"• {m.get('step', '?')} → {m.get('recommendation', '?')}")
    costs = analysis.get("costs", {})
    lines += ["", f"Estimated savings: ${costs.get('monthly_savings_usd', 0):,.0f}/month, "
                  f"payback {costs.get('payback_months', 0)} months"]
    return "\n".join(lines)


def design_workflow(analysis: dict[str, Any], base_url: str = DEFAULT_BASE_URL,
                    name: str | None = None) -> dict[str, Any]:
    """Build an importable n8n workflow from a stored Analysis record."""
    process_id = analysis["process_id"]
    wf_name = name or f"AIOPS — {analysis.get('name', process_id)}"

    nodes = [
        _node("Ticket Intake", WEBHOOK,
              {"path": f"aiops/{process_id}/ticket", "httpMethod": "POST",
               "responseMode": "responseNode"}, 260, 300, 2),
        _node("Run AI Pipeline", HTTP,
              {"method": "POST", "url": f"{base_url}/api/tickets",
               "sendBody": True, "specifyBody": "json",
               "jsonBody": ('={{ JSON.stringify({ customer_email: $json.body.'
                            'customer_email, subject: $json.body.subject,'
                            ' body: $json.body.body, channel: "web" }) }}')},
              480, 300, 4.2),
        _node("Needs Human?", IF_NODE,
              {"conditions": {"string": [{"value1": '={{$json["disposition"]}}',
                                          "operation": "equals",
                                          "value2": "human_review"}]}},
              700, 300),
        _node("Reply to Customer", RESPOND,
              {"respondWith": "json", "responseBody": "={{ $json }}"},
              920, 220, 1.1),
        _node("Escalate in Slack", SLACK,
              {"resource": "message", "operation": "post", "select": "channel",
               "channelId": {"__rl": True, "value": "#ai-escalations", "mode": "name"},
               "text": ('=Review needed for ticket {{ $json["ticket_id"] }} '
                        '(risk {{ $json["decision"]["risk_score"] }}): '
                        'review id {{ $json["review_id"] }}')},
              920, 400, 2.2),
        _node("Hourly Digest", SCHEDULE,
              {"rule": {"interval": [{"field": "hours", "hoursInterval": 1}]}},
              260, 560, 1.2),
        _node("Fetch Analytics", HTTP,
              {"method": "GET", "url": f"{base_url}/api/analytics/summary"},
              480, 560, 4.2),
        _node("Fetch Pending Approvals", HTTP,
              {"method": "GET", "url": f"{base_url}/api/reviews?status=pending"},
              700, 560, 4.2),
        _node("Workflow Notes", STICKY,
              {"content": _mapping_note(analysis),
               "width": 380, "height": 260}, 260, 20),
    ]

    connections: dict[str, Any] = {}
    connections.update(_chain(["Ticket Intake", "Run AI Pipeline", "Needs Human?"]))
    # IF node branches: true → Slack escalation, false → respond.
    connections["Needs Human?"] = {"main": [
        [{"node": "Escalate in Slack", "type": "main", "index": 0}],
        [{"node": "Reply to Customer", "type": "main", "index": 0}],
    ]}
    connections.update(_chain(["Hourly Digest", "Fetch Analytics",
                               "Fetch Pending Approvals"]))

    workflow = {
        "name": wf_name,
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
        "meta": {"instanceId": "aiops-workflow-designer"},
        "tags": ["aiops"],
    }
    return {
        "id": new_id("wf"),
        "process_id": process_id,
        "name": wf_name,
        "created_at": iso_now(),
        "base_url": base_url,
        "node_count": len(nodes),
        "source_score": analysis.get("score", {}).get("total"),
        "workflow": workflow,
    }


def design_from_request(analysis: dict[str, Any], req: WorkflowDesignRequest,
                        base_url: str = DEFAULT_BASE_URL) -> dict[str, Any]:
    return design_workflow(analysis, base_url=base_url, name=req.workflow_name)
