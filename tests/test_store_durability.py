"""Storage durability: a torn write, and a file that will not parse.

Both of these used to fail in the same direction — towards an empty report.
`write_text` truncates before it writes, so an interrupted save left a
half-written file; `_load` returned `[]` on any parse error, so the half file
read as "no runs, no tickets, no receipts". The API answered 200, the
dashboard showed nothing, and nothing anywhere said data was lost.

These tests hold the two replacements to their word: a save is atomic (no
temp file survives it, and a reader never sees half of one), and an
unreadable collection is quarantined with its bytes intact and surfaced by
health — never mistaken for an empty one.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.store import COLLECTION_NAMES, Storage  # noqa: E402


def test_a_collection_that_has_never_been_written_reads_as_empty(tmp_path):
    """The legitimate []: nothing was ever stored. Distinct from corrupt."""
    store = Storage()
    assert store.all("runs") == []
    assert not (tmp_path / "data").exists() or not list((tmp_path / "data").glob("*.json"))
    assert store.corruptions == []


def test_saves_leave_no_temp_file_behind(tmp_path, monkeypatch):
    """Atomicity's visible half: the temp file is renamed, never lingered."""
    monkeypatch.setattr("backend.config.DATA_DIR", tmp_path)
    store = Storage()
    store.append("runs", {"id": "r1"})
    store.append("runs", {"id": "r2"})

    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == [], f"a save left litter behind: {leftovers}"
    on_disk = json.loads((tmp_path / "runs.json").read_text(encoding="utf-8"))
    assert [r["id"] for r in on_disk] == ["r1", "r2"]


def test_a_failed_replace_preserves_the_previous_file(tmp_path, monkeypatch):
    """The property the temp file exists for: the destination is never torn.

    If the rename fails (disk full on some platforms, a locked file on
    Windows), the file on disk must still be the last good save — not a
    truncated mixture of old and new.
    """
    monkeypatch.setattr("backend.config.DATA_DIR", tmp_path)
    store = Storage()
    store.append("runs", {"id": "original"})

    def exploding_replace(src, dst):
        raise OSError("simulated: the rename failed")

    monkeypatch.setattr("backend.store.os.replace", exploding_replace)
    with pytest.raises(OSError):
        store.append("runs", {"id": "never-landed"})

    survivors = json.loads((tmp_path / "runs.json").read_text(encoding="utf-8"))
    assert [r["id"] for r in survivors] == ["original"]
    assert list(tmp_path.glob("*.tmp")) == [], "a failed save must not litter"


def test_a_truncated_file_is_quarantined_not_read_as_empty(tmp_path, monkeypatch):
    """The interruption this whole change exists for.

    A save killed mid-write leaves `[{"id": "r1"}, {"id"` on disk. The old
    code returned [] — every run gone, no error anywhere. The bytes must
    survive and the incident must be recorded.
    """
    monkeypatch.setattr("backend.config.DATA_DIR", tmp_path)
    store = Storage()
    (tmp_path / "runs.json").write_text('[{"id": "r1"}, {"id"', encoding="utf-8")

    assert store.all("runs") == []            # callers keep working
    assert len(store.corruptions) == 1        # but the loss is on the record

    incident = store.corruptions[0]
    assert incident["collection"] == "runs"
    assert "error" in incident and incident["error"]

    preserved = Path(incident["quarantined"])
    assert preserved.exists()
    assert preserved.read_text(encoding="utf-8") == '[{"id": "r1"}, {"id"'
    # Not .json, so nothing mistakes the wreckage for a live collection.
    assert not preserved.name.endswith(".json")
    assert not (tmp_path / "runs.json").exists()

    # The collection is usable again — and appending must not resurrect the
    # damaged bytes.
    store.append("runs", {"id": "after"})
    assert store.all("runs") == [{"id": "after"}]


def test_a_collection_that_is_not_a_list_is_quarantined_too(tmp_path, monkeypatch):
    """`{"runs": [...]}` parses fine and is still the wrong shape — the old
    code handed it to callers as if it were the collection itself."""
    monkeypatch.setattr("backend.config.DATA_DIR", tmp_path)
    store = Storage()
    (tmp_path / "feedback.json").write_text('{"feedback": [{"id": "f1"}]}',
                                            encoding="utf-8")

    assert store.all("feedback") == []
    assert store.corruptions[0]["collection"] == "feedback"
    assert "expected a JSON list" in store.corruptions[0]["error"]
    assert Path(store.corruptions[0]["quarantined"]).exists()


def test_the_second_failure_does_not_overwrite_the_first(tmp_path, monkeypatch):
    """Incidents accumulate: a report of one loss must not be replaced by
    the next, and two failures in the same second must not share a file."""
    monkeypatch.setattr("backend.config.DATA_DIR", tmp_path)
    store = Storage()
    (tmp_path / "runs.json").write_text("{broken", encoding="utf-8")
    store.all("runs")
    (tmp_path / "runs.json").write_text("{broken again", encoding="utf-8")
    store.all("runs")

    assert [i["collection"] for i in store.corruptions] == ["runs", "runs"]
    quarantined = [i for i in store.corruptions if i["quarantined"]]
    names = {Path(i["quarantined"]).name for i in quarantined}
    assert len(names) == len(quarantined), "two incidents, one preserved file"


def test_the_incident_survives_a_rebind_and_health_reports_it():
    """What the operator sees at 2am: /api/health says storage is degraded
    and names the file, instead of reporting an empty system as healthy."""
    from backend import health, store as store_module

    data_dir = store_module.storage._paths["runs"].parent
    data_dir.joinpath("runs.json").write_text('[{"id": "gone"', encoding="utf-8")
    store_module.storage.all("runs")           # triggers the quarantine

    report = health.health_report()
    storage_check = report["checks"]["storage"]
    assert storage_check["status"] == "degraded"
    assert storage_check["unreadable_files"][0]["collection"] == "runs"
    assert storage_check["unreadable_files"][0]["preserved_at"]
    assert report["status"] == "degraded"
    assert "storage" in report["degraded"]
    # The record counts still describe what is genuinely readable.
    assert storage_check["records"]["runs"] == 0


def test_an_unreadable_storage_error_is_not_downgraded_to_degraded(tmp_path,
                                                                   monkeypatch):
    """Two bad news at once: the worse one wins. Unwritable storage stays
    `error` even when an unreadable file was also found."""
    from backend import health, store as store_module

    store_module.storage.corruptions = [{
        "collection": "runs", "error": "truncated",
        "quarantined": None, "at": "2025-10-22T00:00:00+00:00"}]

    real_write_text = Path.write_text

    def refusing_write_text(self, *args, **kwargs):
        if self.name == ".health_probe":
            raise OSError("read-only filesystem")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", refusing_write_text)
    report = health.health_report()
    assert report["checks"]["storage"]["status"] == "error"
    assert report["status"] == "error"
