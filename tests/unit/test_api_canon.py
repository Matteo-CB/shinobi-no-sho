"""Phase 9 : tests routes /canon (characters, techniques, villages, resolve)."""
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


# === /canon/characters ===================================================


def test_list_characters_default_pagination(client: TestClient) -> None:
    """GET /canon/characters retourne offset=0 limit=50 par defaut."""
    r = client.get("/canon/characters")
    assert r.status_code == 200
    body = r.json()
    assert body["offset"] == 0
    assert body["limit"] == 50
    assert body["total"] >= 1
    assert len(body["characters"]) <= 50


def test_list_characters_playable_only(client: TestClient) -> None:
    """playable_only=True ne renvoie que les chars avec birth_year."""
    r = client.get("/canon/characters", params={"playable_only": True})
    assert r.status_code == 200
    body = r.json()
    for c in body["characters"]:
        assert c["birth_year"] is not None


def test_list_characters_pagination_offset(client: TestClient) -> None:
    """offset=5 saute les 5 premiers."""
    r1 = client.get("/canon/characters", params={"limit": 5, "offset": 0})
    r2 = client.get("/canon/characters", params={"limit": 5, "offset": 5})
    ids1 = [c["id"] for c in r1.json()["characters"]]
    ids2 = [c["id"] for c in r2.json()["characters"]]
    assert set(ids1).isdisjoint(set(ids2))


def test_list_characters_filter_village(client: TestClient) -> None:
    """Filtre village."""
    r = client.get(
        "/canon/characters",
        params={"village": "konohagakure", "limit": 200},
    )
    assert r.status_code == 200
    for c in r.json()["characters"]:
        assert c["village_of_origin"] == "konohagakure"


def test_get_character_known_id(client: TestClient) -> None:
    """GET /canon/characters/{id} pour Naruto (id stable)."""
    r = client.get("/canon/characters/uzumaki_naruto")
    if r.status_code == 404:
        pytest.skip("uzumaki_naruto absent du canon local")
    body = r.json()
    assert body["id"] == "uzumaki_naruto"
    assert body["village_of_origin"] == "konohagakure"


def test_get_character_unknown_returns_404(client: TestClient) -> None:
    """ID inconnu -> 404."""
    r = client.get("/canon/characters/inexistant_xyz")
    assert r.status_code == 404


def test_resolve_canon_id_exact(client: TestClient) -> None:
    """POST /canon/characters/resolve avec id exact."""
    r = client.post(
        "/canon/characters/resolve",
        json={"query": "uzumaki_naruto"},
    )
    assert r.status_code == 200
    body = r.json()
    if body["canon_id"] is not None:
        assert body["canon_id"] == "uzumaki_naruto"


def test_resolve_canon_id_fuzzy_substring(client: TestClient) -> None:
    """Recherche fuzzy par substring."""
    r = client.post(
        "/canon/characters/resolve",
        json={"query": "Itachi"},
    )
    assert r.status_code == 200
    body = r.json()
    # Soit canon_id resolu, soit candidats listes
    assert body["canon_id"] is not None or len(body["candidates"]) > 0


# === /canon/techniques ===================================================


def test_list_techniques_default(client: TestClient) -> None:
    """Liste des techniques canon."""
    r = client.get("/canon/techniques")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1


def test_list_techniques_filter_nature(client: TestClient) -> None:
    """Filtre par nature katon."""
    r = client.get(
        "/canon/techniques", params={"nature": "katon", "limit": 200},
    )
    assert r.status_code == 200
    for t in r.json()["techniques"]:
        assert "katon" in t["natures"]


def test_get_technique_unknown_returns_404(client: TestClient) -> None:
    """Tech inconnue -> 404."""
    r = client.get("/canon/techniques/inexistant_xyz")
    assert r.status_code == 404


# === /canon/villages =====================================================


def test_list_villages_returns_canonical(client: TestClient) -> None:
    """GET /canon/villages renvoie au moins quelques villages."""
    r = client.get("/canon/villages")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    village_ids = {v["id"] for v in body["villages"]}
    # Konoha doit etre present dans tout canon valide
    assert any("konoha" in vid.lower() for vid in village_ids)


# === /canon/clans ========================================================


def test_list_clans_returns_canon(client: TestClient) -> None:
    """GET /canon/clans liste les clans canon."""
    r = client.get("/canon/clans")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1


def test_get_clan_unknown_returns_404(client: TestClient) -> None:
    r = client.get("/canon/clans/inexistant_xyz")
    assert r.status_code == 404


# === /canon/organizations ================================================


def test_list_organizations_returns_canon(client: TestClient) -> None:
    """GET /canon/organizations liste les organisations canon."""
    r = client.get("/canon/organizations")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1


# === /canon/eras ==========================================================


def test_list_eras_returns_canon(client: TestClient) -> None:
    """GET /canon/eras liste les eras canon, triees par year_start."""
    r = client.get("/canon/eras")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    years = [
        e["year_start"] for e in body["eras"]
        if e["year_start"] is not None
    ]
    assert years == sorted(years)


# === /canon/kekkei_genkai ================================================


def test_list_kekkei_genkai(client: TestClient) -> None:
    """GET /canon/kekkei_genkai retourne les KG canon."""
    r = client.get("/canon/kekkei_genkai")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1


# === Error schema =========================================================


def test_error_response_uniform_schema(client: TestClient) -> None:
    """Les 404 renvoient bien le schema {error, detail}."""
    r = client.get("/canon/characters/inexistant_xyz")
    assert r.status_code == 404
    body = r.json()
    assert "error" in body
    assert "detail" in body
    assert "inexistant_xyz" in body["detail"]
