"""Bridge AgentAction -> mutation KG.

Spec docs/02 §6.3 :
> Ces actions modifient le KG, qui a son tour change ce que les autres
> PNJ peuvent observer.

Pour CHAQUE AgentAction emise par un agent :
1. On insere un fact KG sur l'action (subject=agent, relation=did_X, object=details)
2. On insere des Observations 'percues' pour les autres agents presents dans
   la meme location (visibilite directe)
3. Les rumeurs (Phase B existant) peuvent ensuite propager si l'action est
   notable (importance >= seuil).

Mapping AgentActionType -> Fact triplet :
- speak    : (npc, said_to, target_npc) + value=content
- attack   : (npc, attacked, target_npc) + value=content
- travel   : (npc, traveled_to, location)
- declare_intention : (npc, declared, content) value
- search_information : (npc, searched_for, content)
- meditate : (npc, meditated, content) low importance, pas propage
- plot     : (npc, plotted_against, target_npc) - SECRET, known_by_npc_ids = [npc]
- idle     : pas de fact KG (trivial)
- custom   : (npc, custom_action, content)

Le module est branchable : ne touche au KG QUE si un store est fourni.
"""

from __future__ import annotations

from collections.abc import Iterable

from shinobi.agents.action_space import AgentAction, AgentActionType
from shinobi.agents.types import Observation
from shinobi.kg.schema import Canonicity, Fact, ObjectType
from shinobi.kg.store import KnowledgeGraphStore

# Importance seuil pour generer des observations chez les agents temoins
WITNESS_OBSERVATION_THRESHOLD: float = 0.0  # all visible actions seen
SECRET_ACTION_TYPES: frozenset[AgentActionType] = frozenset({
    AgentActionType.plot,
})


# Mapping AgentActionType -> relation triplet KG
_ACTION_RELATIONS: dict[AgentActionType, str] = {
    AgentActionType.speak: "said_to",
    AgentActionType.attack: "attacked",
    AgentActionType.travel: "traveled_to",
    AgentActionType.declare_intention: "declared_intention",
    AgentActionType.search_information: "searched_for",
    AgentActionType.meditate: "meditated",
    AgentActionType.plot: "plotted_against",
    AgentActionType.idle: "idled",
    AgentActionType.custom: "custom_action",
}


def action_to_fact(action: AgentAction) -> Fact | None:
    """Convertit une AgentAction en Fact pour le KG. Retourne None pour idle."""
    if action.type == AgentActionType.idle:
        return None
    relation = _ACTION_RELATIONS.get(action.type, "did")

    # L'objet du triplet : target_npc_id pour speak/attack/plot,
    # location_id pour travel, sinon le content texte
    if action.target_npc_id and action.type in (
        AgentActionType.speak, AgentActionType.attack, AgentActionType.plot,
    ):
        obj = action.target_npc_id
        obj_type = ObjectType.entity
    elif action.type == AgentActionType.travel and action.location_id:
        obj = action.location_id
        obj_type = ObjectType.entity
    else:
        obj = action.content[:200] if action.content else action.type.value
        obj_type = ObjectType.value

    # Action secrete : known_by_npc_ids = [actor seul]
    known_by: list[str] = []
    if action.type in SECRET_ACTION_TYPES:
        known_by = [action.npc_id]

    return Fact(
        subject=action.npc_id,
        relation=relation,
        object=obj,
        object_type=obj_type,
        valid_from_year=action.year,
        valid_to_year=action.year,  # action ponctuelle
        source=f"player_action:agent_{action.id}",
        canonicity=Canonicity.divergent,  # action emergente
        confidence=action.importance,
        known_by_npc_ids=known_by,
    )


def witness_observation(
    action: AgentAction,
    *,
    witness_npc_id: str,
    importance_dampening: float = 0.7,
) -> Observation:
    """Genere une Observation pour un PNJ temoin de l'action.

    Le temoin enregistre l'action comme 'fait percu' avec une importance
    diminuee (dampening 0.7 par defaut, sauf si action secrete -> skip).
    """
    if action.target_npc_id == witness_npc_id:
        # Le PNJ EST la cible : importance pleine
        importance = min(1.0, action.importance + 0.1)
        text = (
            f"{action.npc_id} {action.type.value} envers moi "
            f"({action.content[:100] if action.content else ''})"
        )
    else:
        importance = action.importance * importance_dampening
        text = (
            f"J'ai vu {action.npc_id} {action.type.value} "
            f"({action.content[:100] if action.content else ''})"
        )
    return Observation(
        npc_id=witness_npc_id,
        text=text,
        year=action.year,
        importance=importance,
        source_npc_id=action.npc_id,
        location_id=action.location_id,
    )


def push_action_to_kg(
    action: AgentAction,
    *,
    kg_store: KnowledgeGraphStore | None,
) -> int | None:
    """Insere l'action comme Fact dans le KG. Retourne fact_id (ou None).

    No-op si kg_store=None ou action triviale (idle).
    """
    if kg_store is None:
        return None
    fact = action_to_fact(action)
    if fact is None:
        return None
    return kg_store.add_fact(fact)


