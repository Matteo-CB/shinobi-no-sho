"""Consequences emergentes : actions modifient indirectement les stats.

Philosophie : un perso qui combat reellement gagne du taijutsu meme s'il ne
s'entraine pas explicitement, un perso qui parle beaucoup gagne du charisme,
un perso qui rate apprend de ses erreurs (willpower + perception).

Anti-abus :
- Petits gains par action (3-8h equivalentes pour les actions concretes)
- Diminishing returns naturels via train_stat (plus la stat est haute, plus
  il faut d'effort)
- Echecs catastrophiques ne donnent pas plus que les succes (pas de farming)
- Consequences plafonnees a max 5 stats par action (pas un buffet)

Immersion :
- Chaque (action, stat) a une justification RP courte affichee au joueur
- Le narrateur LLM peut s'en inspirer pour la narration suivante
"""

from __future__ import annotations

from dataclasses import dataclass

from shinobi.engine.character import Character
from shinobi.engine.missions import Mission
from shinobi.engine.progression import StatChange, train_stat
from shinobi.types import ActionOutcome, ActionType


@dataclass(frozen=True)
class ConsequenceRule:
    """Une consequence : stat, heures equivalentes, justification narrative."""

    stat: str
    hours: int
    why_fr: str


# Pour chaque type d'action : liste de consequences possibles avec justifications.
# Les heures sont multipliees par OUTCOME_MULTIPLIERS.
CONSEQUENCE_RULES: dict[ActionType, list[ConsequenceRule]] = {
    ActionType.fight: [
        ConsequenceRule("taijutsu", 8, "le combat reel a ete un veritable apprentissage"),
        ConsequenceRule("strength", 4, "ton corps s'est durci sous l'effort"),
        ConsequenceRule("speed", 3, "tu as du esquiver et reagir vite"),
        ConsequenceRule("willpower", 5, "tu as tenu sous la pression"),
        ConsequenceRule("perception", 3, "tu as appris a lire les mouvements adverses"),
    ],
    ActionType.use_technique: [
        ConsequenceRule("ninjutsu", 5, "executer une technique a affine ton controle"),
        ConsequenceRule("hand_seals", 3, "tes signes sont devenus plus fluides"),
        ConsequenceRule("chakra_control", 3, "moduler le chakra t'a forme"),
    ],
    ActionType.talk: [
        ConsequenceRule("social_charisma", 3, "echanger forge ton aisance sociale"),
        ConsequenceRule("intelligence", 1, "tu as ecoute et appris quelque chose"),
    ],
    ActionType.intimidate: [
        ConsequenceRule("strength", 1, "tu as projete ta force physique"),
        ConsequenceRule("social_charisma", 2, "imposer sa presence est un art"),
        ConsequenceRule("leadership", 2, "tu as pris l'ascendant"),
    ],
    ActionType.seduce: [
        ConsequenceRule("social_charisma", 5, "la seduction affine la lecture des autres"),
        ConsequenceRule("perception", 2, "lire le langage corporel s'apprend"),
    ],
    ActionType.spy: [
        ConsequenceRule("perception", 6, "rester invisible aiguise tes sens"),
        ConsequenceRule("speed", 2, "tu t'es deplace en silence"),
        ConsequenceRule("intelligence", 3, "deduire depuis l'ombre est un exercice mental"),
    ],
    ActionType.steal: [
        ConsequenceRule("speed", 4, "agir vite avant d'etre vu"),
        ConsequenceRule("perception", 3, "reperer les angles morts"),
        ConsequenceRule("intelligence", 2, "evaluer le risque"),
    ],
    ActionType.bribe: [
        ConsequenceRule("social_charisma", 3, "tu as appris a convaincre par l'argent"),
        ConsequenceRule("intelligence", 1, "evaluer la cupidite des autres"),
    ],
    ActionType.research: [
        ConsequenceRule("intelligence", 6, "l'etude profonde forge l'intellect"),
        ConsequenceRule("perception", 1, "remarquer les details des sources"),
    ],
    ActionType.meditate: [
        ConsequenceRule("willpower", 7, "centrer son esprit forge la volonte"),
        ConsequenceRule("chakra_control", 3, "le chakra circule mieux quand le mental est calme"),
        ConsequenceRule("senjutsu_aptitude", 1, "premiers pas vers la perception du chakra naturel"),
    ],
    ActionType.work: [
        ConsequenceRule("willpower", 2, "la routine forge la persistance"),
        ConsequenceRule("strength", 1, "le travail manuel fortifie le corps"),
    ],
    ActionType.move: [
        ConsequenceRule("speed", 2, "le deplacement entretient l'agilite"),
        ConsequenceRule("stamina", 2, "marcher developpe l'endurance"),
        ConsequenceRule("perception", 1, "observer le paysage en chemin"),
    ],
    ActionType.pray: [
        ConsequenceRule("willpower", 4, "la priere centre l'esprit"),
    ],
    ActionType.challenge: [
        ConsequenceRule("taijutsu", 6, "se mesurer a un adversaire fort accelere l'apprentissage"),
        ConsequenceRule("willpower", 8, "oser le defi forge le mental"),
        ConsequenceRule("leadership", 3, "imposer le respect par le defi"),
        ConsequenceRule("perception", 2, "lire le style adverse"),
    ],
    ActionType.accept_mission: [
        ConsequenceRule("willpower", 1, "s'engager forge la determination"),
    ],
    ActionType.submit_mission: [
        ConsequenceRule("willpower", 2, "rendre compte demande de la rigueur"),
        ConsequenceRule("social_charisma", 1, "presenter ses resultats est un exercice"),
    ],
    ActionType.buy: [
        ConsequenceRule("intelligence", 1, "evaluer la qualite des marchandises"),
        ConsequenceRule("social_charisma", 1, "negocier avec le marchand"),
    ],
    ActionType.sell: [
        ConsequenceRule("social_charisma", 2, "vendre est un art de la persuasion"),
        ConsequenceRule("intelligence", 1, "fixer le bon prix"),
    ],
    ActionType.declare_goal: [
        ConsequenceRule("willpower", 3, "nommer son ambition la rend reelle"),
    ],
    ActionType.request_objective_path: [
        ConsequenceRule("intelligence", 2, "chercher l'information aiguise l'esprit"),
    ],
    ActionType.pay_for_information: [
        ConsequenceRule("intelligence", 2, "savoir ou et quoi demander"),
        ConsequenceRule("social_charisma", 1, "approcher les bonnes personnes"),
    ],
}

