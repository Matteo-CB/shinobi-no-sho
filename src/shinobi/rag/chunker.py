"""Strategies de chunking par type d'entite canonique.

Chaque chunk porte des metadonnees riches qui permettent un filtering precis
au moment du retrieval (par village, par ere, par character_id, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shinobi.canon.models import (
    CanonBundle,
    Character,
    Clan,
    TimelineEvent,
    Village,
    VoiceProfile,
)
from shinobi.canon.models import Technique as TechniqueModel
from shinobi.types import ChunkType


@dataclass(frozen=True)
class Chunk:
    """Chunk indexable dans le RAG."""

    id: str
    text: str
    type: ChunkType
    source_id: str
    canonicity: str
    metadata: dict[str, Any] = field(default_factory=dict)


def chunk_character(char: Character) -> list[Chunk]:
    """Un chunk principal par personnage avec metadonnees indexables."""
    parts = [
        f"# {char.name_romaji}",
    ]
    if char.name_kanji:
        parts.append(f"Nom kanji : {char.name_kanji}")
    if char.aliases:
        parts.append("Alias : " + ", ".join(char.aliases))
    if char.gender:
        parts.append(f"Genre : {char.gender.value}")
    if char.birth_year is not None:
        parts.append(f"Annee de naissance : {char.birth_year}")
    if char.death_year is not None:
        parts.append(f"Annee de mort : {char.death_year}")
    parts.append(f"Village d'origine : {char.village_of_origin}")
    if char.clan:
        parts.append(f"Clan : {char.clan}")
    if char.kekkei_genkai:
        parts.append("Kekkei genkai : " + ", ".join(char.kekkei_genkai))
    if char.kekkei_mora:
        parts.append("Kekkei mora : " + ", ".join(char.kekkei_mora))
    if char.tailed_beast:
        parts.append(f"Bijuu : {char.tailed_beast}")
    if char.natures:
        parts.append("Natures : " + ", ".join(char.natures))
    if char.personality_fr:
        parts.append(f"Personnalite : {char.personality_fr}")
    if char.teachable_techniques:
        parts.append("Techniques enseignables : " + ", ".join(char.teachable_techniques))
    if char.teaching_conditions_fr:
        parts.append(f"Conditions d'enseignement : {char.teaching_conditions_fr}")
    if char.knowledge_domains:
        parts.append("Domaines de connaissance : " + ", ".join(char.knowledge_domains))

    metadata = {
        "character_id": char.id,
        "village": char.village_of_origin,
        "clan": char.clan or "",
        "canonicity": str(char.canonicity),
        "alive_until": char.death_year if char.death_year is not None else 9999,
        "born_year": char.birth_year if char.birth_year is not None else -9999,
    }

    return [
        Chunk(
            id=f"character:{char.id}",
            text="\n".join(parts),
            type=ChunkType.character,
            source_id=char.id,
            canonicity=str(char.canonicity),
            metadata=metadata,
        )
    ]


def chunk_technique(tech: TechniqueModel) -> list[Chunk]:
    """Un chunk par technique."""
    parts = [
        f"# {tech.name_romaji} ({tech.name_fr})",
        f"Categorie : {tech.category.value}, rang : {tech.rank.value}",
        f"Description : {tech.description_fr}",
    ]
    if tech.natures:
        parts.append("Natures : " + ", ".join(tech.natures))
    if tech.canonical_users:
        parts.append("Utilisateurs canoniques : " + ", ".join(tech.canonical_users))
    if tech.creator_id:
        parts.append(f"Createur : {tech.creator_id}")
    pre = tech.prerequisites
    pre_parts: list[str] = []
    if pre.required_techniques:
        pre_parts.append(f"techniques requises : {', '.join(pre.required_techniques)}")
    if pre.required_natures:
        pre_parts.append(f"natures requises : {', '.join(pre.required_natures)}")
    if pre.kekkei_genkai_restriction:
        pre_parts.append(f"kekkei genkai requis : {pre.kekkei_genkai_restriction}")
    if pre.clan_restriction:
        pre_parts.append(f"clan requis : {pre.clan_restriction}")
    if pre.notes_fr:
        pre_parts.append(pre.notes_fr)
    if pre_parts:
        parts.append("Prerequis : " + " ; ".join(pre_parts))
    if tech.counters:
        parts.append("Contre-mesures : " + ", ".join(tech.counters))

    metadata = {
        "technique_id": tech.id,
        "category": tech.category.value,
        "rank": tech.rank.value,
        "natures": ",".join(tech.natures),
        "canonicity": str(tech.canonicity),
    }
    return [
        Chunk(
            id=f"technique:{tech.id}",
            text="\n".join(parts),
            type=ChunkType.technique,
            source_id=tech.id,
            canonicity=str(tech.canonicity),
            metadata=metadata,
        )
    ]


def chunk_clan(clan: Clan) -> list[Chunk]:
    """Un chunk par clan."""
    parts = [f"# Clan {clan.name_romaji}"]
    if clan.village_of_origin:
        parts.append(f"Village d'origine : {clan.village_of_origin}")
    if clan.history_summary_fr:
        parts.append(f"Histoire : {clan.history_summary_fr}")
    if clan.key_kekkei_genkai:
        parts.append("Kekkei genkai cles : " + ", ".join(clan.key_kekkei_genkai))
    if clan.key_natures:
        parts.append("Natures cles : " + ", ".join(clan.key_natures))
    if clan.exclusive_techniques:
        parts.append("Techniques exclusives : " + ", ".join(clan.exclusive_techniques))
    if clan.social_structure_fr:
        parts.append(f"Structure sociale : {clan.social_structure_fr}")

    metadata = {
        "clan_id": clan.id,
        "village": clan.village_of_origin or "",
        "canonicity": str(clan.canonicity),
    }
    return [
        Chunk(
            id=f"clan:{clan.id}",
            text="\n".join(parts),
            type=ChunkType.clan,
            source_id=clan.id,
            canonicity=str(clan.canonicity),
            metadata=metadata,
        )
    ]


def chunk_village(village: Village) -> list[Chunk]:
    """Un chunk principal par village."""
    parts = [f"# {village.name_romaji} ({village.name_fr})"]
    parts.append(f"Pays : {village.country_name_fr} ({village.country})")
    if village.geography_fr:
        parts.append(f"Geographie : {village.geography_fr}")
    if village.main_clans:
        parts.append("Clans principaux : " + ", ".join(village.main_clans))
    if village.specialties:
        parts.append("Specialites : " + ", ".join(village.specialties))
    if village.kage_lineage:
        kages = ", ".join(
            f"{e.character_id} (an {e.from_year} a {e.to_year if e.to_year else 'present'})"
            for e in sorted(village.kage_lineage, key=lambda k: k.from_year)
        )
        parts.append(f"Lignee des Kage : {kages}")

    metadata = {
        "village_id": village.id,
        "country": village.country,
        "canonicity": str(village.canonicity),
    }
    return [
        Chunk(
            id=f"village:{village.id}",
            text="\n".join(parts),
            type=ChunkType.village,
            source_id=village.id,
            canonicity=str(village.canonicity),
            metadata=metadata,
        )
    ]


def chunk_event(event: TimelineEvent) -> list[Chunk]:
    """Un chunk par evenement de timeline."""
    parts = [f"# {event.name_fr} (an {event.year})"]
    if event.location:
        parts.append(f"Lieu : {event.location}")
    if event.involved_characters:
        parts.append("Personnages impliques : " + ", ".join(event.involved_characters))
    parts.append(f"Resume : {event.narrative_summary_fr}")

    metadata = {
        "event_id": event.id,
        "year": event.year,
        "location": event.location or "",
        "canonicity": str(event.canonicity),
    }
    return [
        Chunk(
            id=f"event:{event.id}",
            text="\n".join(parts),
            type=ChunkType.event,
            source_id=event.id,
            canonicity=str(event.canonicity),
            metadata=metadata,
        )
    ]


def chunk_voice_profile(voice: VoiceProfile) -> list[Chunk]:
    """Voix d'un personnage : un chunk par sample line."""
    chunks: list[Chunk] = []
    base_meta = {
        "character_id": voice.character_id,
        "voice_profile_id": voice.id,
        "canonicity": "manga",
    }
    if voice.sample_lines:
        for i, sample in enumerate(voice.sample_lines):
            chunks.append(
                Chunk(
                    id=f"dialogue:{voice.id}:{i}",
                    text=f"{voice.character_id}: {sample}",
                    type=ChunkType.dialogue,
                    source_id=voice.character_id,
                    canonicity="manga",
                    metadata=base_meta,
                )
            )
    intro = "\n".join(
        [
            f"# Voix de {voice.character_id}",
            f"Registre : {voice.register_fr}",
            "Tics verbaux : " + ", ".join(voice.verbal_tics),
            "Themes lexicaux : " + ", ".join(voice.vocabulary_themes),
            "Patterns syntaxiques : " + ", ".join(voice.syntactic_patterns),
        ]
    )
    chunks.append(
        Chunk(
            id=f"dialogue:{voice.id}:profile",
            text=intro,
            type=ChunkType.dialogue,
            source_id=voice.character_id,
            canonicity="manga",
            metadata=base_meta,
        )
    )
    return chunks


def chunk_all(bundle: CanonBundle) -> list[Chunk]:
    """Genere tous les chunks pour un bundle complet."""
    out: list[Chunk] = []
    for char in bundle.characters.values():
        out.extend(chunk_character(char))
    for tech in bundle.techniques.values():
        out.extend(chunk_technique(tech))
    for clan in bundle.clans.values():
        out.extend(chunk_clan(clan))
    for village in bundle.villages.values():
        out.extend(chunk_village(village))
    for event in bundle.timeline_events.values():
        out.extend(chunk_event(event))
    for voice in bundle.voice_profiles.values():
        out.extend(chunk_voice_profile(voice))
    return out
