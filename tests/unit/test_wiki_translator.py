"""Phase i18n.6.B : tests pour le wiki_translator (cache + Qwen mock + fallback)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from shinobi.i18n.wiki_translator import (
    PENDING_MARKER_KEY,
    WIKI_SECTIONS,
    TranslatorBackend,
    cache_path,
    fallback_to_source,
    get_wiki_sections,
    load_cached,
    write_cache,
)


class _FakeChar:
    """Substitut minimal de canon.Character pour les tests."""

    def __init__(self, wiki: dict[str, str]) -> None:
        self.wiki_sections = wiki


class _MockBackend(TranslatorBackend):
    """Backend qui retourne des traductions deterministes."""

    def __init__(self, marker: str = "[TRANSLATED]", *, fail: bool = False) -> None:
        self.marker = marker
        self.fail = fail
        self.calls = 0

    def translate(self, sections: dict[str, str], lang: str) -> dict[str, str]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("backend simulated failure")
        return {section: f"{self.marker} {value}" for section, value in sections.items()}


@pytest.fixture()
def tmp_wiki_dir(tmp_path: Path) -> Path:
    return tmp_path / "wiki"


@pytest.fixture()
def char_with_wiki() -> _FakeChar:
    return _FakeChar({
        "Background": "Born in Konoha. Lost his family young.",
        "Personality": "Stoic, driven by revenge.",
        "Abilities": "Sharingan, Chidori, Amaterasu.",
    })


@pytest.fixture()
def char_empty_wiki() -> _FakeChar:
    return _FakeChar({"Background": "", "Personality": "", "Abilities": ""})


# === Tests cache write/read ===

def test_write_then_load_roundtrip(tmp_wiki_dir: Path) -> None:
    sections = {s: f"content {s}" for s in WIKI_SECTIONS}
    p = write_cache("uchiha_sasuke", "ja", sections, base_dir=tmp_wiki_dir)
    assert p == cache_path("uchiha_sasuke", "ja", tmp_wiki_dir)
    loaded = load_cached("uchiha_sasuke", "ja", tmp_wiki_dir)
    assert loaded is not None
    assert loaded["_language"] == "ja"
    assert loaded["_char_id"] == "uchiha_sasuke"
    for s in WIKI_SECTIONS:
        assert loaded[s] == f"content {s}"


def test_load_cached_returns_none_if_absent(tmp_wiki_dir: Path) -> None:
    assert load_cached("inexistant", "ja", tmp_wiki_dir) is None


def test_load_cached_returns_none_if_corrupt(tmp_wiki_dir: Path) -> None:
    p = cache_path("broken", "ja", tmp_wiki_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json {{{", encoding="utf-8")
    assert load_cached("broken", "ja", tmp_wiki_dir) is None


def test_pending_marker_persisted(tmp_wiki_dir: Path) -> None:
    write_cache("x", "fr", dict.fromkeys(WIKI_SECTIONS, "x"), pending=True, base_dir=tmp_wiki_dir)
    loaded = load_cached("x", "fr", tmp_wiki_dir)
    assert loaded is not None
    assert loaded.get(PENDING_MARKER_KEY) is True


# === Tests get_wiki_sections strategy ===

def test_lang_en_returns_source_directly(char_with_wiki: _FakeChar, tmp_wiki_dir: Path) -> None:
    result = get_wiki_sections(
        "uchiha_sasuke", "en",
        canon_characters={"uchiha_sasuke": char_with_wiki},
        base_dir=tmp_wiki_dir,
    )
    assert result["Background"] == "Born in Konoha. Lost his family young."
    # No cache file written for EN
    assert not cache_path("uchiha_sasuke", "en", tmp_wiki_dir).exists()


def test_unknown_char_returns_empty_sections(tmp_wiki_dir: Path) -> None:
    result = get_wiki_sections(
        "ghost_char", "ja",
        canon_characters={},
        base_dir=tmp_wiki_dir,
    )
    assert result == dict.fromkeys(WIKI_SECTIONS, "")


def test_cache_hit_skips_backend(char_with_wiki: _FakeChar, tmp_wiki_dir: Path) -> None:
    pre_cached = {s: f"PRE_CACHED {s}" for s in WIKI_SECTIONS}
    write_cache("uchiha_sasuke", "ja", pre_cached, base_dir=tmp_wiki_dir)
    backend = _MockBackend()
    result = get_wiki_sections(
        "uchiha_sasuke", "ja",
        canon_characters={"uchiha_sasuke": char_with_wiki},
        backend=backend, base_dir=tmp_wiki_dir,
    )
    assert backend.calls == 0
    for s in WIKI_SECTIONS:
        assert result[s] == f"PRE_CACHED {s}"


def test_cache_miss_calls_backend_then_caches(char_with_wiki: _FakeChar, tmp_wiki_dir: Path) -> None:
    backend = _MockBackend(marker="[JA]")
    result = get_wiki_sections(
        "uchiha_sasuke", "ja",
        canon_characters={"uchiha_sasuke": char_with_wiki},
        backend=backend, base_dir=tmp_wiki_dir,
    )
    assert backend.calls == 1
    for s in WIKI_SECTIONS:
        assert result[s].startswith("[JA] ")
    # Cache file written
    cached = load_cached("uchiha_sasuke", "ja", tmp_wiki_dir)
    assert cached is not None
    assert PENDING_MARKER_KEY not in cached


def test_force_bypasses_cache(char_with_wiki: _FakeChar, tmp_wiki_dir: Path) -> None:
    write_cache("uchiha_sasuke", "ja", dict.fromkeys(WIKI_SECTIONS, "OLD"), base_dir=tmp_wiki_dir)
    backend = _MockBackend(marker="[NEW]")
    result = get_wiki_sections(
        "uchiha_sasuke", "ja",
        canon_characters={"uchiha_sasuke": char_with_wiki},
        backend=backend, base_dir=tmp_wiki_dir, force=True,
    )
    assert backend.calls == 1
    assert all(result[s].startswith("[NEW] ") for s in WIKI_SECTIONS)


def test_pending_cache_is_retried(char_with_wiki: _FakeChar, tmp_wiki_dir: Path) -> None:
    """Si le cache existe mais est marque pending (fallback offline), on retente."""
    write_cache(
        "uchiha_sasuke", "ja",
        dict.fromkeys(WIKI_SECTIONS, "EN source"),
        pending=True, base_dir=tmp_wiki_dir,
    )
    backend = _MockBackend(marker="[OK]")
    result = get_wiki_sections(
        "uchiha_sasuke", "ja",
        canon_characters={"uchiha_sasuke": char_with_wiki},
        backend=backend, base_dir=tmp_wiki_dir,
    )
    assert backend.calls == 1
    assert all(result[s].startswith("[OK] ") for s in WIKI_SECTIONS)


def test_backend_failure_returns_source_with_pending_marker(char_with_wiki: _FakeChar, tmp_wiki_dir: Path) -> None:
    backend = _MockBackend(fail=True)
    result = get_wiki_sections(
        "uchiha_sasuke", "ja",
        canon_characters={"uchiha_sasuke": char_with_wiki},
        backend=backend, base_dir=tmp_wiki_dir,
    )
    # Source EN returned
    assert result["Background"] == "Born in Konoha. Lost his family young."
    # Cache marked pending
    cached = load_cached("uchiha_sasuke", "ja", tmp_wiki_dir)
    assert cached is not None
    assert cached.get(PENDING_MARKER_KEY) is True


def test_no_backend_returns_source_with_pending_marker(char_with_wiki: _FakeChar, tmp_wiki_dir: Path) -> None:
    result = get_wiki_sections(
        "uchiha_sasuke", "ja",
        canon_characters={"uchiha_sasuke": char_with_wiki},
        backend=None, base_dir=tmp_wiki_dir,
    )
    assert result["Background"] == "Born in Konoha. Lost his family young."
    cached = load_cached("uchiha_sasuke", "ja", tmp_wiki_dir)
    assert cached is not None
    assert cached.get(PENDING_MARKER_KEY) is True


def test_empty_source_skips_backend(char_empty_wiki: _FakeChar, tmp_wiki_dir: Path) -> None:
    backend = _MockBackend()
    result = get_wiki_sections(
        "ghost", "ja",
        canon_characters={"ghost": char_empty_wiki},
        backend=backend, base_dir=tmp_wiki_dir,
    )
    assert backend.calls == 0
    assert all(v == "" for v in result.values())


def test_fallback_to_source_helper(tmp_wiki_dir: Path) -> None:
    src = {s: f"src {s}" for s in WIKI_SECTIONS}
    out = fallback_to_source("x", "ja", src, base_dir=tmp_wiki_dir)
    assert out == src
    cached = load_cached("x", "ja", tmp_wiki_dir)
    assert cached is not None
    assert cached.get(PENDING_MARKER_KEY) is True


def test_fallback_to_source_no_marker_write(tmp_wiki_dir: Path) -> None:
    src = {s: f"src {s}" for s in WIKI_SECTIONS}
    out = fallback_to_source("x", "ja", src, base_dir=tmp_wiki_dir, write_marker=False)
    assert out == src
    assert load_cached("x", "ja", tmp_wiki_dir) is None


# === Tests _extract_source_sections : 3 forms de canon_char ===

def test_extract_source_from_plain_dict(tmp_wiki_dir: Path) -> None:
    """canon_char est un dict brut JSON (forme courante au runtime)."""
    plain_dict = {
        "id": "test",
        "wiki_sections": {
            "Background": "BG content",
            "Personality": "Pers content",
            "Abilities": "Abil content",
        },
    }
    result = get_wiki_sections(
        "test", "en",
        canon_characters={"test": plain_dict},
        base_dir=tmp_wiki_dir,
    )
    assert result["Background"] == "BG content"
    assert result["Personality"] == "Pers content"
    assert result["Abilities"] == "Abil content"


def test_extract_source_from_object_with_attribute(tmp_wiki_dir: Path) -> None:
    """canon_char a un attribut direct .wiki_sections."""
    char = _FakeChar({"Background": "x", "Personality": "y", "Abilities": "z"})
    result = get_wiki_sections(
        "test", "en",
        canon_characters={"test": char},
        base_dir=tmp_wiki_dir,
    )
    assert result == {"Background": "x", "Personality": "y", "Abilities": "z"}


def test_extract_source_from_pydantic_model(tmp_wiki_dir: Path) -> None:
    """canon_char est un Pydantic v2 model avec model_dump()."""
    class _PydanticLike:
        def model_dump(self) -> dict[str, Any]:
            return {
                "wiki_sections": {
                    "Background": "PYD BG",
                    "Personality": "PYD P",
                    "Abilities": "PYD A",
                },
            }

    result = get_wiki_sections(
        "test", "en",
        canon_characters={"test": _PydanticLike()},
        base_dir=tmp_wiki_dir,
    )
    assert result == {"Background": "PYD BG", "Personality": "PYD P", "Abilities": "PYD A"}


def test_extract_source_handles_missing_wiki_sections(tmp_wiki_dir: Path) -> None:
    """canon_char sans wiki_sections : retourne dict vide."""
    plain_dict = {"id": "test"}  # no wiki_sections key
    result = get_wiki_sections(
        "test", "en",
        canon_characters={"test": plain_dict},
        base_dir=tmp_wiki_dir,
    )
    assert result == {"Background": "", "Personality": "", "Abilities": ""}


# === Tests securite : path traversal ===

@pytest.mark.parametrize("malicious_id", [
    "../../etc/passwd",
    "../sibling",
    "id/with/slash",
    "..",
    ".hidden",
    "",
    "id with spaces",
])
def test_cache_path_rejects_unsafe_char_id(tmp_wiki_dir: Path, malicious_id: str) -> None:
    with pytest.raises(ValueError, match="unsafe char_id"):
        cache_path(malicious_id, "ja", tmp_wiki_dir)


@pytest.mark.parametrize("malicious_lang", [
    "../etc",
    "lang/x",
    "..",
    "",
    ".hidden",
])
def test_cache_path_rejects_unsafe_lang(tmp_wiki_dir: Path, malicious_lang: str) -> None:
    with pytest.raises(ValueError, match="unsafe lang"):
        cache_path("valid_id", malicious_lang, tmp_wiki_dir)


@pytest.mark.parametrize("safe_lang", ["en", "fr", "es", "ja", "zh", "ko", "pt-BR", "de"])
def test_cache_path_accepts_all_supported_langs(tmp_wiki_dir: Path, safe_lang: str) -> None:
    p = cache_path("uchiha_sasuke", safe_lang, tmp_wiki_dir)
    assert p == tmp_wiki_dir / safe_lang / "uchiha_sasuke.json"


# === Tests load_default_glossary ===

def test_load_default_glossary_returns_terms() -> None:
    from shinobi.i18n.wiki_translator import load_default_glossary
    terms = load_default_glossary()
    assert isinstance(terms, tuple)
    # Real glossary should have 100+ terms (chakra, jutsu, etc.)
    assert len(terms) >= 50, f"glossary has only {len(terms)} terms, expected >= 50"
    # Should contain canonical Naruto terms
    terms_lower = {t.lower() for t in terms}
    for expected in ["chakra", "ninjutsu", "sharingan", "konohagakure"]:
        assert expected.lower() in terms_lower, f"missing canonical term: {expected}"


def test_load_default_glossary_handles_missing_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Si le glossary est absent, retourne tuple vide sans crasher."""
    from shinobi.i18n import wiki_translator
    monkeypatch.setattr(wiki_translator, "GLOSSARY_PATH", tmp_path / "missing.json")
    terms = wiki_translator.load_default_glossary()
    assert terms == ()


