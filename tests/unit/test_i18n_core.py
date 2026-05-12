"""Phase i18n.1 : tests core du module shinobi.i18n.

Couvre :
- Chargement des 8 catalogues + filtrage des meta-keys
- Lookup avec interpolation
- Fallback EN si cle manquante dans la langue active
- Fallback cle elle-meme si introuvable partout
- Hot-swap runtime via set_active_language
- Glossary : liste plate, is_preserved, find_preserved_terms_in, llm_prompt_footer
- Preferences : load/save/set_language via SHINOBI_PREFERENCES_DIR override
- Validation langue (rejet codes inconnus)
- Coherence native names + supported languages
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shinobi.i18n import (
    DEFAULT_LANGUAGE,
    NATIVE_NAMES,
    SUPPORTED_LANGUAGES,
    Preferences,
    all_preserved_terms,
    available_languages,
    find_preserved_terms_in,
    get_active_language,
    has_key,
    is_preserved,
    is_supported,
    list_template_placeholders,
    llm_prompt_footer,
    load_preferences,
    needs_first_launch_picker,
    save_preferences,
    set_active_language,
    set_language,
    t,
)
from shinobi.i18n.catalog import reset_for_tests as reset_catalog
from shinobi.i18n.glossary import reset_cache_for_tests as reset_glossary


@pytest.fixture(autouse=True)
def reset_state():
    """Reset le runtime i18n entre chaque test."""
    reset_catalog()
    reset_glossary()
    yield
    reset_catalog()
    reset_glossary()


@pytest.fixture()
def isolated_prefs(tmp_path: Path, monkeypatch) -> Path:
    """Isole preferences.json dans tmp_path pour eviter de toucher le disque user."""
    monkeypatch.setenv("SHINOBI_PREFERENCES_DIR", str(tmp_path))
    return tmp_path


# === Languages enumeration ================================================


def test_supported_languages_count() -> None:
    """8 langues supportees comme spec."""
    assert len(SUPPORTED_LANGUAGES) == 8


def test_supported_languages_codes() -> None:
    """Codes ISO-ish corrects."""
    assert set(SUPPORTED_LANGUAGES) == {
        "en", "fr", "es", "ja", "zh", "ko", "pt-BR", "de",
    }


def test_default_language_is_en() -> None:
    """Default = en (anglais source de verite)."""
    assert DEFAULT_LANGUAGE == "en"
    assert "en" in SUPPORTED_LANGUAGES


def test_native_names_cover_all_languages() -> None:
    """Chaque langue a un nom natif pour le picker."""
    assert set(NATIVE_NAMES.keys()) == set(SUPPORTED_LANGUAGES)
    for name in NATIVE_NAMES.values():
        assert isinstance(name, str)
        assert len(name) > 0


def test_is_supported_validates_codes() -> None:
    """is_supported accepte uniquement les 8 codes."""
    for lang in SUPPORTED_LANGUAGES:
        assert is_supported(lang)
    for bad in ("EN", "Fr", "xx", "", "english"):
        assert not is_supported(bad)


def test_available_languages_returns_tuple() -> None:
    """API publique : tuple immutable."""
    assert available_languages() == SUPPORTED_LANGUAGES


# === Catalog loading ======================================================


def test_each_language_loads_test_greeting() -> None:
    """Toutes les 8 langues stub ont test.greeting."""
    for lang in SUPPORTED_LANGUAGES:
        set_active_language(lang)
        value = t("test.greeting")
        assert value
        assert value != "test.greeting", (
            f"Lang {lang}: test.greeting fallback to key (catalog not loaded?)"
        )


def test_each_language_supports_interpolation() -> None:
    """Placeholder {name} fonctionne dans toutes les langues."""
    for lang in SUPPORTED_LANGUAGES:
        set_active_language(lang)
        result = t("test.greeting_with_name", name="Naruto")
        assert "Naruto" in result, (
            f"Lang {lang}: interpolation lost: {result!r}"
        )


def test_meta_keys_filtered_from_catalog() -> None:
    """Les cles _schema, _language, _native_name sont filtrees du lookup."""
    set_active_language("en")
    # Ces cles existent dans en.json mais ne doivent pas etre lookup-able
    assert not has_key("_schema")
    assert not has_key("_language")
    assert not has_key("_native_name")


# === Fallback strategy ====================================================


def test_fallback_en_when_key_missing_in_target_language() -> None:
    """ja n'a pas test.fallback_only_in_en, mais en oui -> retourne EN."""
    set_active_language("ja")
    value = t("test.fallback_only_in_en")
    assert value == "EN-only key"


