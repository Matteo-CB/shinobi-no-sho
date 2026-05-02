"""Progression du temps : vieillissement, decay, croissance, training."""

from __future__ import annotations

from dataclasses import dataclass

from shinobi.engine.character import (
    Character,
    Injury,
)
from shinobi.engine.stats import CoreStats, ExtendedStats, aging_decay, aging_growth

# Stats entrainables et la classe a laquelle elles appartiennent (core ou ext).
TRAINABLE_CORE = {
    "ninjutsu",
    "taijutsu",
    "genjutsu",
    "intelligence",
    "strength",
    "speed",
    "stamina",
    "hand_seals",
}
TRAINABLE_EXT = {
    "chakra_control",
    "willpower",
    "perception",
    "social_charisma",
    "leadership",
    "medical_knowledge",
    "fuinjutsu_knowledge",
    "senjutsu_aptitude",
}
NON_TRAINABLE_EXT = {"luck", "beauty", "lineage_value", "chakra_reserves"}

# Calibrage : a genie 1.0, il faut ~1000h de focus pour passer une stat de 1.0 a 2.0.
# Diminishing returns : plus la stat est haute, plus la progression ralentit.
HOURS_PER_STAT_POINT_BASE = 800.0


@dataclass(frozen=True)
class StatChange:
    """Petit delta de stat applique a un tour, pour feedback CLI."""

    stat_name: str
    old: float
    new: float

    @property
    def delta(self) -> float:
        return self.new - self.old


def train_stat(
    character: Character,
    stat_name: str,
    *,
    hours: int,
    quality_modifier: float = 1.0,
) -> tuple[Character, StatChange | None]:
    """Entraine une stat avec rendements decroissants, retourne (character, delta).

    Beauty, luck, lineage ne sont PAS entrainables.
    """
    if stat_name in NON_TRAINABLE_EXT:
        return character, None

    if stat_name in TRAINABLE_CORE:
        current = float(getattr(character.stats, stat_name))
    elif stat_name in TRAINABLE_EXT:
        current = float(getattr(character.extended_stats, stat_name))
    else:
        return character, None

    new_value = _progress_value(
        current=current,
        hours=hours,
        learning_genius=character.extended_stats.learning_genius,
        quality_modifier=quality_modifier,
    )
    if new_value <= current:
        return character, None

    if stat_name in TRAINABLE_CORE:
        new_stats = character.stats.model_copy(update={stat_name: new_value})
        new_char = character.model_copy(update={"stats": new_stats})
    else:
        new_ext = character.extended_stats.model_copy(update={stat_name: new_value})
        new_char = character.model_copy(update={"extended_stats": new_ext})

    return new_char, StatChange(stat_name=stat_name, old=current, new=new_value)


def _progress_value(
    *, current: float, hours: int, learning_genius: float, quality_modifier: float
) -> float:
    """Formule de progression a rendements decroissants :
    - effort_brut = hours * (genie / 3.0) * quality
    - resistance = (current / 5.0) ^ 2  augmente vite quand on approche 5.0
    - gain = effort_brut / (HOURS_PER_STAT_POINT_BASE * (1 + 5 * resistance))
    """
    if current >= 5.0:
        return current  # plafond brut
    effort = hours * max(0.3, learning_genius / 3.0) * quality_modifier
    resistance = (current / 5.0) ** 2
    cost_per_point = HOURS_PER_STAT_POINT_BASE * (1.0 + 5.0 * resistance)
    gain = effort / cost_per_point
    return min(5.0, current + gain)


def apply_rest(character: Character, *, hours: int) -> Character:
    """Repos : recupere chakra et reduit fatigue."""
    chakra_gain = hours * 5
    fatigue_loss = hours * 4
    new_chakra = character.chakra.model_copy(
        update={"current": min(character.chakra.max, character.chakra.current + chakra_gain)}
    )
    new_health = character.health.model_copy(
        update={"fatigue": max(0, character.health.fatigue - fatigue_loss)}
    )
    return character.with_chakra(new_chakra).with_health(new_health)