# Multiplicateur des consequences selon l'outcome.
OUTCOME_MULTIPLIERS: dict[ActionOutcome, float] = {
    ActionOutcome.full_success: 1.0,
    ActionOutcome.partial_success: 0.7,
    ActionOutcome.minor_failure: 0.4,
    ActionOutcome.catastrophic_failure: 0.6,
    ActionOutcome.contextual_impossibility: 0.0,
}

# Plafond de stats gagnees par action (anti-abus : pas un buffet)
MAX_STATS_PER_ACTION = 5


@dataclass(frozen=True)
class AppliedConsequence:
    """Une consequence effectivement appliquee, avec son contexte."""

    change: StatChange
    why_fr: str


def apply_action_consequences(
    character: Character,
    *,
    action_type: ActionType,
    outcome: ActionOutcome,
    duration_hours: int = 1,
) -> tuple[Character, list[StatChange], list[AppliedConsequence]]:
    """Applique les consequences passives d'une action.

    Retourne (character, stat_changes, applied_with_justifications).
    """
    rules = CONSEQUENCE_RULES.get(action_type, [])
    if not rules:
        return character, [], []
    multiplier = OUTCOME_MULTIPLIERS.get(outcome, 0.0)
    if multiplier <= 0:
        return character, [], []

    # Bonus duree : longue session = plus d'apprentissage (mais log scale)
    duration_bonus = 1.0
    if duration_hours >= 8:
        duration_bonus = 1.3
    if duration_hours >= 24:
        duration_bonus = 1.6
    if duration_hours >= 72:
        duration_bonus = 2.0

    changes: list[StatChange] = []
    applied: list[AppliedConsequence] = []
    current = character
    for rule in rules[:MAX_STATS_PER_ACTION]:
        actual_hours = max(1, int(rule.hours * multiplier * duration_bonus))
        new_char, change = train_stat(
            current, rule.stat, hours=actual_hours, quality_modifier=multiplier
        )
        if change:
            changes.append(change)
            applied.append(AppliedConsequence(change=change, why_fr=rule.why_fr))
            current = new_char
    return current, changes, applied


