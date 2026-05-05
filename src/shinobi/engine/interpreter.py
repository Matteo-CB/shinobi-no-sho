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
    # Natures de chakra : entrainement direct = ninjutsu (les natures
    # specifiques se debloquent via les techniques apprises, pas via stat).
    "katon": "ninjutsu",
    "suiton": "ninjutsu",
    "fuuton": "ninjutsu",
    "futon": "ninjutsu",
    "doton": "ninjutsu",
    "raiton": "ninjutsu",
    "mokuton": "ninjutsu",
    "hyouton": "ninjutsu",
    "youton": "ninjutsu",
    "feu": "ninjutsu",
    "eau": "ninjutsu",
    "vent": "ninjutsu",
    "terre": "ninjutsu",
    "foudre": "ninjutsu",
    "glace": "ninjutsu",
    # Stats intangibles (entrainables mais lentes + necessitent activites specifiques)
    "beaute": "beauty",
    "beauty": "beauty",
    "apparence": "beauty",
    "physique": "beauty",
    "luck": "luck",
    "chance": "luck",
}

# Stats absolument non entrainables (genetique pure).
LINEAGE_STATS = {"lineage_value", "chakra_reserves"}

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
    r"\bmaitriser?\b",
    r"\blearn\b",
]
# Etudier / suivre des cours : entrainement passif (theorie), gain x0.5
STUDY_PATTERNS = [
    r"\betudier?\b",
    r"\bj[' ]etudie\b",
    r"\bj[' ]ecoute (?:les |des |un |le )?cours\b",
    r"\becouter (?:les |des |un |le )?cours\b",
    r"\b(?:suivre|suis|reprendre|reprends|continuer|continue) (?:les |des |un |le )?cours\b",
    r"\b(?:rester|reste) en classe\b",
    r"\bj[' ]assiste (?:au|aux|a un|a des) (?:cours|leco?ns?|seminaires?)\b",
    r"\bje lis (?:un livre|le livre|des livres|des manuels|le manuel|un parchemin|des parchemins)\b",
    r"\bje revise\b",
    r"\bje reviser?\b",
    r"\bj[' ]observe (?:un|le|les|mon) (?:sensei|maitre|jonin|chunin|professeur)\b",
    r"\btheorie\b",
    r"\bcours (?:de|sur|d['' ])\b",
]
REST_PATTERNS = [r"\bdor(?:s|t|mir|mait|mais)\b", r"\bsommeil\b", r"\bsleep\b"]
RELAX_PATTERNS = [
    r"\bje (?:me )?repose\b",
    r"\bme repose[r]?\b",
    r"\brest(?:e|er|es)\b",
    r"\bpause\b",
]
MEDITATE_PATTERNS = [r"\bje medite\b", r"\bmediter\b", r"\bmeditation\b", r"\bmeditate\b"]
WAIT_PATTERNS = [r"\bj[' ]attends\b", r"\battendre\b", r"\bpasser le temps\b", r"\bwait\b"]
WORK_PATTERNS = [
    r"\btravaill(?:e|er|es|ons|ent)\b",
    r"\bbosser\b",
    r"\bgagner.*ryos\b",
    r"\bwork\b",
]
MISSION_PATTERNS = [r"\bmission\b", r"\bquete\b", r"\bquest\b", r"\baccept.*mission\b"]
COMBAT_PATTERNS = [
    r"\bcombat(?:s|tre|tu|tons)?\b",
    r"\battaqu(?:e|er|es)\b",
    r"\bbattle\b",
    r"\bje me bats\b",
    r"\bj[' ]affronte\b",
]
TALK_PATTERNS = [
    r"\bje parle\b", r"\bdemand[er]\b", r"\bquestion\b", r"\bdiscut[er]\b",
    r"\bje (?:demande|cherche|rejoins|interroge|salue)\b",
    r"\bje vais (?:voir|chercher|trouver|rencontrer|saluer|interpeller)\b",
    r"\bje (?:m[' ]approche|m[' ]adresse) (?:de|a|au|aux)\b",
    r"\bje veux (?:parler|demander|interroger|voir|rencontrer)\b",
]
MOVE_PATTERNS = [
    r"\bvoyag[er]\b",
    r"\bme rends?\b",
    r"\baller a\b",
    r"\bje vais a\b",
    r"\bje pars\b",
]
INTIMIDATE_PATTERNS = [r"\bintimid[er]\b", r"\bmenac[er]\b"]
SEDUCE_PATTERNS = [r"\bseduire\b", r"\bdraguer?\b"]
STEAL_PATTERNS = [r"\bvol[er]\b", r"\bderober?\b", r"\bsubtilis[er]\b"]
SPY_PATTERNS = [r"\bespionner?\b", r"\bsuivre.*discret\b", r"\bobserver?.*discret\b"]
RESEARCH_PATTERNS = [r"\brecherch[er]\b", r"\benquet[er]\b", r"\binvestiguer?\b"]
BUY_PATTERNS = [r"\bj[' ]ach[ea]te[r]?\b", r"\bachet[er]\b", r"\bbuy\b"]
SELL_PATTERNS = [r"\bje vends\b", r"\bvendre\b", r"\bsell\b"]
USE_ITEM_PATTERNS = [r"\bje (?:bois|mange|consomme|utilise|avale)\b", r"\butilise[r]?\b"]
DECLARE_GOAL_PATTERNS = [
    r"\bje (?:decide|declare|m[' ]engage|veux devenir|veux maitriser|jure)\b",
    r"\bmon objectif\b",
    r"\bje me fixe (?:l[' ]objectif|comme but)\b",
    r"\bdeclare?r? un objectif\b",
]
PATH_REQUEST_PATTERNS = [
    r"\bje cherche (?:le |un )?chemin (?:vers|pour|de)\b",
    r"\bcomment (?:atteindre|parvenir|y arriver)\b.*\b(?:objectif|but|goal)\b",
    r"\bje demande (?:la voie|le chemin) (?:vers|pour)\b",
    r"\bquelle (?:est la|voie) (?:meilleure )?(?:methode|approche|strategie) (?:pour|vers)\b",
    r"\bpathfinder\b", r"\bdonne[r]? un indice\b",
]
INFO_PAYMENT_PATTERNS = [
    r"\bje paie pour (?:des |un )?(?:info|renseignement|indice|tuyau)\w*",
    r"\bje (?:demande|cherche|achete) (?:un|des) (?:indice|info|renseignement|tuyau)\w*",
    r"\bje soudoie (?:pour|afin de)\b",
    r"\bje glisse (?:un peu d[' ])?argent\b",
]
PRAY_PATTERNS = [r"\bje prie\b", r"\bprier\b", r"\bpriere\b", r"\bje me recueille\b"]
CHALLENGE_PATTERNS = [
    r"\bje defi(?:e|er|es)\b",
    r"\bje provoque (?:en duel|en combat)\b",
    r"\blancer? un defi\b",
]
DESERT_PATTERNS = [
    r"\bje deserte\b",
    r"\bje fuis (?:le|mon) village\b",
    r"\bje quitte (?:le|mon) village (?:pour de bon|definitivement)\b",
    r"\bje deviens nukenin\b",
    r"\bje renonce a (?:mon|ce) village\b",
    r"\bje brise (?:mon |le )?bandeau\b",
]


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

    # Ordre prioritaire : declare/path/info > mission > combat > learn > train > rest > work > talk > move > custom
    if _matches_any(lower, DECLARE_GOAL_PATTERNS):
        return ParsedIntent(
            action_type=ActionType.declare_goal,
            parameters={"description": text.strip()},
            summary=summary,
        )
    if _matches_any(lower, PATH_REQUEST_PATTERNS):
        return ParsedIntent(
            action_type=ActionType.request_objective_path,
            parameters={},
            summary=summary,
        )
    if _matches_any(lower, INFO_PAYMENT_PATTERNS):
        # Detection grossiere d'un montant en ryos.
        amount_match = re.search(r"(\d+)\s*(?:ryos|r)\b", lower)
        amount = int(amount_match.group(1)) if amount_match else 100
        return ParsedIntent(
            action_type=ActionType.pay_for_information,
            parameters={"amount_ryos": amount},
            summary=summary,
        )
    if _matches_any(lower, MISSION_PATTERNS):
        return ParsedIntent(
            action_type=ActionType.accept_mission,
            parameters={},
            summary=summary,
        )
    if _matches_any(lower, DESERT_PATTERNS):
        return ParsedIntent(
            action_type=ActionType.custom,
            parameters={"_desert": True},
            summary=summary,
        )
    if _matches_any(lower, CHALLENGE_PATTERNS):
        return ParsedIntent(action_type=ActionType.challenge, parameters={}, summary=summary)
    if _matches_any(lower, COMBAT_PATTERNS):
        return ParsedIntent(action_type=ActionType.fight, parameters={}, summary=summary)
    if _matches_any(lower, PRAY_PATTERNS):
        return ParsedIntent(
            action_type=ActionType.pray,
            parameters={"duration_hours": duration_hours or 1},
            summary=summary,
        )
    # STUDY (cours, lecture, theorie) : train_stat avec quality_modifier 0.5
    # On le check AVANT learn_technique car "etudier" est dans les deux,
    # et avant train pour eviter le faux positif "j'etudie" -> learn.
    if _matches_any(lower, STUDY_PATTERNS):
        stat = _detect_stat(lower)
        return ParsedIntent(
            action_type=ActionType.train_stat,
            parameters={
                "stat": stat or "intelligence",
                "duration_hours": duration_hours or 4,
                "quality_modifier": 0.5,  # theorie = moitie d'efficacite vs pratique
                "_study_mode": True,
            },
            summary=summary,
        )
    if _matches_any(lower, LEARN_PATTERNS):
        # tentative d'extraction du nom de technique apres "apprendre"
        m = re.search(
            r"(?:apprends|apprendre|maitrise|maitriser)\s+(?:le |la |les |l['' ])?([^.,;]+)",
            lower,
        )
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
    if _matches_any(lower, BUY_PATTERNS):
        return ParsedIntent(action_type=ActionType.buy, parameters={}, summary=summary)
    if _matches_any(lower, SELL_PATTERNS):
        return ParsedIntent(action_type=ActionType.sell, parameters={}, summary=summary)
    if _matches_any(lower, USE_ITEM_PATTERNS):
        item_match = re.search(
            r"(?:utilise[r]?|bois|mange|consomme|avale)\s+(?:un\s+|une\s+|le\s+|la\s+|du\s+|de\s+la\s+|mon\s+|ma\s+)?([a-z_]+)",
            lower,
        )
        return ParsedIntent(
            action_type=ActionType.custom,
            parameters={"_use_item": item_match.group(1) if item_match else ""},
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
        target = _extract_destination(lower)
        params: dict[str, object] = {}
        if target:
            params["target_location"] = target
        return ParsedIntent(action_type=ActionType.move, parameters=params, summary=summary)
    return ParsedIntent(action_type=ActionType.custom, parameters={}, summary=summary)


_KNOWN_VILLAGES = {
    "konoha": "konohagakure",
    "konohagakure": "konohagakure",
    "suna": "sunagakure",
    "sunagakure": "sunagakure",
    "kiri": "kirigakure",
    "kirigakure": "kirigakure",
    "kumo": "kumogakure",
    "kumogakure": "kumogakure",
    "iwa": "iwagakure",
    "iwagakure": "iwagakure",
    "ame": "amegakure",
    "amegakure": "amegakure",
    "oto": "otogakure",
    "otogakure": "otogakure",
    "taki": "takigakure",
    "takigakure": "takigakure",
    "kusa": "kusagakure",
    "kusagakure": "kusagakure",
    "yuki": "yukigakure",
    "yukigakure": "yukigakure",
}


def _extract_destination(lower: str) -> str | None:
    """Cherche un nom de village connu apres 'vers' / 'a' / 'pour'."""
    m = re.search(r"(?:vers|a|pour|jusqu['' ]a)\s+([a-z]+)", lower)
    if m:
        token = m.group(1)
        return _KNOWN_VILLAGES.get(token)
    for token, vid in _KNOWN_VILLAGES.items():
        if f" {token}" in f" {lower}":
            return vid
    return None
