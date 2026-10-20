"""The offline receipt viewer (docs/receipt_viewer.html).

The receipt's whole promise is that anyone can check it with nothing but a
browser — so the viewer is tested the way an auditor uses it: the HTML file
on disk, opened from file://, with no server behind it. That means these
tests exercise the *JavaScript itself*, not a re-implementation of it:

1. Self-containedness — no network, no external anything. A viewer that
   fetched a CDN script would be evidence of nothing.
2. The embedded SHA-2 tables equal their mathematical definition
   (`scripts/gen_sha_constants`), because a mistyped constant is a viewer
   that rejects every genuine receipt.
3. The JavaScript crypto agrees with node:crypto and OpenSSL, both
   directions (`selftest` in the harness).
4. The JavaScript canonical form is byte-identical to Python's — including
   the int/float distinction (500 vs 500.0) that JSON.parse erases and
   that the payload digest was computed over.
5. Receipts Python signed verify in JavaScript; tampering, an unpublished
   signer and a broken chain fail with the same reasons Python gives.

Tests that need node skip when node is absent (CI installs it; a laptop
verifying a receipt does not need it).
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import receipts  # noqa: E402

VIEWER = ROOT / "docs" / "receipt_viewer.html"
HARNESS = ROOT / "tests" / "viewer_harness.mjs"
needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed")


# ------------------------------------------------------- the file itself
def test_the_viewer_is_one_self_contained_file():
    """A verifier that downloads something is not offline — and the thing
    it downloads is a thing an attacker can replace."""
    html = VIEWER.read_text(encoding="utf-8")
    assert html.count("<script") == 1, "one inline script, no imports"
    for forbidden in ("<script src", "<link", "fetch(", "XMLHttpRequest",
                      "navigator.sendBeacon", "WebSocket", "EventSource",
                      "importScripts(", "@import", "url(http", 'src="',
                      'href="http'):
        assert forbidden not in html, forbidden


def test_the_page_opens_without_a_server_and_says_what_it_cannot_prove():
    html = VIEWER.read_text(encoding="utf-8")
    assert "file://" in html
    assert "What it cannot prove" in html
    assert "trust" in html


@needs_node
@pytest.mark.parametrize("flag,expected", [([], 0), (["--update"], 0)])
def test_the_embedded_sha_tables_match_the_definition(flag, expected):
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.gen_sha_constants", *flag],
        cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == expected, proc.stdout + proc.stderr
    if not flag:
        assert "match the definition" in proc.stdout


def test_a_drifting_constant_would_be_caught(tmp_path, monkeypatch, capsys):
    """The checker is not ceremonial: change one digit of one constant and
    it must fail, naming the entry."""
    from scripts import gen_sha_constants
    html = VIEWER.read_text(encoding="utf-8")
    drifted = html.replace("0x428a2f98d728ae22", "0x428a2f98d728ae23", 1)
    assert drifted != html
    fake_viewer = tmp_path / "receipt_viewer.html"
    fake_viewer.write_text(drifted, encoding="utf-8")
    monkeypatch.setattr(gen_sha_constants, "HTML_PATH", str(fake_viewer))
    monkeypatch.setattr(sys, "argv", ["gen_sha_constants"])
    assert gen_sha_constants.main() == 1
    assert "DRIFT: SHA512_K entry 0 is 0x428a2f98d728ae23" in capsys.readouterr().err


# ------------------------------------------------- the JavaScript under test
@pytest.fixture(scope="module")
def script_path(tmp_path_factory):
    """The viewer's inline script, extracted to a .js node can require."""
    html = VIEWER.read_text(encoding="utf-8")
    m = re.search(r"<script>\n(.*)\n</script>", html, re.S)
    assert m, "the viewer must keep exactly one inline <script> block"
    path = tmp_path_factory.mktemp("viewer") / "viewer_script.js"
    path.write_text(m.group(1), encoding="utf-8")
    return path


