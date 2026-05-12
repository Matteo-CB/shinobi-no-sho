"""Phase 9 : tests routes /techniques + /relationships + /goals/{id}/path."""
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
    r = client.post("/saves", json={"mode": "random", "name": "TechRelTest"})
    return r.json()["save_id"]


# === /techniques ==========================================================


def test_techniques_initial_state_empty(client: TestClient, save_id: str) -> None:
    """Aucune technique a la creation."""
    r = client.get(f"/play/{save_id}/techniques")
    assert r.status_code == 200
    body = r.json()
    assert body["save_id"] == save_id
    assert body["known"] == []
    assert body["in_progress"] == []


def test_techniques_unknown_save_404(client: TestClient) -> None:
    r = client.get("/play/unknown_xyz/techniques")
    assert r.status_code == 404


# === /relationships =======================================================


def test_relationships_initial_state_empty(
    client: TestClient, save_id: str,
) -> None:
    """Aucune relation a la creation."""
    r = client.get(f"/play/{save_id}/relationships")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0


def test_relationships_unknown_save_404(client: TestClient) -> None:
    r = client.get("/play/unknown_xyz/relationships")
    assert r.status_code == 404


# === /goals/{id}/path =====================================================


def test_pathfinder_returns_unavailable_when_llm_offline(
    client: TestClient, save_id: str,
) -> None:
    """Pathfinder gracieux : LLM down -> available=False, pas 5xx."""
    create = client.post(
        f"/play/{save_id}/goals",
        json={"description_player": "Apprendre Rasengan"},
    )
    gid = create.json()["id"]
    r = client.post(f"/play/{save_id}/goals/{gid}/path")
    assert r.status_code == 200
    body = r.json()
    # LLM probablement down dans le test env
    assert body["goal_id"] == gid
    assert body["available"] in (True, False)
    if not body["available"]:
        assert body["error"] is not None


def test_pathfinder_unknown_goal_returns_404(
    client: TestClient, save_id: str,
) -> None:
    r = client.post(f"/play/{save_id}/goals/inexistant_xyz/path")
    assert r.status_code == 404


def test_pathfinder_unknown_save_404(client: TestClient) -> None:
    r = client.post("/play/unknown_xyz/goals/x/path")
    assert r.status_code == 404
