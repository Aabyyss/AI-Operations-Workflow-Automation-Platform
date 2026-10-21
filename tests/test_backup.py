"""Tests for backup snapshots and retention pruning."""
import sys

import pytest
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import backup  # noqa: E402


def _seed(src: Path) -> None:
    src.mkdir(parents=True, exist_ok=True)  # conftest pre-creates tmp_path/data
    (src / "approvals.json").write_text("[]", encoding="utf-8")
    (src / "runs.json").write_text('{"runs": []}', encoding="utf-8")


def test_snapshot_contains_all_store_files(tmp_path):
    src = tmp_path / "data"
    dest = tmp_path / "backups"
    _seed(src)

    snap = backup.create_snapshot(src, dest)

    assert snap.exists() and snap.parent == dest
    assert backup.snapshot_stamp(snap) is not None
    with tarfile.open(snap) as tar:
        names = set(tar.getnames())
    assert names == {"approvals.json", "runs.json"}


def test_prune_keeps_newest_n(tmp_path):
    dest = tmp_path / "backups"
    dest.mkdir()
    # Five snapshots, one minute apart.
    stamps = ["20260101-00000%d" % i for i in range(5)]
    for s in stamps:
        (dest / f"backup-{s}.tar.gz").write_bytes(b"x")

    removed = backup.prune_snapshots(dest, keep=3)

    assert sorted(p.name for p in removed) == [
        "backup-20260101-000000.tar.gz",
        "backup-20260101-000001.tar.gz",
    ]
    remaining = sorted(p.name for p in dest.glob("backup-*.tar.gz"))
    assert remaining == [
        "backup-20260101-000002.tar.gz",
        "backup-20260101-000003.tar.gz",
        "backup-20260101-000004.tar.gz",
    ]


def test_list_snapshots_oldest_first(tmp_path):
    dest = tmp_path / "backups"
    dest.mkdir()
    (dest / "backup-20260102-000000.tar.gz").write_bytes(b"x" * 10)
    (dest / "backup-20260101-000000.tar.gz").write_bytes(b"x" * 20)

    rows = backup.list_snapshots(dest)

    assert [p.name for p, _ in rows] == [
        "backup-20260101-000000.tar.gz",
        "backup-20260102-000000.tar.gz",
    ]
    assert rows[0][1] == 20


def test_non_snapshot_files_are_ignored(tmp_path):
    dest = tmp_path / "backups"
    dest.mkdir()
    (dest / "backup-20260101-000000.tar.gz").write_bytes(b"x")
    (dest / "notes.txt").write_text("keep me", encoding="utf-8")

    removed = backup.prune_snapshots(dest, keep=0)

    assert len(removed) == 1
    assert (dest / "notes.txt").exists()


# --------------------------------------------------------------- completeness
def test_a_snapshot_includes_nested_data(tmp_path):
    """Top-level-only walking looks like a backup and restores as a loss."""
    src = tmp_path / "data"
    _seed(src)
    (src / "attach").mkdir()
    (src / "attach" / "notes.txt").write_text("nested", encoding="utf-8")

    snap = backup.create_snapshot(src, tmp_path / "backups")

    with tarfile.open(snap) as tar:
        assert "attach/notes.txt" in tar.getnames()


# --------------------------------------------------------------------- verify
def test_verify_accepts_a_snapshot_it_could_restore(tmp_path):
    src = tmp_path / "data"
    _seed(src)
    snap = backup.create_snapshot(src, tmp_path / "backups")

    report = backup.verify_snapshot(snap)

    assert report["ok"] is True
    assert report["problems"] == []
    assert report["collections"]["approvals"] == 0      # [] parses as a list
    assert report["collections"]["runs"] == 0           # {"runs": []}


def test_verify_refuses_a_truncated_archive(tmp_path):
    snap = tmp_path / "backup-20260101-000000.tar.gz"
    snap.write_bytes(b"\x1f\x8b\x08\x00garbage")

    report = backup.verify_snapshot(snap)

    assert report["ok"] is False
    assert any("unreadable archive" in p for p in report["problems"])


def test_verify_refuses_half_the_signing_identity(tmp_path):
    """Seed without history (or the reverse) restores into a deployment that
    refuses to sign and cannot explain why — so the snapshot is unusable, not
    merely incomplete."""
    src = tmp_path / "data"
    _seed(src)
    (src / "receipt_signing_key.json").write_text('{"seed": "aa"}', encoding="utf-8")
    snap = backup.create_snapshot(src, tmp_path / "backups")

    report = backup.verify_snapshot(snap)

    assert report["ok"] is False
    assert any("evidence is incomplete" in p and "receipt_keys.json" in p
               for p in report["problems"])


def test_verify_names_the_file_that_is_not_json(tmp_path):
    src = tmp_path / "data"
    _seed(src)
    (src / "runs.json").write_text("{not json", encoding="utf-8")
    snap = backup.create_snapshot(src, tmp_path / "backups")

    report = backup.verify_snapshot(snap)

    assert report["ok"] is False
    assert any("runs.json is not valid JSON" in p for p in report["problems"])


# -------------------------------------------------------------------- restore
def test_restore_refuses_to_write_over_live_data(tmp_path):
    src = tmp_path / "data"
    _seed(src)
    snap = backup.create_snapshot(src, tmp_path / "backups")
    live = tmp_path / "live"
    live.mkdir()
    (live / "runs.json").write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="non-empty"):
        backup.restore_snapshot(snap, live)


def test_restore_writes_the_files_into_an_empty_target(tmp_path):
    src = tmp_path / "data"
    _seed(src)
    snap = backup.create_snapshot(src, tmp_path / "backups")

    written = backup.restore_snapshot(snap, tmp_path / "restored")

    assert sorted(written) == ["approvals.json", "runs.json"]
    assert (tmp_path / "restored" / "runs.json").read_text(encoding="utf-8") == '{"runs": []}'


def test_restore_refuses_an_archive_that_escapes_the_target(tmp_path):
    """A tarball is input from elsewhere; `../` in it must not become a write
    outside the directory the operator named."""
    snap = tmp_path / "backup-20260101-000000.tar.gz"
    payload = tmp_path / "evil.json"
    payload.write_text("{}", encoding="utf-8")
    with tarfile.open(snap, "w:gz") as tar:
        tar.add(payload, arcname="../escaped.json")

    with pytest.raises(ValueError, match="outside the target"):
        backup.restore_snapshot(snap, tmp_path / "restored")
    assert not (tmp_path / "escaped.json").exists()


# ------------------------------------------------------------------------ CLI
def test_the_verify_flag_exits_nonzero_on_an_unusable_snapshot(tmp_path):
    snap = tmp_path / "backup-20260101-000000.tar.gz"
    snap.write_bytes(b"not a tarball")
    argv = sys.argv
    try:
        sys.argv = ["backup", "--verify", str(snap)]
        assert backup.main() == 1
    finally:
        sys.argv = argv


def test_restore_to_without_a_snapshot_is_a_usage_error(tmp_path):
    argv = sys.argv
    try:
        sys.argv = ["backup", "--restore-to", str(tmp_path / "out")]
        assert backup.main() == 2
    finally:
        sys.argv = argv
