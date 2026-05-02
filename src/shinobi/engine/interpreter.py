"""Interpreteur d'intentions joueur : texte libre vers ActionType + parametres.

Heuristique cote moteur (deterministe). Si l'intention est ambigue, on tombe
sur ActionType.custom et le narrateur LLM se charge de l'interpretation contextuelle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from shinobi.types import ActionType


@dataclass(frozen=True)
class ParsedIntent:
    """Intention extraite du texte libre."""

    action_type: ActionType
    parameters: dict[str, object]
    summary: str


# Mapping mots-cles -> stat databook (utilise par train_stat).
STAT_KEYWORDS: dict[str, str] = {
    "ninjutsu": "ninjutsu",
    "taijutsu": "taijutsu",
    "genjutsu": "genjutsu",
    "intelligence": "intelligence",
    "intellect": "intelligence",
    "force": "strength",
    "strength": "strength",
    "vitesse": "speed",
    "speed": "speed",
    "agilite": "speed",
    "endurance": "stamina",
    "stamina": "stamina",
    "constitution": "stamina",
    "hand seals": "hand_seals",
    "mudra": "hand_seals",
    "hand_seals": "hand_seals",
    "controle": "chakra_control",
    "chakra control": "chakra_control",
    "chakra_control": "chakra_control",
    "volonte": "willpower",
    "willpower": "willpower",
    "perception": "perception",
    "charisme": "social_charisma",
    "charisma": "social_charisma",
    "leadership": "leadership",
    "medical": "medical_knowledge",
    "medecine": "medical_knowledge",
    "fuinjutsu": "fuinjutsu_knowledge",
    "fuin": "fuinjutsu_knowledge",
    "senjutsu": "senjutsu_aptitude",
}

# Mots-cles pour identifier le type d'action.
TRAIN_PATTERNS = [
    r"\bm[' ]entrai?ne[r]?\b",
    r"\bj[' ]entrai?ne[r]?\b",
    r"\bs[' ]entrai?ner\b",
    r"\bentrai?nement\b",
    r"\btrain\b",
    r"\bpratique[r]?\b",
    r"\bameliorer?\b",
]
LEARN_PATTERNS = [
    r"\bj[' ]apprends\b",
    r"\bapprendre\b",
    r"\betudier?\b",
    r"\bj[' ]etudie\b",
    r"\bmaitriser?\b",
    r"\blearn\b",
]
REST_PATTERNS = [r"\bdor(?:s|t|mir|mait|mais)\b", r"\bsommeil\b", r"\bsleep\b"]
RELAX_PATTERNS = [r"\bje (?:me )?repose\b", r"\bme repose[r]?\b", r"\brest(?:e|er|es)\b", r"\bpause\b"]
MEDITATE_PATTERNS = [r"\bje medite\b", r"\bmediter\b", r"\bmeditation\b", r"\bmeditate\b"]
WAIT_PATTERNS = [r"\bj[' ]attends\b", r"\battendre\b", r"\bpasser le temps\b", r"\bwait\b"]
WORK_PATTERNS = [r"\btravaill(?:e|er|es|ons|ent)\b", r"\bbosser\b", r"\bgagner.*ryos\b", r"\bwork\b"]
MISSION_PATTERNS = [r"\bmission\b", r"\bquete\b", r"\bquest\b", r"\baccept.*mission\b"]
COMBAT_PATTERNS = [r"\bcombat(?:s|tre|tu|tons)?\b", r"\battaqu(?:e|er|es)\b", r"\bbattle\b", r"\bje me bats\b", r"\bj[' ]affronte\b"]
TALK_PATTERNS = [r"\bje parle\b", r"\bdemand[er]\b", r"\bquestion\b", r"\bdiscut[er]\b"]
MOVE_PATTERNS = [r"\bvoyag[er]\b", r"\bme rends?\b", r"\baller a\b", r"\bje vais a\b", r"\bje pars\b"]
INTIMIDATE_PATTERNS = [r"\bintimid[er]\b", r"\bmenac[er]\b"]
SEDUCE_PATTERNS = [r"\bseduire\b", r"\bdraguer?\b"]
STEAL_PATTERNS = [r"\bvol[er]\b", r"\bderober?\b", r"\bsubtilis[er]\b"]
SPY_PATTERNS = [r"\bespionner?\b", r"\bsuivre.*discret\b", r"\bobserver?.*discret\b"]
RESEARCH_PATTERNS = [r"\brecherch[er]\b", r"\benquet[er]\b", r"\binvestiguer?\b"]


def _matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text) for p in patterns)


def _detect_stat(text: str) -> str | None:
    for kw, stat in STAT_KEYWORDS.items():
        if kw in text:
            return stat
    return None


def _detect_duration_hours(text: str) -> int | None:
    """Extrait une duree en heures depuis le texte si presente."""
    m = re.search(r"(\d+)\s*(heure|h|hour)", text)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*(jour|day)", text)
    if m:
        return int(m.group(1)) * 8  # 8h utiles par jour
    m = re.search(r"(\d+)\s*(semaine|week)", text)
    if m:
        return int(m.group(1)) * 7 * 8
    m = re.search(r"(\d+)\s*(mois|month)", text)
    if m:
        return int(m.group(1)) * 30 * 8
    return None


def interpret(text: str) -> ParsedIntent:
    """Parse une intention texte libre vers une intention structuree."""
    lower = text.lower().strip()
    summary = text.strip()

    duration_hours = _detect_duration_hours(lower)

    # Ordre prioritaire : mission > combat > learn > train > rest/sleep/meditate > work > talk > move > custom
    if _matches_any(lower, MISSION_PATTERNS):
        return ParsedIntent(
            action_type=ActionType.accept_mission,
            parameters={},
            summary=summary,
        )
    if _matches_any(lower, COMBAT_PATTERNS):
        return ParsedIntent(action_type=ActionType.fight, parameters={}, summary=summary)
    if _matches_any(lower, LEARN_PATTERNS):
        # tentative d'extraction du nom de technique apres "apprendre"
        m = re.search(r"(?:apprends|apprendre|etudie|etudier|maitrise|maitriser)\s+(?:le |la |les |l['' ])?([^.,;]+)", lower)
        target = m.group(1).strip() if m else ""
        return ParsedIntent(
            action_type=ActionType.train_technique,
            parameters={"target_name": target, "duration_hours": duration_hours or 8},
            summary=summary,
        )
    if _matches_any(lower, TRAIN_PATTERNS):
        stat = _detect_stat(lower)
        return ParsedIntent(
            action_type=ActionType.train_stat,
            parameters={"stat": stat or "stamina", "duration_hours": duration_hours or 4},
            summary=summary,
        )
    if _matches_any(lower, MEDITATE_PATTERNS):
        return ParsedIntent(
            action_type=ActionType.meditate,
            parameters={"duration_hours": duration_hours or 1},
            summary=summary,
        )
    if _matches_any(lower, REST_PATTERNS):
        return ParsedIntent(
            action_type=ActionType.rest,
            parameters={"duration_hours": duration_hours or 8, "sleep": True},
            summary=summary,
        )
    if _matches_any(lower, RELAX_PATTERNS):
        return ParsedIntent(
            action_type=ActionType.rest,
            parameters={"duration_hours": duration_hours or 1, "sleep": False},
            summary=summary,
        )
    if _matches_any(lower, WAIT_PATTERNS):
        return ParsedIntent(
            action_type=ActionType.wait,
            parameters={"duration_hours": duration_hours or 1},
            summary=summary,
        )
    if _matches_any(lower, WORK_PATTERNS):
        return ParsedIntent(
            action_type=ActionType.work,
            parameters={"duration_hours": duration_hours or 6},
            summary=summary,
        )
    if _matches_any(lower, INTIMIDATE_PATTERNS):
        return ParsedIntent(action_type=ActionType.intimidate, parameters={}, summary=summary)
    if _matches_any(lower, SEDUCE_PATTERNS):
        return ParsedIntent(action_type=ActionType.seduce, parameters={}, summary=summary)
    if _matches_any(lower, STEAL_PATTERNS):
        return ParsedIntent(action_type=ActionType.steal, parameters={}, summary=summary)
    if _matches_any(lower, SPY_PATTERNS):
        return ParsedIntent(action_type=ActionType.spy, parameters={}, summary=summary)
    if _matches_any(lower, RESEARCH_PATTERNS):
        return ParsedIntent(
            action_type=ActionType.research,
            parameters={"duration_hours": duration_hours or 2},
            summary=summary,
        )
    if _matches_any(lower, TALK_PATTERNS):
        return ParsedIntent(action_type=ActionType.talk, parameters={}, summary=summary)
    if _matches_any(lower, MOVE_PATTERNS):
        return ParsedIntent(action_type=ActionType.move, parameters={}, summary=summary)
    return ParsedIntent(action_type=ActionType.custom, parameters={}, summary=summary)
