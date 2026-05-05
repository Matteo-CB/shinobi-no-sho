"""Couche : detection d'anachronismes de roles politiques (Kage, etc.).

Detecte les enonces du type 'Tsunade, la cinquieme Hokage' alors qu'a
year=6, le Sandaime (Hiruzen) est en fonction et Tsunade vagabonde.

Approche : timeline structuree des Kages canon (1er Hashirama -> 7e Naruto)
avec leurs annees d'investiture. Si la narration mentionne un rang+role
(ex: '5e Hokage'), on compare avec le rang en fonction a current_year.

Le regle est purement deterministe : la donnee canon dit que le 3e Hokage
est en fonction en l'an 6 (Hiruzen), donc 'Tsunade 5e Hokage en l'an 6'
est un anachronisme.

Note : c'est une couche deterministe sans LLM. Couvre les Kages des 5
grands villages (Konoha, Suna, Kiri, Kumo, Iwa). N'invalide pas les
mentions sans rang explicite ('Tsunade arrive a Konoha' reste OK).
"""

from __future__ import annotations

import re

from shinobi.state.age_calculator import CanonView
from shinobi.state.world_state import RuntimeState
from shinobi.validation.validator import (
    NarrativeOutput,
    ValidationResult,
)

# Pattern : 'X(,) le/la Ne (Hokage|Kazekage|...)'
_ROLE_PATTERN = re.compile(
    r"\b(?P<name>[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\s*,?\s*"
    r"(?:la?|le|du|d'|en\s+tant\s+que)?\s*"
    r"(?P<rank>premier|deuxieme|troisieme|quatrieme|cinquieme|sixieme|septieme|huitieme"
    r"|1er|2[èe]me|3[èe]me|4[èe]me|5[èe]me|6[èe]me|7[èe]me|8[èe]me)\s+"
    r"(?P<role>Hokage|Kazekage|Mizukage|Raikage|Tsuchikage|Otokage)\b",
    re.IGNORECASE,
)


# Rang -> entier
_RANK_TO_NUM: dict[str, int] = {
    "premier": 1, "1er": 1,
    "deuxieme": 2, "2eme": 2, "2ème": 2,
    "troisieme": 3, "3eme": 3, "3ème": 3,
    "quatrieme": 4, "4eme": 4, "4ème": 4,
    "cinquieme": 5, "5eme": 5, "5ème": 5,
    "sixieme": 6, "6eme": 6, "6ème": 6,
    "septieme": 7, "7eme": 7, "7ème": 7,
    "huitieme": 8, "8eme": 8, "8ème": 8,
}


# Timeline canon : role.lower() -> liste (rang_num, from_year, to_year_or_None)
_KAGE_TIMELINE: dict[str, list[tuple[int, int, int | None]]] = {
    "hokage": [
        (1, -100, -40),  # Hashirama Senju
        (2, -40, -20),   # Tobirama Senju
        (3, -20, -5),    # Hiruzen 1er mandat
        (4, -5, 0),      # Minato (mort an 0, attaque Kyuubi)
        (3, 0, 12),      # Hiruzen reprend (mort an 12 contre Orochimaru)
        (5, 12, 17),     # Tsunade
        (6, 17, 30),     # Kakashi
        (7, 30, 9999),   # Naruto
    ],
    "kazekage": [
        (3, -50, -5),    # Sandaime Kazekage
        (4, -5, 12),     # Yondaime (mort an 12)
        (5, 14, 9999),   # Gaara devient Godaime
    ],
    "raikage": [
        (3, -65, -10),
        (4, -10, 9999),  # Ay (Yondaime)
    ],
    "mizukage": [
        (4, -25, 4),     # Yagura
        (5, 4, 9999),    # Mei Terumi
    ],
    "tsuchikage": [
        (3, -71, 9999),  # Onoki (Sandaime)
    ],
}


def _active_kage_at(role: str, year: int) -> int | None:
    """Retourne le rang en fonction a year, ou None si role inconnu / hors timeline."""
    timeline = _KAGE_TIMELINE.get(role.lower())
    if not timeline:
        return None
    for kage_num, from_y, to_y in timeline:
        if from_y <= year and (to_y is None or year < to_y):
            return kage_num
    return None


class AnachronismLayer:
    """Couche : detecte les anachronismes de role politique (Kage)."""

    name = "anachronism"

    def validate(
        self,
        *,
        narrative_output: NarrativeOutput,
        state: RuntimeState,
        canon: CanonView,
    ) -> ValidationResult:
        details: list[str] = []
        year = state.narrative_time.approximate_year
        seen: set[tuple[str, str, int]] = set()

        all_texts: list[str] = [narrative_output.narrative]
        all_texts.extend(narrative_output.world_observations or [])
        for d in narrative_output.npc_dialogue:
            if d.line:
                all_texts.append(d.line)
        for a in narrative_output.proposed_actions:
            if a.label_fr:
                all_texts.append(a.label_fr)

        for text in all_texts:
            if not text:
                continue
            for m in _ROLE_PATTERN.finditer(text):
                rank_str = m.group("rank").lower().replace("è", "e")
                role = m.group("role").lower()
                name = m.group("name")
                claimed_num = _RANK_TO_NUM.get(rank_str)
                if claimed_num is None:
                    continue
                key = (name, role, claimed_num)
                if key in seen:
                    continue
                seen.add(key)
                active_num = _active_kage_at(role, year)
                if active_num is None:
                    continue  # role hors timeline
                if claimed_num != active_num:
                    details.append(
                        f"'{name}, le {rank_str} {role.capitalize()}' en l'an {year} "
                        f"est un anachronisme : le {active_num}e {role.capitalize()} "
                        f"est en fonction a cette date, pas le {claimed_num}e."
                    )

        if details:
            return ValidationResult(
                is_valid=False,
                layer=self.name,
                reason=f"{len(details)} anachronisme(s) de role politique.",
                details=details,
            )
        return ValidationResult(is_valid=True, layer=self.name)
