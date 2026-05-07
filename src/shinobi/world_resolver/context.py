"""Context builders Phase F : extraction WorldState + KG -> texte LLM.

Spec doc 02 §8.2 : "LLM analyse le cancelled + etat actuel du KG".
La pipeline `WorldResolverPipeline.close_loop` accepte des strings
`world_state_summary` et `kg_recent_facts`. Ces helpers les construisent
de maniere structuree et concise pour ne pas exploser le contexte LLM.

Auto-detection mode : `select_validation_mode` decide canon_strict vs
alternate_timeline en fonction de la presence de facts divergents en KG.
"""

from __future__ import annotations

from shinobi.engine.world import WorldState
from shinobi.kg.store import KnowledgeGraphStore
from shinobi.world_resolver.types import ValidationMode

DEFAULT_KG_FACTS_LIMIT = 30
DEFAULT_DIVERGENT_THRESHOLD = 1


def build_world_state_summary(
    world: WorldState, *, max_chars: int = 600,
) -> str:
    """Construit un summary compact du WorldState pour le prompt LLM.

    Inclut les events recemment completed/cancelled, les rumeurs en cours,
    le climat politique - tout ce qui peut aider le LLM a generer un
    substitute coherent.
    """
    lines: list[str] = [
        f"Annee courante : {world.current_year} (date {world.current_date})",
    ]
    if world.completed_events:
        recent_completed = world.completed_events[-5:]
        lines.append(
            "Events canon recemment declenches : "
            + ", ".join(e.event_id for e in recent_completed)
        )
    if world.cancelled_events:
        recent_cancelled = world.cancelled_events[-5:]
        lines.append(
            "Events canon recemment annules : "
            + ", ".join(f"{e.event_id} ({e.reason})" for e in recent_cancelled)
        )
    if world.rumors:
        recent_rumors = [r for r in world.rumors if not r.received_by_player][:3]
        if recent_rumors:
            lines.append(
                "Rumeurs en circulation : "
                + " | ".join(r.content[:80] for r in recent_rumors)
            )
    npcs_alive = sum(1 for n in world.npc_states.values() if n.is_alive)
    if npcs_alive:
        lines.append(f"NPCs vivants tracked : {npcs_alive}")

    summary = "\n".join(lines)
    if len(summary) > max_chars:
        summary = summary[:max_chars] + "..."
    return summary


def build_kg_recent_facts(
    kg: KnowledgeGraphStore,
    *,
    current_year: int,
    limit: int = DEFAULT_KG_FACTS_LIMIT,
    only_divergent: bool = False,
) -> str:
    """Construit un summary des facts KG recents, idealement divergents.

    Spec §8.2 : le LLM doit savoir ce qui a deja change dans cette branche.
    On priorise les facts source='player_action' et canonicity='divergent'
    qui sont les indicateurs de divergence joueur.
    """
    lines: list[str] = []
    # Round 21 : dedup par couple (subject, relation) coherent entre les
    # deux boucles. Avant, la boucle divergente stockait juste `subject`
    # ce qui (a) sur-deduplique meme relation differente, (b) ne matchait
    # pas le composite "subject.relation" utilise dans la boucle player.
    seen_keys: set[str] = set()

    # Facts divergents en priorite
    divergent_facts = kg.get_facts(canonicity="divergent", limit=limit)
    for f in divergent_facts[:limit // 2]:
        key = f"{f.subject}.{f.relation}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        lines.append(f"  [divergent] {f.subject}.{f.relation} = {f.object}")

    if not only_divergent:
        # Facts player_action
        player_facts = kg.get_facts(source_prefix="player_action", limit=limit)
        for f in player_facts[: limit // 2]:
            key = f"{f.subject}.{f.relation}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            lines.append(f"  [player] {f.subject}.{f.relation} = {f.object}")

    if not lines:
        return "(aucun fait notable - branche encore proche du canon)"
    return "\n".join(lines)


def select_validation_mode(
    kg: KnowledgeGraphStore,
    *,
    divergent_threshold: int = DEFAULT_DIVERGENT_THRESHOLD,
) -> ValidationMode:
    """Selection auto du mode validation.

    Spec §8.3 : 'Mode strict par defaut sur les arcs pre-divergence joueur,
    mode alternate apres que le joueur ait cause une divergence majeure.'

    Heuristique : compte les divergences **causees par le joueur**
    (source LIKE 'player_action%') en KG. Si >= `divergent_threshold`,
    branche est alternate. Sinon canon_strict.

    Round 68 : avant, on comptait TOUS les divergents incluant ceux emis
    par l'injector Phase F (source='substitute:...'). Phase F lui-meme
    declenchait la bascule, alors que la spec demande "le joueur ait cause"
    -> les consequences Phase F sont des effets, pas des causes.
    """
    n_player_divergent = kg.count(
        canonicity="divergent", source_prefix="player_action",
    )
    if n_player_divergent >= divergent_threshold:
        return ValidationMode.alternate_timeline
    return ValidationMode.canon_strict


__all__ = [
    "DEFAULT_DIVERGENT_THRESHOLD",
    "DEFAULT_KG_FACTS_LIMIT",
    "build_kg_recent_facts",
    "build_world_state_summary",
    "select_validation_mode",
]
