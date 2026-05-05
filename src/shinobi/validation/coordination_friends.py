"""Couche : detection de listes d'amis inventees ('X a des amis comme Y, Z, W').

Pattern observe en jeu : 'Naruto a des amis comme Sakura, Shino, Choji et
Kiba'. Cette enumeration brise le canon car a year=6 Naruto est ostracise
et n'a aucun ami.

Approche : regex pour 'amis comme/tels que X, Y, Z et W'. Pour chaque NPC
liste, check :
- si l'un d'eux a des notes psycho stipulant 'pas d'amis / ostracise /
  sans ami' a son age courant (via psycho_notes.json), violation
- si un autre NPC du canon a des `forbidden_relations` qui inclut un
  membre de la liste, violation

NOTE TRANSITOIRE : couche pertinente jusqu'a ce que le KG dynamique (Phase
A de la roadmap) prenne le relai. Voir docs/02-PROJET-ROADMAP-SUITE.md §5.4.
"""

from __future__ import annotations

import re

from shinobi.state.age_calculator import CanonView, get_age
from shinobi.state.world_state import RuntimeState
from shinobi.validation.validator import (
    NarrativeOutput,
    ValidationResult,
)

# 'amis comme X, Y, Z et W' / 'allies tels que ...' / 'compagnons : ...'
# Capture optionnellement le sujet (le NPC dont on parle des amis) en preambule.
_COORDINATION_LIST = re.compile(
    # Sujet optionnel : 'NomNPC ... (a / avait / repond / a des) ... amis'
    r"(?:(?P<subject>[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)"
    r"[^.!?\n]{0,80}?)?"
    r"(?:amis?|allies?|compagnons?|coequipiers?|camarades?)\s+"
    r"(?:comme|tels?\s+que|incluant|notamment|sont|dont|:|avec|;)\s*"
    r"(?P<list>(?:[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?(?:\s*[,;]\s*|\s+et\s+)){1,5}"
    r"[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)",
    re.IGNORECASE,
)


def _resolve_alias(name_token: str) -> str | None:
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


def _psycho_says_no_friends(npc_id: str, age: int) -> str | None:
    """Retourne la note psycho si elle dit 'pas d'amis / ostracise', sinon None."""
    try:
        from shinobi.canon.fact_sheet import _psycho_entry_at  # type: ignore
    except ImportError:
        return None
    entry = _psycho_entry_at(npc_id, age)
    if not entry:
        return None
    note = (entry.get("note") or "").lower()
    if any(kw in note for kw in ("pas d'amis", "sans ami", "ostracise", "ostracisé")):
        return entry.get("note")
    return None


def _forbidden_pair(x_id: str, y_id: str, x_age: int) -> str | None:
    """Si forbidden_relations(x_id, x_age) contient y_id, retourne la raison."""
    try:
        from shinobi.canon.fact_sheet import _psycho_entry_at  # type: ignore
    except ImportError:
        return None
    entry = _psycho_entry_at(x_id, x_age)
    if not entry:
        return None
    for forb in entry.get("forbidden_relations", []) or []:
        forb_low = forb.lower()
        if y_id in forb_low:
            return forb
    return None


class CoordinationFriendsLayer:
    """Couche : detecte les enumerations 'amis comme X, Y, Z'."""

    name = "coordination_friends"

    def validate(
        self,
        *,
        narrative_output: NarrativeOutput,
        state: RuntimeState,
        canon: CanonView,
    ) -> ValidationResult:
        details: list[str] = []
        year = state.narrative_time.approximate_year
        seen_pairs: set[tuple[str, str]] = set()
        seen_solo: set[str] = set()

        # Liste de (texte, speaker_id_optionnel). Pour les dialogues, le
        # speaker est implicite (subject de la phrase 'J'ai des amis...').
        scan_units: list[tuple[str, str | None]] = []
        scan_units.append((narrative_output.narrative, None))
        for obs in narrative_output.world_observations or []:
            scan_units.append((obs, None))
        for d in narrative_output.npc_dialogue:
            if d.line:
                scan_units.append((d.line, d.character_id))

        for text, speaker_id in scan_units:
            if not text:
                continue
            for m in _COORDINATION_LIST.finditer(text):
                chunk = m.group("list")
                subject_token = m.group("subject")
                names = re.split(r"\s*,\s*|\s*;\s*|\s+et\s+", chunk)
                listed_ids: list[str] = []
                for name in names:
                    cid = _resolve_alias(name.strip())
                    if cid:
                        listed_ids.append(cid)

                # Check supplementaire : le SUJET ('Naruto a des amis comme ...')
                # Si on est dans un dialogue, le speaker_id est implicitement
                # le sujet de la phrase ('J'ai des amis ...').
                subject_id = _resolve_alias(subject_token) if subject_token else None
                if subject_id is None and speaker_id:
                    subject_id = speaker_id
                check_targets: list[str] = list(listed_ids)
                if subject_id and subject_id not in check_targets:
                    check_targets.insert(0, subject_id)

                # 1. Check note psycho 'pas d'amis' pour le sujet ET chaque NPC liste
                for cid in check_targets:
                    if cid in seen_solo:
                        continue
                    seen_solo.add(cid)
                    try:
                        age = get_age(cid, year, canon, strict=False)
                    except Exception:
                        continue
                    note = _psycho_says_no_friends(cid, age)
                    if note:
                        role = "sujet de" if cid == subject_id else "liste dans"
                        details.append(
                            f"{cid} (age {age}) {role} une enumeration d'amis "
                            f"alors que sa note psycho canon dit : '{note[:120]}'."
                        )

                # 2. Check forbidden_relations entre paires de la liste
                for i, x_id in enumerate(listed_ids):
                    for y_id in listed_ids[i + 1:]:
                        pair = (x_id, y_id) if x_id < y_id else (y_id, x_id)
                        if pair in seen_pairs:
                            continue
                        seen_pairs.add(pair)
                        try:
                            x_age = get_age(x_id, year, canon, strict=False)
                            y_age = get_age(y_id, year, canon, strict=False)
                        except Exception:
                            continue
                        reason = _forbidden_pair(x_id, y_id, x_age)
                        if reason:
                            details.append(
                                f"{x_id} (age {x_age}) et {y_id} (age {y_age}) "
                                f"co-listes comme amis alors que c'est interdit "
                                f"par le canon : {reason[:120]}."
                            )
                            continue
                        reason = _forbidden_pair(y_id, x_id, y_age)
                        if reason:
                            details.append(
                                f"{y_id} (age {y_age}) et {x_id} (age {x_age}) "
                                f"co-listes comme amis alors que c'est interdit "
                                f"par le canon : {reason[:120]}."
                            )

        if details:
            return ValidationResult(
                is_valid=False,
                layer=self.name,
                reason=f"{len(details)} liste(s) d'amis incoherente(s) avec le canon.",
                details=details,
            )
        return ValidationResult(is_valid=True, layer=self.name)
