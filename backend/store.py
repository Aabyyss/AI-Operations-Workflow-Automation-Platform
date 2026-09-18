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


class Storage:
    def __init__(self) -> None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._paths = {
            "analyses": config.DATA_DIR / "analyses.json",
            "tickets": config.DATA_DIR / "tickets.json",
            "runs": config.DATA_DIR / "runs.json",
            "reviews": config.DATA_DIR / "reviews.json",
            "usage": config.DATA_DIR / "usage.json",
            "audit": config.DATA_DIR / "audit.json",
            "outbox": config.DATA_DIR / "outbox.json",
        }

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


storage = Storage()