# Bonus de stats octroyes a la fin d'une mission selon son rang et son type.
RANK_TO_HOURS = {"D": 12, "C": 35, "B": 80, "A": 180, "S": 400}


def mission_consequences(
    character: Character,
    mission: Mission,
    *,
    success: bool,
) -> tuple[Character, list[AppliedConsequence]]:
    """Bonus de stats apres avoir reussi (ou rate) une mission."""
    base_hours = RANK_TO_HOURS.get(mission.rank, 12)
    if not success:
        rules = [
            ConsequenceRule("willpower", base_hours // 2, "echouer enseigne plus que rien essayer"),
            ConsequenceRule("perception", base_hours // 3, "tu vois mieux ce qui a manque"),
        ]
        multiplier = 0.6
    else:
        rules = _profile_for_mission(mission, base_hours)
        multiplier = 1.0

    changes: list[AppliedConsequence] = []
    current = character
    for rule in rules:
        actual_hours = max(1, int(rule.hours * multiplier))
        new_char, change = train_stat(
            current, rule.stat, hours=actual_hours, quality_modifier=multiplier
        )
        if change:
            changes.append(AppliedConsequence(change=change, why_fr=rule.why_fr))
            current = new_char
    return current, changes


def _profile_for_mission(mission: Mission, base_hours: int) -> list[ConsequenceRule]:
    """Deduit les stats prioritairement gagnees selon le contexte de la mission."""
    title_lower = (mission.title + " " + mission.description_fr).lower()

    if any(kw in title_lower for kw in ("combat", "eliminer", "assassiner", "detruire", "tuer")):
        return [
            ConsequenceRule("taijutsu", base_hours, "le combat reel forge le corps"),
            ConsequenceRule("willpower", base_hours, "tuer (ou risquer sa vie) trempe le mental"),
            ConsequenceRule("strength", base_hours // 2, "tu as du frapper avec force"),
            ConsequenceRule("ninjutsu", base_hours // 2, "tu as use de techniques"),
            ConsequenceRule("social_charisma", base_hours // 4, "ta reussite te vaut un certain respect"),
        ]
    if any(kw in title_lower for kw in ("infiltrer", "espionner", "voler", "subtiliser", "discret")):
        return [
            ConsequenceRule("speed", base_hours, "te deplacer sans bruit a ete crucial"),
            ConsequenceRule("perception", base_hours, "rester en alerte permanente"),
            ConsequenceRule("intelligence", base_hours // 2, "deduire et planifier en silence"),
        ]
    if any(kw in title_lower for kw in ("escorter", "proteger", "garder", "veiller", "convoyer")):
        return [
            ConsequenceRule("willpower", base_hours, "veiller longtemps demande de la discipline"),
            ConsequenceRule("perception", base_hours, "anticiper les menaces"),
            ConsequenceRule("taijutsu", base_hours // 2, "tu as du intervenir physiquement"),
            ConsequenceRule("social_charisma", base_hours // 4, "le client te recommande"),
        ]
    if any(kw in title_lower for kw in ("diplomatique", "delegation", "complot", "intrigue")):
        return [
            ConsequenceRule("social_charisma", base_hours, "naviguer la diplomatie a ete formateur"),
            ConsequenceRule("leadership", base_hours, "tu as pris des decisions politiques"),
            ConsequenceRule("intelligence", base_hours // 2, "lire les enjeux caches"),
        ]
    if any(kw in title_lower for kw in ("recuperer", "retrouver", "enquet", "investigu")):
        return [
            ConsequenceRule("perception", base_hours, "trouver des indices a aiguise tes sens"),
            ConsequenceRule("intelligence", base_hours, "deduire a partir de fragments"),
            ConsequenceRule("speed", base_hours // 2, "il a fallu agir vite quand la piste s'est presentee"),
        ]
    # Mission civile (chat egare, ferme, sanctuaire)
    return [
        ConsequenceRule("willpower", base_hours // 2, "tenir une mission jusqu'au bout, meme banale"),
        ConsequenceRule("social_charisma", base_hours // 2, "rendre service developpe les liens"),
        ConsequenceRule("perception", base_hours // 3, "rester attentif aux details"),
    ]