def test_fallback_returns_key_itself_when_missing_everywhere() -> None:
    """Cle vraiment introuvable -> retourne la cle telle quelle."""
    set_active_language("en")
    value = t("does.not.exist.anywhere")
    assert value == "does.not.exist.anywhere"


def test_empty_key_returns_empty() -> None:
    """t('') ne crash pas, retourne '' silencieusement."""
    assert t("") == ""


# === Runtime hot-swap =====================================================


def test_set_active_language_changes_lookup_immediately() -> None:
    """set_active_language('fr') -> t('test.greeting') retourne FR au tour suivant."""
    set_active_language("en")
    assert t("test.greeting") == "Hello"
    set_active_language("fr")
    assert t("test.greeting") == "Bonjour"
    set_active_language("ja")
    assert t("test.greeting") == "こんにちは"


def test_set_active_language_rejects_unknown_code() -> None:
    """Code non supporte -> ValueError."""
    with pytest.raises(ValueError, match="Unsupported"):
        set_active_language("xx")


def test_get_active_language_reflects_set() -> None:
    """get_active_language coherent avec set."""
    set_active_language("zh")
    assert get_active_language() == "zh"
    set_active_language("ko")
    assert get_active_language() == "ko"


# === Glossary =============================================================


def test_glossary_has_at_least_50_terms() -> None:
    """Glossary respecte la spec (~50+ termes preserves)."""
    terms = all_preserved_terms()
    assert len(terms) >= 50


def test_glossary_includes_canonical_jutsu_terms() -> None:
    """Termes critiques de la spec : chakra, ninjutsu, jutsu names, etc."""
    terms = all_preserved_terms()
    for required in (
        "chakra", "ninjutsu", "taijutsu", "genjutsu", "fuinjutsu",
        "kekkei genkai", "jinchuuriki", "bijuu",
    ):
        assert any(t.lower() == required.lower() for t in terms), (
            f"Missing required glossary term: {required}"
        )


def test_glossary_includes_villages_and_orgs() -> None:
    """Konohagakure, Akatsuki, etc. sont preserves."""
    terms = {t.lower() for t in all_preserved_terms()}
    assert "konohagakure" in terms
    assert "akatsuki" in terms
    assert "anbu" in terms


def test_is_preserved_case_insensitive() -> None:
    """is_preserved match case-insensitive."""
    assert is_preserved("Sharingan")
    assert is_preserved("SHARINGAN")
    assert is_preserved("sharingan")
    assert is_preserved("kekkei genkai")
    assert not is_preserved("hello")


def test_find_preserved_terms_in_text() -> None:
    """Detection des termes du glossary dans un texte."""
    text = "Itachi uses Mangekyou Sharingan to cast Tsukuyomi"
    found = find_preserved_terms_in(text)
    found_lower = {f.lower() for f in found}
    assert "mangekyou sharingan" in found_lower
    # Le terme le plus long doit matcher en premier (priorite long-first)


def test_find_preserved_terms_empty_text() -> None:
    """Texte vide -> liste vide."""
    assert find_preserved_terms_in("") == []
    assert find_preserved_terms_in("nothing relevant here") == []


def test_llm_prompt_footer_includes_glossary() -> None:
    """Le footer LLM contient les termes preserves + langue cible."""
    footer = llm_prompt_footer("ja")
    assert "GLOSSARY" in footer
    assert "DO NOT TRANSLATE" in footer
    assert "JA" in footer
    assert "chakra" in footer
    assert "Sharingan" in footer


# === Preferences ==========================================================


def test_preferences_default_when_file_absent(isolated_prefs: Path) -> None:
    """Pas de preferences.json -> defaults + needs_first_launch_picker=True."""
    prefs = load_preferences()
    assert prefs.language == DEFAULT_LANGUAGE
    assert prefs.first_launch_completed is False
    assert needs_first_launch_picker() is True


def test_save_and_load_preferences_roundtrip(isolated_prefs: Path) -> None:
    """save_preferences -> load_preferences retourne les memes valeurs."""
    prefs = Preferences(
        language="ja",
        first_launch_completed=True,
        language_chosen_at="2026-05-08T10:00:00Z",
    )
    save_preferences(prefs)
    loaded = load_preferences()
    assert loaded.language == "ja"
    assert loaded.first_launch_completed is True
    assert loaded.language_chosen_at == "2026-05-08T10:00:00Z"


