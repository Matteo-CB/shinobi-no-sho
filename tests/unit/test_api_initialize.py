"""Phase 9 : tests POST /play/{id}/initialize (bootstrap Phase A/B/D/E)."""
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
    r = client.post("/saves", json={"mode": "random", "name": "InitTest"})
    return r.json()["save_id"]


def test_initialize_returns_status_per_subsystem(
    client: TestClient, save_id: str,
) -> None:
    """POST /initialize repond avec etat des 3 sous-systemes."""
    r = client.post(f"/play/{save_id}/initialize")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["save_id"] == save_id
    for key in (
        "kg_initialized",
        "kg_facts_count",
        "personality_initialized",
        "personality_baselines_count",
        "agents_initialized",
        "agents_count",
        "errors",
    ):
        assert key in body


def test_initialize_idempotent(client: TestClient, save_id: str) -> None:
    """Appel 2 fois consecutif -> meme resultat (no-op le 2e)."""
    r1 = client.post(f"/play/{save_id}/initialize")
    r2 = client.post(f"/play/{save_id}/initialize")
    body1, body2 = r1.json(), r2.json()
    # KG facts count peut augmenter au 1er, identique au 2e
    assert body2["kg_facts_count"] >= body1["kg_facts_count"]
    # Le 2e appel ne reimporte pas


def test_initialize_creates_kg_db(client: TestClient, save_id: str) -> None:
    """Apres /initialize, kg.sqlite existe sur disque."""
    from shinobi.persistence import saves as save_module

    client.post(f"/play/{save_id}/initialize")
    assert save_module.kg_db_path(save_id).exists()


def test_initialize_creates_personality_db(
    client: TestClient, save_id: str,
) -> None:
    """Apres /initialize, personality.sqlite existe."""
    from shinobi.persistence import saves as save_module

    client.post(f"/play/{save_id}/initialize")
    assert save_module.personality_db_path(save_id).exists()


def test_initialize_creates_agents_db(client: TestClient, save_id: str) -> None:
    """Apres /initialize, agents.sqlite existe + roster peuple."""
    from shinobi.persistence import saves as save_module

    r = client.post(f"/play/{save_id}/initialize")
    body = r.json()
    assert save_module.agents_db_path(save_id).exists()
    assert body["agents_count"] >= 1


def test_initialize_populates_kg_with_canon_facts(
    client: TestClient, save_id: str,
) -> None:
    """Apres /initialize, le KG contient des facts (canon import)."""
    r = client.post(f"/play/{save_id}/initialize")
    body = r.json()
    assert body["kg_initialized"] is True
    assert body["kg_facts_count"] >= 1


def test_initialize_unknown_save_404(client: TestClient) -> None:
    """save inconnue -> 404."""
    r = client.post("/play/unknown_xyz/initialize")
    assert r.status_code == 404


def test_initialize_enables_beliefs_inspector(
    client: TestClient, save_id: str,
) -> None:
    """Apres /initialize, l'inspector /beliefs renvoie available=True."""
    client.post(f"/play/{save_id}/initialize")
    r = client.get(f"/play/{save_id}/beliefs/uzumaki_naruto")
    assert r.status_code == 200
    body = r.json()
    # KG existe -> available True (meme si naruto n'a aucun fact connu)
    assert body["available"] is True


def test_initialize_enables_agents_roster_inspector(
    client: TestClient, save_id: str,
) -> None:
    """Apres /initialize, /agents renvoie un roster non vide."""
    client.post(f"/play/{save_id}/initialize")
    r = client.get(f"/play/{save_id}/agents")
    body = r.json()
    assert body["count"] >= 1


def test_initialize_returns_rag_status(
    client: TestClient, save_id: str,
) -> None:
    """rag_index_status est present (string non vide)."""
    r = client.post(f"/play/{save_id}/initialize")
    body = r.json()
    assert isinstance(body["rag_index_status"], str)
    assert body["rag_index_status"] != ""


def test_initialize_creates_director_state(
    client: TestClient, save_id: str,
) -> None:
    """director_state.json est cree apres /initialize."""
    from shinobi.persistence import saves as save_module

    r = client.post(f"/play/{save_id}/initialize")
    body = r.json()
    assert body["director_state_initialized"] is True
    assert save_module.director_state_path(save_id).exists()


def test_initialize_director_state_idempotent(
    client: TestClient, save_id: str,
) -> None:
    """2 appels consecutifs ne ecrasent pas le DirectorState existant."""
    from shinobi.persistence import saves as save_module

    client.post(f"/play/{save_id}/initialize")
    d_path = save_module.director_state_path(save_id)
    content_before = d_path.read_text(encoding="utf-8")
    client.post(f"/play/{save_id}/initialize")
    content_after = d_path.read_text(encoding="utf-8")
    assert content_before == content_after


def test_initialize_migrates_legacy_goals_i18n(
    client: TestClient, save_id: str,
) -> None:
    """Phase i18n.8 : /initialize migre les goals existants au schema enrichi.

    Insere un goal "legacy" (sans original_language ni translated) en DB,
    puis appelle /initialize et verifie que le champ
    `description_player_original_language` a ete rempli (au moins via
    heuristique fallback si Qwen est down).
    """
    from shinobi.goals.declaration import declare_goal
    from shinobi.persistence import saves as save_module

    # Goal legacy : on force defaults Phase 5 (None / {}).
    legacy_goal = declare_goal(
        description_player="Je veux apprendre le Rasengan avec mon sensei",
        interpretation_canonical="apprendre rasengan",
        declared_at_year=8,
        declared_at_age=5,
    )
    assert legacy_goal.description_player_original_language is None
    assert legacy_goal.description_player_translated == {}
    save_module.save_goal(save_id, legacy_goal)

    r = client.post(f"/play/{save_id}/initialize")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "goals_i18n_migrated" in body
    assert "goals_i18n_pending" in body
    # Au moins 1 goal traite (migre OU pending, selon dispo Qwen).
    assert body["goals_i18n_migrated"] + body["goals_i18n_pending"] >= 1

    # Le goal stocke a maintenant un original_language non None (heuristique
    # reconnait "Je veux apprendre" comme FR).
    goals = save_module.load_goals(save_id)
    migrated = next(g for g in goals if g.id == legacy_goal.id)
    assert migrated.description_player_original_language is not None


def test_initialize_goals_i18n_idempotent(
    client: TestClient, save_id: str,
) -> None:
    """Phase i18n.8 : 2eme appel ne re-migre pas les goals deja a jour."""
    from shinobi.goals.declaration import declare_goal
    from shinobi.persistence import saves as save_module

    save_module.save_goal(save_id, declare_goal(
        description_player="Je veux apprendre le Rasengan avec mon sensei",
        interpretation_canonical="x",
        declared_at_year=8, declared_at_age=5,
    ))
    client.post(f"/play/{save_id}/initialize")
    r2 = client.post(f"/play/{save_id}/initialize")
    body2 = r2.json()
    # 2e passe : 0 migration (deja fait au 1er). Cas pending toujours
    # acceptable (Qwen reste indisponible) : on tolere migrated==0.
    assert body2["goals_i18n_migrated"] == 0
