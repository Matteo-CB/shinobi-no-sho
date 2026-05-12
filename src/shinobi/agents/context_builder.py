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
from typing import Any

from shinobi.i18n import t
from shinobi.kg.schema import Canonicity
from shinobi.kg.social import SocialNetwork
from shinobi.kg.store import KnowledgeGraphStore

# Relations dont la verbalisation est gerée via i18n. Les ids de relation
# inconnus retombent sur l'id brut (cf `_relation_verb`).
_RELATION_KEYS: frozenset[str] = frozenset({
    "said_to", "attacked", "traveled_to", "declared_intention", "searched_for",
    "plotted_against", "current_village", "located_at", "alive", "rank",
    "involves", "has_kekkei_genkai", "is_jinchuriki_of", "kage",
})


def _relation_verb(relation: str) -> str:
    """Resout le verbe localise d'une relation KG (fallback sur l'id brut)."""
    if relation in _RELATION_KEYS:
        return t(f"agents.context_builder.relation.{relation}")
    return relation


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
        verb = _relation_verb(fact.relation)
        if fact.object:
            lines.append(f"  {fact.subject} {verb} {fact.object}")
        else:
            lines.append(f"  {fact.subject} {verb}")
    return t("agents.context_builder.world_summary.header") + "\n" + "\n".join(lines)


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
            t(
                "agents.context_builder.relations.entry",
                link_type=link.link_type,
                other=other,
                sign=sign,
                strength=f"{link.strength:.2f}",
                scene_tag=scene_tag,
            )
        )
    return t("agents.context_builder.relations.header") + "\n" + "\n".join(lines)


def build_fallback_motivations_from_canon(
    *,
    canon_character: Any,
    npc_id: str,
) -> str:
    """Phase H 9.2 fallback : derive un profil minimal depuis canon.Character
    pour les NPCs sans profil 9.2 enrichi.

    Couverture canon partielle : 50 chars sur 1360 ont un profil 9.2 enrichi.
    Pour les 1310 autres (notamment 38/52 secondaires roster), on derive un
    profil heuristique a partir du clan + village_of_origin afin que :
    1. Le LLM selector ait quand-meme un block [MOTIVATIONS PROFONDES]
       qui le ramene au contexte canon (clan/village allegiances).
    2. Pas de prompt-vide qui force le LLM a inventer des motivations
       hors-canon.
    3. Distinguable du profil 9.2 enrichi via le marqueur "[fallback canon]"
       afin que la qualite soit visible.

    Defensive : si canon_character None ou sans clan/village utiles,
    retourne "".
    """
    if canon_character is None:
        return ""
    clan = getattr(canon_character, "clan", None)
    village = getattr(canon_character, "village_of_origin", None)
    if not clan and not village:
        return ""
    lines: list[str] = [t("agents.context_builder.fallback.header")]
    if clan:
        lines.append(t("agents.context_builder.fallback.drive_clan", clan=clan))
    if village:
        lines.append(t("agents.context_builder.fallback.drive_village", village=village))
    if clan:
        lines.append(t("agents.context_builder.fallback.never_header"))
        lines.append(t("agents.context_builder.fallback.never_betray_clan", clan=clan))
        if village:
            lines.append(
                t("agents.context_builder.fallback.never_destroy_village", village=village)
            )
    return "\n".join(lines)


