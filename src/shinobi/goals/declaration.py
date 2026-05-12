"""Declaration d'objectifs par le joueur."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from shinobi.types import GoalStatus


class GoalTargetType:
    """Constantes pour les types d'objectif."""

    learn_technique = "learn_technique"
    achieve_rank = "achieve_rank"
    kill_character = "kill_character"
    befriend_character = "befriend_character"
    marry_character = "marry_character"
    join_organization = "join_organization"
    leave_village = "leave_village"
    found_organization = "found_organization"
    obtain_object = "obtain_object"
    survive_event = "survive_event"
    prevent_event = "prevent_event"
    cause_event = "cause_event"
    master_kekkei_genkai = "master_kekkei_genkai"
    master_nature = "master_nature"
    revive_character = "revive_character"
    transcend_humanity = "transcend_humanity"
    free_form = "free_form"


class Goal(BaseModel):
    """Objectif declare.

    Phase i18n.8 : `description_player` reste verbatim dans la langue saisie
    par le joueur. `description_player_original_language` stocke le code ISO
    detecte (ex: "fr"). `description_player_translated` est un cache des
    traductions vers d'autres langues config, ex: {"en": "...", "ja": "..."}.
    Ces deux champs sont retro-compatibles : valeurs par defaut vides pour
    les goals serialises avant Phase 8.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    declared_at_year: int
    declared_at_age: int
    description_player: str
    interpretation_canonical: str
    target_type: str = GoalTargetType.free_form
    target_id: str | None = None
    status: GoalStatus = GoalStatus.declared
    declared_priority: int = 5
    breadcrumbs: list[str] = Field(default_factory=list)
    completed_at_year: int | None = None
    abandoned_at_year: int | None = None
    description_player_original_language: str | None = None
    description_player_translated: dict[str, str] = Field(default_factory=dict)


def declare_goal(
    *,
    description_player: str,
    interpretation_canonical: str,
    declared_at_year: int,
    declared_at_age: int,
    target_type: str = GoalTargetType.free_form,
    target_id: str | None = None,
    declared_priority: int = 5,
    description_player_original_language: str | None = None,
    description_player_translated: dict[str, str] | None = None,
) -> Goal:
    """Cree un nouveau Goal avec un id unique.

    Les champs `description_player_original_language` et
    `description_player_translated` (Phase i18n.8) sont optionnels et
    typiquement remplis par les call-sites CLI/API via le
    `PlayerTranslator` avant l'appel.
    """
    return Goal(
        id=str(uuid.uuid4()),
        declared_at_year=declared_at_year,
        declared_at_age=declared_at_age,
        description_player=description_player,
        interpretation_canonical=interpretation_canonical,
        target_type=target_type,
        target_id=target_id,
        declared_priority=declared_priority,
        description_player_original_language=description_player_original_language,
        description_player_translated=dict(description_player_translated or {}),
    )


def describe_goal_for_lang(goal: Goal, lang: str) -> str:
    """Retourne la description du goal a afficher pour une langue donnee.

    Phase i18n.8 :
    1. Si `lang` matche `description_player_original_language` -> verbatim
       (`description_player`).
    2. Si `lang` est present dans `description_player_translated` -> traduction.
    3. Sinon -> fallback sur `description_player` verbatim (le joueur verra
       sa langue d'origine, ce qui reste comprehensible plutot que vide).
    """
    if not lang:
        return goal.description_player
    if (
        goal.description_player_original_language
        and goal.description_player_original_language == lang
    ):
        return goal.description_player
    translated = goal.description_player_translated.get(lang)
    if translated:
        return translated
    return goal.description_player


def abandon_goal(goal: Goal, year: int) -> Goal:
    return goal.model_copy(update={"status": GoalStatus.abandoned, "abandoned_at_year": year})


def complete_goal(goal: Goal, year: int) -> Goal:
    return goal.model_copy(update={"status": GoalStatus.completed, "completed_at_year": year})


def mark_goal_in_progress(goal: Goal) -> Goal:
    """Transition declared -> in_progress quand >=1 breadcrumb est revele.

    Phase 5 : sans cette transition, le statut reste 'declared' pour un
    goal dont le pathfinder a deja produit des indices. Permet au CLI /
    Director de distinguer un goal stale d'un goal exploré activement.

    Idempotent : si le goal est deja in_progress / completed / abandoned,
    retourne tel quel.
    """
    if goal.status != GoalStatus.declared:
        return goal
    return goal.model_copy(update={"status": GoalStatus.in_progress})


def fail_goal(goal: Goal, year: int, *, reason: str = "") -> Goal:
    """Transition vers failed (goal devenu impossible).

    Cas typiques :
    - target_id (befriend_character, kill_character, marry_character) est
      maintenant mort dans le canon ou KG.
    - Le joueur est mort avec un goal actif.
    - Un evenement canon a rendu le but inatteignable.

    Idempotent : si le goal est deja completed / abandoned / failed,
    retourne tel quel sans muter year.
    """
    if goal.status in (
        GoalStatus.completed, GoalStatus.abandoned, GoalStatus.failed,
    ):
        return goal
    update: dict = {"status": GoalStatus.failed}
    # On reutilise abandoned_at_year comme champ "fin de vie" du goal :
    # le schema Goal n'a pas de failed_at_year dedie pour rester compact.
    update["abandoned_at_year"] = year
    return goal.model_copy(update=update)


def detect_goal_failure(
    goal: Goal,
    *,
    canon_characters: dict | None = None,
    current_year: int,
    player_is_dead: bool = False,
) -> str | None:
    """Detecte automatiquement si un goal est devenu impossible.

    Returns une raison FR si le goal doit etre marque failed, sinon None.

    Heuristiques actuelles :
    - Joueur mort -> tous les goals fail (raison : mort joueur)
    - target_type befriend/marry/kill_character + target_id avec death_year
      <= current_year -> fail (target deja mort)
    """
    if goal.status in (
        GoalStatus.completed, GoalStatus.abandoned, GoalStatus.failed,
    ):
        return None
    if player_is_dead:
        return "joueur decede avec goal actif"

    target_dependent = {"befriend_character", "marry_character", "kill_character"}
    if (
        goal.target_type in target_dependent
        and goal.target_id is not None
        and canon_characters is not None
    ):
        char = canon_characters.get(goal.target_id)
        if char is not None:
            death_year = getattr(char, "death_year", None)
            if isinstance(death_year, int) and death_year <= current_year:
                if goal.target_type == "kill_character":
                    # Specifique : si quelqu'un d'autre l'a tue, le goal du
                    # joueur est echoue (il n'a pas accompli l'action).
                    return f"target {goal.target_id} deja mort en {death_year}"
                return (
                    f"target {goal.target_id} mort en {death_year}, "
                    f"goal {goal.target_type} impossible"
                )
    return None
