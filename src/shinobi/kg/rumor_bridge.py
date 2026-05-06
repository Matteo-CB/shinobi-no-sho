"""Pont entre engine/rumors.py (Rumor world-level) et kg/belief.py (Beliefs par NPC).

Pourquoi : la roadmap §5.4 specifie textuellement
> Les rumeurs (systeme existant `engine/rumors.py`) propagent les faits
> entre sous-KG selon les liens sociaux

Sans ce pont, le BeliefPropagator vit en parallele du systeme Rumor existant
sans s'integrer. Le pont fait :

A. Rumor -> Fact + Beliefs : quand une rumeur nait dans le world, on cree
   un Fact dans le KG (source='rumor:<rumor_id>') et on insere des Beliefs
   pour les NPCs touches par le radius.

B. Belief -> Rumor : un fait apparu en jeu (player_action) peut devenir
   une rumeur publique propagee a tous les NPCs dans le rayon.

C. Cascade hybride : propagate_rumor_via_social() combine la propagation
   par radius (existant) ET la cascade BFS dans le reseau social (Phase B
   nouveau). Une rumeur 'regional' touche les NPCs du village, et de la
   chaque NPC peut diffuser dans son cercle social local.

Le radius_fidelity du systeme Rumor existant est preserve :
  proximity 0.95, regional 0.8, international 0.6, secret 0.5
"""

from __future__ import annotations

from collections.abc import Iterable

from shinobi.engine.rumors import _RADIUS_FIDELITY
from shinobi.engine.world import Rumor, WorldState
from shinobi.kg.belief import CHANNEL_DECAY, BeliefPropagator
from shinobi.kg.schema import Belief, Canonicity, Fact, ObjectType
from shinobi.kg.store import KnowledgeGraphStore


def rumor_to_fact(rumor: Rumor, *, subject_override: str | None = None) -> Fact:
    """Convertit un Rumor en Fact pour insertion dans le KG.

    Le `Rumor` du world n'est pas tres structure (juste content text). On
    cree un fact synthetique :
      subject = source_event_id (ou rumor_id en fallback)
      relation = 'is_rumored'
      object = rumor.content (text de la rumeur)
      object_type = belief
      source = 'rumor:<rumor_id>'
      canonicity = canon_modified (les rumeurs sont par nature contestees)
    """
    return Fact(
        subject=subject_override or rumor.source_event_id or rumor.id,
        relation="is_rumored",
        object=rumor.content,
        object_type=ObjectType.belief,
        valid_from_year=rumor.born_at_year,
        valid_to_year=rumor.expires_at_year,
        source=f"rumor:{rumor.id}",
        confidence=rumor.fidelity,
        canonicity=Canonicity.canon_modified,
    )


def insert_rumor_as_fact(
    store: KnowledgeGraphStore, rumor: Rumor,
    *, subject_override: str | None = None,
) -> int:
    """Persiste un Rumor en Fact + retourne le fact_id."""
    fact = rumor_to_fact(rumor, subject_override=subject_override)
    return store.add_fact(fact)


def propagate_rumor_to_npcs(
    propagator: BeliefPropagator,
    rumor: Rumor,
    fact_id: int,
    npcs_in_radius: Iterable[str],
    *,
    channel: str = "rumor",
) -> dict[str, float]:
    """Pour chaque NPC dans le rayon, cree un Belief avec fidelity du rumor.

    Le canal par defaut est 'rumor' (decay 0.7). On peut override avec
    'spy', 'witness', 'report' selon le contexte.

    Le rumor.fidelity est deja attenue par radius (proximity 0.95, regional
    0.8, etc.). Chaque NPC le recoit avec fidelity = rumor.fidelity *
    channel_decay (sauf si on dit 'witness' qui prend rumor.fidelity tel quel).

    Retourne le dict {npc_id: fidelity_finale}.
    """
    decay = CHANNEL_DECAY.get(channel, 0.7)
    if channel == "witness":
        decay = 1.0
    spread: dict[str, float] = {}
    year = rumor.born_at_year
    for npc_id in npcs_in_radius:
        new_fid = rumor.fidelity * decay
        propagator.add_belief(Belief(
            fact_id=fact_id, npc_id=npc_id,
            fidelity=new_fid,
            learned_at_year=year,
            learned_via_npc_id=None,
            learned_via_channel=channel,
        ))
        spread[npc_id] = new_fid
    return spread