def build_deep_motivations_text(
    *,
    deep_motivations_dataset: dict[str, Any] | None,
    npc_id: str,
    max_red_lines: int = 3,
    max_secrets: int = 2,
    canon_character: Any = None,
) -> str:
    """Phase H wiring 9.2 : compose un block FR depuis canon.deep_motivations.

    Retourne string vide si :
    - dataset None ou empty (Phase H 9.2 pas chargee).
    - npc_id absent du dataset (couvert pour 50 chars top, pas tous).

    Format intentionnellement compact (~400-600 chars) pour ne pas exploser
    le user prompt. Seuls les champs vraiment actionnables pour le selector
    sont inclus :
    - primary motivation (drive principal)
    - 1-2 red_lines (ce que le perso ne fera JAMAIS)
    - secret_ambition #1 (ce qu'il vise en cachette)
    - deepest_fear (ce qu'il evite a tout prix)
    - self_image (comment il se voit -> influence le ton)

    Les `what_others_dont_know` sont OMIS volontairement : c'est meta-info
    auteur, pas info dont le PNJ "se sert" pour decider.
    """
    if not deep_motivations_dataset or not isinstance(
        deep_motivations_dataset, dict,
    ):
        # Phase H 9.2 fallback : pas de dataset 9.2 charge -> tente le
        # fallback canon si fourni. Sans canon_character, retourne "".
        return build_fallback_motivations_from_canon(
            canon_character=canon_character, npc_id=npc_id,
        )
    profile = deep_motivations_dataset.get(npc_id)
    if not isinstance(profile, dict):
        # Phase H 9.2 fallback : npc_id absent du dataset (cas frequent :
        # 1310/1360 chars n'ont pas de profil 9.2). Fallback canon-derive
        # plutot que prompt vide.
        return build_fallback_motivations_from_canon(
            canon_character=canon_character, npc_id=npc_id,
        )

    lines: list[str] = []
    motivations = profile.get("deep_motivations")
    if isinstance(motivations, dict):
        primary = motivations.get("primary")
        if isinstance(primary, str) and primary:
            lines.append(t("agents.context_builder.deep.drive_primary", value=primary))
        secondary = motivations.get("secondary")
        if isinstance(secondary, str) and secondary:
            lines.append(t("agents.context_builder.deep.drive_secondary", value=secondary))

    red_lines = profile.get("moral_red_lines")
    if isinstance(red_lines, list) and red_lines:
        lines.append(t("agents.context_builder.fallback.never_header"))
        for rl in red_lines[:max_red_lines]:
            if isinstance(rl, str) and rl:
                lines.append(f"  - {rl}")

    secrets = profile.get("secret_ambitions")
    if isinstance(secrets, list) and secrets:
        for s in secrets[:max_secrets]:
            if isinstance(s, str) and s:
                lines.append(t("agents.context_builder.deep.secret_ambition", value=s))
                break  # un seul secret pour rester compact

    fear = profile.get("deepest_fear")
    if isinstance(fear, str) and fear:
        # Cap a 200 chars pour eviter prompts bloated.
        f_short = fear[:200] + ("..." if len(fear) > 200 else "")
        lines.append(t("agents.context_builder.deep.deepest_fear", value=f_short))

    self_image = profile.get("self_image")
    if isinstance(self_image, str) and self_image:
        si_short = self_image[:200] + ("..." if len(self_image) > 200 else "")
        lines.append(t("agents.context_builder.deep.self_image", value=si_short))

    if not lines:
        return ""
    return "\n".join(lines)


def auto_fill_selection_context(
    ctx,
    *,
    kg_store: KnowledgeGraphStore | None = None,
    social_network: SocialNetwork | None = None,
    deep_motivations_dataset: dict[str, Any] | None = None,
    canon_character: Any = None,
):
    """Helper : enrichit un SelectionContext en remplissant world_summary,
    relations_summary, et deep_motivations_text depuis les sources fournies.

    Phase H wiring 9.2 : passer `deep_motivations_dataset=canon.deep_motivations`
    pour activer le block [MOTIVATIONS PROFONDES] dans le user prompt. Si
    None ou empty, le ctx revient identique sur ce champ (vide).

    Phase H 9.2 fallback : `canon_character` permet de deriver un profil
    minimal pour les NPCs sans entry 9.2 (~96% des chars canon).
    """
    from shinobi.agents.selector import SelectionContext

    world = ctx.world_summary or build_world_summary_for_npc(
        kg_store=kg_store, npc_id=ctx.npc_id, year=ctx.year,
    )
    relations = ctx.relations_summary or build_relations_summary_for_npc(
        social_network=social_network, npc_id=ctx.npc_id,
        present_npc_ids=ctx.present_npc_ids,
    )
    motivations = ctx.deep_motivations_text or build_deep_motivations_text(
        deep_motivations_dataset=deep_motivations_dataset,
        npc_id=ctx.npc_id,
        canon_character=canon_character,
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
        deep_motivations_text=motivations,
        # Phase G+E wiring : director_nudge_text n'est pas auto-fill par ce
        # helper (le caller doit l'avoir set en amont depuis Director.tick).
        director_nudge_text=ctx.director_nudge_text,
        extras=ctx.extras,
    )


