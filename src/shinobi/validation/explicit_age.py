"""Couche : detection de contradictions d'age explicites dans la prose narrative.

Complementaire a `AgeCoherenceLayer` (qui verifie le vocabulaire vs age) :
ici on capture les cas ou la narration AFFIRME un age incompatible avec le
canon. Exemples reels detectes en jeu :

- 'Naruto, jeune ninja de 10 ans, marche...' alors qu'a year=6 il a 6 ans
- 'Sasuke, age de 12 ans...' alors qu'a year=8 il a 8 ans

Approche : regex sur les patterns 'X (jeune) (ninja|enfant) de N ans' ou
'X, age de N ans'. Pour chaque match, resolution alias -> birth_year canon
-> comparaison. Tolerance 1 an pour les cas frontaliers.

Skip generique pour PNJ inconnus du canon ou sans birth_year.
"""

from __future__ import annotations

import re

from shinobi.errors import StateError
from shinobi.state.age_calculator import CanonView, get_age
from shinobi.state.world_state import RuntimeState
from shinobi.validation.validator import (
    NarrativeOutput,
    ValidationResult,
)

# 'X, age de N ans' / 'X qui a N ans' / 'X de ses N ans'
_AGE_NEAR_NPC = re.compile(
    r"\b(?P<name>[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\b"
    r"[^.!?\n]{0,80}?"
    r"\b(?:age\s+de\s+|d['e]\s+|[ée]g[ée]\s+de\s+|qui\s+a\s+|de\s+ses\s+)"
    r"(?P<age>\d{1,3})\s*(?:ans|ann[ée]es|ann[ée]e)\b",
    re.IGNORECASE,
)
# 'X, (un) jeune ninja de N ans'
_NINJA_OF_N = re.compile(
    r"\b(?P<name>[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\s*,?\s*"
    r"(?:un|une|jeune)\s+(?:ninja|shinobi|enfant|gar[cç]on|fille|adulte|guerrier|sage)\s+"
    r"de\s+(?P<age>\d{1,3})\s*(?:ans|ann[ée]es)",
    re.IGNORECASE,
)

_AGE_TOLERANCE = 2  # tolerance d'1 an de chaque cote


class ExplicitAgeLayer:
    """Couche : detecte les enonces d'age incompatibles avec le canon."""

    name = "explicit_age"

    def validate(
        self,
        *,
        narrative_output: NarrativeOutput,
        state: RuntimeState,
        canon: CanonView,
    ) -> ValidationResult:
        details: list[str] = []
        year = state.narrative_time.approximate_year
        seen: set[tuple[str, int]] = set()

        # Texte a scanner : narrative + observations + dialogues
        all_texts: list[str] = [narrative_output.narrative]
        all_texts.extend(narrative_output.world_observations or [])
        for d in narrative_output.npc_dialogue:
            if d.line:
                all_texts.append(d.line)

        for text in all_texts:
            if not text:
                continue
            details.extend(self._scan(text, year, canon, seen))

        if details:
            return ValidationResult(
                is_valid=False,
                layer=self.name,
                reason=f"{len(details)} contradiction(s) d'age explicite vs canon.",
                details=details,
            )
        return ValidationResult(is_valid=True, layer=self.name)

    @staticmethod
    def _scan(
        text: str,
        year: int,
        canon: CanonView,
        seen: set[tuple[str, int]],
    ) -> list[str]:
        out: list[str] = []
        for pattern in (_AGE_NEAR_NPC, _NINJA_OF_N):
            for m in pattern.finditer(text):
                name_token = m.group("name").strip()
                try:
                    claimed_age = int(m.group("age"))
                except (ValueError, TypeError):
                    continue
                # Resout via get_age (gere les alias PRIMARY_NPC_NAMES)
                try:
                    canon_age = get_age(name_token, year, canon, strict=False)
                except StateError:
                    continue  # PNJ inconnu, skip silencieux
                key = (name_token.lower(), claimed_age)
                if key in seen:
                    continue
                seen.add(key)
                if abs(canon_age - claimed_age) >= _AGE_TOLERANCE:
                    out.append(
                        f"{name_token} a {canon_age} ans en l'an {year} (canon), "
                        f"mais la narration dit {claimed_age} ans."
                    )
        return out
