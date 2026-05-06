"""30 regles de drift de personnalite deterministes.

Chaque regle est un mapping `EventCategory -> dict[PersonalityDimension, float]`
ou la valeur est le delta BRUT a appliquer (avant saturation sigmoid).

Principes (docs/02 §6.2) :
- pas de templates de scripts ('si Sasuke + massacre ...')
- regles abstraites de psychologie generique applicables a n'importe quel PNJ
- cumulatif mais saturant (sigmoid) -> aucune dimension ne peut depasser [0,1]
- la divergence emerge des sequences vecues, pas d'un cas particulier hard-code

Saturation sigmoid : on convertit le vecteur courant en logit, on additionne
le delta, on reapplique le sigmoid. Cela donne :
- proche de 0.5 : delta tres effectif (gradient max)
- proche de 0 ou 1 : delta amorti (gradient faible)

Cette propriete est essentielle : un PNJ deja paranoiaque a 0.95 ne peut pas
devenir 'plus paranoiaque' indefiniment. Le drift sature naturellement.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

from shinobi.personality.dimensions import PersonalityDimension as D
from shinobi.personality.types import EventCategory


@dataclass(frozen=True)
class DriftRule:
    """Une regle de drift unique : event_category -> deltas par dimension.

    intensity_factor : multiplicateur applique au delta selon
    `ExperiencedEvent.intensity` (1.0 par defaut). Permet de moduler.

    duration_log_factor : pour les rules cumulatives (long_term_companionship,
    secret_kept_long_term, daily_routine_long), on multiplie le delta par
    log(1+years)*duration_log_factor pour avoir un effet decroissant marginal.
    """

    name: str
    category: EventCategory
    deltas: dict[D, float] = field(default_factory=dict)
    requires_related_npc: bool = False
    duration_log_factor: float = 0.0  # 0 = pas d'effet de duree
    description: str = ""


# Les 30 regles deterministes ----------------------------------------------------

DRIFT_RULES: tuple[DriftRule, ...] = (
    DriftRule(
        name="trauma_event",
        category=EventCategory.trauma_event,
        deltas={D.fear: 0.20, D.paranoia: 0.10, D.melancholy: 0.10},
        description="Trauma generique : peur+, paranoia+, melancholie+",
    ),
    DriftRule(
        name="betrayal_witnessed",
        category=EventCategory.betrayal_witnessed,
        deltas={D.paranoia: 0.15, D.loyalty: -0.10, D.openness: -0.05},
        requires_related_npc=True,
        description="Trahison observee : paranoia+, loyaute-, ouverture-",
    ),
    DriftRule(
        name="long_term_companionship",
        category=EventCategory.long_term_companionship,
        deltas={D.loyalty: 0.05, D.empathy: 0.03, D.isolationism: -0.03},
        requires_related_npc=True,
        duration_log_factor=1.0,
        description="Compagnonnage long : loyaute+, empathie+, isolation-",
    ),
    DriftRule(
        name="violent_combat_won",
        category=EventCategory.violent_combat_won,
        deltas={D.confidence: 0.08, D.aggression: 0.05, D.pride: 0.05},
        description="Combat gagne : confiance+, agression+, fierte+",
    ),
    DriftRule(
        name="violent_combat_lost",
        category=EventCategory.violent_combat_lost,
        deltas={D.confidence: -0.10, D.fear: 0.10, D.vengeance: 0.05},
        description="Combat perdu : confiance-, peur+, vengeance+",
    ),
    DriftRule(
        name="mentor_lost",
        category=EventCategory.mentor_lost,
        deltas={D.melancholy: 0.20, D.fear: 0.10, D.discipline: -0.05, D.vengeance: 0.05},
        requires_related_npc=True,
        description="Perte d'un mentor : melancholie+, peur+, discipline-, vengeance+",
    ),
    DriftRule(
        name="lover_lost",
        category=EventCategory.lover_lost,
        deltas={D.melancholy: 0.25, D.isolationism: 0.15, D.empathy: -0.05, D.vengeance: 0.05},
        requires_related_npc=True,
        description="Perte d'un amour : melancholie+, isolation+, empathie-, vengeance+",
    ),
    DriftRule(
        name="parent_lost",
        category=EventCategory.parent_lost,
        deltas={D.melancholy: 0.20, D.vengeance: 0.10, D.fear: 0.10, D.isolationism: 0.05},
        requires_related_npc=True,
        description="Perte d'un parent : melancholie+, vengeance+, peur+, isolation+",
    ),
    DriftRule(
        name="sibling_lost",
        category=EventCategory.sibling_lost,
        deltas={D.melancholy: 0.20, D.vengeance: 0.15, D.isolationism: 0.10},
        requires_related_npc=True,
        description="Perte d'un fratrie : melancholie+, vengeance++, isolation+",
    ),
    DriftRule(
        name="rescued_by",
        category=EventCategory.rescued_by,
        deltas={D.loyalty: 0.10, D.empathy: 0.05, D.honor: 0.05},
        requires_related_npc=True,
        description="Sauve par X : loyaute+ (envers X), empathie+, honneur+",
    ),
    DriftRule(
        name="witnessed_atrocity",
        category=EventCategory.witnessed_atrocity,
        deltas={D.fear: 0.15, D.melancholy: 0.10, D.idealism: -0.10, D.paranoia: 0.05},
        description="Atrocite vue : peur+, melancholie+, idealisme-, paranoia+",
    ),
    DriftRule(
        name="achieved_goal",
        category=EventCategory.achieved_goal,
        deltas={D.confidence: 0.10, D.ambition: 0.05, D.pride: 0.05},
        description="Objectif atteint : confiance+, ambition+ (saturating), fierte+",
    ),
    DriftRule(
        name="failed_goal",
        category=EventCategory.failed_goal,
        deltas={D.confidence: -0.10, D.fear: 0.05, D.melancholy: 0.05, D.discipline: 0.05},
        description="Objectif echoue : confiance-, peur+, melancholie+, discipline+",
    ),
    DriftRule(
        name="rank_promotion",
        category=EventCategory.rank_promotion,
        deltas={D.confidence: 0.10, D.pride: 0.10, D.ambition: 0.05},
        description="Promotion : confiance+, fierte+, ambition+",
    ),
    DriftRule(
        name="rank_demotion",
        category=EventCategory.rank_demotion,
        deltas={D.pride: -0.15, D.melancholy: 0.10, D.vengeance: 0.05, D.confidence: -0.05},
        description="Retrogradation : fierte-, melancholie+, vengeance+, confiance-",
    ),
    DriftRule(
        name="mass_killing_committed",
        category=EventCategory.mass_killing_committed,
        deltas={D.empathy: -0.15, D.melancholy: 0.10, D.paranoia: 0.10, D.aggression: 0.05},
        description="Massacre commis : empathie-, melancholie+, paranoia+, agression+",
    ),
    DriftRule(
        name="protected_innocent",
        category=EventCategory.protected_innocent,
        deltas={D.empathy: 0.10, D.idealism: 0.10, D.confidence: 0.05, D.honor: 0.05},
        description="Protege un innocent : empathie+, idealisme+, confiance+, honneur+",
    ),
    DriftRule(
        name="massacre_against_self_clan",
        category=EventCategory.massacre_against_self_clan,
        deltas={
            D.vengeance: 0.30, D.fear: 0.15, D.isolationism: 0.20,
            D.paranoia: 0.15, D.melancholy: 0.20, D.loyalty: -0.10,
        },
        description="Massacre clan : vengeance++, peur+, isolation+, paranoia+, melancholie+, loyaute-",
    ),
    DriftRule(
        name="long_isolation",
        category=EventCategory.long_isolation,
        deltas={D.isolationism: 0.10, D.melancholy: 0.05, D.paranoia: 0.05, D.empathy: -0.03},
        duration_log_factor=1.0,
        description="Isolation prolongee : isolation+, melancholie+, paranoia+",
    ),
    DriftRule(
        name="reconciliation",
        category=EventCategory.reconciliation,
        deltas={D.loyalty: 0.10, D.melancholy: -0.10, D.openness: 0.05, D.vengeance: -0.10},
        requires_related_npc=True,
        description="Reconciliation : loyaute+, melancholie-, ouverture+, vengeance-",
    ),
    DriftRule(
        name="leadership_burden_taken",
        category=EventCategory.leadership_burden_taken,
        deltas={D.confidence: 0.10, D.fear: 0.05, D.pride: 0.10, D.discipline: 0.05},
        description="Fardeau de leadership : confiance+, peur+, fierte+, discipline+",
    ),
    DriftRule(
        name="lover_gained",
        category=EventCategory.lover_gained,
        deltas={D.empathy: 0.10, D.isolationism: -0.10, D.melancholy: -0.05, D.openness: 0.05},
        requires_related_npc=True,
        description="Amour gagne : empathie+, isolation-, melancholie-, ouverture+",
    ),
    DriftRule(
        name="friendship_deepened",
        category=EventCategory.friendship_deepened,
        deltas={D.loyalty: 0.05, D.openness: 0.05, D.isolationism: -0.05},
        requires_related_npc=True,
        description="Amitie approfondie : loyaute+, ouverture+, isolation-",
    ),
    DriftRule(
        name="prophecy_received",
        category=EventCategory.prophecy_received,
        deltas={D.idealism: 0.10, D.ambition: 0.10, D.fear: 0.05, D.honor: 0.05},
        description="Prophetie recue : idealisme+, ambition+, peur+, honneur+",
    ),
    DriftRule(
        name="jutsu_mastery_milestone",
        category=EventCategory.jutsu_mastery_milestone,
        deltas={D.confidence: 0.05, D.pride: 0.05, D.discipline: 0.05},
        description="Maitrise jutsu : confiance+, fierte+, discipline+",
    ),
    DriftRule(
        name="clan_destroyed",
        category=EventCategory.clan_destroyed,
        deltas={D.vengeance: 0.20, D.isolationism: 0.15, D.melancholy: 0.20, D.honor: 0.10},
        description="Clan detruit : vengeance+, isolation+, melancholie+, honneur+",
    ),
    DriftRule(
        name="secret_revealed_about_self",
        category=EventCategory.secret_revealed_about_self,
        deltas={D.paranoia: 0.10, D.secrecy: -0.20, D.fear: 0.05, D.pride: -0.05},
        description="Secret revele : paranoia+, secret-- (force), peur+, fierte-",
    ),
    DriftRule(
        name="secret_kept_long_term",
        category=EventCategory.secret_kept_long_term,
        deltas={D.secrecy: 0.05, D.discipline: 0.03, D.isolationism: 0.03},
        duration_log_factor=1.0,
        description="Secret garde longtemps : secret+, discipline+, isolation+",
    ),
    DriftRule(
        name="daily_routine_long",
        category=EventCategory.daily_routine_long,
        deltas={D.discipline: 0.05, D.openness: -0.03, D.aggression: -0.02},
        duration_log_factor=1.0,
        description="Routine prolongee : discipline+, ouverture-, agression-",
    ),
    DriftRule(
        name="peer_outpaced",
        category=EventCategory.peer_outpaced,
        deltas={D.vengeance: 0.05, D.confidence: -0.05, D.ambition: 0.10, D.melancholy: 0.05},
        requires_related_npc=True,
        description="Depasse par un pair : jalousie -> vengeance+, confiance-, ambition+",
    ),
    DriftRule(
        name="public_humiliation",
        category=EventCategory.public_humiliation,
        deltas={D.pride: -0.15, D.vengeance: 0.10, D.isolationism: 0.10, D.confidence: -0.05},
        description="Humiliation publique : fierte--, vengeance+, isolation+, confiance-",
    ),
)


# Sanity : ~30 rules, une par EventCategory (spec docs/02 §6.2 : "~30 règles génériques")
assert 28 <= len(DRIFT_RULES) <= 35, (
    f"DRIFT_RULES doit contenir ~30 regles, trouve {len(DRIFT_RULES)}"
)
assert len(DRIFT_RULES) == len({r.category for r in DRIFT_RULES}), (
    "Categories d'event en doublon dans DRIFT_RULES"
)


def get_rule_for_category(category: EventCategory) -> DriftRule | None:
    """Retourne la rule unique associee a une category, ou None."""
    for r in DRIFT_RULES:
        if r.category == category:
            return r
    return None


# ----------------------------------------------------------------------------
# Sigmoid-based saturation
# ----------------------------------------------------------------------------


def _logit(value: float, eps: float = 1e-4) -> float:
    """Logit numeriquement stable (clip pour eviter inf)."""
    v = max(eps, min(1.0 - eps, value))
    return math.log(v / (1.0 - v))


def _sigmoid(x: float) -> float:
    """Sigmoid logistique."""
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def apply_delta_with_saturation(
    current: float, delta: float, *, sensitivity: float = 4.0,
) -> float:
    """Applique un delta a une valeur [0,1] avec saturation sigmoid.

    `sensitivity` controle l'echelle : un delta de +0.10 sur sensitivity=4
    deplace le logit de 0.4. Les valeurs proches de 0 ou 1 sont peu affectees,
    les valeurs proches de 0.5 maximalement.

    Garanties :
    - delta > 0 -> resultat > current (sauf si sature au plafond)
    - delta < 0 -> resultat < current
    - resultat dans [0.0, 1.0]
    - apply(apply(x, +d), -d) ~= x (asymptotique)
    """
    if delta == 0.0:
        return current
    logit = _logit(current)
    new_logit = logit + (delta * sensitivity)
    return _sigmoid(new_logit)


def compose_drift_for_event(
    rule: DriftRule,
    *,
    intensity: float = 1.0,
    duration_years: int | None = None,
) -> dict[D, float]:
    """Compose les deltas BRUTS pour un event, modules par intensity et duration.

    intensity : ExperiencedEvent.intensity dans [0,1].
    duration_years : pour les rules cumulatives (factor log).
    """
    out: dict[D, float] = {}
    duration_factor = 1.0
    if rule.duration_log_factor and duration_years and duration_years > 0:
        duration_factor = math.log(1.0 + duration_years) * rule.duration_log_factor
    multiplier = max(0.0, intensity) * duration_factor
    for dim, delta in rule.deltas.items():
        out[dim] = delta * multiplier
    return out


def total_dimensions_touched(rules: Iterable[DriftRule]) -> set[D]:
    """Pour les tests : ensemble des dimensions touchees au moins une fois."""
    out: set[D] = set()
    for r in rules:
        out.update(r.deltas.keys())
    return out


__all__ = [
    "DRIFT_RULES",
    "DriftRule",
    "apply_delta_with_saturation",
    "compose_drift_for_event",
    "get_rule_for_category",
    "total_dimensions_touched",
]