def build_faction_descriptions_block(
    *,
    political_forces: dict[str, Any] | None,
    location_id: str | None,
    present_npc_ids: list[str] | tuple[str, ...] = (),
    max_chars: int = 200,
    max_factions: int = 3,
) -> str:
    """Phase H 9.3 wiring narrator : compose un block de descriptions des
    factions politiques pertinentes a la scene.

    Une faction est pertinente si :
    1. Son id == location_id (joueur dans le village).
    2. Au moins 1 NPC present est dans ses members.

    Returns "" si aucune faction pertinente, dataset vide, ou tous skip.
    Format : `<faction_name> : <description_fr[:max_chars]>` cap N factions.
    """
    if not political_forces or not isinstance(political_forces, dict):
        return ""
    factions = political_forces.get("factions")
    if not isinstance(factions, list):
        return ""
    present_set = set(present_npc_ids or ())

    relevant: list[dict] = []
    for fac in factions:
        if not isinstance(fac, dict):
            continue
        fid = fac.get("id")
        if not isinstance(fid, str) or not fid:
            continue
        members = set(fac.get("members") or [])
        # Pertinent si location matche OU 1 member present
        if fid == location_id or (members & present_set):
            relevant.append(fac)
            if len(relevant) >= max_factions:
                break

    if not relevant:
        return ""
    lines: list[str] = []
    for fac in relevant:
        name = fac.get("name_fr") or fac.get("id")
        desc = fac.get("description_fr")
        if not isinstance(desc, str) or not desc:
            continue
        short = desc[:max_chars] + ("..." if len(desc) > max_chars else "")
        lines.append(f"  - {name} : {short}")
    return "\n".join(lines)


def build_present_npcs_motivations_block(
    *,
    deep_motivations_dataset: dict[str, Any] | None,
    present_npc_ids: list[str] | tuple[str, ...],
    max_npcs: int = 5,
    max_chars_per_npc: int = 150,
) -> str:
    """Phase H 9.2 wiring narrator : compose un block compact des drives
    psycho des NPCs presents dans la scene.

    Pour chaque NPC dans present_npc_ids qui a un profil 9.2 enrichi :
      <npc_id> : drive=<primary>, ne_jamais=<red_lines[:1]>

    Cap N NPCs max (LLM prompt budget). Skip silencieusement les NPCs
    sans profil 9.2 (~96% du canon, mais top 14/15 et important secondaires
    sont profiles).

    Returns "" si dataset absent ou aucun NPC profile dans la scene.
    """
    if not deep_motivations_dataset or not isinstance(
        deep_motivations_dataset, dict,
    ):
        return ""
    if not present_npc_ids:
        return ""
    lines: list[str] = []
    for npc_id in list(present_npc_ids)[:max_npcs]:
        profile = deep_motivations_dataset.get(npc_id)
        if not isinstance(profile, dict):
            continue
        drive = ""
        motiv = profile.get("deep_motivations")
        if isinstance(motiv, dict):
            primary = motiv.get("primary")
            if isinstance(primary, str) and primary:
                drive = primary
        red_line = ""
        red_lines = profile.get("moral_red_lines")
        if isinstance(red_lines, list) and red_lines:
            for rl in red_lines:
                if isinstance(rl, str) and rl:
                    red_line = rl
                    break
        if not drive and not red_line:
            continue
        line = f"  - {npc_id} :"
        if drive:
            line += f" drive={drive[:60]}"
        if red_line:
            line += f", ne_jamais={red_line[:60]}"
        if len(line) > max_chars_per_npc:
            line = line[:max_chars_per_npc - 3] + "..."
        lines.append(line)
    return "\n".join(lines)


__all__ = [
    "auto_fill_selection_context",
    "build_deep_motivations_text",
    "build_faction_descriptions_block",
    "build_present_npcs_motivations_block",
    "build_relations_summary_for_npc",
    "build_world_summary_for_npc",
]