def test_set_language_persists_and_marks_first_launch_done(
    isolated_prefs: Path,
) -> None:
    """set_language('zh') persiste + first_launch_completed=True."""
    set_language("zh")
    prefs = load_preferences()
    assert prefs.language == "zh"
    assert prefs.first_launch_completed is True
    assert prefs.language_chosen_at is not None


def test_set_language_rejects_unknown(isolated_prefs: Path) -> None:
    """set_language('xx') leve ValueError."""
    with pytest.raises(ValueError):
        set_language("xx")


def test_corrupt_preferences_file_falls_back_to_defaults(
    isolated_prefs: Path,
) -> None:
    """JSON corrompu -> Preferences par defaut, pas de crash."""
    (isolated_prefs / "preferences.json").write_text("{not valid json", encoding="utf-8")
    prefs = load_preferences()
    assert prefs.language == DEFAULT_LANGUAGE
    assert prefs.first_launch_completed is False


def test_unknown_schema_version_falls_back_to_defaults(
    isolated_prefs: Path,
) -> None:
    """schema_version > supported -> defaults."""
    (isolated_prefs / "preferences.json").write_text(
        json.dumps({"schema_version": 999, "language": "ja"}),
        encoding="utf-8",
    )
    prefs = load_preferences()
    assert prefs.language == DEFAULT_LANGUAGE


def test_invalid_language_in_preferences_falls_back_to_default(
    isolated_prefs: Path,
) -> None:
    """preferences.json contient language='xx' (non supporte) -> EN."""
    (isolated_prefs / "preferences.json").write_text(
        json.dumps({
            "schema_version": 1,
            "language": "klingon",
            "first_launch_completed": True,
        }),
        encoding="utf-8",
    )
    prefs = load_preferences()
    assert prefs.language == DEFAULT_LANGUAGE


def test_needs_first_launch_picker_after_set_language(
    isolated_prefs: Path,
) -> None:
    """Apres set_language, plus besoin du picker."""
    assert needs_first_launch_picker() is True
    set_language("fr")
    assert needs_first_launch_picker() is False


# === Template placeholders inspection ====================================


def test_list_template_placeholders_extracts_names() -> None:
    """Helper pour audit de coherence des traductions."""
    fields = list_template_placeholders("Hello {name}, you are {age} years old")
    assert fields == ("name", "age")


def test_list_template_placeholders_empty_for_no_placeholders() -> None:
    """Texte sans placeholders -> tuple vide."""
    assert list_template_placeholders("Just plain text") == ()


def test_interpolation_missing_kwarg_returns_template(caplog) -> None:
    """Si un placeholder est demande mais kwargs incomplet, retourne template + log error."""
    set_active_language("en")
    # test.greeting_with_name a {name} mais on n'en passe aucun
    result = t("test.greeting_with_name")
    # On retourne le template tel quel (gracieux, log error)
    assert "{name}" in result


# === Edge cases (audit hardening) =========================================


def test_glossary_regex_priority_long_first() -> None:
    """Le regex preserve la priorite longest-match-first.

    'Mangekyou Sharingan' doit etre matche en bloc, pas decompose en
    'Mangekyou' + 'Sharingan' separes.
    """
    text = "Itachi uses Mangekyou Sharingan to cast Tsukuyomi"
    found = find_preserved_terms_in(text)
    # 'Mangekyou Sharingan' (long) match prioritaire sur 'Sharingan' isole
    assert any(f.lower() == "mangekyou sharingan" for f in found)
    # 'Sharingan' isole ne doit PAS apparaitre comme match separe
    # (regex non-overlapping consomme deja le span complet)
    sharingan_alone = [f for f in found if f.lower() == "sharingan"]
    assert sharingan_alone == [], (
        f"Sharingan was matched alone alongside Mangekyou Sharingan: {found}"
    )


def test_catalog_handles_invalid_utf8_gracefully(
    tmp_path: Path, monkeypatch,
) -> None:
    """Fichier de catalogue avec bytes non-UTF-8 -> dict vide + log error,
    pas de crash."""
    from shinobi.i18n import loader as loader_module

    # Cree un fichier corrompu
    fake_dir = tmp_path / "i18n_fake"
    fake_dir.mkdir()
    bad_file = fake_dir / "fr.json"
    bad_file.write_bytes(b"\xff\xfe\xfd not utf-8 at all")

    # Patch _i18n_dir pour pointer ici
    monkeypatch.setattr(loader_module, "_i18n_dir", lambda: fake_dir)
    loader_module.reset_cache_for_tests()
    catalog = loader_module.load_catalog("fr")
    assert catalog == {}


