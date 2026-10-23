"""Backup and restore for the JSON store.

Every operating system the platform will run on needs an answer to "we
deleted data/ — now what?". This creates timestamped, compressed
snapshots of the data directory and prunes old ones by retention.

    python -m scripts.backup                     # snapshot now, prune old
    python -m scripts.backup --list              # show snapshots
    python -m scripts.backup --retention 30      # keep 30 snapshots
    python -m scripts.backup --verify SNAPSHOT   # can this actually be restored?
    python -m scripts.backup --snapshot SNAPSHOT --restore-to DIR

Snapshots land in AIOPS_BACKUP_DIR (default: data_backups/) as
backup-YYYYmmdd-HHMMSS.tar.gz. Restore = extract over data/ (documented,
not automated — restoring over live data deserves a human). --verify is the
check that makes the extract safe to do: an archive nobody opened before the
day they needed it is a hope, not a backup.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

# Make `python -m scripts.backup` work from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.console import use_utf8  # noqa: E402
from backend import config  # noqa: E402

STAMP_FMT = "%Y%m%d-%H%M%S"
STAMP_RE = re.compile(r"backup-(\d{8}-\d{6})\.tar\.gz$")


def _backup_dir() -> Path:
    raw = os.getenv("AIOPS_BACKUP_DIR", "")
    return Path(raw) if raw else config.PROJECT_ROOT / "data_backups"


def create_snapshot(source: Path | None = None, dest_dir: Path | None = None) -> Path:
    """Tar-gz the data directory into a timestamped snapshot. Returns its path.

    Recursive on purpose. A snapshot that only walks the top level silently
    drops whatever lives in a subdirectory — which is the kind of loss you
    discover at restore time, when it is no longer a backup.
    """
    src = source if source is not None else config.DATA_DIR
    dest = dest_dir if dest_dir is not None else _backup_dir()
    if not src.exists():
        raise FileNotFoundError(f"data directory not found: {src}")
    dest.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime(STAMP_FMT)
    out = dest / f"backup-{stamp}.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        for item in sorted(src.rglob("*")):
            if item.is_file():
                tar.add(item, arcname=item.relative_to(src).as_posix())
    return out


# The two halves of the signing identity. Restoring one without the other is
# worse than restoring neither: `receipts.signing_seed` refuses to sign when
# they disagree, so the deployment boots into a state it cannot explain.
EVIDENCE_PAIR = ("receipt_signing_key.json", "receipt_keys.json")


def verify_snapshot(path: Path) -> dict:
    """Can this file actually be restored? A backup nobody opened is a hope.

    Checks, in the order they fail: the archive is readable, it holds files at
    all, the two halves of the signing identity are both present or both
    absent, and any JSON it carries parses. Never raises on a bad file — the
    answer is the report, which is what a cron job and a human both need.
    """
    report: dict = {"path": str(path), "ok": False, "files": 0, "bytes": 0,
                    "problems": [], "warnings": [], "collections": {}}
    # A gzip stream is read lazily, so a truncated archive frequently fails
    # when it is closed rather than when it is opened — the close is inside the
    # same guard on purpose, or the corruption escapes as an exception.
    tar = None
    try:
        tar = tarfile.open(path, "r:gz")
        _inspect(tar, report)
    except (OSError, tarfile.TarError, EOFError) as exc:
        report["problems"].append(f"unreadable archive: {exc}")
    finally:
        if tar is not None:
            try:
                tar.close()
            except (OSError, tarfile.TarError, EOFError) as exc:
                report["problems"].append(f"unreadable archive: {exc}")
    if report["files"] and not report["problems"]:
        report["ok"] = True
    if "receipts" not in report["collections"]:
        report["warnings"].append("no receipts in this snapshot — nothing signed "
                                  "to restore (fine for a fresh deployment)")
    return report


def _inspect(tar, report: dict) -> None:
    """The readable half of verify_snapshot, so a corrupt stream is one report
    instead of an exception escaping into whatever called the backup."""
    members = [m for m in tar.getmembers() if m.isfile()]
    names = {m.name for m in members}
    report["files"] = len(members)
    report["bytes"] = sum(m.size for m in members)
    if not members:
        report["problems"].append("archive contains no files")

    present = [name for name in EVIDENCE_PAIR if name in names]
    if present and len(present) != len(EVIDENCE_PAIR):
        missing = [n for n in EVIDENCE_PAIR if n not in names]
        report["problems"].append(
            "signed-receipt evidence is incomplete: has "
            f"{present[0]}, missing {', '.join(missing)} — restoring this "
            "would leave the deployment unable to sign or unable to prove "
            "its past")

    for member in sorted(members, key=lambda m: m.name):
        if not member.name.endswith(".json"):
            continue
        handle = tar.extractfile(member)
        if handle is None:
            continue
        try:
            doc = json.loads(handle.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            report["problems"].append(f"{member.name} is not valid JSON: {exc}")
            continue
        if isinstance(doc, list):
            report["collections"][member.name[:-5]] = len(doc)
        elif isinstance(doc, dict):
            # The store keeps {collection: [...]}; count what is in it so
            # "restored, but empty" is visible before the restore.
            inner = [v for v in doc.values() if isinstance(v, list)]
            if len(inner) == 1:
                report["collections"][member.name[:-5]] = len(inner[0])

def restore_snapshot(path: Path, target: Path) -> list[str]:
    """Extract a snapshot into `target`, refusing anything that is not empty.

    Restoring over live data is a decision, not a command: the target must be
    a directory that does not exist or is empty, so the operator moves the
    files into place themselves. Member paths are resolved under the target, so
    an archive containing `../` cannot write outside it.
    """
    target = Path(target)
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"refusing to restore into a non-empty directory: {target}")
    target.mkdir(parents=True, exist_ok=True)
    root = target.resolve()
    written: list[str] = []
    with tarfile.open(path, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            destination = (root / member.name).resolve()
            if root not in destination.parents and destination != root:
                raise ValueError(f"refusing to extract outside the target: {member.name}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            handle = tar.extractfile(member)
            if handle is not None:
                destination.write_bytes(handle.read())
                written.append(member.name)
    return written


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
    use_utf8()
    ap = argparse.ArgumentParser(description="Snapshot and prune the JSON store.")
    ap.add_argument("--retention", type=int, default=14,
                    help="snapshots to keep (default 14)")
    ap.add_argument("--list", action="store_true", help="list snapshots and exit")
    ap.add_argument("--verify", type=Path, metavar="SNAPSHOT",
                    help="check that this snapshot can be restored; exit 1 if not")
    ap.add_argument("--restore-to", type=Path, metavar="DIR",
                    help="extract into an empty/directory that does not exist "
                         "(never over live data)")
    ap.add_argument("--snapshot", type=Path, metavar="SNAPSHOT",
                    help="the snapshot --restore-to extracts")
    args = ap.parse_args()

    if args.restore_to is not None:
        if args.snapshot is None:
            print("--restore-to needs --snapshot PATH", file=sys.stderr)
            return 2
        verdict = verify_snapshot(args.snapshot)
        if not verdict["ok"]:
            print(f"refusing to restore {args.snapshot}: "
                  f"{'; '.join(verdict['problems'])}", file=sys.stderr)
            return 1
        try:
            written = restore_snapshot(args.snapshot, args.restore_to)
        except (OSError, ValueError, tarfile.TarError) as exc:
            print(f"cannot restore: {exc}", file=sys.stderr)
            return 2
        for name in written:
            print(f"restored: {name}")
        print(f"\n{len(written)} file(s) in {args.restore_to}. Moving them into "
              f"data/ is a human decision — stop the API first.")
        return 0

    if args.verify is not None:
        report = verify_snapshot(args.verify)
        counts = ", ".join(f"{k} {v}" for k, v in sorted(report["collections"].items()))
        state = "ok" if report["ok"] else "unusable"
        print(f"{state}: {report['path']} - {report['files']} files, "
              f"{report['bytes'] / 1024:.1f} KB" + (f" ({counts})" if counts else ""))
        for problem in report["problems"]:
            print(f"  - {problem}", file=sys.stderr)
        for warning in report["warnings"]:
            print(f"  ! {warning}")
        return 0 if report["ok"] else 1

    if args.snapshot is not None:
        print("--snapshot is only used with --restore-to", file=sys.stderr)
        return 2

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
