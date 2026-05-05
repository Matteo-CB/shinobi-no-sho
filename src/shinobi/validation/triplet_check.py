"""Couche B du validator : triplet check (actor, jutsu) contre les enums canon.

Pour chaque `NarrativeAction` qui specifie un `actor` ET un `jutsu`, verifie
que le couple est canon en consultant `data/canon/jutsu_list.json` :

  actor in jutsu.canonical_users   ->  valide
  actor not in jutsu.canonical_users -> reject

Cas tolerants (pas de reject) :
- actor ou jutsu inconnus du canon : laisse passer (gere par layer A
  ou par la generation contrainte)
- actor manquant ou jutsu manquant : skip (pas un fact triplet)
- generic role pattern (sensei_academie, marchand_taverne) : skip

La verification est purement deterministe, < 1 ms par output (les ids sont
charges en memoire au premier appel).

Le pilier 5 (re-tagging temporel) a venir permettra une couche B+ qui
verifie aussi que le jutsu est CONNU par l'actor a l'annee in-game (un
Naruto de 12 ans ne maitrise pas Rasenshuriken). Pour l'instant le check
est binaire : in canonical_users ou non.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from shinobi.state.age_calculator import CanonView
from shinobi.state.world_state import RuntimeState
from shinobi.validation.validator import (
    NarrativeAction,
    NarrativeOutput,
    ValidationResult,
)

ROOT = Path(__file__).resolve().parents[3]
CANON_ENUMS_DIR = ROOT / "data" / "canon"

_GENERIC_ROLE_RE = re.compile(
    r"^(?:sensei|marchand|garde|civil|pnj|villageois|enfant|inconnu|"
    r"jonin|chunin|genin|anbu)_[a-z0-9_]+$",
    re.IGNORECASE,
)


def _is_generic_role(actor_id: str | None) -> bool:
    return bool(actor_id and _GENERIC_ROLE_RE.match(actor_id))


_JUTSU_CANONICAL_USERS: dict[str, set[str]] | None = None
_CHARACTER_IDS: set[str] | None = None
_JUTSU_IDS: set[str] | None = None


def _load_canon() -> tuple[dict[str, set[str]], set[str], set[str]]:
    """Charge en lazy-init les maps depuis data/canon/."""
    global _JUTSU_CANONICAL_USERS, _CHARACTER_IDS, _JUTSU_IDS
    if _JUTSU_CANONICAL_USERS is not None:
        assert _CHARACTER_IDS is not None and _JUTSU_IDS is not None
        return _JUTSU_CANONICAL_USERS, _CHARACTER_IDS, _JUTSU_IDS

    jutsu_path = CANON_ENUMS_DIR / "jutsu_list.json"
    char_path = CANON_ENUMS_DIR / "character_list.json"
    if not jutsu_path.exists() or not char_path.exists():
        # Sans enums, la couche se comporte en no-op (tolerant)
        _JUTSU_CANONICAL_USERS = {}
        _CHARACTER_IDS = set()
        _JUTSU_IDS = set()
        return _JUTSU_CANONICAL_USERS, _CHARACTER_IDS, _JUTSU_IDS

    jutsus = json.loads(jutsu_path.read_text(encoding="utf-8"))
    chars = json.loads(char_path.read_text(encoding="utf-8"))
    _JUTSU_CANONICAL_USERS = {
        j["id"]: set(j.get("canonical_users") or []) for j in jutsus
    }
    _JUTSU_IDS = set(_JUTSU_CANONICAL_USERS.keys())
    _CHARACTER_IDS = {c["id"] for c in chars}
    return _JUTSU_CANONICAL_USERS, _CHARACTER_IDS, _JUTSU_IDS


def _check_action(action: NarrativeAction) -> str | None:
    """Verifie un triplet. Retourne un message si reject, None si valide ou skip."""
    actor = action.actor
    jutsu = action.jutsu
    if not actor or not jutsu:
        return None
    if _is_generic_role(actor):
        return None

    users_map, char_ids, jutsu_ids = _load_canon()
    if not users_map:
        return None  # no-op si enums non charges

    if actor not in char_ids:
        return None  # actor inconnu : on laisse passer (gere ailleurs)
    if jutsu not in jutsu_ids:
        return None  # jutsu inconnu : on laisse passer

    canonical_users = users_map.get(jutsu, set())
    if actor not in canonical_users:
        return (
            f"{actor} n'est pas dans canonical_users de {jutsu} "
            f"(triplet non canon)."
        )
    return None


class TripletCheckLayer:
    """Couche B : validation des couples (actor, jutsu) contre le canon."""

    name = "triplet_check"

    def validate(
        self,
        *,
        narrative_output: NarrativeOutput,
        state: RuntimeState,
        canon: CanonView,
    ) -> ValidationResult:
        details: list[str] = []

        for action in narrative_output.actions:
            msg = _check_action(action)
            if msg:
                details.append(f"action : {msg}")

        for action in narrative_output.proposed_actions:
            msg = _check_action(action)
            if msg:
                details.append(f"proposed_action : {msg}")

        if details:
            return ValidationResult(
                is_valid=False,
                layer=self.name,
                reason=f"{len(details)} triplet(s) non canon.",
                details=details,
            )
        return ValidationResult(is_valid=True, layer=self.name)
