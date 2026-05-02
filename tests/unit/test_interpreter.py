"""Tests sur l'interpreteur d'intentions."""

from __future__ import annotations

import pytest

from shinobi.engine.interpreter import interpret
from shinobi.types import ActionType


@pytest.mark.parametrize(
    "text,expected",
    [
        ("je m'entraine au taijutsu", ActionType.train_stat),
        ("je m entraine au ninjutsu", ActionType.train_stat),
        ("je pratique le genjutsu pendant 2 heures", ActionType.train_stat),
        ("j'apprends le katon goukakyu", ActionType.train_technique),
        ("je medite", ActionType.meditate),
        ("je dors 8 heures", ActionType.rest),
        ("je me repose un moment", ActionType.rest),
        ("j'attends le matin", ActionType.wait),
        ("je travaille au champ", ActionType.work),
        ("j'accepte une mission", ActionType.accept_mission),
        ("je combats le bandit", ActionType.fight),
        ("je voyage vers Sunagakure", ActionType.move),
        ("je discute avec Itachi", ActionType.talk),
        ("j'intimide le marchand", ActionType.intimidate),
        ("je vole le rouleau", ActionType.steal),
        ("j'espionne le quartier general", ActionType.spy),
        ("je recherche dans les archives", ActionType.research),
    ],
)
def test_interpret_basic(text: str, expected: ActionType) -> None:
    parsed = interpret(text)
    assert parsed.action_type == expected


def test_interpret_train_extracts_stat() -> None:
    parsed = interpret("je m'entraine au ninjutsu")
    assert parsed.action_type == ActionType.train_stat
    assert parsed.parameters.get("stat") == "ninjutsu"


def test_interpret_extracts_duration() -> None:
    parsed = interpret("je m'entraine au taijutsu pendant 7 jours")
    assert parsed.action_type == ActionType.train_stat
    # 7 jours * 8h = 56h
    assert parsed.parameters.get("duration_hours") == 56


def test_interpret_unknown_falls_back_to_custom() -> None:
    parsed = interpret("je rumine en regardant les nuages")
    assert parsed.action_type == ActionType.custom
