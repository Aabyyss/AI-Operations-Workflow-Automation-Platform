"""One-shot migration: JSON store -> Postgres (the `documents` table).

Usage:
    pip install -r requirements-postgres.txt
    python -m scripts.migrate_to_postgres [--dsn <url>] [--replace] [--pgvector] [--verify]

- Reads every collection through the existing Storage interface, so the
  payload shape is guaranteed to match what the API writes.
- Creates the schema first (documents table; add --pgvector to also
  provision the extension + embeddings table for live retrieval).
- Default mode appends; pass --replace to wipe target collections first
  (making the migration re-runnable without duplicates).
- --verify compares source/destination record counts and exits non-zero
  on any mismatch, so you can run it after the switch as a drift check.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402
from backend.store import COLLECTION_NAMES, storage as json_storage  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", default=config.DATABASE_URL,
                    help="Postgres DSN (defaults to AIOPS_DATABASE_URL)")
    ap.add_argument("--replace", action="store_true",
                    help="wipe each target collection before copying")
    ap.add_argument("--pgvector", action="store_true",
                    help="also provision the pgvector extension + embeddings table")
    ap.add_argument("--verify", action="store_true",
                    help="after copying, compare per-collection counts; exit 1 on mismatch")
    args = ap.parse_args()

    if not args.dsn:
        print("error: no DSN — pass --dsn or set AIOPS_DATABASE_URL", file=sys.stderr)
        return 2

    from backend.pgstore import PostgresStorage

    pg = PostgresStorage(args.dsn)
    pg.ensure_schema(with_pgvector=args.pgvector)
    print(f"schema ready ({'with' if args.pgvector else 'without'} pgvector artifacts)")

    total = 0
    for name in COLLECTION_NAMES:
        records = json_storage.all(name)
        if args.replace:
            pg.replace_all(name, records)
        else:
            for record in records:
                pg.append(name, record)
        total += len(records)
        print(f"  {name:<12} {len(records):>6} records {'replaced' if args.replace else 'appended'}")

    print(f"migrated {total} records across {len(COLLECTION_NAMES)} collections")

    if args.verify:
        mismatches = []
        for name in COLLECTION_NAMES:
            src, dst = len(json_storage.all(name)), len(pg.all(name))
            state = "ok" if src == dst else "MISMATCH"
            if src != dst:
                mismatches.append(name)
            print(f"  verify {name:<12} src={src:>6} dst={dst:>6} {state}")
        if mismatches:
            print(f"verification FAILED: {', '.join(mismatches)}", file=sys.stderr)
            return 1
        print("verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