def apply_meditation(character: Character, *, hours: int) -> Character:
    """Meditation : recupere chakra plus efficacement, plus willpower."""
    chakra_gain = hours * 25
    new_chakra = character.chakra.model_copy(
        update={"current": min(character.chakra.max, character.chakra.current + chakra_gain)}
    )
    new_char = character.with_chakra(new_chakra)
    # Petit bonus willpower si entrainement long
    if hours >= 4:
        new_char, _ = train_stat(new_char, "willpower", hours=hours)
    return new_char


def apply_sleep(character: Character, *, hours: int) -> Character:
    """Sommeil : recupere fatigue completement (au-dela de 6h), regenere HP."""
    chakra_gain = hours * 15
    fatigue_loss = max(80, hours * 10)
    hp_gain = hours * 2
    new_chakra = character.chakra.model_copy(
        update={"current": min(character.chakra.max, character.chakra.current + chakra_gain)}
    )
    new_health = character.health.model_copy(
        update={
            "fatigue": max(0, character.health.fatigue - fatigue_loss),
            "hp_current": min(character.health.hp_max, character.health.hp_current + hp_gain),
        }
    )
    return character.with_chakra(new_chakra).with_health(new_health)


def apply_chakra_cost(character: Character, amount: int) -> Character:
    """Consomme du chakra (jamais sous 0)."""
    new_chakra = character.chakra.model_copy(
        update={"current": max(0, character.chakra.current - amount)}
    )
    return character.with_chakra(new_chakra)


def apply_fatigue(character: Character, amount: int) -> Character:
    """Augmente la fatigue (cap 100)."""
    new_health = character.health.model_copy(
        update={"fatigue": min(100, character.health.fatigue + amount)}
    )
    return character.with_health(new_health)


def apply_damage(character: Character, amount: int, *, description: str = "blessure") -> Character:
    """Inflige des degats. Si HP <= 0, le perso meurt."""
    new_hp = max(0, character.health.hp_current - amount)
    if new_hp == 0:
        return character.model_copy(
            update={
                "is_dead": True,
                "death_circumstances": description,
                "health": character.health.model_copy(update={"hp_current": 0}),
            }
        )
    new_health = character.health.model_copy(update={"hp_current": new_hp})
    if amount >= 30:
        new_injuries = [
            *character.health.injuries,
            Injury(description=description, severity="moderate" if amount < 60 else "major"),
        ]
        new_health = new_health.model_copy(update={"injuries": new_injuries})
    return character.with_health(new_health)


def add_money(character: Character, delta: int) -> Character:
    """Ajoute (ou retire) des ryos."""
    return character.with_money(delta)


def advance_age(character: Character, new_age: int) -> Character:
    """Avance l'age et applique les modifications stats par tranche d'age."""
    if new_age <= character.age_years:
        return character
    new_stats = _apply_aging_core(character.stats, age=new_age)
    new_extended = _apply_aging_extended(character.extended_stats, age=new_age)
    return character.model_copy(
        update={"age_years": new_age, "stats": new_stats, "extended_stats": new_extended}
    )


def _apply_aging_core(stats: CoreStats, *, age: int) -> CoreStats:
    """Decay physique apres 30, croissance avant 18."""
    if age >= 30:
        return stats.model_copy(
            update={
                "speed": aging_decay(stats.speed, age=age),
                "strength": aging_decay(stats.strength, age=age),
                "stamina": aging_decay(stats.stamina, age=age),
                "taijutsu": aging_decay(stats.taijutsu, age=age),
            }
        )
    if age < 18:
        target = 3.0
        return stats.model_copy(
            update={
                "speed": aging_growth(stats.speed, age=age, target=target, rate=0.02),
                "strength": aging_growth(stats.strength, age=age, target=target, rate=0.02),
                "stamina": aging_growth(stats.stamina, age=age, target=target, rate=0.02),
                "taijutsu": aging_growth(stats.taijutsu, age=age, target=target, rate=0.02),
                "ninjutsu": aging_growth(stats.ninjutsu, age=age, target=target, rate=0.02),
            }
        )
    return stats


def _apply_aging_extended(stats: ExtendedStats, *, age: int) -> ExtendedStats:
    """Sagesse augmente avec l'age, beauty pic vers 25."""
    if age >= 30:
        return stats.model_copy(
            update={
                "beauty": aging_decay(stats.beauty, age=age, peak_age=25),
            }
        )
    return stats