def _harness(script, *args):
    """Run the harness. stdout is decoded as UTF-8 explicitly: node writes
    UTF-8, and on Windows text=True would decode with the locale codec —
    which quietly corrupts any receipt containing an em dash."""
    proc = subprocess.run(["node", str(HARNESS), str(script), *args],
                          cwd=ROOT, capture_output=True)
    return subprocess.CompletedProcess(proc.args, proc.returncode,
                                       proc.stdout.decode("utf-8"),
                                       proc.stderr.decode("utf-8"))


@needs_node
def test_the_javascript_crypto_matches_node_and_openssl(script_path):
    """SHA-256/512 against node:crypto, Ed25519 against OpenSSL — in both
    directions, so a shared misunderstanding cannot pass."""
    proc = _harness(script_path, "selftest")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout) == {"ok": True, "failures": []}


@needs_node
@pytest.mark.parametrize("literal", ["0", "-0", "1", "500", "-500", "500.0",
                                     "0.1", "0.31", "120.5", "1e2", "1E2",
                                     "1e-5", "1e16", "1e21", "-2.5e-10",
                                     "0.0001", "0.00001", "2618.123456"])
def test_javascript_numbers_serialise_the_way_python_would(script_path, literal):
    """The digest was computed over Python's spelling of each number —
    500 (int) and 500.0 (float) are different bytes — so the viewer has to
    reproduce repr() for floats and str() for ints, exactly."""
    proc = _harness(script_path, "pynum", literal)
    assert proc.returncode == 0, proc.stderr
    mine = proc.stdout.strip()
    python = json.dumps(json.loads(literal))  # same rule: repr for floats
    assert mine == python, f"{literal}: js {mine!r} vs python {python!r}"


# ---------------------------------------------- Python-signed, JS-verified
@pytest.fixture()
def signed(client):
    """Two receipts signed around a key rotation, plus the artifacts an
    auditor would be handed: files, and the published key history."""
    first = client.post("/api/tickets", json={
        "customer_email": "a@b.com", "subject": "Charged twice",
        "body": "You charged my card twice — $49.00 on the 3rd and again on the 5th."}).json()
    r1 = receipts.receipt_for_run(first["id"])
    receipts.rotate_key()
    second = client.post("/api/tickets", json={
        "customer_email": "c@d.com", "subject": "Refund the duplicate",
        "body": "Refund $120 for the duplicate charge on my card today."}).json()
    r2 = receipts.receipt_for_run(second["id"])
    return {"r1": r1, "r2": r2, "keys": receipts.key_history_document()}


@pytest.fixture()
def files(signed, tmp_path):
    out = {}
    for name in ("r1", "r2", "keys"):
        path = tmp_path / f"{name}.json"
        with path.open("w", encoding="utf-8") as f:      # UTF-8: em dashes happen
            json.dump(signed[name], f, ensure_ascii=False, indent=1)
        out[name] = path
    bundle = tmp_path / "bundle.json"
    with bundle.open("w", encoding="utf-8") as f:
        json.dump({"receipts": [signed["r1"], signed["r2"]]}, f, ensure_ascii=False)
    out["bundle"] = bundle
    return out


@needs_node
def test_the_canonical_form_is_byte_identical_to_python(script_path, signed, tmp_path):
    """The one place a second implementation can silently disagree: the
    exact bytes that were signed. Unicode, floats and int-shaped floats all
    appear in a real payload, and all of them have bitten JSON stacks."""
    payload_file = tmp_path / "payload.json"
    with payload_file.open("w", encoding="utf-8") as f:
        json.dump(signed["r1"]["payload"], f, ensure_ascii=False)
    proc = _harness(script_path, "canonical", str(payload_file))
    assert proc.returncode == 0, proc.stderr
    python = receipts.canonical_bytes(signed["r1"]["payload"]).decode("utf-8")
    assert proc.stdout == python


