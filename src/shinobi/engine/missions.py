"""Systeme de missions ninja par rang."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal

# Nombre d'heures typiques de la mission par rang.
MISSION_DURATION_HOURS = {
    "D": 8,    # 1 jour
    "C": 48,   # 2 jours
    "B": 96,   # 4 jours
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
_MISSION_POOL = {
    "D": [
        ("Retrouver le chat egare de Madame Shijimi", "Le chat Tora s'est echappe pour la N-ieme fois. Mission classique des genins de Konoha."),
        ("Aider a la moisson chez les fermiers", "Les paysans ont besoin de mains supplementaires pendant la recolte."),
        ("Escorter un marchand jusqu'au village voisin", "Un marchand veut atteindre le village suivant en securite."),
        ("Garder un sanctuaire pendant la nuit", "Veiller sur un petit sanctuaire local jusqu'a l'aube."),
        ("Reparer la cloture d'un eleveur", "Travail manuel basique mais essentiel pour la securite des animaux."),
    ],
    "C": [
        ("Escorter un marchand entre deux pays", "Voyage d'une semaine, risque de bandits sur la route."),
        ("Demanteler un petit gang de bandits", "Un groupe de 3 ou 4 bandits sevit sur une route commerciale."),
        ("Recuperer un artefact vole", "Un objet de famille noble a ete vole, le retrouver et le ramener."),
        ("Proteger une caravane pendant 3 jours", "Voyage avec marchandises de valeur a travers une zone fragile."),
    ],
    "B": [
        ("Eliminer un deserteur de rang chunin", "Un nukenin a fui le village avec des secrets sensibles."),
        ("Infiltrer un fief mineur d'Otogakure", "Mission d'espionnage prolongee, contact avec sources locales."),
        ("Escorter un seigneur pendant une mission diplomatique", "Risque d'attentat eleve."),
        ("Detruire un repaire de bandits organise", "Une trentaine d'hommes armes, certains avec ninjutsu."),
    ],
    "A": [
        ("Assassiner un ninja deserteur de rang jonin", "Cible dangereuse, possede des techniques avancees."),
        ("Prevenir un complot contre le Kage", "Mission politique sensible avec risque de trahison interne."),
        ("Escorter une delegation diplomatique a un sommet", "Plusieurs villages ennemis pourraient saboter la rencontre."),
        ("Recuperer un parchemin de technique interdite", "Le parchemin est dans le repaire d'un sannin disgracie."),
    ],
    "S": [
        ("Eliminer un sannin renegat", "Cible legendaire, plusieurs gardes du corps ninjas, repaire fortifie."),
        ("Saboter une operation d'invasion en cours", "Plusieurs jours derriere les lignes ennemies."),
        ("Capturer un jinchuuriki vivant", "Mission moralement difficile, cible probablement nationale."),
        ("Voler les plans de bataille du Hokage rival", "Infiltration d'un quartier general adverse."),
    ],
}


def generate_mission(*, player_rank: str, seed: int = 0) -> Mission:
    """Genere aleatoirement une mission appropriee au rang du joueur."""
    rank = _rank_for_player(player_rank)
    rng = random.Random(seed)
    title, desc = rng.choice(_MISSION_POOL[rank])
    return Mission(
        id=f"mission_{rank}_{rng.randint(10000, 99999)}",
        rank=rank,
        title=title,
        description_fr=desc,
        duration_hours=MISSION_DURATION_HOURS[rank],
        difficulty_dc=MISSION_DIFFICULTY[rank],
        reward_ryos=MISSION_REWARDS[rank],
        reputation_delta=5 if rank in ("D", "C") else 10 if rank in ("B", "A") else 20,
    )


def list_available_missions(*, player_rank: str, count: int = 4, seed: int = 0) -> list[Mission]:
    """Liste de N missions disponibles."""
    return [generate_mission(player_rank=player_rank, seed=seed + i) for i in range(count)]
