"""Integration Mission -> KG facts.

Pour chaque Mission, on insere N facts dans le Knowledge Graph dynamique.
Cela permet :
- aux invariants Phase C de detecter des tensions emergentes ('lone_survivor'
  via Mission failure type 'rescue', etc.)
- a la Phase D (drift de personnalite) de consommer les events de mission
  ('trauma_event' apres failure ou casualties)
- au RAG de retrouver les missions par retrieval semantique

Les facts crees pour la mission `mission_wave_country_zabuza` (year 12) :

    (mission_wave_country_zabuza, type, mission)
    (mission_wave_country_zabuza, name_fr, "Mission Pays des Vagues...")
    (mission_wave_country_zabuza, rank, "C")
    (mission_wave_country_zabuza, mission_type, "escort")
    (mission_wave_country_zabuza, outcome, "success")
    (mission_wave_country_zabuza, occurs_in_year, 12) [valid_from_year=12]
    (mission_wave_country_zabuza, occurs_at, wave_country) [entity]
    (mission_wave_country_zabuza, assigned_by, sarutobi_hiruzen) [entity]
    (mission_wave_country_zabuza, target, tazuna) [entity]
    (mission_wave_country_zabuza, canonical_arc, "wave_country")
    (mission_wave_country_zabuza, involves, hatake_kakashi) [entity, valid_from=12]
    (mission_wave_country_zabuza, involves, uzumaki_naruto) [entity, valid_from=12]
    ... (un fact par participant)
    (uzumaki_naruto, participated_in_mission, mission_wave_country_zabuza) [entity, valid_from=12]
    ...
    (mission_wave_country_zabuza, consequence, "Mort de Zabuza et Haku")
    (mission_wave_country_zabuza, source, "narutopedia:Land_of_Waves_Arc")

Idempotent : si clear_first=True, on supprime les facts existants
(source = 'mission:<id>') avant l'import.
"""

from __future__ import annotations

from collections.abc import Iterable

from shinobi.kg.schema import (
    Canonicity, Fact, ObjectType, map_source_canonicity,
)
from shinobi.kg.store import KnowledgeGraphStore
from shinobi.logging_setup import get_logger
from shinobi.missions.types import Mission

logger = get_logger(__name__)