def test_qwen_backend_auto_loads_glossary() -> None:
    """Sans glossary explicit, QwenHttpBackend charge le default."""
    from shinobi.i18n.wiki_translator import QwenHttpBackend
    backend = QwenHttpBackend()
    assert len(backend._glossary) >= 50  # auto-loaded


def test_qwen_backend_explicit_empty_glossary_overrides_default() -> None:
    """Si glossary=[] est passe explicitement, il remplace le default."""
    from shinobi.i18n.wiki_translator import QwenHttpBackend
    backend = QwenHttpBackend(glossary=[])
    assert backend._glossary == ()


def test_qwen_backend_explicit_glossary_used() -> None:
    from shinobi.i18n.wiki_translator import QwenHttpBackend
    backend = QwenHttpBackend(glossary=["foo", "bar"])
    assert backend._glossary == ("foo", "bar")


# === Test backend protocol ===

def test_translator_backend_protocol_raises_not_implemented() -> None:
    backend = TranslatorBackend()
    with pytest.raises(NotImplementedError):
        backend.translate({"Background": "x"}, "ja")


# === Tests QwenHttpBackend (mock httpx) ===

class _FakeHttpResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("err", request=None, response=None)  # type: ignore[arg-type]

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeHttpClient:
    def __init__(self, response: _FakeHttpResponse) -> None:
        self._response = response
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def __enter__(self) -> _FakeHttpClient:
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def post(self, url: str, *, json: dict[str, Any]) -> _FakeHttpResponse:
        self.posts.append((url, json))
        return self._response