def propagate_rumor_via_social(
    store: KnowledgeGraphStore,
    propagator: BeliefPropagator,
    rumor: Rumor,
    primary_witnesses: list[str],
    *,
    max_depth: int = 3,
    cascade_channel: str = "rumor",
) -> tuple[int, dict[str, float]]:
    """Propagation hybride : insere le fact, marque les temoins, puis
    cascade BFS via reseau social pour les contacts indirects.

    primary_witnesses = NPCs qui sont temoins ou directement informes
    (ex: villageois du lieu d'un event).

    Retourne (fact_id, dict {npc_id: fidelity}).
    """
    fact_id = insert_rumor_as_fact(store, rumor)
    # Cascade BFS depuis chaque temoin primaire avec rumor.fidelity comme
    # fidelity de depart (eviter de re-promouvoir a 1.0 le temoin secondaire
    # d'une rumeur).
    all_spread: dict[str, float] = {}
    for witness in primary_witnesses:
        cascade = propagator.propagate_cascade(
            witness, fact_id,
            year=rumor.born_at_year,
            max_depth=max_depth,
            channel=cascade_channel,
            initial_fidelity=rumor.fidelity,
        )
        for npc, fid in cascade.items():
            if npc not in all_spread or all_spread[npc] < fid:
                all_spread[npc] = fid
    return fact_id, all_spread


def belief_to_rumor(
    fact: Fact,
    belief: Belief,
    *,
    radius: str = "regional",
    expires_in_years: int = 5,
) -> Rumor:
    """Convertit un Belief + son Fact en Rumor pour publication world-level.

    Cas d'usage : un fait genere en jeu (player_action) qui prend assez
    d'ampleur pour devenir une rumeur publique.

    La fidelity du rumor = belief.fidelity (note : le radius decay est NE
    PAS applique ici, la rumeur preserve la fidelity du belief sous-jacent.
    Le radius sert juste a determiner qui peut entendre).
    """
    import uuid

    # Synthese textuelle du fact (a affiner plus tard via LLM si besoin)
    content = fact.object or f"{fact.subject} {fact.relation}"
    born_year = belief.learned_at_year or fact.valid_from_year or 0
    return Rumor(
        id=str(uuid.uuid4()),
        source_event_id=fact.subject if fact.object_type == ObjectType.entity else None,
        content=str(content),
        fidelity=belief.fidelity,
        diffusion_radius=radius,  # type: ignore[arg-type]
        born_at_year=born_year,
        expires_at_year=born_year + expires_in_years,
    )


def sync_world_rumors_to_kg(
    store: KnowledgeGraphStore,
    propagator: BeliefPropagator,
    world: WorldState,
    *,
    npcs_per_rumor: dict[str, list[str]] | None = None,
) -> dict[str, int]:
    """Idempotent : pour chaque Rumor du world, cree un Fact + Beliefs.

    npcs_per_rumor : si fourni, dict {rumor_id: [npc_ids]} indiquant qui
    a entendu cette rumeur. Sinon, aucun belief n'est cree (juste le fact).

    Retourne stats {rumors_processed, facts_created, beliefs_created}.
    """
    npcs_per_rumor = npcs_per_rumor or {}
    facts_created = 0
    beliefs_created = 0

    for rumor in world.rumors:
        # Idempotence : skip si un fact avec source 'rumor:<id>' existe deja
        existing = store.get_facts(source_prefix=f"rumor:{rumor.id}")
        if existing:
            fact_id = existing[0].id  # type: ignore[assignment]
        else:
            fact_id = insert_rumor_as_fact(store, rumor)
            facts_created += 1

        npcs = npcs_per_rumor.get(rumor.id, [])
        for npc in npcs:
            propagator.add_belief(Belief(
                fact_id=fact_id,  # type: ignore[arg-type]
                npc_id=npc,
                fidelity=rumor.fidelity * CHANNEL_DECAY.get("rumor", 0.7),
                learned_at_year=rumor.born_at_year,
                learned_via_channel="rumor",
            ))
            beliefs_created += 1

    return {
        "rumors_processed": len(world.rumors),
        "facts_created": facts_created,
        "beliefs_created": beliefs_created,
    }


__all__ = [
    "_RADIUS_FIDELITY",
    "belief_to_rumor",
    "insert_rumor_as_fact",
    "propagate_rumor_to_npcs",
    "propagate_rumor_via_social",
    "rumor_to_fact",
    "sync_world_rumors_to_kg",
]
