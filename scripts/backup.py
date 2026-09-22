"""Backup and restore for the JSON store.

Every operating system the platform will run on needs an answer to "we
deleted data/ — now what?". This creates timestamped, compressed
snapshots of the data directory and prunes old ones by retention.

    python -m scripts.backup                     # snapshot now, prune old
    python -m scripts.backup --list              # show snapshots
    python -m scripts.backup --retention 30      # keep 30 snapshots

Snapshots land in AIOPS_BACKUP_DIR (default: data_backups/) as
backup-YYYYmmdd-HHMMSS.tar.gz. Restore = extract over data/ (documented,
not automated — restoring over live data deserves a human).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make `python -m scripts.backup` work from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402

STAMP_FMT = "%Y%m%d-%H%M%S"
STAMP_RE = re.compile(r"backup-(\d{8}-\d{6})\.tar\.gz$")


def _backup_dir() -> Path:
    raw = os.getenv("AIOPS_BACKUP_DIR", "")
    return Path(raw) if raw else config.PROJECT_ROOT / "data_backups"


def create_snapshot(source: Path | None = None, dest_dir: Path | None = None) -> Path:
    """Tar-gz the data directory into a timestamped snapshot. Returns its path."""
    src = source if source is not None else config.DATA_DIR
    dest = dest_dir if dest_dir is not None else _backup_dir()
    if not src.exists():
        raise FileNotFoundError(f"data directory not found: {src}")
    dest.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime(STAMP_FMT)
    out = dest / f"backup-{stamp}.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        for item in sorted(src.iterdir()):
            if item.is_file():
                tar.add(item, arcname=item.name)
    return out


def snapshot_stamp(path: Path) -> datetime | None:
    """Parse a snapshot filename back to its UTC timestamp."""
    m = STAMP_RE.search(path.name)
    if not m:
        return None
    return datetime.strptime(m.group(1), STAMP_FMT).replace(tzinfo=timezone.utc)


def prune_snapshots(dest_dir: Path, keep: int) -> list[Path]:
    """Delete the oldest snapshots beyond `keep`. Returns the removed paths."""
    snaps = sorted((p for p in dest_dir.glob("backup-*.tar.gz") if snapshot_stamp(p)),
                   key=snapshot_stamp, reverse=True)
    removed = snaps[keep:]
    for p in removed:
        p.unlink()
    return removed


def list_snapshots(dest_dir: Path | None = None) -> list[tuple[Path, int]]:
    """Snapshot paths with their sizes in bytes, oldest first."""
    dest = dest_dir if dest_dir is not None else _backup_dir()
    if not dest.exists():
        return []
    snaps = [p for p in dest.glob("backup-*.tar.gz") if snapshot_stamp(p)]
    return sorted(((p, p.stat().st_size) for p in snaps), key=lambda t: snapshot_stamp(t[0]))


def main() -> int:
    ap = argparse.ArgumentParser(description="Snapshot and prune the JSON store.")
    ap.add_argument("--retention", type=int, default=14,
                    help="snapshots to keep (default 14)")
    ap.add_argument("--list", action="store_true", help="list snapshots and exit")
    args = ap.parse_args()

    if args.list:
        rows = list_snapshots()
        if not rows:
            print("no snapshots yet")
            return 0
        for p, size in rows:
            print(f"{p.name}  {size / 1024:8.1f} KB")
        return 0

    out = create_snapshot()
    print(f"snapshot: {out}")
    removed = prune_snapshots(out.parent, max(1, args.retention))
    for p in removed:
        print(f"pruned:   {p.name}")
    print(f"retention: keeping {args.retention}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
