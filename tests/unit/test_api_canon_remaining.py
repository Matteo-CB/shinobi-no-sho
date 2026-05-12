"""Phase 9 : tests datasets canon manquants
(world_rules, ranks, kekkei_mora, phase_h/<dataset>) + health llm_available.
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


# === World rules / ranks / kekkei_mora ====================================


def test_world_rules_returns_six_sections(client: TestClient) -> None:
    """World rules contient les 6 sections (chakra/learning/combat/social/economy/time)."""
    r = client.get("/canon/world_rules")
    assert r.status_code == 200, r.text
    body = r.json()
    for section in ("chakra", "learning", "combat", "social", "economy", "time"):
        assert section in body
        assert isinstance(body[section], dict)


def test_ranks_lists_canonical_ranks(client: TestClient) -> None:
    """Liste des grades canon : academy/genin/chunin/jonin/.../kage."""
    r = client.get("/canon/ranks")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    rank_ids = {x["id"] for x in body["ranks"]}
    # Au moins quelques rangs essentiels doivent etre presents
    assert any(rid in rank_ids for rid in ("genin", "chunin", "jonin", "kage"))


def test_kekkei_mora_endpoint(client: TestClient) -> None:
    """Liste kekkei_mora (peut etre vide selon canon, mais doit repondre 200)."""
    r = client.get("/canon/kekkei_mora")
    assert r.status_code == 200
    body = r.json()
    assert "kekkei_mora" in body
    assert "count" in body


# === Phase H datasets =====================================================


@pytest.mark.parametrize(
    "dataset_id",
    [
        "deep_motivations",
        "political_forces",
        "divergence_points",
        "narrative_patterns",
        "timeline_events_enriched",
    ],
)
def test_phase_h_known_dataset(client: TestClient, dataset_id: str) -> None:
    """Les 5 datasets Phase H repondent 200 (available True ou False selon
    presence sur disque)."""
    r = client.get(f"/canon/phase_h/{dataset_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["dataset_id"] == dataset_id
    assert isinstance(body["available"], bool)


def test_phase_h_unknown_dataset_returns_404(client: TestClient) -> None:
    """Dataset inconnu -> 404 avec liste des dataset_ids attendus."""
    r = client.get("/canon/phase_h/inexistant_xyz")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert "deep_motivations" in detail


def test_phase_h_payload_loaded_when_available(client: TestClient) -> None:
    """Si Phase H est presente sur disque, payload est non-null + count > 0."""
    r = client.get("/canon/phase_h/deep_motivations")
    body = r.json()
    if body["available"]:
        assert body["payload"] is not None
        assert body["count"] is not None and body["count"] > 0


# === Health llm_available =================================================


def test_health_includes_llm_available_field(client: TestClient) -> None:
    """/health contient le champ llm_available (booleen)."""
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert "llm_available" in body
    assert isinstance(body["llm_available"], bool)
