"""The receipt as an artifact you can hand over: HTTP export, and the CLI.

The interesting question is not "does the endpoint return JSON" — it is
whether the thing the endpoint returns, saved to disk and checked by a
stranger with nothing but Python, still verifies. So these tests take the
download, write it to a file, and run the CLI against it as a subprocess.

Covered:
  * the download is a file (Content-Disposition), not a page;
  * the published public key is the key that signed the receipts;
  * verification over HTTP agrees with verification in-process;
  * a tampered file fails the CLI with exit 1, and the message says why;
  * a two-receipt run verifies as a chain, and `--require-root` behaves.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import receipts  # noqa: E402


def _run(client, subject="Charged twice for my subscription"):
    resp = client.post("/api/tickets", json={
        "customer_email": "a@b.com", "subject": subject,
        "body": "You charged my card twice — $49.00 on the 3rd and again on the 5th.",
    })
    assert resp.status_code == 200
    return resp.json()


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "scripts.verify_receipt", *args],
                          cwd=ROOT, capture_output=True, text=True)


# -------------------------------------------------------------------- export
def test_the_receipt_downloads_as_a_file_that_verifies(client):
    run = _run(client)
    resp = client.get(f"/api/runs/{run['id']}/receipt.json")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert run["id"] in resp.headers["content-disposition"]
    assert receipts.verify_receipt(resp.json())["ok"] is True


def test_an_unknown_run_gets_a_404_that_says_so(client):
    resp = client.get("/api/runs/run_does_not_exist/receipt.json")
    assert resp.status_code == 404
    assert "no receipt for run" in resp.json()["detail"]


def test_the_published_key_is_the_key_that_signed(client):
    _run(client)
    body = client.get("/api/receipts/public-key").json()
    assert body["alg"] == "ed25519"
    assert body["public_key"] == receipts.public_key_hex()
    assert body["key_id"] == body["public_key"][:16]
    stored = client.get("/api/receipts").json()
    assert stored
    assert {r["signature"]["public_key"] for r in stored} == {body["public_key"]}


def test_the_stored_chain_verifies_over_http(client):
    _run(client)
    _run(client, subject="API returning 429 errors")
    body = client.get("/api/receipts/verify").json()
    assert body["ok"] is True
    assert body["checked"] == 2
    assert body["linkage_complete"] is True
    assert body["starts_at_index"] == 0


def test_verification_over_http_matches_in_process_verification(client):
    run = _run(client)
    receipt = client.get(f"/api/runs/{run['id']}/receipt.json").json()
    over_http = client.post("/api/receipts/verify", json=receipt).json()
    in_process = receipts.verify_receipt(receipt)
    assert over_http["ok"] == in_process["ok"] is True
    assert over_http["checks"]["payload_sha256"] == in_process["checks"]["payload_sha256"]


def test_a_tampered_receipt_posted_for_verification_is_refused(client):
    run = _run(client)
    receipt = client.get(f"/api/runs/{run['id']}/receipt.json").json()
    receipt["payload"]["decision"]["risk_score"] = 0.0
    body = client.post("/api/receipts/verify", json=receipt).json()
    assert body["ok"] is False
    assert "digest" in body["reason"]


def test_a_bundle_can_be_verified_over_http(client):
    _run(client)
    _run(client)
    bundle = {"receipts": client.get("/api/receipts").json()}
    body = client.post("/api/receipts/verify", json=bundle).json()
    assert body["ok"] is True
    assert body["checked"] == 2


# ----------------------------------------------------------------------- CLI
def test_cli_verifies_a_downloaded_receipt(tmp_path, client):
    run = _run(client)
    path = tmp_path / "receipt.json"
    path.write_text(client.get(f"/api/runs/{run['id']}/receipt.json").text,
                    encoding="utf-8")
    proc = _cli(str(path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK:" in proc.stdout
    assert run["id"] in proc.stdout


def test_cli_json_output_is_machine_readable(tmp_path, client):
    run = _run(client)
    path = tmp_path / "receipt.json"
    path.write_text(client.get(f"/api/runs/{run['id']}/receipt.json").text,
                    encoding="utf-8")
    proc = _cli(str(path), "--json")
    assert proc.returncode == 0
    body = json.loads(proc.stdout)
    assert body["ok"] is True
    assert body["checks"]["signature_valid"] is True


def test_cli_fails_a_tampered_file_with_an_explanation(tmp_path, client):
    run = _run(client)
    receipt = client.get(f"/api/runs/{run['id']}/receipt.json").json()
    receipt["payload"]["disposition"] = "failed"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    proc = _cli(str(path))
    assert proc.returncode == 1
    assert "FAILED" in proc.stderr
    assert "digest" in proc.stderr


def test_cli_explains_when_the_chain_does_not_start_at_zero(tmp_path, client):
    first = _run(client)
    second = _run(client, subject="Pricing question")
    only_second = tmp_path / "second.json"
    only_second.write_text(client.get(f"/api/runs/{second['id']}/receipt.json").text,
                           encoding="utf-8")
    loose = _cli(str(only_second))
    assert loose.returncode == 0
    assert "starts mid-ledger" in loose.stdout

    strict = _cli(str(only_second), "--require-root")
    assert strict.returncode == 1
    assert "earlier receipts" in strict.stderr


def test_cli_verifies_a_two_file_chain_in_order(tmp_path, client):
    first = _run(client)
    second = _run(client, subject="Pricing question")
    paths = []
    for run, name in ((first, "a.json"), (second, "b.json")):
        path = tmp_path / name
        path.write_text(client.get(f"/api/runs/{run['id']}/receipt.json").text,
                        encoding="utf-8")
        paths.append(str(path))
    ok = _cli(*paths)
    assert ok.returncode == 0, ok.stderr
    reordered = _cli(paths[1], paths[0])
    assert reordered.returncode == 1
    assert "chain broken" in reordered.stderr


def test_cli_rejects_unreadable_input(tmp_path):
    bad = tmp_path / "not.json"
    bad.write_text("{not json", encoding="utf-8")
    assert _cli(str(bad)).returncode == 2
    assert _cli(str(tmp_path / "missing.json")).returncode == 2


@pytest.mark.parametrize("bundle", [{"receipts": []}])
def test_cli_reports_an_empty_bundle(tmp_path, bundle):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    proc = _cli(str(path))
    assert proc.returncode == 1
    assert "no receipts supplied" in proc.stderr
