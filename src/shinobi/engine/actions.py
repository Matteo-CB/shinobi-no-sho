"""Pipeline de resolution d'actions joueur avec effets reels sur l'etat."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from shinobi.constants import (
    DIFFICULTY_EASY,
    DIFFICULTY_HARD,
    DIFFICULTY_MODERATE,
    DIFFICULTY_TRIVIAL,
    DIFFICULTY_VERY_HARD,
)
from shinobi.engine.character import Character
from shinobi.engine.consequences import apply_action_consequences
from shinobi.engine.learning import (
    can_attempt_learning,
    compute_learning_hours_required,
    progress_learning,
    start_learning,
)
from shinobi.engine.missions import Mission
from shinobi.engine.progression import (
    INTANGIBLE_EXT,
    NON_TRAINABLE_EXT,
    StatChange,
    add_money,
    apply_chakra_cost,
    apply_damage,
    apply_fatigue,
    apply_meditation,
    apply_rest,
    apply_sleep,
    fatigue_for_duration,
    train_stat,
)
from shinobi.engine.relations import add_reputation, update_affinity
from shinobi.engine.rng import roll
from shinobi.engine.stats import average_combat_stat
from shinobi.engine.time import estimate_duration
from shinobi.engine.world import WorldState
from shinobi.types import ActionOutcome, ActionType


class Action(BaseModel):
    """Action declaree par le joueur."""

    model_config = ConfigDict(frozen=True)

    action_type: ActionType
    summary: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    target_id: str | None = None
    declared_text: str | None = None


class ActionResult(BaseModel):
    """Resultat d'une action."""

    model_config = ConfigDict(frozen=True)

    action: Action
    outcome: ActionOutcome
    summary_fr: str
    consequences: list[dict[str, Any]] = Field(default_factory=list)
    chakra_cost: int = 0
    duration_minutes: int = 0
    seed_after: int = 0
    stat_changes: list[dict[str, Any]] = Field(default_factory=list)
    money_delta: int = 0
    hp_delta: int = 0
    fatigue_delta: int = 0


@dataclass
class ResolutionInputs:
    """Donnees passees au resolver pour resoudre une action."""

    character: Character
    world: WorldState
    action: Action
    seed: int


def difficulty_for(action: Action, character: Character) -> int:
    """Difficulte par defaut selon le type d'action."""
    if action.action_type in (ActionType.rest, ActionType.meditate, ActionType.wait):
        return DIFFICULTY_TRIVIAL
    if action.action_type in (ActionType.talk, ActionType.move, ActionType.buy, ActionType.sell):
        return DIFFICULTY_EASY
    if action.action_type in (
        ActionType.train_stat,
        ActionType.train_technique,
        ActionType.work,
        ActionType.research,
    ):
        return DIFFICULTY_MODERATE
    if action.action_type in (
        ActionType.spy,
        ActionType.steal,
        ActionType.intimidate,
        ActionType.seduce,
        ActionType.bribe,
        ActionType.fight,
        ActionType.use_technique,
    ):
        return DIFFICULTY_HARD
    if action.action_type == ActionType.challenge:
        return DIFFICULTY_VERY_HARD
    return DIFFICULTY_MODERATE


_STAT_LABELS_FR = {
    "ninjutsu": "ton ninjutsu",
    "taijutsu": "ton taijutsu",
    "genjutsu": "ton genjutsu",
    "intelligence": "ton intelligence",
    "strength": "ta force physique",
    "speed": "ta vitesse",
    "stamina": "ton endurance",
    "hand_seals": "ta dexterite aux mudras",
    "chakra_control": "ton controle du chakra",
    "willpower": "ta volonte",
    "perception": "ta perception",
    "social_charisma": "ton charisme",
    "leadership": "ton leadership",
    "medical_knowledge": "tes connaissances medicales",
    "fuinjutsu_knowledge": "ton fuinjutsu",
    "senjutsu_aptitude": "ton senjutsu",
    "beauty": "ton apparence",
    "luck": "ta fortune",
    "lineage_value": "ton sang",
    "chakra_reserves": "ta reserve de chakra naturelle",
}


def _stat_label_fr(stat_name: str) -> str:
    return _STAT_LABELS_FR.get(stat_name, stat_name)


