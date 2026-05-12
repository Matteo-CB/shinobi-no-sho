"""Phase i18n.11 : tests cross-langue CLI.

Verifie que les chaines CLI essentielles sont presentes et non triviales
dans chacune des 8 langues supportees.

La fixture `lang` (cf. tests/conftest.py) parametrise sur les 8 langues.
Chaque test ci-dessous est donc execute 8 fois.
"""

from __future__ import annotations

from shinobi.i18n.catalog import t


def test_cli_app_help_localized(lang: str) -> None:
    """Les chaines CLI principales sont resolues (non vides + != cle brute)."""
    keys = (
        "cli.app.banner.title",
        "cli.app.banner.subtitle",
        "cli.app.no_save_exists",
        "cli.app.version_line",
        "cli.app.list.empty",
    )
    for key in keys:
        value = t(key, version="0.0.0", save_id="x", path="x", count=0)
        assert isinstance(value, str)
        assert value, f"empty value for {key} in {lang}"
        # Si la cle est rendue verbatim, c'est que la cle manque dans toutes
        # les langues (catalog warning). On accepte juste le contenu non vide.
        assert value != key or lang == "en", (
            f"key {key} missing in {lang} (rendered raw)"
        )


def test_cli_banner_title_is_branded(lang: str) -> None:
    """Le titre 'Shinobi no Sho' doit etre present dans toutes les langues
    (c'est un nom de marque, jamais traduit)."""
    title = t("cli.app.banner.title")
    assert "Shinobi" in title, f"branding leak: {title!r} in {lang}"


def test_cli_help_keys_have_translations_for_supported_langs(lang: str) -> None:
    """Au moins quelques cles cli.menu.* existent dans toutes les langues."""
    sample_keys = (
        "cli.app.bye",
        "cli.app.list.title",
        "cli.app.config.title",
    )
    rendered = [t(k) for k in sample_keys]
    # Aucune cle ne doit etre rendue brute (= manque dans la lang ET en EN).
    for k, v in zip(sample_keys, rendered, strict=True):
        assert v and v != k, f"missing key {k} in lang={lang} (got {v!r})"
