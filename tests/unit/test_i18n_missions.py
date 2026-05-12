"""Phase i18n.11 : tests cross-langue listing de missions.

`test_mission_listing_localized[lang]` (spec doc 14 §i18n.11 §2) :
verifie que `list_available_missions` retourne des titres + descriptions
resolus dans la langue active via `t("engine.missions.<rank>.<mid>.*")`.
"""

from __future__ import annotations

from shinobi.engine.missions import list_available_missions


def test_mission_listing_localized(lang: str) -> None:
    """8 instances : 1 par langue. Les missions generees ont un titre +
    description non vides + != cle brute (donc bien resolus depuis i18n)."""
    missions = list_available_missions(
        player_rank="genin", count=4, seed=42,
    )
    assert len(missions) == 4
    for m in missions:
        assert m.id, f"empty mission id for {lang}"
        assert isinstance(m.title, str)
        assert m.title, f"empty title for {lang}/{m.id}"
        # Si la cle n'est pas resolue, t() retourne la cle brute (commence
        # par "engine.missions.")
        assert not m.title.startswith("engine.missions."), (
            f"raw key returned as title for {lang}/{m.id}: {m.title!r}"
        )
        assert m.description_fr, f"empty description for {lang}/{m.id}"
        assert not m.description_fr.startswith("engine.missions."), (
            f"raw key as description for {lang}/{m.id}"
        )


def test_mission_listing_changes_with_active_language() -> None:
    """Cross-check : pour le meme seed, FR et JA produisent des titres
    differents (preuve que le t() s'applique bien)."""
    from shinobi.i18n.catalog import set_active_language

    # Reset cache : missions.py utilise un cache module-level si present.
    set_active_language("fr")
    fr = list_available_missions(player_rank="genin", count=4, seed=42)
    set_active_language("ja")
    ja = list_available_missions(player_rank="genin", count=4, seed=42)
    set_active_language("en")
    # Les titres et descriptions doivent differer entre FR et JA. Au moins
    # 1 mission sur 4 doit etre differente en title (ou en description).
    differ_count = sum(
        1 for f, j in zip(fr, ja, strict=True)
        if f.title != j.title or f.description_fr != j.description_fr
    )
    assert differ_count >= 1, (
        f"FR and JA mission listings are identical for the same seed; "
        f"got {fr[0].title!r} vs {ja[0].title!r}"
    )
