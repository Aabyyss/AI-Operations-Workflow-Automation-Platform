"""Tiny JSON-file persistence layer.

The point is a portfolio-grade architecture, not a DB admin exam: one
swappable Storage class behind the API. Swap this file for SQLAlchemy +
Postgres later without touching any agent or route.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from . import config

_lock = threading.Lock()

# The canonical managed collections, in one place so every backend
# (JSON files, Postgres) exposes the same names to health, backup and docs.
COLLECTION_NAMES = ("analyses", "tickets", "runs", "reviews", "usage",
                    "audit", "outbox", "workflows", "feedback",
                    "deliveries", "cycle_times")


class Storage:
    def __init__(self) -> None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._paths = {name: config.DATA_DIR / f"{name}.json"
                       for name in COLLECTION_NAMES}

    def _load(self, name: str) -> list[dict[str, Any]]:
        path = self._paths[name]
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, name: str, items: list[dict[str, Any]]) -> None:
        self._paths[name].write_text(json.dumps(items, indent=1), encoding="utf-8")

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
