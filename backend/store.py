"""Tiny JSON-file persistence layer.

The point is a portfolio-grade architecture, not a DB admin exam: one
swappable Storage class behind the API. Swap this file for SQLAlchemy +
Postgres later without touching any agent or route.

Two properties this layer guarantees, because both were missing and both
fail silently in the worst way:

* **A write is atomic.** `write_text` truncates before it writes, so a
  Ctrl+C, an OOM kill or a CI teardown mid-save leaves a half-written file
  — and a half-written JSON file reads as "no records" forever after. Every
  save therefore writes a temp file and renames it over the destination:
  a reader sees the old file or the new one, never a torn one. This is
  process-crash safety (the realistic failure), not power-loss durability.
* **A corrupt file is loud.** Previously a parse failure returned `[]`,
  indistinguishable from an empty collection: every run, ticket and receipt
  vanished behind a 200 response and an empty dashboard. Now the bytes are
  preserved by renaming the file aside, the incident is recorded on the
  store, and `/api/health` reports the component as degraded and names the
  quarantine path. The system keeps running on the other collections — one
  bad file must not take the deployment down — but nobody is told that data
  loss is an empty report.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config

_lock = threading.Lock()

# The canonical managed collections, in one place so every backend
# (JSON files, Postgres) exposes the same names to health, backup and docs.
COLLECTION_NAMES = ("analyses", "tickets", "runs", "reviews", "usage",
                    "audit", "outbox", "workflows", "feedback",
                    "deliveries", "cycle_times", "receipts", "shadow")


class Storage:
    def __init__(self) -> None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._paths = {name: config.DATA_DIR / f"{name}.json"
                       for name in COLLECTION_NAMES}
        # Files that were found unreadable, with where their bytes went.
        # Reset by __init__, so the per-test rebinding in conftest starts
        # clean, and read by /api/health to report storage honestly.
        self.corruptions: list[dict[str, Any]] = []

    def _load(self, name: str) -> list[dict[str, Any]]:
        path = self._paths[name]
        if not path.exists():
            return []                      # a collection that was never written
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._quarantine(name, path, str(exc))
            return []
        except OSError as exc:
            # Unreadable (permissions, lock): report it, but the bytes stay
            # where they are — moving a file we cannot read may fail too.
            self.corruptions.append({
                "collection": name, "error": str(exc), "quarantined": None,
                "at": datetime.now(timezone.utc).isoformat(),
            })
            return []
        if not isinstance(doc, list):
            # `{"runs": [...]}` or a bare scalar: same dishonest answer as a
            # parse failure, so it takes the same path.
            self._quarantine(name, path,
                             f"expected a JSON list, found {type(doc).__name__}")
            return []
        return doc

    def _quarantine(self, name: str, path: Path, error: str) -> None:
        """Move an unreadable collection aside and record it.

        Renaming — never deleting: if something is wrong, the bytes are the
        only way to find out what. The timestamped name keeps every incident
        (a second failure does not overwrite the first), and because it no
        longer ends in `.json` no tool will mistake it for live data.
        """
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        quarantine: Path | None = path.with_name(f"{path.name}.corrupt-{stamp}")
        try:
            path.rename(quarantine)
        except OSError:
            quarantine = None             # leave it in place and say so
        self.corruptions.append({
            "collection": name,
            "error": error,
            "quarantined": str(quarantine) if quarantine else None,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        print(f"storage: {path.name} is unreadable ({error})"
              + (f" — preserved at {quarantine.name}" if quarantine else ""),
              flush=True)

    def _save(self, name: str, items: list[dict[str, Any]]) -> None:
        """Atomic: write a sibling temp file, then rename it over the target.

        The rename is the whole trick — on every supported OS it replaces the
        destination in one step, so the file is never observably half-written.
        """
        path = self._paths[name]
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(items, indent=1), encoding="utf-8")
            os.replace(tmp, path)
        except BaseException:
            # The destination is still the old, intact file — the only thing
            # to clean is the partial temp file. Bare except: a KeyboardInterrupt
            # mid-save must not leave litter either.
            tmp.unlink(missing_ok=True)
            raise

    def append(self, name: str, item: dict[str, Any]) -> None:
        with _lock:
            items = self._load(name)
            items.append(item)
            self._save(name, items)

    def all(self, name: str) -> list[dict[str, Any]]:
        with _lock:
            return self._load(name)

    def get(self, name: str, item_id: str) -> dict[str, Any] | None:
        for item in self.all(name):
            if item.get("id") == item_id:
                return item
        return None

    def update(self, name: str, item_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        with _lock:
            items = self._load(name)
            for i, item in enumerate(items):
                if item.get("id") == item_id:
                    item.update(patch)
                    self._save(name, items)
                    return item
        return None

    def collection_names(self) -> list[str]:
        """Public view of the managed collections (health, backup, docs)."""
        return sorted(self._paths)

    def replace_all(self, name: str, items: list[dict[str, Any]]) -> None:
        """Atomically swap a collection (used by retention pruning)."""
        with _lock:
            self._save(name, items)


def build_storage():
    """Pick the configured backend: JSON files (default) or Postgres.

    Postgres is a deliberate opt-in: AIOPS_STORAGE=postgres + a DSN. The
    driver check happens at boot with a readable error, but the connection
    itself is lazy — nothing dials the network until the first query.
    """
    if config.STORAGE_BACKEND == "postgres":
        if not config.DATABASE_URL:
            raise RuntimeError("AIOPS_STORAGE=postgres requires AIOPS_DATABASE_URL")
        try:
            import psycopg  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "AIOPS_STORAGE=postgres requires the psycopg driver: "
                "pip install -r requirements-postgres.txt") from exc
        from .pgstore import PostgresStorage
        return PostgresStorage(config.DATABASE_URL)
    return Storage()


storage = build_storage()


# Module-level convenience wrappers (pipeline calls store.append(...)).
def append(name: str, item: dict[str, Any]) -> None:
    storage.append(name, item)


def all(name: str) -> list[dict[str, Any]]:
    return storage.all(name)
