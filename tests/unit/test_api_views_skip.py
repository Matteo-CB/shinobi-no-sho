"""Phase 9 : tests routes /play/{id}/skip-time + status views."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    from shinobi.config import settings

    monkeypatch.setattr(settings, "saves_path", str(tmp_path))
    monkeypatch.setattr(
        type(settings), "saves_dir", property(lambda self: tmp_path),
    )
    from shinobi.api import app
    return TestClient(app)


@pytest.fixture()
def save_id(client: TestClient) -> str:
    r = client.post(
        "/saves",
        json={"mode": "random", "name": "ViewsTest", "starting_year": 5},
    )
    return r.json()["save_id"]


# === Skip-time ============================================================


def test_skip_30_days_advances_world(client: TestClient, save_id: str) -> None:
    """Skip 30 jours avance la date du world."""
    r = client.post(
        f"/play/{save_id}/skip-time",
        json={"days": 30},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["days_skipped"] == 30
    # current_date initial 01-01 + 30j = 02-01
    assert body["new_date"] == "02-01"


def test_skip_12_months_advances_year(client: TestClient, save_id: str) -> None:
    """12 mois = 360 jours = +1 an."""
    r = client.post(
        f"/play/{save_id}/skip-time",
        json={"months": 12},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["new_year"] == 6  # starting_year=5 + 1


def test_skip_zero_returns_422(client: TestClient, save_id: str) -> None:
    """Pas de skip = 422."""
    r = client.post(
        f"/play/{save_id}/skip-time",
        json={"days": 0, "weeks": 0, "months": 0},
    )
    assert r.status_code == 422


def test_skip_unknown_save_404(client: TestClient) -> None:
    r = client.post("/play/unknown_xyz/skip-time", json={"days": 1})
    assert r.status_code == 404


# === Status views =========================================================


def test_biography_empty_initially(client: TestClient, save_id: str) -> None:
    """Biographie vide au depart."""
    r = client.get(f"/play/{save_id}/biography")
    assert r.status_code == 200
    assert r.json() == []


def test_rumors_empty_initially(client: TestClient, save_id: str) -> None:
    """Rumors vides au depart."""
    r = client.get(f"/play/{save_id}/rumors")
    assert r.status_code == 200
    assert r.json() == []


def test_breadcrumbs_empty_initially(client: TestClient, save_id: str) -> None:
    """Breadcrumbs vides au depart."""
    r = client.get(f"/play/{save_id}/breadcrumbs")
    assert r.status_code == 200
    assert r.json() == []


def test_reputation_empty_initially(client: TestClient, save_id: str) -> None:
    """Reputation vide initialement."""
    r = client.get(f"/play/{save_id}/reputation")
    assert r.status_code == 200
    body = r.json()
    assert body["save_id"] == save_id
    assert body["bingo_book_entry"] is False
    assert body["reputation"] == []


def test_knowledge_initial_state(client: TestClient, save_id: str) -> None:
    """Knowledge initial : tout vide."""
    r = client.get(f"/play/{save_id}/knowledge")
    assert r.status_code == 200
    body = r.json()
    assert body["known_events"] == {}
    assert body["known_techniques_existence"] == []


def test_views_unknown_save_404(client: TestClient) -> None:
    """Save inconnue partout -> 404."""
    for path in ("biography", "rumors", "breadcrumbs", "reputation", "knowledge"):
        r = client.get(f"/play/unknown_xyz/{path}")
        assert r.status_code == 404, path
