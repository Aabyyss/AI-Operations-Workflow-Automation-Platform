"""Tests for backup snapshots and retention pruning."""
import sys
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
