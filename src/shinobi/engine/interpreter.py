"""Interpreteur d'intentions joueur : texte libre vers ActionType + parametres.

Heuristique cote moteur (deterministe). Si l'intention est ambigue, on tombe
sur ActionType.custom et le narrateur LLM se charge de l'interpretation contextuelle.

Phase i18n.4 : les patterns regex sont scopes par langue via `_PATTERNS_BY_LANG`.
La langue courante est lue depuis `shinobi.i18n.current_language()`. Si la langue
courante n'a pas de pack regex defini, on retombe sur le pack FR (qui est la
source historique et la plus dense). Cela garantit que :
- Les regex FR continuent de matcher pour le canon (les patterns FR ne sont
  jamais perdus, meme en mode EN/JA/etc).
- Les regex EN sont ajoutees pour les commandes joueur en anglais.
- Les autres locales (es/ja/zh/ko/pt-BR/de) seront ajoutees plus tard ; en
  attendant elles tombent en fallback FR sans erreur.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from shinobi.i18n import get_active_language
from shinobi.types import ActionType


@dataclass(frozen=True)
class ParsedIntent:
    """Intention extraite du texte libre."""

    action_type: ActionType
    parameters: dict[str, object]
    summary: str


# Mapping mots-cles -> stat databook (utilise par train_stat).
# Multi-locale par construction : on accepte EN + FR keywords pour chaque stat.
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
    "fire": "ninjutsu",
    "water": "ninjutsu",
    "wind": "ninjutsu",
    "earth": "ninjutsu",
    "lightning": "ninjutsu",
    "ice": "ninjutsu",
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


# Pack de patterns FR (source historique).
_FR_PATTERNS: dict[str, list[str]] = {
    "train": [
        r"\bm[' ]entrai?ne[r]?\b",
        r"\bj[' ]entrai?ne[r]?\b",
        r"\bs[' ]entrai?ner\b",
        r"\bentrai?nement\b",
        r"\btrain\b",
        r"\bpratique[r]?\b",
        r"\bameliorer?\b",
    ],
    "learn": [
        r"\bj[' ]apprends\b",
        r"\bapprendre\b",
        r"\bmaitriser?\b",
        r"\blearn\b",
    ],
    "study": [
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
    ],
    "rest": [r"\bdor(?:s|t|mir|mait|mais)\b", r"\bsommeil\b", r"\bsleep\b"],
    "relax": [
        r"\bje (?:me )?repose\b",
        r"\bme repose[r]?\b",
        r"\brest(?:e|er|es)\b",
        r"\bpause\b",
    ],
    "meditate": [r"\bje medite\b", r"\bmediter\b", r"\bmeditation\b", r"\bmeditate\b"],
    "wait": [r"\bj[' ]attends\b", r"\battendre\b", r"\bpasser le temps\b", r"\bwait\b"],
    "work": [
        r"\btravaill(?:e|er|es|ons|ent)\b",
        r"\bbosser\b",
        r"\bgagner.*ryos\b",
        r"\bwork\b",
    ],
    "mission": [r"\bmission\b", r"\bquete\b", r"\bquest\b", r"\baccept.*mission\b"],
    "combat": [
        r"\bcombat(?:s|tre|tu|tons)?\b",
        r"\battaqu(?:e|er|es)\b",
        r"\bbattle\b",
        r"\bje me bats\b",
        r"\bj[' ]affronte\b",
    ],
    "talk": [
        r"\bje parle\b", r"\bdemand[er]\b", r"\bquestion\b", r"\bdiscut[er]\b",
        r"\bje (?:demande|cherche|rejoins|interroge|salue)\b",
        r"\bje vais (?:voir|chercher|trouver|rencontrer|saluer|interpeller)\b",
        r"\bje (?:m[' ]approche|m[' ]adresse) (?:de|a|au|aux)\b",
        r"\bje veux (?:parler|demander|interroger|voir|rencontrer)\b",
    ],
    "move": [
        r"\bvoyag[er]\b",
        r"\bme rends?\b",
        r"\baller a\b",
        r"\bje vais a\b",
        r"\bje pars\b",
    ],
    "intimidate": [r"\bintimid[er]\b", r"\bmenac[er]\b"],
    "seduce": [r"\bseduire\b", r"\bdraguer?\b"],
    "steal": [r"\bvol[er]\b", r"\bderober?\b", r"\bsubtilis[er]\b"],
    "spy": [r"\bespionner?\b", r"\bsuivre.*discret\b", r"\bobserver?.*discret\b"],
    "research": [r"\brecherch[er]\b", r"\benquet[er]\b", r"\binvestiguer?\b"],
    "buy": [r"\bj[' ]ach[ea]te[r]?\b", r"\bachet[er]\b", r"\bbuy\b"],
    "sell": [r"\bje vends\b", r"\bvendre\b", r"\bsell\b"],
    "use_item": [r"\bje (?:bois|mange|consomme|utilise|avale)\b", r"\butilise[r]?\b"],
    "declare_goal": [
        r"\bje (?:decide|declare|m[' ]engage|veux devenir|veux maitriser|jure)\b",
        r"\bmon objectif\b",
        r"\bje me fixe (?:l[' ]objectif|comme but)\b",
        r"\bdeclare?r? un objectif\b",
    ],
    "path_request": [
        r"\bje cherche (?:le |un )?chemin (?:vers|pour|de)\b",
        r"\bcomment (?:atteindre|parvenir|y arriver)\b.*\b(?:objectif|but|goal)\b",
        r"\bje demande (?:la voie|le chemin) (?:vers|pour)\b",
        r"\bquelle (?:est la|voie) (?:meilleure )?(?:methode|approche|strategie) (?:pour|vers)\b",
        r"\bpathfinder\b", r"\bdonne[r]? un indice\b",
    ],
    "info_payment": [
        r"\bje paie pour (?:des |un )?(?:info|renseignement|indice|tuyau)\w*",
        r"\bje (?:demande|cherche|achete) (?:un|des) (?:indice|info|renseignement|tuyau)\w*",
        r"\bje soudoie (?:pour|afin de)\b",
        r"\bje glisse (?:un peu d[' ])?argent\b",
    ],
    "pray": [r"\bje prie\b", r"\bprier\b", r"\bpriere\b", r"\bje me recueille\b"],
    "challenge": [
        r"\bje defi(?:e|er|es)\b",
        r"\bje provoque (?:en duel|en combat)\b",
        r"\blancer? un defi\b",
    ],
    "desert": [
        r"\bje deserte\b",
        r"\bje fuis (?:le|mon) village\b",
        r"\bje quitte (?:le|mon) village (?:pour de bon|definitivement)\b",
        r"\bje deviens nukenin\b",
        r"\bje renonce a (?:mon|ce) village\b",
        r"\bje brise (?:mon |le )?bandeau\b",
    ],
}


# Pack EN : verbes/expressions joueur typiques en anglais.
_EN_PATTERNS: dict[str, list[str]] = {
    "train": [
        r"\bi (?:want to )?train\b", r"\btrain (?:my|the)\b", r"\bpractice\b",
        r"\bworkout\b", r"\bimprove\b",
    ],
    "learn": [
        r"\bi (?:want to )?learn\b", r"\bmaster\b", r"\bstudy how to\b",
    ],
    "study": [
        r"\bi study\b", r"\bstudy(?:ing)?\b",
        r"\bi (?:listen to|attend|follow|sit through|stay in) (?:the |a |my )?(?:class|course|lecture|lesson|seminar)\b",
        r"\bi read (?:a |the |some )?(?:book|books|manual|scroll|scrolls)\b",
        r"\bi review\b", r"\bi revise\b",
        r"\bi observe (?:a|the|my) (?:sensei|master|jonin|chunin|teacher|professor)\b",
        r"\btheory\b", r"\bclass on\b",
    ],
    "rest": [r"\bi sleep\b", r"\bsleep\b", r"\bgo to bed\b"],
    "relax": [r"\bi rest\b", r"\brest\b", r"\btake a break\b", r"\bpause\b"],
    "meditate": [r"\bi meditate\b", r"\bmeditate\b", r"\bmeditation\b"],
    "wait": [r"\bi wait\b", r"\bwait\b", r"\bpass the time\b", r"\bkill time\b"],
    "work": [r"\bi work\b", r"\bwork\b", r"\bearn (?:some )?ryos\b"],
    "mission": [r"\bmission\b", r"\bquest\b", r"\baccept (?:the |a )?mission\b"],
    "combat": [
        r"\bi fight\b", r"\bfight\b", r"\bbattle\b", r"\battack\b",
        r"\bi attack\b", r"\bi face\b", r"\bi confront\b",
    ],
    "talk": [
        r"\bi talk\b", r"\bi speak\b", r"\bi ask\b", r"\bi greet\b",
        r"\bi (?:approach|address) (?:him|her|them|the)\b",
        r"\bi want to (?:talk|speak|ask|see|meet)\b",
        r"\bi go (?:see|find|meet|greet)\b",
    ],
    "move": [
        r"\btravel\b", r"\bgo to\b", r"\bi head (?:to|toward)\b",
        r"\bi leave\b", r"\bi depart\b",
    ],
    "intimidate": [r"\bintimidate\b", r"\bthreaten\b"],
    "seduce": [r"\bseduce\b", r"\bflirt\b"],
    "steal": [r"\bsteal\b", r"\bswipe\b", r"\bpilfer\b"],
    "spy": [r"\bspy\b", r"\bdiscretly follow\b", r"\bobserve.*discret\b"],
    "research": [r"\bresearch\b", r"\binvestigate\b", r"\binquire\b"],
    "buy": [r"\bi buy\b", r"\bi purchase\b", r"\bbuy\b"],
    "sell": [r"\bi sell\b", r"\bsell\b"],
    "use_item": [
        r"\bi (?:drink|eat|consume|use|swallow)\b", r"\buse\b",
    ],
    "declare_goal": [
        r"\bi (?:decide|declare|commit|swear|want to become|want to master)\b",
        r"\bmy goal\b", r"\bmy objective\b",
        r"\bi set (?:myself )?(?:the goal|a goal|the objective)\b",
        r"\bdeclare (?:a |an )?(?:goal|objective)\b",
    ],
    "path_request": [
        r"\bi (?:seek|look for) (?:a |the )?(?:path|way) (?:to|toward)\b",
        r"\bhow (?:do i|can i|to) (?:reach|achieve|get)\b.*\b(?:goal|objective|target)\b",
        r"\bi ask for (?:a |the )?(?:path|way) (?:to|toward)\b",
        r"\bwhat (?:is the|the) best (?:method|approach|strategy) (?:to|for)\b",
        r"\bpathfinder\b", r"\bgive (?:me )?a hint\b",
    ],
    "info_payment": [
        r"\bi pay for (?:some |an? )?(?:info|information|tip|intel|hint)\w*",
        r"\bi (?:ask|seek|buy) (?:a |an |some )?(?:hint|info|information|tip|intel)\w*",
        r"\bi bribe (?:for|to)\b",
        r"\bi slip (?:some )?money\b",
    ],
    "pray": [r"\bi pray\b", r"\bpray\b", r"\bprayer\b", r"\bi recollect myself\b"],
    "challenge": [
        r"\bi challenge\b", r"\bi provoke (?:a duel|to combat)\b",
        r"\bissue a challenge\b",
    ],
    "desert": [
        r"\bi desert\b", r"\bi flee (?:the|my) village\b",
        r"\bi leave (?:the|my) village (?:for good|forever|definitively)\b",
        r"\bi become (?:a )?nukenin\b",
        r"\bi renounce (?:my|this) village\b",
        r"\bi break (?:my |the )?headband\b",
    ],
}


# Registre des packs par locale. Les locales absentes retombent sur FR
# (la source la plus dense). Phase i18n.5 ajoutera ES/JA/ZH/KO/PT-BR/DE.
_PATTERNS_BY_LANG: dict[str, dict[str, list[str]]] = {
    "fr": _FR_PATTERNS,
    "en": _EN_PATTERNS,
}


def _patterns(category: str) -> list[str]:
    """Patterns du `category` pour la langue courante + fallback FR."""
    lang = get_active_language()
    pack = _PATTERNS_BY_LANG.get(lang) or _PATTERNS_BY_LANG["fr"]
    out = list(pack.get(category, ()))
    # Toujours fusionner avec FR (source historique, comprends les ids canon
    # comme "katon", "kuchiyose" etc qui sont identiques en toutes langues).
    if lang != "fr":
        for p in _FR_PATTERNS.get(category, ()):
            if p not in out:
                out.append(p)
    return out


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
    if _matches_any(lower, _patterns("declare_goal")):
        return ParsedIntent(
            action_type=ActionType.declare_goal,
            parameters={"description": text.strip()},
            summary=summary,
        )
    if _matches_any(lower, _patterns("path_request")):
        return ParsedIntent(
            action_type=ActionType.request_objective_path,
            parameters={},
            summary=summary,
        )
    if _matches_any(lower, _patterns("info_payment")):
        # Detection grossiere d'un montant en ryos.
        amount_match = re.search(r"(\d+)\s*(?:ryos|r)\b", lower)
        amount = int(amount_match.group(1)) if amount_match else 100
        return ParsedIntent(
            action_type=ActionType.pay_for_information,
            parameters={"amount_ryos": amount},
            summary=summary,
        )
    if _matches_any(lower, _patterns("mission")):
        return ParsedIntent(
            action_type=ActionType.accept_mission,
            parameters={},
            summary=summary,
        )
    if _matches_any(lower, _patterns("desert")):
        return ParsedIntent(
            action_type=ActionType.custom,
            parameters={"_desert": True},
            summary=summary,
        )
    if _matches_any(lower, _patterns("challenge")):
        return ParsedIntent(action_type=ActionType.challenge, parameters={}, summary=summary)
    if _matches_any(lower, _patterns("combat")):
        return ParsedIntent(action_type=ActionType.fight, parameters={}, summary=summary)
    if _matches_any(lower, _patterns("pray")):
        return ParsedIntent(
            action_type=ActionType.pray,
            parameters={"duration_hours": duration_hours or 1},
            summary=summary,
        )
    # STUDY (cours, lecture, theorie) : train_stat avec quality_modifier 0.5
    # On le check AVANT learn_technique car "etudier" est dans les deux,
    # et avant train pour eviter le faux positif "j'etudie" -> learn.
    if _matches_any(lower, _patterns("study")):
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
    if _matches_any(lower, _patterns("learn")):
        # tentative d'extraction du nom de technique apres "apprendre"
        m = re.search(
            r"(?:apprends|apprendre|maitrise|maitriser|learn|master)\s+(?:le |la |les |l['' ]|the )?([^.,;]+)",
            lower,
        )
        target = m.group(1).strip() if m else ""
        return ParsedIntent(
            action_type=ActionType.train_technique,
            parameters={"target_name": target, "duration_hours": duration_hours or 8},
            summary=summary,
        )
    if _matches_any(lower, _patterns("train")):
        stat = _detect_stat(lower)
        return ParsedIntent(
            action_type=ActionType.train_stat,
            parameters={"stat": stat or "stamina", "duration_hours": duration_hours or 4},
            summary=summary,
        )
    if _matches_any(lower, _patterns("meditate")):
        return ParsedIntent(
            action_type=ActionType.meditate,
            parameters={"duration_hours": duration_hours or 1},
            summary=summary,
        )
    if _matches_any(lower, _patterns("rest")):
        return ParsedIntent(
            action_type=ActionType.rest,
            parameters={"duration_hours": duration_hours or 8, "sleep": True},
            summary=summary,
        )
    if _matches_any(lower, _patterns("relax")):
        return ParsedIntent(
            action_type=ActionType.rest,
            parameters={"duration_hours": duration_hours or 1, "sleep": False},
            summary=summary,
        )
    if _matches_any(lower, _patterns("wait")):
        return ParsedIntent(
            action_type=ActionType.wait,
            parameters={"duration_hours": duration_hours or 1},
            summary=summary,
        )
    if _matches_any(lower, _patterns("work")):
        return ParsedIntent(
            action_type=ActionType.work,
            parameters={"duration_hours": duration_hours or 6},
            summary=summary,
        )
    if _matches_any(lower, _patterns("buy")):
        return ParsedIntent(action_type=ActionType.buy, parameters={}, summary=summary)
    if _matches_any(lower, _patterns("sell")):
        return ParsedIntent(action_type=ActionType.sell, parameters={}, summary=summary)
    if _matches_any(lower, _patterns("use_item")):
        item_match = re.search(
            r"(?:utilise[r]?|bois|mange|consomme|avale|use|drink|eat|consume|swallow)\s+(?:un\s+|une\s+|le\s+|la\s+|du\s+|de\s+la\s+|mon\s+|ma\s+|the\s+|a\s+|an\s+|my\s+)?([a-z_]+)",
            lower,
        )
        return ParsedIntent(
            action_type=ActionType.custom,
            parameters={"_use_item": item_match.group(1) if item_match else ""},
            summary=summary,
        )
    if _matches_any(lower, _patterns("intimidate")):
        return ParsedIntent(action_type=ActionType.intimidate, parameters={}, summary=summary)
    if _matches_any(lower, _patterns("seduce")):
        return ParsedIntent(action_type=ActionType.seduce, parameters={}, summary=summary)
    if _matches_any(lower, _patterns("steal")):
        return ParsedIntent(action_type=ActionType.steal, parameters={}, summary=summary)
    if _matches_any(lower, _patterns("spy")):
        return ParsedIntent(action_type=ActionType.spy, parameters={}, summary=summary)
    if _matches_any(lower, _patterns("research")):
        return ParsedIntent(
            action_type=ActionType.research,
            parameters={"duration_hours": duration_hours or 2},
            summary=summary,
        )
    if _matches_any(lower, _patterns("talk")):
        return ParsedIntent(action_type=ActionType.talk, parameters={}, summary=summary)
    if _matches_any(lower, _patterns("move")):
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
    """Cherche un nom de village connu apres 'vers' / 'a' / 'pour' / 'to' / 'toward'."""
    m = re.search(r"(?:vers|a|pour|jusqu['' ]a|to|toward)\s+([a-z]+)", lower)
    if m:
        token = m.group(1)
        return _KNOWN_VILLAGES.get(token)
    for token, vid in _KNOWN_VILLAGES.items():
        if f" {token}" in f" {lower}":
            return vid
    return None


# Backwards-compat aliases : modules externes importent peut-etre les anciens
# noms de patterns (TRAIN_PATTERNS, LEARN_PATTERNS, etc). On expose les listes
# FR sous leur ancien nom pour ne rien casser.
TRAIN_PATTERNS = _FR_PATTERNS["train"]
LEARN_PATTERNS = _FR_PATTERNS["learn"]
STUDY_PATTERNS = _FR_PATTERNS["study"]
REST_PATTERNS = _FR_PATTERNS["rest"]
RELAX_PATTERNS = _FR_PATTERNS["relax"]
MEDITATE_PATTERNS = _FR_PATTERNS["meditate"]
WAIT_PATTERNS = _FR_PATTERNS["wait"]
WORK_PATTERNS = _FR_PATTERNS["work"]
MISSION_PATTERNS = _FR_PATTERNS["mission"]
COMBAT_PATTERNS = _FR_PATTERNS["combat"]
TALK_PATTERNS = _FR_PATTERNS["talk"]
MOVE_PATTERNS = _FR_PATTERNS["move"]
INTIMIDATE_PATTERNS = _FR_PATTERNS["intimidate"]
SEDUCE_PATTERNS = _FR_PATTERNS["seduce"]
STEAL_PATTERNS = _FR_PATTERNS["steal"]
SPY_PATTERNS = _FR_PATTERNS["spy"]
RESEARCH_PATTERNS = _FR_PATTERNS["research"]
BUY_PATTERNS = _FR_PATTERNS["buy"]
SELL_PATTERNS = _FR_PATTERNS["sell"]
USE_ITEM_PATTERNS = _FR_PATTERNS["use_item"]
DECLARE_GOAL_PATTERNS = _FR_PATTERNS["declare_goal"]
PATH_REQUEST_PATTERNS = _FR_PATTERNS["path_request"]
INFO_PAYMENT_PATTERNS = _FR_PATTERNS["info_payment"]
PRAY_PATTERNS = _FR_PATTERNS["pray"]
CHALLENGE_PATTERNS = _FR_PATTERNS["challenge"]
DESERT_PATTERNS = _FR_PATTERNS["desert"]
