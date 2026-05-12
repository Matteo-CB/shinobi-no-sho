"""Phase 9 : tests /play/{id}/fast-forward + /play/{id}/tensions-llm.

Parite finale CLI /fast-forward + /tensions-llm.
"""
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
        json={
            "mode": "random", "name": "FFTest",
            "starting_year": 5, "starting_age": 10,
        },
    )
    return r.json()["save_id"]


# === /fast-forward ========================================================


def test_fast_forward_1_month(client: TestClient, save_id: str) -> None:
    """1 mois -> world avance de 30 jours, character age inchange (pas de
    nouvelle annee)."""
    r = client.post(
        f"/play/{save_id}/fast-forward", json={"months": 1},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["months_simulated"] == 1
    assert body["new_age"] == 10  # 1 mois ne change pas l'annee
    assert body["new_year"] == 5
    assert isinstance(body["fired_event_ids"], list)


def test_fast_forward_12_months_ages_character(
    client: TestClient, save_id: str,
) -> None:
    """12 mois -> +1 an pour le character + le world."""
    r = client.post(
        f"/play/{save_id}/fast-forward", json={"months": 12},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["new_year"] == 6  # starting=5 + 1
    assert body["new_age"] == 11  # starting age=10 + 1


def test_fast_forward_returns_digest_lists(
    client: TestClient, save_id: str,
) -> None:
    """Le digest contient toutes les listes attendues + flag llm_used."""
    r = client.post(
        f"/play/{save_id}/fast-forward", json={"months": 3},
    )
    body = r.json()
    for key in (
        "fired_event_ids",
        "cancelled_event_ids",
        "substitute_injected",
        "llm_used",
    ):
        assert key in body
    assert isinstance(body["llm_used"], bool)


def test_fast_forward_persists_state(client: TestClient, save_id: str) -> None:
    """Apres fast-forward, /status reflete le nouveau year/age."""
    client.post(f"/play/{save_id}/fast-forward", json={"months": 24})
    r = client.get(f"/play/{save_id}/status")
    body = r.json()
    assert body["current_year"] == 7  # +2 ans
    assert body["age_years"] == 12


def test_fast_forward_invalid_months_returns_422(
    client: TestClient, save_id: str,
) -> None:
    """months <= 0 -> 422."""
    r = client.post(
        f"/play/{save_id}/fast-forward", json={"months": 0},
    )
    assert r.status_code == 422


def test_fast_forward_unknown_save_404(client: TestClient) -> None:
    r = client.post("/play/unknown_xyz/fast-forward", json={"months": 1})
    assert r.status_code == 404


# === /tensions-llm ========================================================


def test_tensions_llm_returns_empty_when_no_kg(
    client: TestClient, save_id: str,
) -> None:
    """KG absent -> liste vide, pas de crash."""
    r = client.post(f"/play/{save_id}/tensions-llm")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["tensions"] == []


def test_tensions_llm_unknown_save_404(client: TestClient) -> None:
    r = client.post("/play/unknown_xyz/tensions-llm")
    assert r.status_code == 404