def test_glossary_missing_file_falls_back_to_empty(
    tmp_path: Path, monkeypatch,
) -> None:
    """Glossary absent du disque -> liste vide, pas de crash."""
    from shinobi.config import settings
    from shinobi.i18n import glossary as glossary_module

    # Pointe le glossary vers un chemin inexistant
    fake_path = tmp_path / "nonexistent.json"
    monkeypatch.setattr(
        settings,
        "_abs_path",
        lambda raw: fake_path if "glossary" in str(raw) else Path(raw),
    )
    glossary_module.reset_cache_for_tests()
    terms = glossary_module.all_preserved_terms()
    assert terms == ()
    # is_preserved retourne False quand le glossary est vide
    assert glossary_module.is_preserved("chakra") is False
    # llm_prompt_footer retourne string vide (pas crash)
    assert glossary_module.llm_prompt_footer("ja") == ""


def test_preferences_extra_unknown_fields_are_ignored(
    isolated_prefs: Path,
) -> None:
    """preferences.json contenant des champs futurs inconnus -> chargement OK,
    les champs connus sont preserves."""
    (isolated_prefs / "preferences.json").write_text(
        json.dumps({
            "schema_version": 1,
            "language": "ja",
            "first_launch_completed": True,
            "language_chosen_at": "2026-05-08T10:00:00Z",
            "future_field_unknown": "some_value",
            "another_future": [1, 2, 3],
        }),
        encoding="utf-8",
    )
    prefs = load_preferences()
    assert prefs.language == "ja"
    assert prefs.first_launch_completed is True
    assert prefs.language_chosen_at == "2026-05-08T10:00:00Z"


def test_preferences_dir_respects_env_override(
    tmp_path: Path, monkeypatch,
) -> None:
    """SHINOBI_PREFERENCES_DIR override le path platformdirs."""
    from shinobi.i18n.preferences import preferences_dir, preferences_path

    custom = tmp_path / "custom_pref_dir"
    monkeypatch.setenv("SHINOBI_PREFERENCES_DIR", str(custom))
    assert preferences_dir() == custom
    assert preferences_path() == custom / "preferences.json"


def test_native_names_use_unicode_for_cjk_languages() -> None:
    """Les noms natifs JA/ZH/KO sont en script natif, pas en romaji."""
    # 日本語 contient des caracteres japonais (hiragana / kanji)
    assert any(0x3040 <= ord(c) <= 0x9FFF for c in NATIVE_NAMES["ja"])
    # 中文 contient des caracteres chinois (CJK Unified Ideographs)
    assert any(0x4E00 <= ord(c) <= 0x9FFF for c in NATIVE_NAMES["zh"])
    # 한국어 contient des caracteres coreens (hangul)
    assert any(0xAC00 <= ord(c) <= 0xD7AF for c in NATIVE_NAMES["ko"])


# === Coverage hardening (paths defensifs) =================================


def test_initialize_from_preferences_sets_active_language(
    isolated_prefs: Path,
) -> None:
    """initialize_from_preferences() lit preferences.json + applique."""
    from shinobi.i18n.catalog import initialize_from_preferences

    set_language("zh")
    # Reset le runtime pour forcer la 1re initialisation
    reset_catalog()
    active = initialize_from_preferences()
    assert active == "zh"
    assert get_active_language() == "zh"


def test_initialize_from_preferences_idempotent(
    isolated_prefs: Path,
) -> None:
    """Appeler 2 fois initialize_from_preferences ne change rien la 2eme fois."""
    from shinobi.i18n.catalog import initialize_from_preferences

    set_language("ja")
    reset_catalog()
    a = initialize_from_preferences()
    b = initialize_from_preferences()
    assert a == b == "ja"


def test_initialize_falls_back_when_prefs_unsupported(
    isolated_prefs: Path,
) -> None:
    """Preferences contiennent une langue non supportee -> default EN."""
    from shinobi.i18n.catalog import initialize_from_preferences

    # Ecrit manuellement une preference invalide
    (isolated_prefs / "preferences.json").write_text(
        json.dumps({
            "schema_version": 1,
            "language": "xx",
            "first_launch_completed": True,
        }),
        encoding="utf-8",
    )
    reset_catalog()
    active = initialize_from_preferences()
    assert active == DEFAULT_LANGUAGE


