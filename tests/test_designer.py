"""Tests for the workflow designer (Analysis → importable n8n workflow)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402


def _analyze(client: TestClient) -> str:
    payload = {
        "name": "Ticket Handling",
        "department": "support",
        "monthly_volume": 1500,
        "hourly_rate_usd": 25.0,
        "steps": [
            {"name": "Read ticket", "minutes_per_item": 1.0, "touches_pii": True},
            {"name": "Categorize", "minutes_per_item": 0.5, "repetitive": True},
            {"name": "Draft reply", "minutes_per_item": 3.0, "requires_judgment": True},
            {"name": "Refund approval", "minutes_per_item": 2.0, "touches_money": True},
        ],
    }
    r = client.post("/api/analyze", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["process_id"]


def test_design_returns_importable_n8n_json(client: TestClient):
    process_id = _analyze(client)
    r = client.post("/api/workflows/design",
                    json={"process_id": process_id,
                          "workflow_name": "Support Automation"})
    assert r.status_code == 200, r.text
    body = r.json()

    # Record metadata
    assert body["process_id"] == process_id
    assert body["name"] == "Support Automation"
    assert body["source_score"] is not None
    assert body["node_count"] >= 8

    wf = body["workflow"]
    # The keys n8n's importer expects.
    assert {"name", "nodes", "connections", "settings"} <= set(wf)

    types = {n["type"] for n in wf["nodes"]}
    assert "n8n-nodes-base.webhook" in types
    assert "n8n-nodes-base.httpRequest" in types
    assert "n8n-nodes-base.if" in types
    assert "n8n-nodes-base.slack" in types
    assert "n8n-nodes-base.stickyNote" in types

    # Intake chain is wired: webhook → pipeline call → routing IF.
    conns = wf["connections"]
    assert conns["Ticket Intake"]["main"][0][0]["node"] == "Run AI Pipeline"
    assert conns["Run AI Pipeline"]["main"][0][0]["node"] == "Needs Human?"
    # IF node has two branches: escalation and reply.
    assert len(conns["Needs Human?"]["main"]) == 2

    # Pipeline node targets this API.
    pipe = next(n for n in wf["nodes"] if n["name"] == "Run AI Pipeline")
    assert pipe["parameters"]["url"].endswith("/api/tickets")

    # Sticky note carries the human-readable AI mapping.
    note = next(n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.stickyNote")
    assert "Read ticket" in note["parameters"]["content"]
    assert "Estimated savings" in note["parameters"]["content"]


def test_design_unknown_process_404(client: TestClient):
    r = client.post("/api/workflows/design",
                    json={"process_id": "proc_nope"})
    assert r.status_code == 404


def test_design_is_deterministic_and_listed(client: TestClient):
    process_id = _analyze(client)
    a = client.post("/api/workflows/design", json={"process_id": process_id}).json()
    b = client.post("/api/workflows/design", json={"process_id": process_id}).json()
    # Same analysis → same node graph (ids/names stable), only metadata differs.
    strip = lambda d: {n["name"] for n in d["workflow"]["nodes"]}
    assert strip(a) == strip(b)

    listed = client.get("/api/workflows").json()
    assert len(listed) >= 2
    assert all("workflow" not in rec for rec in listed)  # metadata only


def test_download_returns_workflow_only(client: TestClient):
    process_id = _analyze(client)
    designed = client.post("/api/workflows/design",
                           json={"process_id": process_id}).json()
    r = client.get(f"/api/workflows/{designed['id']}/download")
    assert r.status_code == 200
    wf = r.json()
    assert set(wf) == {"name", "nodes", "connections", "settings", "meta", "tags"}
    assert wf["name"].startswith("AIOPS —")

    missing = client.get("/api/workflows/wf_nope/download")
    assert missing.status_code == 404
