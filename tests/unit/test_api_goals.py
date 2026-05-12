"""Phase 9 : tests routes /goals (declare/list/abandon/complete)."""
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
    r = client.post("/saves", json={"mode": "random", "name": "GoalsTest"})
    return r.json()["save_id"]


def test_list_goals_empty(client: TestClient, save_id: str) -> None:
    """Aucun goal initialement."""
    r = client.get(f"/play/{save_id}/goals")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["goals"] == []


def test_declare_goal_creates_new(client: TestClient, save_id: str) -> None:
    """POST /goals declare un nouveau goal."""
    r = client.post(
        f"/play/{save_id}/goals",
        json={
            "description_player": "Devenir Hokage",
            "interpretation_canonical": "atteindre rank=hokage",
            "target_type": "achieve_rank",
            "target_id": "hokage",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["description_player"] == "Devenir Hokage"
    assert body["status"] == "declared"
    assert body["target_type"] == "achieve_rank"


def test_declare_then_list(client: TestClient, save_id: str) -> None:
    """Liste contient le goal declare."""
    client.post(
        f"/play/{save_id}/goals",
        json={"description_player": "Apprendre Rasengan"},
    )
    r = client.get(f"/play/{save_id}/goals")
    body = r.json()
    assert body["count"] == 1
    assert body["goals"][0]["description_player"] == "Apprendre Rasengan"


def test_abandon_goal_changes_status(client: TestClient, save_id: str) -> None:
    """POST /goals/{id}/abandon -> status=abandoned."""
    create = client.post(
        f"/play/{save_id}/goals",
        json={"description_player": "Test goal"},
    )
    gid = create.json()["id"]
    r = client.post(f"/play/{save_id}/goals/{gid}/abandon")
    assert r.status_code == 200
    assert r.json()["status"] == "abandoned"


def test_complete_goal_changes_status(client: TestClient, save_id: str) -> None:
    """POST /goals/{id}/complete -> status=completed."""
    create = client.post(
        f"/play/{save_id}/goals",
        json={"description_player": "Mission accomplie"},
    )
    gid = create.json()["id"]
    r = client.post(f"/play/{save_id}/goals/{gid}/complete")
    assert r.status_code == 200
    assert r.json()["status"] == "completed"


def test_abandon_unknown_goal_returns_404(client: TestClient, save_id: str) -> None:
    """Goal id inconnu -> 404."""
    r = client.post(f"/play/{save_id}/goals/inexistant/abandon")
    assert r.status_code == 404


def test_goals_unknown_save_returns_404(client: TestClient) -> None:
    """Save inconnue -> 404."""
    r = client.get("/play/unknown_xyz/goals")
    assert r.status_code == 404