def _facts_from_mission(mission: Mission) -> list[Fact]:
    """Convertit une Mission en liste de Facts pour le KG.

    Spec Phase A round 41 : la canonicity de la mission (manga, anime_canon,
    boruto, filler, ...) est mappee vers Canonicity runtime (canon_strict /
    canon_modified) via schema.map_source_canonicity. Tous les facts
    derives de la mission heritent de ce Canonicity, coherent avec
    l'import canon standard via _import_list.
    """
    mid = mission.id
    source = f"mission:{mid}"
    runtime_canon = map_source_canonicity(mission.canonicity)
    facts: list[Fact] = []

    # Type entity
    facts.append(Fact(
        subject=mid, relation="type", object="mission",
        object_type=ObjectType.value,
        source=source, canonicity=runtime_canon,
    ))

    # Metadata scalaires
    # Note round 28 : `sourced_from` est le nom unifie cross-pipeline pour
    # la canonicity source brute (alias historique de mission : canonicity_source).
    for relation, value in (
        ("name_fr", mission.name_fr),
        ("name_romaji", mission.name_romaji),
        ("rank", mission.rank.value),
        ("mission_type", mission.type.value),
        ("outcome", mission.outcome.value),
        ("canonical_arc", mission.canonical_arc),
        ("starting_village", mission.starting_village),
        ("summary_fr", mission.summary_fr),
        ("sourced_from", mission.canonicity),
    ):
        if value is None:
            continue
        facts.append(Fact(
            subject=mid, relation=relation, object=str(value),
            object_type=ObjectType.value,
            source=source, canonicity=runtime_canon,
        ))

    # Date in-game
    facts.append(Fact(
        subject=mid, relation="occurs_in_year",
        object=str(mission.year),
        object_type=ObjectType.value,
        valid_from_year=mission.year,
        source=source, canonicity=runtime_canon,
    ))
    if mission.month is not None:
        facts.append(Fact(
            subject=mid, relation="occurs_in_month",
            object=str(mission.month),
            object_type=ObjectType.value,
            valid_from_year=mission.year,
            source=source, canonicity=runtime_canon,
        ))
    if mission.duration_days is not None:
        facts.append(Fact(
            subject=mid, relation="duration_days",
            object=str(mission.duration_days),
            object_type=ObjectType.value,
            source=source, canonicity=runtime_canon,
        ))

    # Liens entites
    if mission.location_id:
        facts.append(Fact(
            subject=mid, relation="occurs_at", object=mission.location_id,
            object_type=ObjectType.entity,
            valid_from_year=mission.year,
            source=source, canonicity=runtime_canon,
        ))
    if mission.assigning_authority:
        facts.append(Fact(
            subject=mid, relation="assigned_by",
            object=mission.assigning_authority,
            object_type=ObjectType.entity,
            valid_from_year=mission.year,
            source=source, canonicity=runtime_canon,
        ))
    if mission.target_subject:
        facts.append(Fact(
            subject=mid, relation="target",
            object=mission.target_subject,
            object_type=ObjectType.entity,
            valid_from_year=mission.year,
            source=source, canonicity=runtime_canon,
        ))

    # Participants : double-direction (mission -> npc et npc -> mission)
    for p in mission.participants:
        facts.append(Fact(
            subject=mid, relation="involves", object=p.character_id,
            object_type=ObjectType.entity,
            valid_from_year=mission.year,
            source=source, canonicity=runtime_canon,
        ))
        facts.append(Fact(
            subject=p.character_id, relation="participated_in_mission",
            object=mid,
            object_type=ObjectType.entity,
            valid_from_year=mission.year,
            source=source, canonicity=runtime_canon,
        ))
        if p.role and p.role != "operative":
            facts.append(Fact(
                subject=p.character_id, relation=f"mission_role_{mid}",
                object=p.role,
                object_type=ObjectType.value,
                valid_from_year=mission.year,
                source=source, canonicity=runtime_canon,
            ))
        # Spec Phase A round 51 : preserver les notes canoniques sur le
        # role du participant (forward-compat, 0/26 missions actuelles).
        if p.notes:
            facts.append(Fact(
                subject=p.character_id, relation=f"mission_notes_{mid}",
                object=p.notes,
                object_type=ObjectType.value,
                valid_from_year=mission.year,
                source=source, canonicity=runtime_canon,
            ))

    # Objectives + consequences (en values libres)
    for i, obj in enumerate(mission.objectives):
        facts.append(Fact(
            subject=mid, relation=f"objective_{i}", object=obj,
            object_type=ObjectType.value,
            source=source, canonicity=runtime_canon,
        ))
    for i, cons in enumerate(mission.consequences):
        facts.append(Fact(
            subject=mid, relation=f"consequence_{i}", object=cons,
            object_type=ObjectType.value,
            valid_from_year=mission.year,
            source=source, canonicity=runtime_canon,
        ))

    # Liens narratifs croises
    for ev_id in mission.related_event_ids:
        facts.append(Fact(
            subject=mid, relation="related_event", object=ev_id,
            object_type=ObjectType.entity,
            source=source, canonicity=runtime_canon,
        ))
    for other_mid in mission.related_mission_ids:
        facts.append(Fact(
            subject=mid, relation="related_mission", object=other_mid,
            object_type=ObjectType.entity,
            source=source, canonicity=runtime_canon,
        ))

    # Spec Phase A : preserver les refs canon (narutopedia/databook).
    # Module separation : has_source pointe vers le narutopedia, mais le
    # `source` du Fact reste 'mission:<id>' (provenance moteur).
    for src_ref in mission.sources or []:
        if not isinstance(src_ref, str) or not src_ref:
            continue
        facts.append(Fact(
            subject=mid, relation="has_source", object=src_ref,
            object_type=ObjectType.value,
            source=source, canonicity=runtime_canon,
        ))

    return facts


def import_missions_to_kg(
    store: KnowledgeGraphStore,
    missions: Iterable[Mission],
    *,
    clear_first: bool = True,
) -> dict[str, int]:
    """Importe une iterable de Missions dans le KG. Retourne stats.

    Si clear_first=True, supprime d'abord tous les facts dont
    source = 'mission:<id>' pour les missions a importer (idempotent).
    """
    missions_list = list(missions)
    if clear_first:
        for m in missions_list:
            existing = store.get_facts(source_prefix=f"mission:{m.id}")
            for f in existing:
                if f.id is not None:
                    store.delete_fact(f.id)

    facts_total: list[Fact] = []
    for mission in missions_list:
        facts_total.extend(_facts_from_mission(mission))

    if facts_total:
        store.add_facts_batch(facts_total)

    stats = {
        "missions_imported": len(missions_list),
        "facts_inserted": len(facts_total),
    }
    logger.info("kg_missions_imported", **stats)
    return stats


__all__ = ["import_missions_to_kg"]