@needs_node
def test_a_receipt_python_signed_verifies_in_javascript(script_path, files, signed):
    proc = _harness(script_path, "verify", str(files["r1"]), str(files["keys"]))
    verdict = json.loads(proc.stdout)
    assert verdict["ok"] is True
    assert verdict["checks"]["signature_valid"] is True
    assert verdict["checks"]["key_trusted"] is True
    assert verdict["checks"]["key_status"] == "retired"   # rotated out, still valid
    python = receipts.verify_receipt(
        signed["r1"], trusted_keys=receipts.trusted_keys_from(signed["keys"]))
    assert python["ok"] is True
    assert python["checks"]["key_status"] == "retired"

    proc = _harness(script_path, "verify", str(files["r2"]), str(files["keys"]))
    assert json.loads(proc.stdout)["checks"]["key_status"] == "active"


@needs_node
def test_a_bundle_verifies_as_a_chain_in_javascript(script_path, files):
    proc = _harness(script_path, "chain", str(files["bundle"]), str(files["keys"]))
    verdict = json.loads(proc.stdout)
    assert verdict["ok"] is True
    assert verdict["checked"] == 2
    assert verdict["linkage_complete"] is True


@needs_node
def test_tampering_fails_in_javascript_with_the_python_reason(script_path, files,
                                                              signed, tmp_path):
    tampered = json.loads(json.dumps(signed["r1"]))
    tampered["payload"]["decision"]["risk_score"] = 0.01
    path = tmp_path / "tampered.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(tampered, f, ensure_ascii=False)
    proc = _harness(script_path, "verify", str(path), str(files["keys"]))
    verdict = json.loads(proc.stdout)
    python = receipts.verify_receipt(tampered)
    assert verdict["ok"] is python["ok"] is False
    assert verdict["reason"] == python["reason"]


@needs_node
def test_an_unpublished_signer_fails_the_pinned_viewer(script_path, files, signed,
                                                        tmp_path):
    """A self-consistent forgery: valid signature, unknown key. Verifying
    without the key history passes (and says why that is all it means);
    with it, the viewer must refuse, naming the key."""
    from backend import ed25519
    forged = json.loads(json.dumps(signed["r2"]))
    forged["payload"]["decision"]["reason"] = "verified by legal"
    other = bytes(range(32))
    body = receipts.canonical_bytes(forged["payload"])
    forged["payload_sha256"] = receipts._sha256(body)
    forged["signature"] = {
        "alg": "ed25519",
        "key_id": ed25519.public_key(other).hex()[:16],
        "public_key": ed25519.public_key(other).hex(),
        "signature": ed25519.sign(other, body).hex(),
    }
    path = tmp_path / "forged.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(forged, f, ensure_ascii=False)

    pinned = json.loads(_harness(script_path, "verify", str(path),
                                 str(files["keys"])).stdout)
    assert pinned["ok"] is False
    assert "never published that key" in pinned["reason"]
    python = receipts.verify_receipt(
        forged, trusted_keys=receipts.trusted_keys_from(signed["keys"]))
    assert python["ok"] is False
    assert python["reason"] == pinned["reason"]

    loose = json.loads(_harness(script_path, "verify", str(path)).stdout)
    assert loose["ok"] is True          # self-consistent…
    assert loose["checks"]["key_status"] == "unpublished"  # …and says so


@needs_node
def test_the_verifier_refuses_garbage_with_a_reason(script_path, tmp_path):
    """A file that is not a receipt produces a verdict, not a stack trace —
    the page shows the same reason in its banner."""
    cases = {"not-json.json": ("{not json", "not valid JSON"),
             "not-object.json": ("[1, 2, 3]", "receipt is not a JSON object"),
             # Same as Python: with an empty payload/signature block the
             # digest check fires first, and says so.
             "no-block.json": ('{"payload": {}, "signature": {}}', "digest")}
    for name, (text, expected) in cases.items():
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        proc = _harness(script_path, "verify", str(path))
        verdict = json.loads(proc.stdout)
        assert verdict["ok"] is False, name
        assert expected in verdict["reason"], (name, verdict["reason"])


@needs_node
def test_an_unusable_key_history_is_refused(script_path, files):
    bad = files["keys"].parent / "bad-keys.json"
    bad.write_text(json.dumps({"keys": [{"public_key": "zz"}]}), encoding="utf-8")
    proc = _harness(script_path, "verify", str(files["r1"]), str(bad))
    assert proc.returncode != 0
    assert "not hex" in proc.stderr
