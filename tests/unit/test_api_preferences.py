"""Phase i18n.2 : tests routes /preferences (GET + PUT /language)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shinobi.i18n import SUPPORTED_LANGUAGES, get_active_language
from shinobi.i18n.catalog import reset_for_tests as reset_catalog


@pytest.fixture(autouse=True)
def reset_state():
    reset_catalog()
    yield
    reset_catalog()


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    """TestClient avec preferences isolees + saves isolees."""
    from shinobi.config import settings

    monkeypatch.setattr(settings, "saves_path", str(tmp_path / "saves"))
    monkeypatch.setattr(
        type(settings), "saves_dir",
        property(lambda self: tmp_path / "saves"),
    )
    monkeypatch.setenv("SHINOBI_PREFERENCES_DIR", str(tmp_path / "prefs"))
    from shinobi.api import app
    return TestClient(app)


# === GET /preferences ====================================================


def test_get_preferences_returns_defaults_on_first_use(
    client: TestClient,
) -> None:
    """Sans preferences.json, GET retourne defaults (lang=en, first_launch=False)."""
    r = client.get("/preferences")
    assert r.status_code == 200
    body = r.json()
    assert body["language"] == "en"
    assert body["first_launch_completed"] is False
    assert body["language_chosen_at"] is None


def test_get_preferences_lists_8_supported_languages(
    client: TestClient,
) -> None:
    """available_languages contient les 8 codes supportes."""
    r = client.get("/preferences")
    body = r.json()
    assert set(body["available_languages"]) == set(SUPPORTED_LANGUAGES)
    assert len(body["available_languages"]) == 8


def test_get_preferences_includes_native_names(client: TestClient) -> None:
    """native_names mappe chaque code vers son nom natif."""
    r = client.get("/preferences")
    names = r.json()["native_names"]
    assert names["en"] == "English"
    assert names["ja"] == "日本語"
    assert names["zh"] == "中文"
    assert names["ko"] == "한국어"


# === PUT /preferences/language ==========================================


def test_put_language_changes_active_and_persists(client: TestClient) -> None:
    """PUT /preferences/language met a jour le runtime + persiste."""
    r = client.put(
        "/preferences/language",
        json={"language": "ja"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["language"] == "ja"
    assert body["first_launch_completed"] is True
    assert body["language_chosen_at"] is not None
    # Verification : GET reflete le changement
    r2 = client.get("/preferences")
    assert r2.json()["language"] == "ja"
    # Et le runtime du serveur aussi
    assert get_active_language() == "ja"


def test_put_language_rejects_unsupported_code(client: TestClient) -> None:
    """Code non supporte -> 422 avec liste des available."""
    r = client.put(
        "/preferences/language",
        json={"language": "klingon"},
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "klingon" in detail
    assert "Available" in detail


def test_put_language_works_for_all_8_supported(client: TestClient) -> None:
    """Chaque langue supportee peut etre activee via l'API."""
    for code in SUPPORTED_LANGUAGES:
        r = client.put(
            "/preferences/language",
            json={"language": code},
        )
        assert r.status_code == 200, f"Failed for {code}: {r.text}"
        assert r.json()["language"] == code


def test_put_language_missing_field_returns_422(client: TestClient) -> None:
    """Body sans 'language' -> 422 (Pydantic validation)."""
    r = client.put("/preferences/language", json={})
    assert r.status_code == 422


def test_put_language_marks_first_launch_completed(
    client: TestClient,
) -> None:
    """Apres le 1er PUT, first_launch_completed devient True."""
    # Etat initial : False
    assert client.get("/preferences").json()["first_launch_completed"] is False
    # Apres PUT
    client.put("/preferences/language", json={"language": "fr"})
    assert client.get("/preferences").json()["first_launch_completed"] is True


def test_api_factory_handles_init_failure_gracefully(
    monkeypatch,
) -> None:
    """Si initialize_from_preferences leve une exception, create_app()
    continue sans crash (best-effort, l'API demarre en EN par defaut)."""
    import shinobi.i18n as i18n_mod
    import shinobi.i18n.catalog as catalog_mod

    # Force initialize_from_preferences a raise (patche les 2 chemins
    # d'import pour couvrir la branche `from shinobi.i18n import ...`).
    def _broken_init():
        raise OSError("simulated platformdirs failure")

    monkeypatch.setattr(
        catalog_mod, "initialize_from_preferences", _broken_init,
    )
    monkeypatch.setattr(
        i18n_mod, "initialize_from_preferences", _broken_init,
    )
    reset_catalog()
    from shinobi.api.server import create_app

    app = create_app()
    # L'app est cree malgre l'exception
    assert app is not None


def test_api_factory_initializes_runtime_from_preferences(
    tmp_path: Path, monkeypatch,
) -> None:
    """Phase i18n.2 fix : create_app() initialise la langue runtime depuis
    preferences.json (sinon mismatch entre GET /preferences et runtime)."""
    import json

    from shinobi.config import settings

    monkeypatch.setattr(settings, "saves_path", str(tmp_path / "saves"))
    monkeypatch.setattr(
        type(settings), "saves_dir",
        property(lambda self: tmp_path / "saves"),
    )
    prefs_dir = tmp_path / "prefs"
    prefs_dir.mkdir()
    monkeypatch.setenv("SHINOBI_PREFERENCES_DIR", str(prefs_dir))
    # Pre-cree preferences.json avec lang=zh
    (prefs_dir / "preferences.json").write_text(
        json.dumps({
            "schema_version": 1,
            "language": "zh",
            "first_launch_completed": True,
            "language_chosen_at": "2026-05-08T10:00:00Z",
        }),
        encoding="utf-8",
    )
    # Reset le catalog runtime puis cree une nouvelle app
    reset_catalog()
    from shinobi.api.server import create_app

    create_app()
    # Verifie que le runtime a ete initialise depuis preferences.json
    assert get_active_language() == "zh"
