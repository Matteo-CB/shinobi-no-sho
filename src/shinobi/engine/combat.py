"""Moteur de combat tour par tour."""

from __future__ import annotations

from dataclasses import dataclass

from shinobi.engine.character import Character
from shinobi.engine.rng import roll


@dataclass(frozen=True)
class CombatantSnapshot:
    """Snapshot d'un combattant pour un tour de combat."""

    character_id: str
    hp: int
    chakra: int
    fatigue: int
    speed: float
    taijutsu: float
    ninjutsu: float
    genjutsu: float
    willpower: float


@dataclass(frozen=True)
class CombatHitResult:
    hit: bool
    margin: int
    seed_after: int


@dataclass(frozen=True)
class CombatDamageResult:
    damage: int
    seed_after: int


def make_snapshot(c: Character) -> CombatantSnapshot:
    """Convertit un character en snapshot combat."""
    return CombatantSnapshot(
        character_id=c.id,
        hp=c.health.hp_current,
        chakra=c.chakra.current,
        fatigue=c.health.fatigue,
        speed=c.stats.speed,
        taijutsu=c.stats.taijutsu,
        ninjutsu=c.stats.ninjutsu,
        genjutsu=c.stats.genjutsu,
        willpower=c.extended_stats.willpower,
    )


def initiative_order(
    snapshots: list[CombatantSnapshot], seed: int
) -> tuple[list[CombatantSnapshot], int]:
    """Calcule l'ordre d'initiative (descendant)."""
    s = seed
    rolled: list[tuple[CombatantSnapshot, int]] = []
    for c in snapshots:
        r = roll(s, "1d20", modifier=int(c.speed * 4))
        rolled.append((c, r.total))
        s = r.seed_after
    rolled.sort(key=lambda kv: -kv[1])
    return [c for c, _ in rolled], s


def hit_roll(
    attacker: CombatantSnapshot,
    defender: CombatantSnapshot,
    *,
    seed: int,
    style: str = "taijutsu",
) -> CombatHitResult:
    """Jet de toucher en taijutsu ou ninjutsu."""
    attacker_skill = (
        attacker.taijutsu
        if style == "taijutsu"
        else attacker.ninjutsu
        if style == "ninjutsu"
        else attacker.genjutsu
    )
    defender_dc = 10 + int(defender.speed * 2) + int(defender.taijutsu * 2)
    r = roll(seed, "1d20", modifier=int(attacker_skill * 4))
    margin = r.total - defender_dc
    return CombatHitResult(hit=margin >= 0, margin=margin, seed_after=r.seed_after)


def damage_roll(
    attacker: CombatantSnapshot,
    defender: CombatantSnapshot,
    *,
    seed: int,
    base_power: int,
    relevant_stat_value: float,
    defender_resistance: int = 0,
) -> CombatDamageResult:
    """Calcul de degats simple."""
    scaling = 1.0 + (relevant_stat_value / 10.0)
    base = int(base_power * scaling)
    r = roll(seed, "1d6", modifier=base)
    damage = max(0, r.total - defender_resistance)
    return CombatDamageResult(damage=damage, seed_after=r.seed_after)


def genjutsu_resist(
    attacker: CombatantSnapshot,
    defender: CombatantSnapshot,
    *,
    seed: int,
) -> CombatHitResult:
    """Resistance au genjutsu : willpower contre genjutsu de l'attaquant."""
    dc = int((attacker.genjutsu - defender.genjutsu) * 2) + 10
    r = roll(seed, "1d20", modifier=int(defender.willpower * 4))
    margin = r.total - dc
    # Inverse semantique : margin >= 0 = resiste
    return CombatHitResult(hit=margin >= 0, margin=margin, seed_after=r.seed_after)
