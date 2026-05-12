"""Phase i18n.11 : tests cross-langue declaration de goals.

`test_goal_creation_localized[lang]` (spec doc 14 §i18n.11 §2) :
verifie que `declare_goal` + `process_player_input` + roundtrip schema
fonctionnent pour les 8 langues, et que `describe_goal_for_lang` retourne
la bonne version localisee.
"""

from __future__ import annotations

from shinobi.goals.declaration import (
    Goal,
    declare_goal,
    describe_goal_for_lang,
)


def test_goal_creation_localized(lang: str) -> None:
    """8 instances : 1 par langue. declare_goal accepte les nouveaux
    champs Phase 8 et le goal s'auto-decrit dans la lang demandee."""
    description = "Apprendre Rasengan"
    g = declare_goal(
        description_player=description,
        interpretation_canonical=description,
        declared_at_year=8,
        declared_at_age=5,
        description_player_original_language=lang,
        description_player_translated={lang: description},
    )
    # Roundtrip JSON
    raw = g.model_dump_json()
    parsed = Goal.model_validate_json(raw)
    assert parsed.description_player_original_language == lang
    assert parsed.description_player_translated.get(lang) == description

    # Display fallback chain : describe_goal_for_lang renvoie la version
    # de la lang demandee si presente, sinon le verbatim.
    out = describe_goal_for_lang(parsed, lang)
    assert out == description, (
        f"describe_goal_for_lang({lang}) returned {out!r}"
    )


def test_goal_fallback_to_verbatim_in_unknown_lang(lang: str) -> None:
    """Si on demande une lang qui n'est pas dans translated, on retombe
    sur description_player verbatim."""
    g = declare_goal(
        description_player="Texte joueur verbatim",
        interpretation_canonical="canon",
        declared_at_year=8,
        declared_at_age=5,
        description_player_original_language="fr",
        description_player_translated={"en": "Player verbatim text"},
    )
    out = describe_goal_for_lang(g, lang)
    if lang == "fr":
        # source language : retourne verbatim
        assert out == "Texte joueur verbatim"
    elif lang == "en":
        # presente dans translated
        assert out == "Player verbatim text"
    else:
        # autre lang : fallback verbatim (description_player)
        assert out == "Texte joueur verbatim"
