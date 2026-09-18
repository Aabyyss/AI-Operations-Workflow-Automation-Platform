"""Pytest fixtures: isolated data dir per test, shared API client."""
import sys
from pathlib import Path

import pytest

# Ensure backend imports work from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Point the storage singleton at a fresh temp dir for every test."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr("backend.config.DATA_DIR", data_dir)
    from backend.store import storage as _storage
    _storage.__init__()  # rebind file paths to the isolated dir
    yield


@pytest.fixture()
def client():
    """FastAPI TestClient (storage already isolated by autouse fixture)."""
    from fastapi.testclient import TestClient
    from backend.main import app
    with TestClient(app) as c:
        yield c
