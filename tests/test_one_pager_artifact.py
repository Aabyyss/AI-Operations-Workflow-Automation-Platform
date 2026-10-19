"""The committed one-pager must still be what the renderer produces.

`marketing/cost-one-pager.html` is a generated document that leaves the
building. Its failure mode is quiet: a caveat reworded in the renderer, a
rounding fix, a new market preset — and the committed page keeps claiming
last month's numbers to a buyer who has no way to know. Or someone edits
the HTML by hand to make a headline nicer, and nothing anywhere notices.

The sidecar (`cost-one-pager.source.json`) records the inputs, the exact
ledger slice the comparison read, and the digest of the file that was
written. These tests check the pair, offline, with no ledger and no server:

1. the committed artifact verifies against its sidecar and re-renders
   byte-for-byte from the current code;
2. a hand-edited artifact fails on the digest;
3. a renderer change fails on the re-render, with a message that says what
   to run;
4. the CLI exit code is the CI gate, so a pipeline can rely on it.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import provenance  # noqa: E402
from backend.provenance import sha256_text, source_path_for, verify  # noqa: E402

ARTIFACT = ROOT / "marketing" / "cost-one-pager.html"
SIDECAR = source_path_for(ARTIFACT)


# ------------------------------------------------------------------ the gate
def test_committed_artifact_verifies_against_its_provenance():
    result = verify(ARTIFACT)
    assert result["ok"], result["reason"]


def test_committed_artifact_re_renders_byte_for_byte():
    """The strongest form of the check: same inputs, same HTML, today."""
    source = json.loads(SIDECAR.read_text(encoding="utf-8"))
    assert provenance.rebuild(source) == ARTIFACT.read_text(encoding="utf-8")


def test_sidecar_declares_the_digest_of_the_file_it_ships_with():
    source = json.loads(SIDECAR.read_text(encoding="utf-8"))
    assert source["artifact"] == ARTIFACT.name
    assert source["artifact_sha256"] == sha256_text(ARTIFACT.read_text(encoding="utf-8"))
    assert source["manifest_version"] == provenance.MANIFEST_VERSION


def test_sidecar_records_inputs_and_ledger_basis_but_no_customer_data():
    source = json.loads(SIDECAR.read_text(encoding="utf-8"))
    assert source["request"]["monthly_volume"] > 0
    assert source["ledger"]["decisions"] == len(source["ledger_excerpt"])
    assert source["ledger"]["measurement_basis"] in {"measured", "indicative", "default"}
    # The excerpt exists so this can be committed: outcomes and prices only.
    for row in source["ledger_excerpt"]:
        assert set(row) == {"disposition", "total_cost_usd"}
    blob = SIDECAR.read_text(encoding="utf-8")
    assert "@" not in blob  # no email addresses
    assert "customer" not in blob.lower()


# --------------------------------------------------------------- drift cases
def _copy(tmp_path: Path) -> tuple[Path, Path]:
    artifact = tmp_path / ARTIFACT.name
    artifact.write_text(ARTIFACT.read_text(encoding="utf-8"), encoding="utf-8")
    sidecar = source_path_for(artifact)
    sidecar.write_text(SIDECAR.read_text(encoding="utf-8"), encoding="utf-8")
    return artifact, sidecar


def test_a_hand_edited_figure_fails_on_the_digest(tmp_path):
    artifact, _ = _copy(tmp_path)
    html = artifact.read_text(encoding="utf-8")
    assert "$742.50" in html  # the Intercom monthly figure
    artifact.write_text(html.replace("$742.50", "$684.00", 1), encoding="utf-8")
    result = verify(artifact)
    assert not result["ok"]
    assert "digest" in result["reason"]
    assert "edited or replaced" in result["reason"]


def test_a_renderer_change_fails_on_the_re_render(tmp_path, monkeypatch):
    """Any change to the page's output — wording, columns, rounding — is drift."""
    artifact, _ = _copy(tmp_path)
    real = provenance.render_one_pager

    def nudged(comparison, **kwargs):
        return real(comparison, **kwargs).replace("</body>", "<p>new caveat</p></body>")

    monkeypatch.setattr(provenance, "render_one_pager", nudged)
    result = verify(artifact)
    assert not result["ok"]
    assert "current renderer produces different HTML" in result["reason"]
    assert "scripts.cost_onepager" in result["reason"]


def test_a_missing_sidecar_is_reported_not_crashed(tmp_path):
    artifact = tmp_path / ARTIFACT.name
    artifact.write_text("<!DOCTYPE html>", encoding="utf-8")
    result = verify(artifact)
    assert not result["ok"]
    assert "sidecar missing" in result["reason"]


def test_an_unknown_manifest_version_is_refused(tmp_path):
    artifact, sidecar = _copy(tmp_path)
    source = json.loads(sidecar.read_text(encoding="utf-8"))
    source["manifest_version"] = 99
    sidecar.write_text(json.dumps(source), encoding="utf-8")
    result = verify(artifact)
    assert not result["ok"]
    assert "manifest_version" in result["reason"]


# ------------------------------------------------------------------- the CLI
def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "scripts.cost_onepager", *args],
                          cwd=ROOT, capture_output=True, text=True)


def test_cli_verify_exits_zero_for_the_committed_artifact():
    proc = _cli("--verify")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.startswith("ok:")


@pytest.mark.skipif(not ARTIFACT.exists(), reason="artifact not generated")
def test_cli_verify_exits_one_on_drift(tmp_path):
    artifact, _ = _copy(tmp_path)
    artifact.write_text(artifact.read_text(encoding="utf-8") + "\n<!-- stale -->\n",
                        encoding="utf-8")
    proc = _cli("--verify", "--out", str(artifact))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "DRIFT" in proc.stdout
