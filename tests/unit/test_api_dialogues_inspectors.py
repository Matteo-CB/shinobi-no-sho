"""Phase 9 : tests routes /dialogues + Phase A-H inspectors (read-only)."""
from __future__ import annotations

import json
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
    r = client.post("/saves", json={"mode": "random", "name": "InspectTest"})
    return r.json()["save_id"]


# === /dialogues ===========================================================


def test_dialogues_empty_initially(client: TestClient, save_id: str) -> None:
    """Pas de DialogueLog -> liste vide."""
    r = client.get(f"/play/{save_id}/dialogues")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["lines"] == []


def test_dialogues_reads_persisted_log(client: TestClient, save_id: str) -> None:
    """Si un dialogues.jsonl existe, l'API le lit."""
    from shinobi.persistence import saves as save_module

    log_path = save_module.dialogue_log_path(save_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "id": "dline_t1",
        "speaker_id": "narrator",
        "text": "Le vent se leve sur Konoha.",
        "in_game_year": 12,
        "in_game_date": "01-15",
        "turn_number": 1,
    }
    log_path.write_text(json.dumps(line) + "\n", encoding="utf-8")
    r = client.get(f"/play/{save_id}/dialogues")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["lines"][0]["speaker"] == "narrator"


def test_dialogues_export_returns_vn_payload(
    client: TestClient, save_id: str,
) -> None:
    """L'export VN renvoie un JSON structure."""
    r = client.get(f"/play/{save_id}/dialogues/export")
    assert r.status_code == 200
    payload = r.json()
    assert "in_game_metadata" in payload


def test_dialogues_unknown_save_404(client: TestClient) -> None:
    r = client.get("/play/unknown_xyz/dialogues")
    assert r.status_code == 404


# === Phase A-H inspectors (gracefully no-data) ============================


def test_personality_unavailable_when_no_db(
    client: TestClient, save_id: str,
) -> None:
    """Personality DB absente -> available=False."""
    r = client.get(f"/play/{save_id}/personality/uzumaki_naruto")
    assert r.status_code == 200
    assert r.json()["available"] is False


def test_beliefs_unavailable_when_no_kg(client: TestClient, save_id: str) -> None:
    """KG DB absente -> available=False, facts vide."""
    r = client.get(f"/play/{save_id}/beliefs/uzumaki_naruto")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["facts"] == []


def test_tensions_returns_empty_when_no_kg(
    client: TestClient, save_id: str,
) -> None:
    """KG absent -> liste de tensions vide (pas de crash)."""
    r = client.get(f"/play/{save_id}/tensions")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["tensions"] == []


def test_agents_roster_empty_when_no_db(
    client: TestClient, save_id: str,
) -> None:
    """Roster Phase E absent -> liste vide."""
    r = client.get(f"/play/{save_id}/agents")
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_agent_detail_unavailable_when_no_db(
    client: TestClient, save_id: str,
) -> None:
    """Agent detail sans DB -> available=False."""
    r = client.get(f"/play/{save_id}/agents/uzumaki_naruto")
    assert r.status_code == 200
    assert r.json()["available"] is False


def test_inspectors_unknown_save_404(client: TestClient) -> None:
    """Save inconnue -> 404 sur tous les inspectors."""
    for path in (
        "personality/x",
        "beliefs/x",
        "tensions",
        "agents",
        "agents/x",
    ):
        r = client.get(f"/play/unknown_xyz/{path}")
        assert r.status_code == 404, path