def test_qwen_backend_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from shinobi.i18n.wiki_translator import QwenHttpBackend

    expected = {
        "Background": "Naruto est ne...",
        "Personality": "Naruto est joyeux...",
        "Abilities": "Maitrise du Rasengan...",
    }
    fake_response = _FakeHttpResponse({
        "choices": [{"message": {"content": json.dumps(expected, ensure_ascii=False)}}],
    })
    fake_client = _FakeHttpClient(fake_response)
    monkeypatch.setattr("shinobi.i18n.wiki_translator.httpx.Client", lambda **_: fake_client)

    backend = QwenHttpBackend(glossary=("chakra", "Naruto"))
    out = backend.translate({"Background": "Naruto was born...", "Personality": "joyful", "Abilities": "Rasengan"}, "fr")
    assert out == expected
    assert len(fake_client.posts) == 1
    url, payload = fake_client.posts[0]
    assert url.endswith("/v1/chat/completions")
    assert payload["model"]
    assert any("Background" in m["content"] for m in payload["messages"])


def test_qwen_backend_http_error_raises_runtimeerror(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from shinobi.i18n.wiki_translator import QwenHttpBackend

    class _FailingClient:
        def __enter__(self) -> _FailingClient:
            return self
        def __exit__(self, *args: Any) -> None:
            pass
        def post(self, *args: Any, **kwargs: Any) -> Any:
            raise httpx.ConnectError("server down")

    monkeypatch.setattr("shinobi.i18n.wiki_translator.httpx.Client", lambda **_: _FailingClient())
    backend = QwenHttpBackend()
    with pytest.raises(RuntimeError, match="Qwen HTTP backend failed"):
        backend.translate({"Background": "x", "Personality": "x", "Abilities": "x"}, "ja")


def test_qwen_backend_malformed_json_raises_runtimeerror(monkeypatch: pytest.MonkeyPatch) -> None:
    from shinobi.i18n.wiki_translator import QwenHttpBackend

    fake_response = _FakeHttpResponse({
        "choices": [{"message": {"content": "not a json {{{ broken"}}],
    })
    fake_client = _FakeHttpClient(fake_response)
    monkeypatch.setattr("shinobi.i18n.wiki_translator.httpx.Client", lambda **_: fake_client)

    backend = QwenHttpBackend()
    with pytest.raises(RuntimeError, match="JSON parse fail"):
        backend.translate({"Background": "x", "Personality": "x", "Abilities": "x"}, "ja")


def test_qwen_backend_empty_choices_raises_runtimeerror(monkeypatch: pytest.MonkeyPatch) -> None:
    from shinobi.i18n.wiki_translator import QwenHttpBackend

    fake_response = _FakeHttpResponse({"choices": []})
    fake_client = _FakeHttpClient(fake_response)
    monkeypatch.setattr("shinobi.i18n.wiki_translator.httpx.Client", lambda **_: fake_client)

    backend = QwenHttpBackend()
    with pytest.raises(RuntimeError, match="missing 'choices'"):
        backend.translate({"Background": "x", "Personality": "x", "Abilities": "x"}, "ja")


def test_qwen_backend_falls_through_to_pending_marker_when_failing(
    char_with_wiki: _FakeChar, tmp_wiki_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration : un backend Qwen qui echoue declenche le fallback pending."""
    import httpx

    from shinobi.i18n.wiki_translator import QwenHttpBackend

    class _FailingClient:
        def __enter__(self) -> _FailingClient: return self
        def __exit__(self, *args: Any) -> None: pass
        def post(self, *args: Any, **kwargs: Any) -> Any:
            raise httpx.TimeoutException("Qwen down")

    monkeypatch.setattr("shinobi.i18n.wiki_translator.httpx.Client", lambda **_: _FailingClient())
    backend = QwenHttpBackend()
    out = get_wiki_sections(
        "uchiha_sasuke", "ja",
        canon_characters={"uchiha_sasuke": char_with_wiki},
        backend=backend, base_dir=tmp_wiki_dir,
    )
    # Source EN returned
    assert out["Background"] == "Born in Konoha. Lost his family young."
    # Cache marked pending
    cached = load_cached("uchiha_sasuke", "ja", tmp_wiki_dir)
    assert cached is not None
    assert cached.get(PENDING_MARKER_KEY) is True
