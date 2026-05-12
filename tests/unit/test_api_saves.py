"""Phase 9 : tests routes /saves (CRUD complet)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    """TestClient avec saves_dir isole."""
    from shinobi.config import settings

    monkeypatch.setattr(settings, "saves_path", str(tmp_path))
    monkeypatch.setattr(
        type(settings), "saves_dir", property(lambda self: tmp_path),
    )
    from shinobi.api import app
    return TestClient(app)


def test_list_saves_empty(client: TestClient) -> None:
    """Liste vide quand aucune save."""
    r = client.get("/saves")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["saves"] == []


def test_create_save_random_minimal(client: TestClient) -> None:
    """POST /saves mode='random' avec payload minimal."""
    r = client.post(
        "/saves",
        json={"mode": "random", "name": "Test Hero"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["save_id"].startswith("test_hero_")
    assert body["character_name"] == "Test Hero"
    assert body["current_year"] == 12  # defaut


def test_create_save_random_custom_year_age(client: TestClient) -> None:
    """POST /saves mode='random' avec year/age explicites."""
    r = client.post(
        "/saves",
        json={
            "mode": "random",
            "name": "Sora",
            "starting_year": 5,
            "starting_age": 8,
            "village": "sunagakure",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["current_year"] == 5


def test_create_save_invalid_mode_returns_422(client: TestClient) -> None:
    """Mode different de 'random'/'canon' rejete."""
    r = client.post("/saves", json={"mode": "xxx"})
    assert r.status_code == 422


def test_get_save_returns_meta(client: TestClient) -> None:
    """GET /saves/{id} retourne le SaveMeta."""
    create = client.post("/saves", json={"mode": "random", "name": "Meta Test"})
    sid = create.json()["save_id"]
    r = client.get(f"/saves/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert body["save_id"] == sid
    assert body["character_name"] == "Meta Test"
    assert body["total_turns"] == 0


def test_get_save_unknown_returns_404(client: TestClient) -> None:
    """ID inexistant -> 404."""
    r = client.get("/saves/inexistant_xyz")
    assert r.status_code == 404


def test_list_saves_after_create(client: TestClient) -> None:
    """List voit la save apres creation."""
    client.post("/saves", json={"mode": "random", "name": "ListTest"})
    r = client.get("/saves")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["saves"][0]["character_name"] == "ListTest"


def test_delete_save(client: TestClient) -> None:
    """DELETE supprime la save."""
    create = client.post("/saves", json={"mode": "random", "name": "DelTest"})
    sid = create.json()["save_id"]
    r = client.delete(f"/saves/{sid}")
    assert r.status_code == 204
    r2 = client.get(f"/saves/{sid}")
    assert r2.status_code == 404


def test_delete_unknown_returns_404(client: TestClient) -> None:
    """DELETE id inconnu -> 404."""
    r = client.delete("/saves/nope_xyz")
    assert r.status_code == 404


def test_duplicate_save(client: TestClient) -> None:
    """POST /saves/{id}/duplicate cree une copie."""
    create = client.post("/saves", json={"mode": "random", "name": "Original"})
    sid = create.json()["save_id"]
    r = client.post(
        f"/saves/{sid}/duplicate",
        json={"label": "branche_alternative"},
    )
    assert r.status_code == 200
    new_id = r.json()["save_id"]
    assert new_id != sid
    assert new_id.startswith("branche_alternative_")
    # Les deux saves coexistent
    listing = client.get("/saves").json()
    assert listing["count"] == 2


def test_export_then_import_save_roundtrip(client: TestClient, tmp_path: Path) -> None:
    """Export -> bytes -> Import -> retrouve la save dans un dir vierge."""
    # Cree, exporte
    create = client.post("/saves", json={"mode": "random", "name": "RoundTrip"})
    sid = create.json()["save_id"]
    exp = client.get(f"/saves/{sid}/export")
    assert exp.status_code == 200
    archive_bytes = exp.content
    assert archive_bytes[:2] == b"\x1f\x8b"  # gzip magic

    # Supprime puis re-import
    client.delete(f"/saves/{sid}")
    assert client.get(f"/saves/{sid}").status_code == 404

    imp = client.post(
        "/saves/import",
        content=archive_bytes,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert imp.status_code == 200, imp.text
    body = imp.json()
    assert body["save_id"] == sid


def test_import_empty_body_rejected(client: TestClient) -> None:
    """Body vide -> 422."""
    r = client.post(
        "/saves/import",
        content=b"",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 422
