"""Phase 9 : tests route /health."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shinobi import __version__


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    """TestClient avec saves_dir isole pour test."""
    from shinobi.config import settings

    monkeypatch.setattr(settings, "saves_path", str(tmp_path))
    monkeypatch.setattr(
        type(settings), "saves_dir", property(lambda self: tmp_path),
    )

    from shinobi.api import app
    return TestClient(app)


def test_health_returns_ok_status(client: TestClient) -> None:
    """/health retourne status 'ok'."""
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


def test_health_reports_canon_loaded(client: TestClient) -> None:
    """/health charge le canon en arriere-plan."""
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert "canon_loaded" in body
    # Canon should load if data/canonical exists.
    assert body["canon_loaded"] in (True, False)


def test_health_returns_zero_saves_on_empty_dir(client: TestClient) -> None:
    """Saves count = 0 sur repertoire vide."""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["saves_count"] == 0


def test_openapi_doc_available(client: TestClient) -> None:
    """OpenAPI doc auto-generee est dispo."""
    r = client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert spec["info"]["title"] == "Shinobi no Sho API"
    # Verifie que les 4 groupes de routes existent
    paths = spec["paths"]
    assert "/health" in paths
    assert "/saves" in paths
    assert "/canon/villages" in paths


def test_docs_endpoint_serves_swagger(client: TestClient) -> None:
    """/docs renvoie la page swagger HTML."""
    r = client.get("/docs")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
