"""Phase 9 : tests des datasets canon manquants
(locations, tailed_beasts, hiden, weapons_tools, natures,
timeline_events, voice_profiles).
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


def test_locations(client: TestClient) -> None:
    r = client.get("/canon/locations")
    assert r.status_code == 200
    assert "locations" in r.json()


def test_tailed_beasts(client: TestClient) -> None:
    r = client.get("/canon/tailed_beasts")
    assert r.status_code == 200
    body = r.json()
    # Au moins quelques bijuu canon
    assert body["count"] >= 1


def test_hiden(client: TestClient) -> None:
    r = client.get("/canon/hiden")
    assert r.status_code == 200


def test_weapons_tools(client: TestClient) -> None:
    r = client.get("/canon/weapons_tools")
    assert r.status_code == 200


def test_natures(client: TestClient) -> None:
    r = client.get("/canon/natures")
    assert r.status_code == 200
    body = r.json()
    nature_ids = {n["id"] for n in body["natures"]}
    # 5 natures de base au moins
    assert any(n in nature_ids for n in ("katon", "suiton", "fuuton", "doton", "raiton"))


def test_timeline_events_default(client: TestClient) -> None:
    r = client.get("/canon/timeline_events")
    assert r.status_code == 200
    body = r.json()
    assert body["offset"] == 0
    assert body["limit"] == 50
    assert body["total"] >= 1


def test_timeline_events_year_filter(client: TestClient) -> None:
    r = client.get(
        "/canon/timeline_events", params={"year_min": 0, "year_max": 20},
    )
    body = r.json()
    for ev in body["events"]:
        if ev["year"] is not None:
            assert 0 <= ev["year"] <= 20


def test_timeline_events_pagination(client: TestClient) -> None:
    r1 = client.get("/canon/timeline_events", params={"limit": 5})
    r2 = client.get("/canon/timeline_events", params={"limit": 5, "offset": 5})
    ids1 = [e["id"] for e in r1.json()["events"]]
    ids2 = [e["id"] for e in r2.json()["events"]]
    assert set(ids1).isdisjoint(set(ids2))


def test_voice_profiles(client: TestClient) -> None:
    r = client.get("/canon/voice_profiles")
    assert r.status_code == 200
    assert "voice_profiles" in r.json()
