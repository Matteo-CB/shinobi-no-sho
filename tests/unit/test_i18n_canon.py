"""Phase i18n.11 : tests cross-langue canon (character summary).

Verifie que `localize_name` / `localize_description` resolvent correctement
dans chacune des 8 langues + que les fallback respectent l'ordre attendu.
"""

from __future__ import annotations

import pytest

from shinobi.api.i18n_helpers import localize_description, localize_name


class _FakeCanonChar:
    """Mock minimal : nomme par lang_suffix attributes."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_canon_character_summary_localized(lang: str) -> None:
    """`localize_name` retourne `name_<lang>` si dispo, sinon fallback chain."""
    char = _FakeCanonChar(
        name_romaji="Uchiha Itachi",
        name_fr="Uchiwa Itachi",
        name_en="Itachi Uchiha",
        name_ja="うちはイタチ",
        name_de="Uchiha Itachi (de)",
        name_es="Uchiha Itachi (es)",
        name_zh="宇智波 鼬",
        name_ko="우치하 이타치",
        **{"name_pt-BR": "Uchiha Itachi (pt-BR)"},
    )
    out = localize_name(char)
    assert out == getattr(char, f"name_{lang.replace('-', '_')}", None) \
        or out == getattr(char, f"name_{lang}", None), (
        f"localize_name({lang}) returned {out!r}"
    )


@pytest.mark.parametrize(
    "lang_with_only_fr",
    [
        # On ne fournit que name_fr : tous les autres lang doivent fallback FR.
        {"name_fr": "Uchiwa Itachi"},
    ],
)
def test_canon_character_localize_fallback_chain(
    lang: str, lang_with_only_fr: dict,
) -> None:
    """Si seul `name_fr` existe, toutes les langues retombent dessus."""
    char = _FakeCanonChar(**lang_with_only_fr)
    out = localize_name(char)
    assert out == "Uchiwa Itachi", (
        f"fallback should resolve to name_fr for lang={lang}, got {out!r}"
    )


def test_canon_character_description_localized(lang: str) -> None:
    """`localize_description` resout avec la meme chaine de fallback que name."""
    char = _FakeCanonChar(
        description_fr="Un genie tragique",
        description_en="A tragic genius",
        description_ja="悲劇の天才",
    )
    out = localize_description(char)
    expected_map = {
        "fr": "Un genie tragique",
        "en": "A tragic genius",
        "ja": "悲劇の天才",
        # autres lang : fallback FR (premier dans la chaine fallback)
        "es": "Un genie tragique",
        "zh": "Un genie tragique",
        "ko": "Un genie tragique",
        "pt-BR": "Un genie tragique",
        "de": "Un genie tragique",
    }
    assert out == expected_map[lang], (
        f"description for lang={lang} got {out!r}, expected {expected_map[lang]!r}"
    )


def test_canon_character_romaji_is_never_translated(lang: str) -> None:
    """`name_romaji` est preserve verbatim dans toutes les langues (jamais
    traduit). Test : si on n'a que name_romaji, localize_name le retourne."""
    char = _FakeCanonChar(name_romaji="Uchiha Itachi")
    out = localize_name(char)
    assert out == "Uchiha Itachi", (
        f"name_romaji should be returned verbatim for lang={lang}, got {out!r}"
    )
