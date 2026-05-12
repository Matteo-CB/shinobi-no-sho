"""Systeme de missions ninja par rang."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal

# Nombre d'heures typiques de la mission par rang.
MISSION_DURATION_HOURS = {
    "D": 8,  # 1 jour
    "C": 48,  # 2 jours
    "B": 96,  # 4 jours
    "A": 168,  # 7 jours
    "S": 336,  # 14 jours
}

# Difficulte (DC) d'une mission par rang.
MISSION_DIFFICULTY = {
    "D": 8,
    "C": 14,
    "B": 20,
    "A": 26,
    "S": 32,
}

# Recompenses fixes en ryos par rang.
MISSION_REWARDS = {
    "D": 5000,
    "C": 50000,
    "B": 200000,
    "A": 800000,
    "S": 3000000,
}


@dataclass(frozen=True)
class Mission:
    """Mission acceptee par le joueur."""

    id: str
    rank: Literal["D", "C", "B", "A", "S"]
    title: str
    description_fr: str
    duration_hours: int
    difficulty_dc: int
    reward_ryos: int
    reputation_delta: int
    template_id: str = ""  # mid d'origine dans _MISSION_POOL_IDS (pour categorisation locale-agnostique)


def _rank_for_player(player_rank: str) -> str:
    """Choisit le rang de mission approprie pour un perso de rang donne."""
    mapping = {
        "academy_student": "D",
        "genin": "D",
        "chunin": "C",
        "tokubetsu_jonin": "B",
        "jonin": "B",
        "anbu": "A",
        "kage": "S",
        "missing_nin": "B",
        "civilian": "D",
    }
    return mapping.get(player_rank, "D")


# Mini-pool de missions par rang. Le LLM peut en generer d'autres.
# Les ids sont stables ; titre et description sont resolus via i18n
# au moment de la generation (snapshot dans la save).
_MISSION_POOL_IDS: dict[str, list[str]] = {
    "D": [
        "tora_cat",
        "harvest_help",
        "escort_merchant_local",
        "guard_shrine",
        "repair_fence",
    ],
    "C": [
        "escort_merchant_intercountry",
        "dismantle_bandits",
        "recover_stolen_artifact",
        "protect_caravan",
    ],
    "B": [
        "eliminate_chunin_deserter",
        "infiltrate_oto",
        "escort_lord",
        "destroy_organized_bandits",
    ],
    "A": [
        "assassinate_jonin_deserter",
        "prevent_kage_plot",
        "escort_diplomatic",
        "recover_forbidden_scroll",
    ],
    "S": [
        "eliminate_renegade_sannin",
        "sabotage_invasion",
        "capture_jinchuuriki",
        "steal_battle_plans",
    ],
}


def _mission_pool_resolved(rank: str) -> list[tuple[str, str, str]]:
    """Resout les (mid, title, description) du pool pour le rang via i18n."""
    from shinobi.i18n import t as _t

    out: list[tuple[str, str, str]] = []
    for mid in _MISSION_POOL_IDS.get(rank, []):
        title = _t(f"engine.missions.{rank}.{mid}.title")
        desc = _t(f"engine.missions.{rank}.{mid}.description")
        out.append((mid, title, desc))
    return out


def generate_mission(
    *,
    player_rank: str,
    seed: int = 0,
    global_tension: int = 0,
    inflation_factor: float = 1.0,
) -> Mission:
    """Genere aleatoirement une mission appropriee au rang du joueur.

    global_tension : 0-100. Augmente la difficulte (DC + tension/10) et la paie (* (1 + tension/200)).
    inflation_factor : multiplicateur applique a la recompense en ryos.
    """
    rank = _rank_for_player(player_rank)
    rng = random.Random(seed)
    pool = _mission_pool_resolved(rank)
    mid, title, desc = rng.choice(pool)
    base_dc = MISSION_DIFFICULTY[rank]
    tension_bonus = max(0, global_tension) // 10
    base_reward = MISSION_REWARDS[rank]
    inflated_reward = int(base_reward * max(0.1, inflation_factor) * (1 + max(0, global_tension) / 200))
    return Mission(
        id=f"mission_{rank}_{rng.randint(10000, 99999)}",
        rank=rank,
        title=title,
        description_fr=desc,
        duration_hours=MISSION_DURATION_HOURS[rank],
        difficulty_dc=base_dc + tension_bonus,
        reward_ryos=inflated_reward,
        reputation_delta=5 if rank in ("D", "C") else 10 if rank in ("B", "A") else 20,
        template_id=mid,
    )


def list_available_missions(
    *,
    player_rank: str,
    count: int = 4,
    seed: int = 0,
    global_tension: int = 0,
    inflation_factor: float = 1.0,
) -> list[Mission]:
    """Liste de N missions disponibles. Difficulte/recompense modulees par tension+inflation."""
    return [
        generate_mission(
            player_rank=player_rank,
            seed=seed + i,
            global_tension=global_tension,
            inflation_factor=inflation_factor,
        )
        for i in range(count)
    ]
