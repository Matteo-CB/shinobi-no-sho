"""Geographie et deplacement entre lieux."""

from __future__ import annotations

# Distances approximatives entre lieux majeurs en jours de voyage a pied de ninja.
TRAVEL_DAYS: dict[tuple[str, str], int] = {
    ("konohagakure", "sunagakure"): 5,
    ("konohagakure", "kirigakure"): 7,
    ("konohagakure", "kumogakure"): 7,
    ("konohagakure", "iwagakure"): 6,
    ("sunagakure", "kirigakure"): 9,
    ("sunagakure", "kumogakure"): 9,
    ("sunagakure", "iwagakure"): 5,
    ("kumogakure", "kirigakure"): 6,
    ("kumogakure", "iwagakure"): 8,
    ("kirigakure", "iwagakure"): 9,
}


def travel_days(from_id: str, to_id: str) -> int:
    """Jours de voyage estimes entre deux lieux."""
    if from_id == to_id:
        return 0
    return (
        TRAVEL_DAYS.get((from_id, to_id)) or TRAVEL_DAYS.get((to_id, from_id)) or 5  # defaut
    )


def travel_minutes(from_id: str, to_id: str) -> int:
    """Conversion en minutes pour le moteur de temps."""
    return travel_days(from_id, to_id) * 24 * 60
