"""Phase i18n.11 : tests cross-langue API via Accept-Language.

Pour chacune des 8 langues, GET /health avec Accept-Language: <lang>
retourne 200 + Content-Language: <lang>. Garantit la chaine middleware
-> ContextVar -> handler -> response header.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from shinobi.config import settings

    monkeypatch.setattr(settings, "saves_path", str(tmp_path))
    monkeypatch.setattr(
        type(settings), "saves_dir", property(lambda self: tmp_path),
    )
    from shinobi.api import app
    return TestClient(app)


def test_api_status_with_accept_language(client: TestClient, lang: str) -> None:
    """8 instances : 1 par langue. /health doit toujours repondre 200
    + Content-Language echo."""
    r = client.get("/health", headers={"Accept-Language": lang})
    assert r.status_code == 200, r.text
    assert r.headers.get("Content-Language") == lang


def test_api_preferences_returns_supported_langs(client: TestClient, lang: str) -> None:
    """GET /preferences avec Accept-Language: <lang> liste toujours les 8
    langues supportees (indemne de la lang requete)."""
    r = client.get("/preferences", headers={"Accept-Language": lang})
    assert r.status_code == 200
    body = r.json()
    assert "available_languages" in body
    assert sorted(body["available_languages"]) == sorted([
        "en", "fr", "es", "ja", "zh", "ko", "pt-BR", "de",
    ])


def test_api_canon_character_localized_via_accept_language(
    client: TestClient, lang: str,
) -> None:
    """GET /canon/characters/uchiha_itachi avec Accept-Language: <lang>
    retourne 200 + name_romaji intact + name non vide."""
    r = client.get(
        "/canon/characters/uchiha_itachi",
        headers={"Accept-Language": lang},
    )
    if r.status_code == 404:
        pytest.skip("canon uchiha_itachi absent dans cette install")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("name_romaji"), str)
    assert "Itachi" in body["name_romaji"]
    assert isinstance(body.get("name"), str)
    assert body["name"]