def collect_witness_observations(
    actions: Iterable[AgentAction],
    *,
    npcs_in_scene_per_location: dict[str, set[str]] | None = None,
    skip_secret: bool = True,
) -> dict[str, list[Observation]]:
    """Pour un batch d'actions, calcule les observations de chaque temoin.

    `npcs_in_scene_per_location` : map location_id -> set of npc_ids
    presents a cet endroit. Si None ou si une action n'a pas de location,
    on ne genere d'observation que pour le target_npc_id.

    Retourne dict[witness_npc_id -> list[Observation]] pret a injecter
    via TickEngine.tick(observations_per_npc=...).
    """
    out: dict[str, list[Observation]] = {}
    for action in actions:
        if skip_secret and action.type in SECRET_ACTION_TYPES:
            continue

        # 1. Le target temoin (toujours)
        if action.target_npc_id and action.target_npc_id != action.npc_id:
            obs = witness_observation(
                action, witness_npc_id=action.target_npc_id,
            )
            out.setdefault(action.target_npc_id, []).append(obs)

        # 2. Les autres NPCs presents a la meme location (si connu)
        if (
            action.location_id
            and npcs_in_scene_per_location is not None
            and action.location_id in npcs_in_scene_per_location
        ):
            for witness_id in npcs_in_scene_per_location[action.location_id]:
                if witness_id == action.npc_id:
                    continue
                if witness_id == action.target_npc_id:
                    continue  # deja ajoute
                obs = witness_observation(action, witness_npc_id=witness_id)
                out.setdefault(witness_id, []).append(obs)
    return out


def push_actions_to_kg_batch(
    actions: Iterable[AgentAction],
    *,
    kg_store: KnowledgeGraphStore | None,
) -> int:
    """Bulk : insere toutes les actions en facts. Retourne nb facts inseres."""
    if kg_store is None:
        return 0
    n = 0
    for a in actions:
        fid = push_action_to_kg(a, kg_store=kg_store)
        if fid is not None:
            n += 1
    return n


# Spec §6.5 : 'events canon se declenchent ou s'annulent selon les actions
# agents'. Pour que le canon scheduler reagisse vraiment aux actions, les
# actions HIGH-IMPACT doivent muter l'etat du monde lu par evaluate_precondition
# (world.npc_states notamment).

# Threshold importance pour qu'une action mute l'etat du monde
WORLD_IMPACT_THRESHOLD: float = 0.7


def apply_action_to_world_state(action, world):
    """Applique une AgentAction high-impact aux mutations du WorldState.

    Spec §6.5 : 'events canon ... selon les actions agents'. Mappings :
    - travel : update NPCState.current_location pour l'acteur
    - attack (importance>=THRESHOLD) : mark target.psychological_state='threatened'
    - speak (importance>=0.85) : mark target.psychological_state='socially_affected'

    Retourne un nouveau WorldState (ou le meme si pas de mutation).
    Le WorldState est immutable : on utilise model_copy.
    """
    new_npc_states = dict(world.npc_states)
    mutated = False

    if action.type == AgentActionType.travel and action.location_id:
        actor_state = new_npc_states.get(action.npc_id)
        if actor_state is not None:
            new_npc_states[action.npc_id] = actor_state.model_copy(update={
                "current_location": action.location_id,
                "last_updated_year": action.year,
            })
            mutated = True

    if (
        action.type == AgentActionType.attack
        and action.target_npc_id
        and action.importance >= WORLD_IMPACT_THRESHOLD
    ):
        target_state = new_npc_states.get(action.target_npc_id)
        if target_state is not None:
            new_npc_states[action.target_npc_id] = target_state.model_copy(
                update={
                    "psychological_state": "threatened",
                    "last_updated_year": action.year,
                },
            )
            mutated = True

    if (
        action.type == AgentActionType.speak
        and action.target_npc_id
        and action.importance >= 0.85
    ):
        target_state = new_npc_states.get(action.target_npc_id)
        if target_state is not None:
            new_npc_states[action.target_npc_id] = target_state.model_copy(
                update={
                    "psychological_state": "socially_affected",
                    "last_updated_year": action.year,
                },
            )
            mutated = True

    if not mutated:
        return world
    return world.model_copy(update={"npc_states": new_npc_states})


def apply_actions_to_world_state(actions: Iterable, world):
    """Bulk : applique toutes les actions high-impact au monde sequentiellement."""
    cur = world
    for a in actions:
        cur = apply_action_to_world_state(a, cur)
    return cur


__all__ = [
    "SECRET_ACTION_TYPES",
    "WITNESS_OBSERVATION_THRESHOLD",
    "WORLD_IMPACT_THRESHOLD",
    "action_to_fact",
    "apply_action_to_world_state",
    "apply_actions_to_world_state",
    "collect_witness_observations",
    "push_action_to_kg",
    "push_actions_to_kg_batch",
    "witness_observation",
]