_WEAPON_COMBAT_BONUS: dict[str, float] = {
    "kunai": 0.15,
    "shuriken": 0.1,
    "fuma_shuriken": 0.4,
}


def _weapon_combat_bonus(character: Character) -> float:
    """Bonus de combat (en points de stat) si le perso possede des armes equipees."""
    if not character.weapons:
        return 0.0
    return min(0.6, sum(_WEAPON_COMBAT_BONUS.get(w.weapon_id, 0.05) for w in character.weapons))


def relevant_stat(action: Action, character: Character) -> float:
    """Choix de la stat principale selon l'action."""
    s = character.stats
    es = character.extended_stats
    if action.action_type == ActionType.fight:
        return average_combat_stat(s) + _weapon_combat_bonus(character)
    if action.action_type in (
        ActionType.train_stat,
        ActionType.train_technique,
        ActionType.research,
    ):
        return es.learning_genius
    if action.action_type in (ActionType.talk, ActionType.seduce, ActionType.bribe):
        return es.social_charisma
    if action.action_type == ActionType.intimidate:
        return s.strength
    if action.action_type in (ActionType.move, ActionType.spy, ActionType.steal):
        return s.speed
    if action.action_type == ActionType.use_technique:
        return s.ninjutsu
    if action.action_type == ActionType.meditate:
        return es.willpower
    if action.action_type == ActionType.work:
        return es.willpower
    return s.intelligence


