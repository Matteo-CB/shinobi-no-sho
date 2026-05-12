"""Phase i18n.11 : tests cross-langue moteur (outcome labels).

Verifie que les libelles d'outcome sont traduits dans toutes les langues
et que l'interpolation `{summary}` fonctionne.
"""

from __future__ import annotations

import pytest

from shinobi.i18n.catalog import t

OUTCOME_KEYS = (
    "engine.actions.outcome.full_success_brilliant",
    "engine.actions.outcome.full_success",
    "engine.actions.outcome.partial_success",
    "engine.actions.outcome.minor_failure",
    "engine.actions.outcome.catastrophic_failure",
)


@pytest.mark.parametrize("key", OUTCOME_KEYS)
def test_outcome_label_localized(lang: str, key: str) -> None:
    """5 outcome labels x 8 langs = 40 cas. Chaque label est non vide et
    interpole `{summary}` correctement."""
    summary_text = "le joueur frappe"
    rendered = t(key, summary=summary_text)
    assert rendered, f"empty outcome label for {key} in {lang}"
    assert summary_text in rendered, (
        f"placeholder {{summary}} not interpolated in {key} for {lang}"
    )
    assert rendered != key, f"raw key returned: {key} for {lang}"


def test_outcome_labels_differ_between_langs() -> None:
    """Cross-check : le label outcome.full_success doit differer entre EN et JA.
    Garantit qu'on n'expose pas accidentellement la meme chaine partout.
    """
    from shinobi.i18n.catalog import set_active_language

    set_active_language("en")
    en = t("engine.actions.outcome.full_success", summary="x")
    set_active_language("ja")
    ja = t("engine.actions.outcome.full_success", summary="x")
    set_active_language("en")  # reset
    assert en != ja, (
        f"EN and JA outcome label should differ. en={en!r} ja={ja!r}"
    )