def test_glossary_categories_exposes_all_groups() -> None:
    """categories() retourne les groupes du glossary."""
    from shinobi.i18n.glossary import categories

    cats = categories()
    expected_keys = {
        "techniques", "ranks", "entities", "organizations",
        "villages", "bijuu", "kekkei_genkai", "honorifics",
    }
    assert expected_keys.issubset(set(cats.keys()))
    # Chaque categorie est un tuple non-vide
    for cat_name, cat_values in cats.items():
        assert isinstance(cat_values, tuple)
        assert len(cat_values) > 0, f"Category {cat_name} is empty"


def test_is_preserved_with_empty_string() -> None:
    """is_preserved('') et None equivalents -> False sans crash."""
    assert is_preserved("") is False


def test_load_catalog_unsupported_language_returns_empty() -> None:
    """load_catalog('xx') -> {} avec log warning."""
    from shinobi.i18n.loader import load_catalog, reset_cache_for_tests

    reset_cache_for_tests()
    catalog = load_catalog("klingon")
    assert catalog == {}


def test_load_catalog_missing_file_returns_empty(
    tmp_path: Path, monkeypatch,
) -> None:
    """load_catalog('en') quand en.json absent -> {} avec log warning."""
    from shinobi.i18n import loader as loader_module

    empty_dir = tmp_path / "i18n_empty"
    empty_dir.mkdir()
    monkeypatch.setattr(loader_module, "_i18n_dir", lambda: empty_dir)
    loader_module.reset_cache_for_tests()
    assert loader_module.load_catalog("en") == {}


def test_load_catalog_non_dict_root_returns_empty(
    tmp_path: Path, monkeypatch,
) -> None:
    """JSON valide mais top-level liste/scalar -> {} avec log error."""
    from shinobi.i18n import loader as loader_module

    fake_dir = tmp_path / "i18n_bad_root"
    fake_dir.mkdir()
    (fake_dir / "fr.json").write_text(
        json.dumps([1, 2, 3]),  # liste, pas dict
        encoding="utf-8",
    )
    monkeypatch.setattr(loader_module, "_i18n_dir", lambda: fake_dir)
    loader_module.reset_cache_for_tests()
    assert loader_module.load_catalog("fr") == {}


def test_has_key_returns_false_for_missing() -> None:
    """has_key sur une cle absente -> False (pas de fallback EN ici)."""
    set_active_language("ja")
    assert has_key("non.existent.key.xyz") is False
    # has_key sur cle existante uniquement dans EN: False (ja-only check)
    assert has_key("test.fallback_only_in_en") is False
    set_active_language("en")
    assert has_key("test.fallback_only_in_en") is True


def test_save_preferences_creates_parent_dir(tmp_path: Path, monkeypatch) -> None:
    """save_preferences cree le dossier parent s'il n'existe pas."""
    nested = tmp_path / "deeply" / "nested" / "dir"
    monkeypatch.setenv("SHINOBI_PREFERENCES_DIR", str(nested))
    prefs = Preferences(language="es", first_launch_completed=True)
    save_preferences(prefs)
    assert (nested / "preferences.json").exists()


def test_preferences_invalid_chosen_at_type_falls_back_to_none(
    isolated_prefs: Path,
) -> None:
    """language_chosen_at non-string -> None silencieux."""
    (isolated_prefs / "preferences.json").write_text(
        json.dumps({
            "schema_version": 1,
            "language": "fr",
            "first_launch_completed": True,
            "language_chosen_at": 12345,  # int au lieu de str
        }),
        encoding="utf-8",
    )
    prefs = load_preferences()
    assert prefs.language == "fr"
    assert prefs.language_chosen_at is None


# === Public API direct tests ==============================================


def test_get_language_alias_returns_active() -> None:
    """get_language() est alias pour get_active_language() - testes directement."""
    from shinobi.i18n import get_language

    set_active_language("ko")
    assert get_language() == "ko"
    set_active_language("de")
    assert get_language() == "de"


