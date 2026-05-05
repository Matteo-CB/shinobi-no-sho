"""Resolution referentielle des pronoms et ellipses dans la query joueur.

Le pattern PANGeA observe que les LLMs ont tendance a 'commit a une seule
interpretation' face a l'ambiguite referentielle au lieu d'hedger ou de demander.
Ce module preempte le probleme cote pipeline : on resout deterministiquement quand
on peut, on demande clarification IN-CHARACTER quand on ne peut pas.

Le StateView est un Protocol minimal lu par le resolver. Sera implemente par le
pilier 4 (state tracker). Une `NullStateView` est fournie pour les tests et pour
le bootstrap quand le state n'est pas encore initialise.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol


class StateView(Protocol):
    """Vue minimale du state lisible par le resolver.

    Sera fournie par le pilier 4 (state tracker). Pour l'instant, un fake suffit
    pour brancher la logique et la tester.
    """

    @property
    def last_mentioned_character(self) -> str | None: ...

    @property
    def present_characters(self) -> Sequence[str]: ...

    @property
    def current_location(self) -> str | None: ...


@dataclass(frozen=True)
class NullStateView:
    """Implementation neutre quand aucun state n'est disponible."""

    @property
    def last_mentioned_character(self) -> str | None:
        return None

    @property
    def present_characters(self) -> Sequence[str]:
        return ()

    @property
    def current_location(self) -> str | None:
        return None


# Pronoms en francais a resoudre.
_PRONOUN_RE = re.compile(
    r"\b(?:il|elle|ils|elles|le|la|les|lui|leur|eux|sa|son|ses)\b",
    re.IGNORECASE,
)


# Patterns d'ellipse : input court qui necessite expansion via state.
_ELLIPSIS_PATTERNS = (
    r"^\s*ok\s*[.!?]*\s*$",
    r"^\s*okay\s*[.!?]*\s*$",
    r"^\s*d['e]\s*accord\s*[.!?]*\s*$",
    r"^\s*j['e]\s*y\s+vais\s*[.!?]*\s*$",
    r"^\s*on\s+(?:y\s+)?va\s*[.!?]*\s*$",
    r"^\s*allons[- ]y\s*[.!?]*\s*$",
)
_ELLIPSIS_RE = re.compile("|".join(_ELLIPSIS_PATTERNS), re.IGNORECASE)


@dataclass(frozen=True)
class ResolutionResult:
    """Resultat de la resolution referentielle."""

    rewritten: str
    is_ambiguous: bool
    clarification_needed: str | None  # message in-character a poser au joueur
    used_referents: dict[str, str] = field(default_factory=dict)


def resolve_references(text: str, state: StateView) -> ResolutionResult:
    """Tente de resoudre pronoms et ellipses via le state courant.

    Strategie :
    - input vide : ambiguous + clarification
    - ellipse ('ok', 'j'y vais') : expansion deterministe via location/last_mentioned
    - pronoms : si exactement un candidat antecedent dans le state, resolution
      directe. Si plusieurs candidats, ambiguous + clarification listant les
      candidats. Si aucun, ambiguous + clarification generique.
    - aucun pronom ni ellipse : passe-plat (texte inchange, non ambigu)
    """
    if text is None:
        text = ""
    stripped = text.strip()

    if not stripped:
        return ResolutionResult(
            rewritten=stripped,
            is_ambiguous=True,
            clarification_needed="Que veux-tu faire ?",
            used_referents={},
        )

    if _ELLIPSIS_RE.match(stripped):
        return _expand_ellipsis(stripped, state)

    pronouns_found = [m.group(0).lower() for m in _PRONOUN_RE.finditer(stripped)]
    if not pronouns_found:
        return ResolutionResult(
            rewritten=stripped,
            is_ambiguous=False,
            clarification_needed=None,
            used_referents={},
        )

    candidates = list(state.present_characters or ())
    if not candidates and state.last_mentioned_character:
        candidates = [state.last_mentioned_character]

    if len(candidates) == 0:
        return ResolutionResult(
            rewritten=stripped,
            is_ambiguous=True,
            clarification_needed="De qui parles-tu ? Personne n'est mentionné dans la scène.",
            used_referents={},
        )

    if len(candidates) > 1:
        names = ", ".join(candidates)
        return ResolutionResult(
            rewritten=stripped,
            is_ambiguous=True,
            clarification_needed=f"De qui parles-tu ? {names} sont présents.",
            used_referents={},
        )

    target = candidates[0]
    used = dict.fromkeys(set(pronouns_found), target)
    rewritten = _PRONOUN_RE.sub(target, stripped)
    return ResolutionResult(
        rewritten=rewritten,
        is_ambiguous=False,
        clarification_needed=None,
        used_referents=used,
    )


def _expand_ellipsis(text: str, state: StateView) -> ResolutionResult:
    """Expanse une ellipse type 'ok', 'j'y vais' via le state.

    Si le state n'a ni location ni last_mentioned, on demande clarification.
    """
    location = state.current_location
    last = state.last_mentioned_character
    pieces: list[str] = []
    if location:
        pieces.append(f"depuis {location}")
    if last:
        pieces.append(f"vers {last}")

    if not pieces:
        return ResolutionResult(
            rewritten=text,
            is_ambiguous=True,
            clarification_needed="Vas-tu quelque part de spécifique ?",
            used_referents={},
        )

    rewritten = "Le joueur confirme et part " + " ".join(pieces)
    return ResolutionResult(
        rewritten=rewritten,
        is_ambiguous=False,
        clarification_needed=None,
        used_referents={"context": ", ".join(pieces)},
    )
