"""Test de smoke : verifie que les modules de base s'importent et que la config charge."""

from __future__ import annotations

import pytest


def test_config_import() -> None:
    from shinobi.config import settings

    assert settings.llm_backend_url.startswith("http")
    assert settings.llm_model_name


def test_constants_import() -> None:
    from shinobi import constants

    assert constants.PROJECT_NAME == "shinobi-no-sho"
    assert "manga" in constants.CANONICITY_ORDER


def test_errors_module() -> None:
    from shinobi.errors import (
        CanonError,
        EngineError,
        LLMError,
        ShinobiError,
    )

    assert issubclass(CanonError, ShinobiError)
    assert issubclass(EngineError, ShinobiError)
    assert issubclass(LLMError, ShinobiError)


def test_logging_setup() -> None:
    from shinobi.logging_setup import configure_logging, get_logger

    configure_logging()
    logger = get_logger(__name__)
    logger.info("smoke_test", reason="ensure_no_crash")


def test_text_helpers() -> None:
    from shinobi.utils.text import (
        contains_em_dash,
        contains_emoji,
        sanitize_narrative,
        strip_dashes,
    )

    assert contains_em_dash("voici un em—dash")
    assert not contains_em_dash("rien a signaler")
    assert contains_emoji("hop \U0001f600 hop")
    cleaned = sanitize_narrative("Naruto sourit—puis disparait \U0001f600.")
    assert "—" not in cleaned
    assert "\U0001f600" not in cleaned
    assert strip_dashes("a—b") == "a, b"


def test_slugify() -> None:
    from shinobi.utils.slug import slug_character, slug_technique, slugify

    assert slugify("Naruto Uzumaki") == "naruto_uzumaki"
    assert slug_character("Uchiha", "Sasuke") == "uchiha_sasuke"
    assert slug_technique("Katon: Goukakyuu no Jutsu") == "katon_goukakyuu_no_jutsu"


def test_game_date() -> None:
    from shinobi.utils.time_utils import GameDate

    d = GameDate(year=12, month=10, day=10, hour=14, minute=30)
    assert d.date_str == "10-10"
    later = d.add_minutes(45)
    assert later.hour == 15 and later.minute == 15
    next_day = d.add_hours(24)
    assert next_day.day == 11 and next_day.hour == 14
    next_year = d.add_days(360)
    assert next_year.year == 13


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Hatake Kakashi", "hatake_kakashi"),
        ("Maito Gai", "maito_gai"),
        ("Hyuuga Hinata", "hyuuga_hinata"),
    ],
)
def test_slug_examples(name: str, expected: str) -> None:
    from shinobi.utils.slug import slugify

    assert slugify(name) == expected
