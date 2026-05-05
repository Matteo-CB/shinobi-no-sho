"""Couche C : coherence du langage avec l'age calcule (signaux textuels).

Choix MVP : pas de behavior_profiles JSON par perso pour l'instant. Un
profile par perso ne scale pas (1500 persos canon, MVP en couvre 4) et
laisse des trous. Les signaux generiques couvrent le drift le plus visible :
- vocabulaire abstrait/philosophique improbable chez un enfant < 8 ans
- baby talk improbable chez un adulte > 25 ans

Pour les nuances spécifiques par perso (Naruto sans dattebayo, Sasuke
sentimental), un overlay behavior_profiles pourra etre ajoute plus tard
sans casser cette couche.

Les PNJ generiques (sans birth_year) ou avec age non calculable sont skip
silencieusement : pas de reject sur incertitude.
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

# Vocabulaire abstrait / strategique / philosophique improbable chez un enfant.
_ADULT_VOCAB_PATTERNS = (
    r"\bstrat[ée]gie\s+(?:diplomatique|politique|militaire|globale)\b",
    r"\b(?:tactique|diplomatie|compromis|[ée]pist[ée]mologique|rationalisme|pragmatisme|"
    r"realpolitik|g[ée]opolitique|id[ée]ologie)\b",
    r"\bj['e]\s*ai\s+analys[ée]\s+(?:la|les|le)\s+(?:strat[ée]gie|tactique|situation\s+politique)\b",
    r"\bj['e]\s*ai\s+conclu\s+que\s+(?:la|les|le)\s+(?:cons[ée]quence|implication|situation)\b",
    r"\bdoit\s+consid[ée]rer\s+les\s+cons[ée]quences\s+(?:politiques|diplomatiques)\b",
)
_ADULT_VOCAB_RE = re.compile("|".join(_ADULT_VOCAB_PATTERNS), re.IGNORECASE)

# Baby talk evident improbable chez un adulte mur.
_BABY_TALK_PATTERNS = (
    r"\b(?:areu(?:\s+areu)?|guili[\s-]+guili|nananere|nyanya|youpi|atchoum)\b",
    r"\bmaman\s+m[\s\']*[ae]\s+dit\b",
    r"\bje\s+suis\s+un\s+grand\s+gar[cç]on\b",
    r"\bdoudou\s+ch[ée]ri\b",
)
_BABY_TALK_RE = re.compile("|".join(_BABY_TALK_PATTERNS), re.IGNORECASE)


_AGE_CHILD_THRESHOLD = 8
_AGE_ADULT_THRESHOLD = 25


class AgeCoherenceLayer:
    """Couche C : check signaux langage vs age calcule."""

    name = "age_coherence"

    def validate(
        self,
        *,
        narrative_output: NarrativeOutput,
        state: RuntimeState,
        canon: CanonView,
    ) -> ValidationResult:
        details: list[str] = []
        year = state.narrative_time.approximate_year

        for d in narrative_output.npc_dialogue:
            actor = d.character_id
            line = d.line or ""
            if not actor or not line:
                continue
            try:
                age = get_age(actor, year, canon, strict=False)
            except StateError:
                # PNJ generique, birth_year manquant, ou perso inconnu : skip.
                continue

            if age < _AGE_CHILD_THRESHOLD:
                m = _ADULT_VOCAB_RE.search(line)
                if m:
                    details.append(
                        f"{actor} ({age} ans) utilise un vocabulaire abstrait improbable : "
                        f"« {m.group(0)} »."
                    )

            if age > _AGE_ADULT_THRESHOLD:
                m = _BABY_TALK_RE.search(line)
                if m:
                    details.append(
                        f"{actor} ({age} ans) utilise un baby-talk improbable : "
                        f"« {m.group(0)} »."
                    )

        if details:
            return ValidationResult(
                is_valid=False,
                layer=self.name,
                reason=f"{len(details)} violation(s) de cohérence langage/âge.",
                details=details,
            )
        return ValidationResult(is_valid=True, layer=self.name)
