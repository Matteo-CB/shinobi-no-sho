"""Phase i18n.9 : tests middleware Accept-Language + i18n responses API.

Couvre :
1. parse_accept_language : multi-langues + qualites
2. select_language : normalisation (pt -> pt-BR, zh-CN -> zh, en-US -> en)
3. select_language : header absent / vide / * / langue non supportee
4. select_language : quality 0 = filtre
5. Middleware : sans header -> langue par defaut du serveur
6. Middleware : Accept-Language: ja -> Content-Language: ja + active lang scopee
7. Middleware : ContextVar isolee entre requetes (concurrence + reset)
8. Route /canon/characters/{id} : header ja -> name resolu (name_romaji intact)
9. Route /canon/characters/{id}/wiki : header en -> sections source
10. Route /canon/characters/{id}/wiki : header ja avec cache present -> JA
11. Route /canon/characters/{id}/wiki : 404 sur canon_id inconnu
12. Pas de regression : /canon/characters sans header marche en EN par defaut
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shinobi.api.middleware.i18n import (
    parse_accept_language,
    select_language,
)
from shinobi.i18n.catalog import get_active_language, set_active_language

# === Fixtures =========================================================


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from shinobi.config import settings

    monkeypatch.setattr(settings, "saves_path", str(tmp_path))
    monkeypatch.setattr(
        type(settings), "saves_dir", property(lambda self: tmp_path),
    )
    # Reset langue active globale a EN (defaut serveur)
    set_active_language("en")
    from shinobi.api import app
    return TestClient(app)


# === 1. parse_accept_language =========================================


def test_parse_accept_language_multi_with_quality() -> None:
    out = parse_accept_language("en-US, en;q=0.9, fr;q=0.8, ja;q=1.0")
    # ja avec q=1.0 et en-US avec q=1.0 implicite -> tri par q desc puis ordre.
    # en-US normalise en en.
    assert out[0] in ("en", "ja")  # en (q=1.0 implicite) avant fr (q=0.8)
    assert "ja" in out
    assert "fr" in out


def test_parse_accept_language_handles_pt_zh_subtags() -> None:
    # pt-PT -> pt-BR (best-effort, on n'a pas pt-PT).
    # zh-CN -> zh, zh-Hans -> zh.
    assert parse_accept_language("pt-PT") == ["pt-BR"]
    assert parse_accept_language("zh-CN") == ["zh"]
    assert parse_accept_language("zh-Hans;q=0.9") == ["zh"]


# === 2. select_language : normalisation ===============================


def test_select_language_normalizes_codes() -> None:
    assert select_language("en-US") == "en"
    assert select_language("pt") == "pt-BR"
    assert select_language("pt-BR") == "pt-BR"
    assert select_language("zh-CN") == "zh"
    assert select_language("ja-JP") == "ja"


# === 3. Header absent / vide / * / non supporte =======================


def test_select_language_edge_cases() -> None:
    assert select_language(None) is None
    assert select_language("") is None
    assert select_language("*") is None
    assert select_language("xx-XX, klingon") is None


# === 4. Quality 0 = filtre ===========================================


def test_select_language_quality_zero_filtered() -> None:
    # ja;q=0 explicitement refuse -> on tombe sur en
    assert select_language("ja;q=0, en;q=0.9") == "en"
    # tout en q=0 -> rien
    assert select_language("ja;q=0, en;q=0") is None


# === 5. Middleware no-op sans header ==================================


def test_middleware_noop_without_header(client: TestClient) -> None:
    """Sans Accept-Language : utilise la langue globale (EN par defaut)."""
    r = client.get("/health")
    # Pas de Content-Language ajoute si le middleware n'a pas selectionne.
    assert r.headers.get("Content-Language") is None


# === 6. Middleware avec header valide ==================================


def test_middleware_sets_content_language_header(client: TestClient) -> None:
    """Accept-Language: ja -> reponse marquee Content-Language: ja."""
    r = client.get("/health", headers={"Accept-Language": "ja, en;q=0.5"})
    assert r.status_code == 200
    assert r.headers.get("Content-Language") == "ja"


# === 7. ContextVar : isolation entre requetes ========================


def test_middleware_does_not_leak_to_global(client: TestClient) -> None:
    """Apres une requete avec Accept-Language: ja, le global reste a EN."""
    assert get_active_language() == "en"
    client.get("/health", headers={"Accept-Language": "ja"})
    # La ContextVar a ete restee a la sortie du middleware. Le global est
    # inchange (le middleware n'utilise PAS set_active_language).
    assert get_active_language() == "en"


# === 8. Canon character : nom localise selon Accept-Language ==========


def test_canon_character_name_resolves_with_accept_language(
    client: TestClient,
) -> None:
    """GET /canon/characters/uchiha_itachi avec ja -> name=name_romaji ou
    name_ja si dispo. name_romaji reste tel quel."""
    r = client.get(
        "/canon/characters/uchiha_itachi",
        headers={"Accept-Language": "ja"},
    )
    if r.status_code == 404:
        pytest.skip("canon uchiha_itachi absent dans cette installation")
    body = r.json()
    # name_romaji intact (jamais traduit) — peut etre "Itachi Uchiha"
    # ou "Uchiha Itachi" selon l'ordre stocke, mais doit contenir Itachi.
    assert isinstance(body.get("name_romaji"), str)
    assert "Itachi" in body["name_romaji"]
    # name resolu : si name_ja n'existe pas, fallback name_fr / name_en /
    # name_romaji. Mais doit etre une string non vide.
    assert isinstance(body.get("name"), str)
    assert body["name"]


def test_canon_character_default_language_still_works(
    client: TestClient,
) -> None:
    """Sans Accept-Language : le global EN s'applique. Pas de regression."""
    r = client.get("/canon/characters/uchiha_itachi")
    if r.status_code == 404:
        pytest.skip("canon uchiha_itachi absent dans cette installation")
    body = r.json()
    assert body["id"] == "uchiha_itachi"
    # name_fr toujours present (retro-compat)
    assert "name_fr" in body
    assert "name" in body


