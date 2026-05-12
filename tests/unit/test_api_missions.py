"""Phase 9 : tests routes /missions (available/accept/active/submit)."""
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
    r = client.post("/saves", json={"mode": "random", "name": "MissionsTest"})
    return r.json()["save_id"]


def test_list_available_returns_4_missions(client: TestClient, save_id: str) -> None:
    """4 missions generees pour le rang du joueur."""
    r = client.get(f"/play/{save_id}/missions/available")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 4
    for m in body["missions"]:
        assert m["rank"] in ("D", "C", "B", "A", "S")
        assert m["reward_ryos"] > 0


def test_list_available_deterministic(client: TestClient, save_id: str) -> None:
    """Meme save sans tour -> meme liste."""
    r1 = client.get(f"/play/{save_id}/missions/available")
    r2 = client.get(f"/play/{save_id}/missions/available")
    ids1 = [m["id"] for m in r1.json()["missions"]]
    ids2 = [m["id"] for m in r2.json()["missions"]]
    assert ids1 == ids2


def test_accept_mission(client: TestClient, save_id: str) -> None:
    """Accept stocke la mission dans active_missions."""
    avail = client.get(f"/play/{save_id}/missions/available").json()
    mid = avail["missions"][0]["id"]
    r = client.post(
        f"/play/{save_id}/missions/accept",
        json={"mission_id": mid},
    )
    assert r.status_code == 200
    actives = client.get(f"/play/{save_id}/missions/active").json()
    assert actives["count"] == 1
    assert actives["missions"][0]["id"] == mid


def test_accept_unknown_mission_returns_404(client: TestClient, save_id: str) -> None:
    """Mission id inconnu -> 404."""
    r = client.post(
        f"/play/{save_id}/missions/accept",
        json={"mission_id": "nonexistent_xyz"},
    )
    assert r.status_code == 404


def test_submit_success_grants_ryos(client: TestClient, save_id: str) -> None:
    """Soumission avec succes : recompense en ryos."""
    avail = client.get(f"/play/{save_id}/missions/available").json()
    mid = avail["missions"][0]["id"]
    expected_reward = avail["missions"][0]["reward_ryos"]
    client.post(f"/play/{save_id}/missions/accept", json={"mission_id": mid})
    r = client.post(
        f"/play/{save_id}/missions/submit",
        json={"mission_id": mid, "success": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ryos_gained"] == expected_reward
    assert body["new_money"] >= expected_reward


def test_submit_failure_no_ryos(client: TestClient, save_id: str) -> None:
    """Echec : pas de gain de ryos."""
    avail = client.get(f"/play/{save_id}/missions/available").json()
    mid = avail["missions"][0]["id"]
    client.post(f"/play/{save_id}/missions/accept", json={"mission_id": mid})
    r = client.post(
        f"/play/{save_id}/missions/submit",
        json={"mission_id": mid, "success": False},
    )
    assert r.status_code == 200
    assert r.json()["ryos_gained"] == 0


def test_submit_unknown_mission_returns_404(client: TestClient, save_id: str) -> None:
    """Mission non acceptee -> submit 404."""
    r = client.post(
        f"/play/{save_id}/missions/submit",
        json={"mission_id": "never_accepted", "success": True},
    )
    assert r.status_code == 404


def test_missions_unknown_save_404(client: TestClient) -> None:
    """Save inconnue -> 404."""
    r = client.get("/play/unknown_xyz/missions/available")
    assert r.status_code == 404
