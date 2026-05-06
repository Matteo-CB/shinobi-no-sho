"""Builders auto pour SelectionContext : world_summary + relations_summary.

Spec docs/02 §6.3 : le LLM recoit pour chaque agent
- L'etat du monde local (KG filtre sur ce qu'il sait)
- Sa relation avec les autres PNJ presents

Les `selector.SelectionContext` accepte ces deux champs comme strings, mais
ils etaient remplis manuellement par le caller. Ce module fournit des
builders deterministes qui interrogent :
- `KnowledgeGraphStore` (Phase A) avec filtre `known_by_npc_ids` (Phase B)
- `SocialNetwork` (Phase B) pour les liens sociaux

Pas de couplage avec le canon : on lit ce qui EST dans le KG / SocialNetwork
au moment du tick. Si rien n'est connu, retourne string vide.
"""

from __future__ import annotations

from collections.abc import Iterable

from shinobi.kg.schema import Canonicity
from shinobi.kg.social import SocialNetwork
from shinobi.kg.store import KnowledgeGraphStore

# Relations -> verbe FR pour humaniser la summary
_RELATION_VERBS_FR: dict[str, str] = {
    "said_to": "a parle a",
    "attacked": "a attaque",
    "traveled_to": "est alle a",
    "declared_intention": "a declare",
    "searched_for": "cherche",
    "plotted_against": "complote contre",
    "current_village": "reside a",
    "located_at": "se trouve a",
    "alive": "est en vie",
    "rank": "rang",
    "involves": "implique dans",
    "has_kekkei_genkai": "porte le kekkei genkai",
    "is_jinchuriki_of": "est jinchuriki de",
    "kage": "kage de",
}


def build_world_summary_for_npc(
    *,
    kg_store: KnowledgeGraphStore | None,
    npc_id: str,
    year: int,
    max_facts: int = 12,
    include_canon: bool = True,
    include_divergent: bool = True,
) -> str:
    """Compose une summary FR de ce que `npc_id` SAIT du monde a l'annee `year`.

    Strategie deterministe :
    1. Recupere les facts du KG actifs a `year` ou `npc_id` est dans
       `known_by_npc_ids` (sub-KG personnel).
    2. Inclut aussi les facts canon (sauf si include_canon=False) car le PNJ
       a une connaissance baseline du canon.
    3. Limite a `max_facts` (priorise par confidence + recence).
    4. Retourne une string lisible humaine.
    """
    if kg_store is None:
        return ""
    canonicities: list[str] = []
    if include_canon:
        canonicities.append(Canonicity.canon_strict.value)
    if include_divergent:
        canonicities.append(Canonicity.canon_modified.value)
        canonicities.append(Canonicity.divergent.value)

    # Sub-KG : facts connus du NPC (known_by_npc_ids LIKE %"npc_id"%)
    personal = kg_store.known_to(npc_id, year=year)
    # Limite + sort
    personal = sorted(
        personal,
        key=lambda f: (f.confidence or 0.0, f.valid_from_year or 0),
        reverse=True,
    )[:max_facts]

    if not personal:
        return ""

    lines: list[str] = []
    for fact in personal:
        verb = _RELATION_VERBS_FR.get(fact.relation, fact.relation)
        if fact.object:
            lines.append(f"  {fact.subject} {verb} {fact.object}")
        else:
            lines.append(f"  {fact.subject} {verb}")
    return "Faits connus :\n" + "\n".join(lines)


def build_relations_summary_for_npc(
    *,
    social_network: SocialNetwork | None,
    npc_id: str,
    present_npc_ids: Iterable[str] = (),
    max_links: int = 8,
) -> str:
    """Compose une summary FR des relations sociales de `npc_id`.

    Priorise les NPCs presents dans la scene (`present_npc_ids`) puis
    complete par les autres liens forts.
    """
    if social_network is None:
        return ""
    links = social_network.neighbors(npc_id)
    if not links:
        return ""

    present_set = set(present_npc_ids)
    # Tri : NPCs presents d'abord, puis par strength descendant
    def _key(link):
        other = link.other(npc_id)
        in_scene = other in present_set
        return (not in_scene, -abs(link.strength))

    sorted_links = sorted(links, key=_key)[:max_links]

    lines: list[str] = []
    for link in sorted_links:
        other = link.other(npc_id)
        sign = "+" if link.strength >= 0 else ""
        scene_tag = " [present]" if other in present_set else ""
        lines.append(
            f"  {link.link_type} avec {other}: "
            f"{sign}{link.strength:.2f}{scene_tag}"
        )
    return "Relations :\n" + "\n".join(lines)


def auto_fill_selection_context(
    ctx,
    *,
    kg_store: KnowledgeGraphStore | None = None,
    social_network: SocialNetwork | None = None,
):
    """Helper : enrichit un SelectionContext en remplissant world_summary et
    relations_summary depuis le KG + SocialNetwork. Retourne un nouveau
    SelectionContext frozen.
    """
    from shinobi.agents.selector import SelectionContext

    world = ctx.world_summary or build_world_summary_for_npc(
        kg_store=kg_store, npc_id=ctx.npc_id, year=ctx.year,
    )
    relations = ctx.relations_summary or build_relations_summary_for_npc(
        social_network=social_network, npc_id=ctx.npc_id,
        present_npc_ids=ctx.present_npc_ids,
    )
    return SelectionContext(
        npc_id=ctx.npc_id,
        year=ctx.year,
        location_id=ctx.location_id,
        present_npc_ids=ctx.present_npc_ids,
        personality=ctx.personality,
        top_memories=ctx.top_memories,
        active_plans_text=ctx.active_plans_text,
        world_summary=world,
        relations_summary=relations,
        extras=ctx.extras,
    )


__all__ = [
    "auto_fill_selection_context",
    "build_relations_summary_for_npc",
    "build_world_summary_for_npc",
]