# === 9. Wiki sections : EN renvoie source =============================


def test_canon_wiki_returns_en_source(client: TestClient) -> None:
    """GET /canon/characters/uchiha_itachi/wiki avec Accept-Language: en
    retourne les sections source brutes du canon (pas de traduction)."""
    r = client.get(
        "/canon/characters/uchiha_itachi/wiki",
        headers={"Accept-Language": "en"},
    )
    if r.status_code == 404:
        pytest.skip("canon uchiha_itachi absent")
    body = r.json()
    assert body["canon_id"] == "uchiha_itachi"
    assert body["language"] == "en"
    assert body["pending"] is False


# === 10. Wiki : ja avec cache present -> sections JA ==================


def test_canon_wiki_uses_ja_cache_when_present(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si data/i18n/wiki/ja/uchiha_itachi.json existe, on l'utilise direct
    sans appeler Qwen."""
    # On verifie juste le cache reel (deja livre Phase 6). Si absent, skip.
    from shinobi.i18n.wiki_translator import WIKI_DIR

    cached = WIKI_DIR / "ja" / "uchiha_itachi.json"
    if not cached.exists():
        pytest.skip("cache ja/uchiha_itachi absent dans cette installation")
    raw = json.loads(cached.read_text(encoding="utf-8"))
    if raw.get("_translation_pending"):
        pytest.skip("cache marque pending, scenario non utile pour ce test")

    r = client.get(
        "/canon/characters/uchiha_itachi/wiki",
        headers={"Accept-Language": "ja"},
    )
    if r.status_code == 404:
        pytest.skip("canon uchiha_itachi absent")
    body = r.json()
    assert body["language"] == "ja"
    assert body["pending"] is False
    # Les sections doivent matcher le cache (ou au moins etre non vides).
    assert body["Background"]
    assert body["Background"] == raw.get("Background")


# === 11. Wiki : 404 sur canon_id inconnu =============================


def test_canon_wiki_unknown_id_returns_404(client: TestClient) -> None:
    r = client.get(
        "/canon/characters/n_existe_pas_xyz_999/wiki",
        headers={"Accept-Language": "ja"},
    )
    assert r.status_code == 404


# === 12. Wiki : Accept-Language inconnu -> fallback global EN =========


def test_canon_wiki_unknown_lang_falls_back_to_global(
    client: TestClient,
) -> None:
    """Header Accept-Language: klingon -> middleware ne match rien, le
    global EN s'applique, et l'endpoint retourne language=en sans erreur."""
    r = client.get(
        "/canon/characters/uchiha_itachi/wiki",
        headers={"Accept-Language": "klingon, xx-XX"},
    )
    if r.status_code == 404:
        pytest.skip("canon uchiha_itachi absent")
    body = r.json()
    assert body["language"] == "en"
    assert body["pending"] is False


# === 13. Round-trip par langue (1 test par langue supportee) =========
# Spec doc 14 §i18n.9 demande "12 tests, un par langue + middleware +
# fallback". On parametrise sur les 8 SUPPORTED_LANGUAGES pour avoir
# une couverture exhaustive du middleware + de la route canon.


@pytest.mark.parametrize(
    "lang",
    ["en", "fr", "es", "ja", "zh", "ko", "pt-BR", "de"],
)
def test_canon_character_per_language_roundtrip(
    client: TestClient, lang: str,
) -> None:
    """Un test par langue supportee : Accept-Language: <lang> -> header
    Content-Language: <lang> echo + body name_romaji intact + body.name
    string non vide."""
    r = client.get(
        "/canon/characters/uchiha_itachi",
        headers={"Accept-Language": lang},
    )
    if r.status_code == 404:
        pytest.skip("canon uchiha_itachi absent dans cette installation")
    assert r.status_code == 200, r.text
    # Le middleware echo la langue selectionnee dans Content-Language.
    assert r.headers.get("Content-Language") == lang
    body = r.json()
    # name_romaji jamais traduit (toujours en romaji latin).
    assert isinstance(body.get("name_romaji"), str)
    assert body["name_romaji"]
    # name resolu avec la chaine de fallback : meme si name_<lang> n'existe
    # pas, on a toujours au moins name_fr / name_en / name_romaji.
    assert isinstance(body.get("name"), str)
    assert body["name"]
