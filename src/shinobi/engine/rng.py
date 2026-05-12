"""Generateur pseudo-aleatoire seedable et deterministe.

Utilise un PRNG xorshift64 pour rester reproductible et facile a serialiser.
Le seed est un entier 64 bits stocke dans l'etat de partie.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from shinobi.i18n import t

DICE_RE = re.compile(r"^(\d+)d(\d+)$")


@dataclass(frozen=True)
class RollResult:
    """Resultat d'un jet de des."""

    raw: int
    modifier: int
    total: int
    dice: str
    seed_after: int


def xorshift64(seed: int) -> int:
    """Etape PRNG xorshift64."""
    s = seed & 0xFFFFFFFFFFFFFFFF
    s ^= (s << 13) & 0xFFFFFFFFFFFFFFFF
    s ^= (s >> 7) & 0xFFFFFFFFFFFFFFFF
    s ^= (s << 17) & 0xFFFFFFFFFFFFFFFF
    return s & 0xFFFFFFFFFFFFFFFF


def next_seed(seed: int) -> int:
    """Retourne le prochain seed dans la sequence."""
    if seed == 0:
        seed = 0x12345678ABCDEF01
    return xorshift64(seed)


def roll(seed: int, dice: str = "1d20", modifier: int = 0) -> RollResult:
    """Lance des des a partir d'un seed et retourne le resultat + le prochain seed."""
    m = DICE_RE.match(dice)
    if not m:
        raise ValueError(t("engine.rng.error.invalid_dice_format", dice=dice))
    count = int(m.group(1))
    sides = int(m.group(2))
    if count < 1 or sides < 2:
        raise ValueError(t("engine.rng.error.invalid_dice", dice=dice))
    s = seed
    total = 0
    for _ in range(count):
        s = next_seed(s)
        total += (s % sides) + 1
    return RollResult(
        raw=total,
        modifier=modifier,
        total=total + modifier,
        dice=dice,
        seed_after=s,
    )


def random_choice(seed: int, options: list) -> tuple[object, int]:
    """Choix aleatoire d'un element dans une liste."""
    if not options:
        raise ValueError(t("engine.rng.error.empty_options"))
    s = next_seed(seed)
    idx = s % len(options)
    return options[idx], s


def random_int(seed: int, low: int, high: int) -> tuple[int, int]:
    """Entier aleatoire dans [low, high] inclusive."""
    if low > high:
        raise ValueError(f"low {low} > high {high}")
    s = next_seed(seed)
    range_ = high - low + 1
    return low + (s % range_), s
