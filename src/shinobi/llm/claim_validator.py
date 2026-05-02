"""Validateur deterministe des 'claims' d'une narration LLM.

Apres generation, on extrait les paires (NPC_X, action_sociale, NPC_Y) du texte
narratif + observations + dialogues, et on verifie chaque paire contre les
forbidden_relations declarees dans les fact sheets.

Ne fait AUCUN appel LLM (deterministe, ms). Premier filet anti-incoherence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from shinobi.canon.fact_sheet import PRIMARY_NPC_NAMES, _psycho_entry_at
from shinobi.canon.models import CanonBundle


@dataclass(frozen=True)
class ClaimViolation:
    """Une violation detectee dans la narration."""

    type: str  # forbidden_relation, anachronism, contradiction
    description: str
    involved_npcs: tuple[str, ...]


# Verbes / patterns d'interaction sociale a surveiller : si on trouve
# "Naruto verbe Konohamaru", on extrait la paire (Naruto, Konohamaru).
# Les patterns acceptent toutes les conjugaisons (infinitif, present, imparfait, etc.)
_SOCIAL_VERBS = [
    r"jou(?:e|ent|er|ait|aient|es|ons|ez)\s*(?:avec|ensemble|a)",
    r"discut(?:e|ent|er|ait|aient|es|ons|ez)\s+avec",
    r"parl(?:e|ent|er|ait|aient|es|ons|ez)\s+(?:a|avec)",
    r"ecout(?:e|ent|er|ait|aient|es|ons|ez)\b",
    r"regard(?:e|ent|er|ait|aient|es|ons|ez)\b",
    r"rejoin(?:t|s|drait|dre|dront|dre)\b",
    r"accompagn(?:e|ent|er|ait|aient|es|ons|ez)\b",
    r"sour(?:it|ient|ire|iait|iaient|is|ions|iez)\s+a",
    r"salu(?:e|ent|er|ait|aient|es|ons|ez)\b",
    r"appel(?:le|lent|er|lait|laient|les|ons|ez)\b",
    r"se\s+confie\s+a", r"explique[rnts]?\s+a", r"montre[rnts]?\s+a",
    r"propos(?:e|ent|er|ait|aient|es|ons|ez)\s+a",
    r"demand(?:e|ent|er|ait|aient|es|ons|ez)\s+a",
    r"defi(?:e|ent|er|ait|aient|es|ons|ez)\b",
    r"affront(?:e|ent|er|ait|aient|es|ons|ez)\b",
    r"combat(?:s|tre|tu|tons|ent)?\b",
    r"(?:s[\'’ ])?entrain(?:e|ent|er|ait|aient|es|ons|ez)\s+avec",
    r"est\s+(?:ami|amie|copain|copine|amis)\s+(?:avec|de)",
    r"est\s+l[\'’ ]ami\s+de", r"est\s+avec",
    r"donn(?:e|ent|er|ait|aient|es|ons|ez)\s+(?:une|la)\s+main\s+a",
    r"prend\s+(?:la\s+)?main\s+de",
    r"embrass(?:e|ent|er|ait|aient|es|ons|ez)\b",
    r"prend\s+dans\s+ses\s+bras",
    r"intimid(?:e|ent|er|ait|aient|es|ons|ez)\b",
    r"menac(?:e|ent|er|ait|aient|es|ons|ez)\b",
    # Coordination : 'NPC1 et NPC2' suivi d'un verbe d'interaction
    r"\bet\b",  # naive : 'Naruto et Konohamaru' / 'Sakura et Sasuke' = paire implicite
]


def _build_npc_alias_map() -> dict[str, str]:
    """Mappe les noms usuels (lowercase) vers les ids canon. Reutilise PRIMARY_NPC_NAMES."""
    return dict(PRIMARY_NPC_NAMES)


def _resolve_npc_id(token: str, alias_map: dict[str, str]) -> str | None:
    """Convertit un token court (ex: 'naruto') en id canon principal."""
    return alias_map.get(token.lower())


def _scan_npc_pairs(text: str, alias_map: dict[str, str]) -> set[tuple[str, str]]:
    """Trouve toutes les paires (NPC_X, NPC_Y) en interaction sociale dans le texte.

    Une paire est ordonnee : (X, Y) signifie X interagit avec Y.
    Match heuristique : 'X verbe_social Y' avec X et Y dans alias_map.
    """
    if not text:
        return set()
    lower = text.lower()
    pairs: set[tuple[str, str]] = set()
    # Patterns : (npc_x) (verbe) (npc_y)
    # On compose une regex qui capture deux NPCs avec un verbe social entre eux,
    # avec une fenetre de jusqu'a 60 chars entre les deux noms.
    npc_alt = "|".join(re.escape(n) for n in sorted(alias_map.keys(), key=len, reverse=True))
    if not npc_alt:
        return pairs
    verbs_alt = "|".join(_SOCIAL_VERBS)
    pattern = re.compile(
        rf"\b({npc_alt})\b[^.!?\n]*?\b(?:{verbs_alt})\b[^.!?\n]*?\b({npc_alt})\b",
        re.IGNORECASE,
    )
    for m in pattern.finditer(lower):
        x_token = m.group(1).lower()
        y_token = m.group(2).lower()
        x_id = _resolve_npc_id(x_token, alias_map)
        y_id = _resolve_npc_id(y_token, alias_map)
        if x_id and y_id and x_id != y_id:
            pairs.add((x_id, y_id))
    return pairs


def _check_forbidden_pair(
    canon: CanonBundle, x_id: str, y_id: str, current_year: int
) -> str | None:
    """Verifie si la paire (x_id, y_id) viole les forbidden_relations canon.

    Retourne une raison textuelle si violation, None si OK.
    """
    x = canon.characters.get(x_id)
    y = canon.characters.get(y_id)
    if x is None or y is None:
        return None
    x_age = current_year - x.birth_year if x.birth_year is not None else None
    y_age = current_year - y.birth_year if y.birth_year is not None else None
    # Check du cote de X
    if x_age is not None:
        x_entry = _psycho_entry_at(x_id, x_age)
        if x_entry:
            forbidden = x_entry.get("forbidden_relations") or []
            for forb in forbidden:
                if y_id in forb.lower() or (y.name_romaji and y.name_romaji.lower() in forb.lower()):
                    return (
                        f"{x_id} (age {x_age}) en interaction sociale avec {y_id} : "
                        f"interdit canoniquement [{forb}]"
                    )
    # Check du cote de Y (symetrique)
    if y_age is not None:
        y_entry = _psycho_entry_at(y_id, y_age)
        if y_entry:
            forbidden = y_entry.get("forbidden_relations") or []
            for forb in forbidden:
                if x_id in forb.lower() or (x.name_romaji and x.name_romaji.lower() in forb.lower()):
                    return (
                        f"{y_id} (age {y_age}) en interaction sociale avec {x_id} : "
                        f"interdit canoniquement [{forb}]"
                    )
    return None


def validate_narration_claims(
    canon: CanonBundle,
    *,
    narrative: str,
    observations: list[str],
    npc_dialogue: list[dict],
    proposed_actions: list[dict],
    current_year: int,
) -> list[ClaimViolation]:
    """Scan complet de la sortie LLM pour detecter les violations canon.

    Combine narrative + observations + dialogues + labels d'actions.
    Retourne la liste des violations (vide si tout est OK).
    """
    alias_map = _build_npc_alias_map()
    # Reunit tout le texte a scanner
    all_texts = [narrative]
    all_texts.extend(observations or [])
    for d in npc_dialogue or []:
        line = d.get("line", "")
        if line:
            all_texts.append(line)
    for a in proposed_actions or []:
        label = a.get("label_fr", "") or a.get("label", "")
        if label:
            all_texts.append(label)

    violations: list[ClaimViolation] = []
    seen: set[tuple[str, str, str]] = set()  # deduplique
    for text in all_texts:
        pairs = _scan_npc_pairs(text, alias_map)
        for x_id, y_id in pairs:
            key = ("forbidden_relation", x_id, y_id)
            if key in seen:
                continue
            reason = _check_forbidden_pair(canon, x_id, y_id, current_year)
            if reason:
                seen.add(key)
                violations.append(
                    ClaimViolation(
                        type="forbidden_relation",
                        description=reason,
                        involved_npcs=(x_id, y_id),
                    )
                )
    return violations


def format_violations_for_retry(violations: list[ClaimViolation]) -> str:
    """Formate les violations pour les injecter dans le prompt de retry."""
    if not violations:
        return ""
    lines = ["Ta narration precedente contient les VIOLATIONS CANON suivantes :"]
    for v in violations:
        lines.append(f"  - [{v.type}] {v.description}")
    lines.append(
        "\nReformule la narration en respectant STRICTEMENT les FAITS CANONIQUES NPC. "
        "N'inclus AUCUNE des paires NPC interdites. Si une scene devient impossible, "
        "remplace-la par une narration solitaire ou un PNJ generique (sensei_academie, "
        "marchand_taverne, etc.)."
    )
    return "\n".join(lines)
