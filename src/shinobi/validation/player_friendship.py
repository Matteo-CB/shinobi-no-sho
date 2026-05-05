"""Couche : detection d'amities inventees joueur-NPC.

Le perso joueur est un OC (original character) qui n'existe PAS dans le canon
Naruto. Aucun PNJ canon ne le connait, ne l'aime, ne le considere comme ami
sauf si la relation a ete EXPLICITEMENT etablie en jeu (interactions repetees,
quetes communes, etc.).

Cette couche detecte les inventions du LLM du type :
- 'Naruto, ami proche d'Endo, le salua chaleureusement'
- 'Endo est un ami de Sasuke depuis l'enfance'
- 'Kakashi accueille chaleureusement Endo'

Quand `state.player_character.established_npc_relationships` ne contient pas
le NPC mentionne, on considere que la relation est inventee et on reject.

NOTE TRANSITOIRE : couche pertinente jusqu'a ce que le KG dynamique (Phase A
de la roadmap) prenne le relai avec un belief propagator structure. Voir
docs/02-PROJET-ROADMAP-SUITE.md §5.4.
"""

from __future__ import annotations

import re

from shinobi.state.age_calculator import CanonView
from shinobi.state.world_state import RuntimeState
from shinobi.validation.validator import (
    NarrativeOutput,
    ValidationResult,
)


def _build_patterns(player_first: str, player_full: str) -> list[re.Pattern[str]]:
    """Compose les patterns regex de detection (player_first/full quotes ok)."""
    pf = re.escape(player_first)
    pl = re.escape(player_full)
    return [
        # 'X (qui est) (un/une) ami(e) (proche) de Endo'
        re.compile(
            rf"\b(?P<name>[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)"
            rf"[^.!?\n]{{0,40}}?"
            rf"\bami(?:e|s|es)?\s+(?:proche\s+)?(?:de|d['e]|avec)\s*(?:{pf}|{pl})",
            re.IGNORECASE,
        ),
        # 'Endo (est) ami (proche/avec/de) X' / 'Endo est ami avec X'
        re.compile(
            rf"\b(?:{pf}|{pl})"
            rf"[^.!?\n]{{0,40}}?"
            rf"\bami(?:e|s|es)?\s+(?:proche\s+)?(?:avec|de|d['e])\s+"
            rf"(?P<name>[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)",
            re.IGNORECASE,
        ),
        # 'X salue Endo chaleureusement' / 'X accueille Endo chaleureusement'
        re.compile(
            rf"\b(?P<name>[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\s+"
            rf"(?:salu(?:e|a|er)|accueill(?:e|i|ir))\s+"
            rf"(?:{pf}|{pl})\s+chaleureusement",
            re.IGNORECASE,
        ),
        # 'X et Endo sont (de bons) amis'
        re.compile(
            rf"\b(?P<name>[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\s+et\s+"
            rf"(?:{pf}|{pl})"
            rf"[^.!?\n]{{0,30}}?\b(?:sont|etaient)\s+(?:de\s+bons?\s+)?ami(?:e|s|es)?\b",
            re.IGNORECASE,
        ),
    ]


def _resolve_alias(name_token: str) -> str | None:
    """Convertit un nom commun (ex: 'Naruto') en id canon principal via PRIMARY_NPC_NAMES."""
    try:
        from shinobi.canon.fact_sheet import PRIMARY_NPC_NAMES
    except ImportError:
        return None
    n = name_token.lower().strip()
    if n in PRIMARY_NPC_NAMES:
        return PRIMARY_NPC_NAMES[n]
    if " " in n:
        first = n.split()[0]
        return PRIMARY_NPC_NAMES.get(first)
    return None


class PlayerFriendshipLayer:
    """Couche : detecte les amities inventees joueur-NPC.

    Le joueur est un OC ; toute amitie avec un NPC canon doit etre dans
    `state.player_character.established_npc_relationships`. Sinon : violation.
    """

    name = "player_friendship"

    def validate(
        self,
        *,
        narrative_output: NarrativeOutput,
        state: RuntimeState,
        canon: CanonView,
    ) -> ValidationResult:
        details: list[str] = []
        player_full = state.player_character.name or ""
        if not player_full:
            return ValidationResult(is_valid=True, layer=self.name)

        player_first = player_full.split()[0]
        established = {
            cid.lower() for cid in state.player_character.established_npc_relationships
        }
        patterns = _build_patterns(player_first, player_full)

        all_texts: list[str] = [narrative_output.narrative]
        all_texts.extend(narrative_output.world_observations or [])
        for d in narrative_output.npc_dialogue:
            if d.line:
                all_texts.append(d.line)

        seen: set[str] = set()
        for text in all_texts:
            if not text:
                continue
            for pattern in patterns:
                for m in pattern.finditer(text):
                    name_token = m.group("name").strip()
                    cid = _resolve_alias(name_token)
                    if cid is None:
                        continue  # nom non resolu, skip silencieux
                    if cid in seen:
                        continue
                    seen.add(cid)
                    if cid.lower() in established:
                        continue  # relation deja etablie, OK
                    details.append(
                        f"La narration affirme une amitie entre {cid} et {player_full} "
                        f"alors que cette relation n'est pas etablie. Le joueur "
                        f"vient de rencontrer ce PNJ ou ne l'a jamais croise. "
                        f"Les amities se construisent par interactions repetees."
                    )

        if details:
            return ValidationResult(
                is_valid=False,
                layer=self.name,
                reason=f"{len(details)} amitie(s) joueur-NPC inventee(s).",
                details=details,
            )
        return ValidationResult(is_valid=True, layer=self.name)