def resolve_action(inputs: ResolutionInputs) -> ActionResult:
    """Pipeline de resolution. Calcule outcome + duration."""
    action = inputs.action
    character = inputs.character

    if character.is_dead:
        return ActionResult(
            action=action,
            outcome=ActionOutcome.contextual_impossibility,
            summary_fr="Le personnage est decede. Aucune action possible.",
            seed_after=inputs.seed,
        )

    diff = difficulty_for(action, character)
    stat = relevant_stat(action, character)
    modifier = int(stat * 4)

    # Penalite de fatigue : plus tu es fatigue, plus c'est dur.
    fatigue_penalty = -(character.health.fatigue // 25)
    # Penalite de manque de chakra
    chakra_ratio = character.chakra.current / max(1, character.chakra.max)
    chakra_penalty = -3 if chakra_ratio < 0.2 else 0

    r = roll(inputs.seed, "1d20", modifier=modifier + fatigue_penalty + chakra_penalty)
    margin = r.total - diff

    if margin >= 10:
        outcome = ActionOutcome.full_success
        summary = f"Reussite eclatante. {action.summary}"
    elif margin >= 0:
        outcome = ActionOutcome.full_success
        summary = f"Reussite. {action.summary}"
    elif margin >= -5:
        outcome = ActionOutcome.partial_success
        summary = f"Reussite partielle, avec consequences. {action.summary}"
    elif margin >= -10:
        outcome = ActionOutcome.minor_failure
        summary = f"Echec sans degat majeur. {action.summary}"
    else:
        outcome = ActionOutcome.catastrophic_failure
        summary = f"Echec critique. {action.summary}"

    duration_hours = int(action.parameters.get("duration_hours", 0))
    if duration_hours > 0:
        duration = duration_hours * 60
    else:
        duration = estimate_duration(
            action.action_type, action.parameters.get("duration_minutes_override")
        )
    chakra_cost = int(action.parameters.get("chakra_cost", 0))

    return ActionResult(
        action=action,
        outcome=outcome,
        summary_fr=summary,
        consequences=[],
        chakra_cost=chakra_cost,
        duration_minutes=duration,
        seed_after=r.seed_after,
    )


def apply_action_to_state(
    character: Character,
    world: WorldState,
    result: ActionResult,
) -> tuple[Character, WorldState, ActionResult]:
    """Applique le resultat sur l'etat du personnage. Retourne (char, world, result enrichi)."""
    action = result.action
    duration_hours = max(1, result.duration_minutes // 60)

    stat_changes: list[StatChange] = []
    money_delta = 0
    hp_delta = 0
    fatigue_delta = 0
    chakra_cost = result.chakra_cost

    success = result.outcome in (ActionOutcome.full_success, ActionOutcome.partial_success)
    quality = (
        1.0
        if result.outcome == ActionOutcome.full_success
        else (0.6 if result.outcome == ActionOutcome.partial_success else 0.2)
    )

    new_char = character

    if action.action_type == ActionType.train_stat:
        stat_name = str(action.parameters.get("stat", "stamina"))
        if stat_name in NON_TRAINABLE_EXT:
            label = _stat_label_fr(stat_name)
            new_result = result.model_copy(
                update={
                    "outcome": ActionOutcome.contextual_impossibility,
                    "summary_fr": (
                        f"Tu te prends en main et essaies de changer {label}, mais cela ne "
                        f"vient pas de ta volonte. C'est dans ton sang, dans tes ancetres. "
                        f"Aucun effort ne modifiera ce que la naissance t'a donne."
                    ),
                    "stat_changes": [],
                    "money_delta": 0,
                    "hp_delta": 0,
                    "fatigue_delta": 0,
                }
            )
            return new_char, world, new_result
        # Si l'action est en mode "etude" (cours / theorie), le quality_modifier
        # passe en parametre prend la priorite sur le quality derive de l'outcome.
        study_quality = action.parameters.get("quality_modifier")
        effective_quality = (
            float(study_quality) * quality if study_quality is not None else quality
        )
        new_char, change = train_stat(
            new_char, stat_name, hours=duration_hours, quality_modifier=effective_quality
        )
        if change:
            stat_changes.append(change)
        elif stat_name in INTANGIBLE_EXT:
            label = _stat_label_fr(stat_name)
            new_result = result.model_copy(
                update={
                    "outcome": ActionOutcome.partial_success,
                    "summary_fr": (
                        f"Tu consacres du temps a {label}. Le miroir te renvoie une image "
                        f"presque identique a celle d'avant. Ce genre de chose ne se forge pas "
                        f"par la volonte seule : il te faudrait une rencontre, une epreuve, ou "
                        f"un evenement marquant pour qu'un vrai changement opere."
                    ),
                }
            )
            stat_changes = []
        fatigue_delta = fatigue_for_duration(duration_hours)
        if fatigue_delta > 0:
            new_char = apply_fatigue(new_char, fatigue_delta)

    elif action.action_type == ActionType.train_technique:
        # Si une technique cible est specifiee + canon disponible, vraie progression
        target_id = action.parameters.get("target_technique_id") or ""
        canon_bundle = action.parameters.get("_canon")
        rules_bundle = action.parameters.get("_world_rules")
        learned_now = False
        if target_id and canon_bundle is not None and rules_bundle is not None:
            tech = canon_bundle.techniques.get(target_id)
            if tech is not None:
                eligibility = can_attempt_learning(new_char, tech)
                if eligibility.eligible:
                    if not any(
                        t.technique_id == target_id for t in new_char.techniques_in_progress
                    ):
                        required = compute_learning_hours_required(
                            new_char, tech, rules=rules_bundle
                        )
                        new_char = start_learning(
                            new_char,
                            technique_id=target_id,
                            progress_required=required,
                            teacher_id=None,
                            started_year=int(action.parameters.get("current_year", 0)),
                        )
                    new_char, learned_now = progress_learning(
                        new_char,
                        target_id,
                        hours=duration_hours,
                        learn_year=int(action.parameters.get("current_year", 0)),
                    )
        new_char, c1 = train_stat(
            new_char, "intelligence", hours=duration_hours // 2, quality_modifier=quality
        )
        new_char, c2 = train_stat(
            new_char, "chakra_control", hours=duration_hours // 2, quality_modifier=quality
        )
        for c in (c1, c2):
            if c:
                stat_changes.append(c)
        fatigue_delta = fatigue_for_duration(duration_hours)
        if fatigue_delta > 0:
            new_char = apply_fatigue(new_char, fatigue_delta)
        if learned_now and target_id:
            result = result.model_copy(
                update={"summary_fr": result.summary_fr + f" Technique apprise : {target_id}."}
            )

    elif action.action_type == ActionType.rest:
        sleep = bool(action.parameters.get("sleep", False))
        # No-op signal : si deja repose ET HP plein ET chakra plein, le sommeil n'apporte rien
        already_rested = (
            character.health.fatigue == 0
            and character.health.hp_current >= character.health.hp_max
            and character.chakra.current >= character.chakra.max
        )
        if already_rested and sleep:
            new_result = result.model_copy(
                update={
                    "summary_fr": (
                        "Tu te poses pour dormir, mais tu es deja parfaitement repose. "
                        "Le sommeil n'apporte rien de plus. Le temps passe, c'est tout."
                    ),
                    "stat_changes": [],
                    "money_delta": 0,
                    "hp_delta": 0,
                    "fatigue_delta": 0,
                }
            )
            return new_char, world, new_result
        new_char = (
            apply_sleep(new_char, hours=duration_hours)
            if sleep
            else apply_rest(new_char, hours=duration_hours)
        )
        fatigue_delta = -(character.health.fatigue - new_char.health.fatigue)

    elif action.action_type == ActionType.meditate:
        new_char = apply_meditation(new_char, hours=duration_hours)

    elif action.action_type == ActionType.work:
        # Travail civil : gain de ryos selon duree + qualite
        gain = int(50 * duration_hours * quality) if success else 0
        new_char = add_money(new_char, gain)
        new_char = apply_fatigue(new_char, duration_hours)
        money_delta = gain
        fatigue_delta = duration_hours

    elif action.action_type == ActionType.fight:
        # Si echec critique, le perso prend des degats
        if result.outcome == ActionOutcome.catastrophic_failure:
            damage = 30 + duration_hours * 5
            new_char = apply_damage(new_char, damage, description="blessure de combat")
            hp_delta = -damage
            # Risque d'empoisonnement par arme adverse (kunai enduit, senbon, crochet de bete)
            r_poison = roll(result.seed_after, "1d10")
            if r_poison.total >= 7:
                from shinobi.engine.character import Poison

                poison = Poison(
                    name="toxine inconnue",
                    severity="severe",
                    rounds_remaining=3,
                )
                new_health = new_char.health.model_copy(
                    update={"poison_status": [*new_char.health.poison_status, poison]}
                )
                new_char = new_char.with_health(new_health)
        elif result.outcome == ActionOutcome.minor_failure:
            damage = 10
            new_char = apply_damage(new_char, damage)
            hp_delta = -damage
        new_char = apply_fatigue(new_char, 10)
        fatigue_delta = 10
        if chakra_cost == 0:
            chakra_cost = 20

    elif action.action_type == ActionType.use_technique:
        # Cout de chakra par defaut
        if chakra_cost == 0:
            chakra_cost = 25

    elif action.action_type == ActionType.research:
        new_char, c = train_stat(
            new_char, "intelligence", hours=duration_hours, quality_modifier=quality * 0.5
        )
        if c:
            stat_changes.append(c)

    elif action.action_type == ActionType.steal:
        if success:
            gain = int(200 * quality)
            new_char = add_money(new_char, gain)
            money_delta = gain
        elif result.outcome == ActionOutcome.catastrophic_failure:
            new_char = apply_damage(new_char, 5, description="prise au vol")
            hp_delta = -5

    elif action.action_type == ActionType.bribe:
        cost = int(action.parameters.get("amount", 100))
        if character.money >= cost:
            new_char = add_money(new_char, -cost)
            money_delta = -cost

    elif action.action_type == ActionType.pray:
        # Recueillement : willpower + chakra recovery + reduit la fatigue mentale
        new_char, c = train_stat(
            new_char, "willpower", hours=duration_hours, quality_modifier=0.4 * quality
        )
        if c:
            stat_changes.append(c)
        chakra_gain = duration_hours * 8
        new_chakra = new_char.chakra.model_copy(
            update={"current": min(new_char.chakra.max, new_char.chakra.current + chakra_gain)}
        )
        new_char = new_char.with_chakra(new_chakra)
        fatigue_reduction = duration_hours * 2
        new_health = new_char.health.model_copy(
            update={"fatigue": max(0, new_char.health.fatigue - fatigue_reduction)}
        )
        new_char = new_char.with_health(new_health)
        fatigue_delta = -fatigue_reduction

    elif action.action_type == ActionType.spy:
        # Espionnage : perception + petit gain d'info si succes
        new_char, c = train_stat(
            new_char, "perception", hours=max(1, duration_hours // 2), quality_modifier=quality
        )
        if c:
            stat_changes.append(c)
        new_char = apply_fatigue(new_char, max(2, duration_hours))
        fatigue_delta = max(2, duration_hours)
        if result.outcome == ActionOutcome.catastrophic_failure:
            # Repere : penalite reputation + petite blessure
            new_char = apply_damage(new_char, 5, description="repere lors d'une filature")
            hp_delta = -5

    elif action.action_type == ActionType.submit_mission:
        # Rapport de fin de mission : minor willpower training, reset chakra leger
        new_char, c = train_stat(
            new_char, "willpower", hours=max(1, duration_hours // 2), quality_modifier=0.3
        )
        if c:
            stat_changes.append(c)

    # Affinity / reputation : modifications passives selon action sociale
    target_npc = action.parameters.get("target_id") or action.parameters.get("character_id")
    if target_npc:
        if action.action_type == ActionType.talk:
            delta = 3 if success else -1
            new_char = update_affinity(new_char, with_id=target_npc, delta=delta)
        elif action.action_type == ActionType.seduce:
            delta = 8 if result.outcome == ActionOutcome.full_success else (
                4 if result.outcome == ActionOutcome.partial_success else -3
            )
            new_char = update_affinity(new_char, with_id=target_npc, delta=delta)
        elif action.action_type == ActionType.intimidate:
            new_char = update_affinity(new_char, with_id=target_npc, delta=-5)
        elif action.action_type == ActionType.bribe:
            delta = 5 if success else -3
            new_char = update_affinity(new_char, with_id=target_npc, delta=delta)
        elif action.action_type == ActionType.fight:
            new_char = update_affinity(new_char, with_id=target_npc, delta=-15)

    # Reputation village pour actions visibles
    village = character.current_village
    if action.action_type == ActionType.steal and result.outcome == ActionOutcome.catastrophic_failure:
        new_char = add_reputation(new_char, village, -10)
    elif action.action_type == ActionType.work and success:
        new_char = add_reputation(new_char, village, 1)
    elif action.action_type == ActionType.fight and result.outcome == ActionOutcome.catastrophic_failure:
        new_char = add_reputation(new_char, village, -3)

    if chakra_cost > 0:
        new_char = apply_chakra_cost(new_char, chakra_cost)

    # Toute action consomme un peu de chakra et d'energie au minimum
    if action.action_type not in (ActionType.rest, ActionType.meditate, ActionType.wait):
        passive_chakra = max(1, duration_hours // 2)
        new_char = apply_chakra_cost(new_char, passive_chakra)

    # Consequences emergentes : l'action elle-meme apprend des stats indirectes
    # (ex: combat -> taijutsu+strength+willpower meme si train_stat n'a pas ete appele)
    new_char, side_changes, applied_consequences = apply_action_consequences(
        new_char,
        action_type=action.action_type,
        outcome=result.outcome,
        duration_hours=duration_hours,
    )
    stat_changes.extend(side_changes)
    consequences_payload = [
        {
            "stat": ac.change.stat_name,
            "old": ac.change.old,
            "new": ac.change.new,
            "delta": ac.change.delta,
            "why_fr": ac.why_fr,
        }
        for ac in applied_consequences
    ]

    new_result = result.model_copy(
        update={
            "stat_changes": [
                {"stat": c.stat_name, "old": c.old, "new": c.new, "delta": c.delta}
                for c in stat_changes
            ],
            "money_delta": money_delta,
            "hp_delta": hp_delta,
            "fatigue_delta": fatigue_delta,
            "consequences": consequences_payload,
        }
    )
    return new_char, world, new_result


def apply_mission_result(
    character: Character,
    mission: Mission,
    *,
    success: bool,
) -> tuple[Character, int, list]:
    """Applique le resultat d'une mission. Retourne (char, ryos_gagnes, stat_changes)."""
    from shinobi.engine.consequences import mission_consequences

    if not success:
        new_char = apply_damage(character, 15, description=f"echec de mission {mission.title}")
        new_char = apply_fatigue(new_char, 30)
        new_char = add_reputation(new_char, character.current_village, -mission.reputation_delta // 2)
        new_char, changes = mission_consequences(new_char, mission, success=False)
        return new_char, 0, changes
    new_char = add_money(character, mission.reward_ryos)
    new_char = apply_fatigue(new_char, 20)
    new_char = add_reputation(new_char, character.current_village, mission.reputation_delta)
    new_char, changes = mission_consequences(new_char, mission, success=True)
    return new_char, mission.reward_ryos, changes