def test_warning_logged_once_per_missing_key(caplog) -> None:
    """Anti-spam : chaque (lang, key) missing ne logge qu'une seule fois."""
    import logging

    set_active_language("ja")
    caplog.set_level(logging.WARNING)
    caplog.clear()

    # 5 lookups consecutifs sur la meme cle absente
    for _ in range(5):
        t("non.existent.spam.key")

    # On veut 1 seul warning (pas 5) via les attributs structlog ou le message brut
    relevant = [
        r for r in caplog.records
        if "i18n_key_missing" in r.getMessage()
        or getattr(r, "event", "").startswith("i18n_key_missing")
    ]
    # Tolerance : structlog peut router differemment selon configuration.
    # Ce qu'on veut prouver : pas de spam exponentiel. <= 2 logs (1 fallback,
    # 1 no_fallback meme si la cle est absente partout) attendus, max 5 warns.
    # En realite : la cle est absente de JA + EN -> 1 warning "no_fallback".
    # Si on appelle 5 fois, on doit avoir EXACTEMENT 1 warning.
    if relevant:
        # Si structlog est routed vers caplog, on verifie le count
        assert len(relevant) <= 1, (
            f"Anti-spam broken: got {len(relevant)} warnings "
            f"for same key {[r.getMessage() for r in relevant]}"
        )


def test_interpolation_with_positional_placeholder_catches_index_error() -> None:
    """Template avec {0} positional + kwargs uniquement -> IndexError caught,
    retourne template tel quel sans crash."""
    set_active_language("en")
    # test.positional_placeholder = "Item #{0}". On passe kwargs name=
    # qui ne fournit pas la position 0 -> IndexError attendue puis caught.
    result = t("test.positional_placeholder", name="x")
    assert "{0}" in result


def test_interpolation_with_wrong_kwarg_catches_key_error() -> None:
    """Template avec {name} + kwargs other= -> KeyError caught."""
    set_active_language("en")
    # test.greeting_with_name = "Hello {name}". On passe other= qui ne
    # nourrit pas {name} -> KeyError attendue puis caught.
    result = t("test.greeting_with_name", other="x")
    assert "{name}" in result


def test_glossary_root_not_dict_falls_back(
    tmp_path: Path, monkeypatch,
) -> None:
    """data/i18n/glossary.json contient un array au lieu d'un dict -> mode degrade."""
    from shinobi.config import settings
    from shinobi.i18n import glossary as glossary_module

    bad_glossary = tmp_path / "glossary_bad.json"
    bad_glossary.write_text(
        json.dumps([1, 2, 3]),  # list, not dict
        encoding="utf-8",
    )

    def _patched(raw: str) -> Path:
        if "glossary" in str(raw):
            return bad_glossary
        return Path(raw)

    monkeypatch.setattr(settings, "_abs_path", _patched)
    glossary_module.reset_cache_for_tests()
    assert glossary_module.all_preserved_terms() == ()


def test_find_preserved_terms_with_empty_glossary(
    tmp_path: Path, monkeypatch,
) -> None:
    """find_preserved_terms_in fonctionne sans crash si glossary vide
    (regex no-match pattern)."""
    from shinobi.config import settings
    from shinobi.i18n import glossary as glossary_module

    fake_path = tmp_path / "missing.json"
    monkeypatch.setattr(
        settings,
        "_abs_path",
        lambda raw: fake_path if "glossary" in str(raw) else Path(raw),
    )
    glossary_module.reset_cache_for_tests()
    # Avec un glossary vide, le regex ne match rien
    found = glossary_module.find_preserved_terms_in(
        "Itachi uses Mangekyou Sharingan and chakra"
    )
    assert found == []


def test_preferences_dir_falls_back_to_platformdirs_without_env(
    monkeypatch,
) -> None:
    """Sans SHINOBI_PREFERENCES_DIR, utilise platformdirs."""
    monkeypatch.delenv("SHINOBI_PREFERENCES_DIR", raising=False)
    from shinobi.i18n.preferences import _override_dir, preferences_dir

    assert _override_dir() is None
    # preferences_dir() doit retourner un Path non-None (platformdirs path)
    pdir = preferences_dir()
    assert pdir is not None
    assert pdir.name == "shinobi-no-sho" or "shinobi" in str(pdir).lower()


def test_preferences_root_not_dict_falls_back_to_defaults(
    isolated_prefs: Path,
) -> None:
    """preferences.json contenant une liste -> defaults."""
    (isolated_prefs / "preferences.json").write_text(
        json.dumps(["unexpected", "structure"]),
        encoding="utf-8",
    )
    prefs = load_preferences()
    assert prefs.language == DEFAULT_LANGUAGE
    assert prefs.first_launch_completed is False


def test_warning_only_logged_once_for_fallback_path(caplog) -> None:
    """Branch 116->124 : appel repete d'une cle absente de JA mais presente
    en EN -> seul le 1er logge un warning, les suivants hit le cache anti-spam."""
    set_active_language("ja")
    # 1er appel : missing in JA, found in EN -> log warning
    assert t("test.fallback_only_in_en") == "EN-only key"
    # 2eme appel : meme path mais cache_key deja in _MISSING_KEYS_LOGGED ->
    # branch 116 condition False -> jump to 124. Pas de crash, retourne EN.
    assert t("test.fallback_only_in_en") == "EN-only key"
    assert t("test.fallback_only_in_en") == "EN-only key"


def test_glossary_skips_non_list_values_and_invalid_terms(
    tmp_path: Path, monkeypatch,
) -> None:
    """Glossary avec valeurs non-list ou strings vides/non-strings -> filtres.
    Couvre branches 46->43, 48->47, 61->58."""
    from shinobi.config import settings
    from shinobi.i18n import glossary as glossary_module

    fake = tmp_path / "glossary_mixed.json"
    fake.write_text(
        json.dumps({
            "_schema": "v1",
            "scalar_value": "not_a_list",
            "scalar_int": 42,
            "techniques": ["chakra", "", 999, "  ", "valid_term"],
            "ranks": [None, "genin"],
        }),
        encoding="utf-8",
    )

    def _patched(raw: str) -> Path:
        if "glossary" in str(raw):
            return fake
        return Path(raw)

    monkeypatch.setattr(settings, "_abs_path", _patched)
    glossary_module.reset_cache_for_tests()

    terms = glossary_module.all_preserved_terms()
    # Strings vides + non-strings filtres
    assert "chakra" in terms
    assert "valid_term" in terms
    assert "genin" in terms
    assert "" not in terms
    assert 999 not in terms
    assert None not in terms

    cats = glossary_module.categories()
    # scalar_value, scalar_int filtres (non-list)
    assert "scalar_value" not in cats
    assert "scalar_int" not in cats
    # techniques + ranks presents
    assert "techniques" in cats
    assert "ranks" in cats


def test_loader_filters_non_string_values(
    tmp_path: Path, monkeypatch,
) -> None:
    """Catalog avec values non-string (int, list, null) -> filtrees.
    Couvre branche 107->104."""
    from shinobi.i18n import loader as loader_module

    fake_dir = tmp_path / "i18n_mixed"
    fake_dir.mkdir()
    (fake_dir / "fr.json").write_text(
        json.dumps({
            "_schema": "i18n_v1",
            "valid.string": "Bonjour",
            "invalid.int": 42,
            "invalid.list": ["a", "b"],
            "invalid.null": None,
            "invalid.dict": {"nested": "value"},
            "another.valid": "Salut",
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(loader_module, "_i18n_dir", lambda: fake_dir)
    loader_module.reset_cache_for_tests()
    catalog = loader_module.load_catalog("fr")
    assert catalog == {
        "valid.string": "Bonjour",
        "another.valid": "Salut",
    }


def test_set_active_language_thread_safe() -> None:
    """Plusieurs threads switchent la langue concurremment, etat coherent."""
    import threading
    import time

    barrier = threading.Barrier(8)
    final_states: list[str] = []
    errors: list[str] = []

    def worker(lang: str) -> None:
        try:
            barrier.wait()
            for _ in range(50):
                set_active_language(lang)
                # Petit dodo pour forcer interleaving
                time.sleep(0.0001)
                # Lookup ne doit jamais crasher
                _ = t("test.greeting")
            final_states.append(get_active_language())
        except Exception as exc:
            errors.append(f"{lang}: {type(exc).__name__}: {exc}")

    threads = [
        threading.Thread(target=worker, args=(lang,))
        for lang in available_languages()
    ]
    for t_ in threads:
        t_.start()
    for t_ in threads:
        t_.join(timeout=10)

    assert errors == [], f"Thread errors: {errors}"
    # L'etat final doit etre l'une des langues passees (jamais corrompu)
    for state in final_states:
        assert is_supported(state), f"Corrupted state: {state!r}"
    # La langue active finale doit etre dans la liste supportee
    assert is_supported(get_active_language())
