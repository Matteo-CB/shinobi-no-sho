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
    HidenTechnique,
    KekkeiGenkai,
    Location,
    Organization,
    TailedBeast,
    TimelineEvent,
    Village,
    VoiceProfile,
    WeaponTool,
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
    """Genere un chunk principal + N chunks de sections wiki pour un personnage.

    Permet au RAG de retrouver Background/Abilities/Personality/Trivia/Part I/II
    de chaque NPC, pas juste l'intro personality_fr (1 KB) qui etait le seul
    contenu indexe avant.
    """
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
        parts.append(f"Personnalite (intro) : {char.personality_fr}")
    if char.teachable_techniques:
        parts.append("Techniques enseignables : " + ", ".join(char.teachable_techniques))
    if char.teaching_conditions_fr:
        parts.append(f"Conditions d'enseignement : {char.teaching_conditions_fr}")
    if char.knowledge_domains:
        parts.append("Domaines de connaissance : " + ", ".join(char.knowledge_domains))

    base_metadata = {
        "character_id": char.id,
        "village": char.village_of_origin,
        "clan": char.clan or "",
        "canonicity": str(char.canonicity),
        "alive_until": char.death_year if char.death_year is not None else 9999,
        "born_year": char.birth_year if char.birth_year is not None else -9999,
    }

    chunks: list[Chunk] = [
        Chunk(
            id=f"character:{char.id}",
            text="\n".join(parts),
            type=ChunkType.character,
            source_id=char.id,
            canonicity=str(char.canonicity),
            metadata={**base_metadata, "section": "header"},
        )
    ]

    # Un chunk par section wiki (Background, Abilities, Personality, Part I, etc.)
    chunks.extend(_chunk_wiki_sections(
        char.wiki_sections,
        chunk_type=ChunkType.character,
        source_id=char.id,
        canonicity=str(char.canonicity),
        base_metadata=base_metadata,
        title_prefix=f"{char.name_romaji}",
    ))
    return chunks


def _chunk_wiki_sections(
    wiki_sections: dict[str, str],
    *,
    chunk_type: ChunkType,
    source_id: str,
    canonicity: str,
    base_metadata: dict,
    title_prefix: str,
    max_chars_per_chunk: int = 2000,
) -> list[Chunk]:
    """Decoupe wiki_sections en chunks RAG (1 par section, split si > max_chars).

    Permet au RAG search de retrouver toute section wiki d'une entite par
    requete semantique (ex: 'comment Naruto a appris le rasengan' -> chunk
    Abilities[Senjutsu] ou Part I[Search for Tsunade arc]).
    """
    out: list[Chunk] = []
    for section_title, section_text in wiki_sections.items():
        if not section_text or not section_text.strip():
            continue
        # Split si la section est trop longue (rare car deja tronquee a 4000 chars)
        text = section_text.strip()
        if len(text) <= max_chars_per_chunk:
            chunks_text = [text]
        else:
            # Decoupe par paragraphe en respectant max_chars
            chunks_text = []
            current: list[str] = []
            current_len = 0
            for line in text.split("\n"):
                if current_len + len(line) > max_chars_per_chunk and current:
                    chunks_text.append("\n".join(current))
                    current = [line]
                    current_len = len(line)
                else:
                    current.append(line)
                    current_len += len(line) + 1
            if current:
                chunks_text.append("\n".join(current))
        for i, ctext in enumerate(chunks_text):
            suffix = f":{i}" if len(chunks_text) > 1 else ""
            section_slug = section_title.lower().replace(" ", "_").replace(":", "")
            out.append(
                Chunk(
                    id=f"{chunk_type.value}:{source_id}:wiki:{section_slug}{suffix}",
                    text=f"# {title_prefix} - {section_title}\n\n{ctext}",
                    type=chunk_type,
                    source_id=source_id,
                    canonicity=canonicity,
                    metadata={**base_metadata, "section": section_slug},
                )
            )
    return out


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

    base_metadata = {
        "technique_id": tech.id,
        "category": tech.category.value,
        "rank": tech.rank.value,
        "natures": ",".join(tech.natures),
        "canonicity": str(tech.canonicity),
    }
    chunks: list[Chunk] = [
        Chunk(
            id=f"technique:{tech.id}",
            text="\n".join(parts),
            type=ChunkType.technique,
            source_id=tech.id,
            canonicity=str(tech.canonicity),
            metadata={**base_metadata, "section": "header"},
        )
    ]
    chunks.extend(_chunk_wiki_sections(
        tech.wiki_sections,
        chunk_type=ChunkType.technique,
        source_id=tech.id,
        canonicity=str(tech.canonicity),
        base_metadata=base_metadata,
        title_prefix=f"{tech.name_romaji}",
    ))
    return chunks


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

    base_metadata = {
        "clan_id": clan.id,
        "village": clan.village_of_origin or "",
        "canonicity": str(clan.canonicity),
    }
    chunks: list[Chunk] = [
        Chunk(
            id=f"clan:{clan.id}",
            text="\n".join(parts),
            type=ChunkType.clan,
            source_id=clan.id,
            canonicity=str(clan.canonicity),
            metadata={**base_metadata, "section": "header"},
        )
    ]
    chunks.extend(_chunk_wiki_sections(
        clan.wiki_sections,
        chunk_type=ChunkType.clan,
        source_id=clan.id,
        canonicity=str(clan.canonicity),
        base_metadata=base_metadata,
        title_prefix=f"Clan {clan.name_romaji}",
    ))
    return chunks


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

    base_metadata = {
        "village_id": village.id,
        "country": village.country,
        "canonicity": str(village.canonicity),
    }
    chunks: list[Chunk] = [
        Chunk(
            id=f"village:{village.id}",
            text="\n".join(parts),
            type=ChunkType.village,
            source_id=village.id,
            canonicity=str(village.canonicity),
            metadata={**base_metadata, "section": "header"},
        )
    ]
    chunks.extend(_chunk_wiki_sections(
        village.wiki_sections,
        chunk_type=ChunkType.village,
        source_id=village.id,
        canonicity=str(village.canonicity),
        base_metadata=base_metadata,
        title_prefix=f"{village.name_romaji}",
    ))
    return chunks


def chunk_event(event: TimelineEvent) -> list[Chunk]:
    """Un chunk par evenement de timeline."""
    parts = [f"# {event.name_fr} (an {event.year})"]
    if event.location:
        parts.append(f"Lieu : {event.location}")
    if event.involved_characters:
        parts.append("Personnages impliques : " + ", ".join(event.involved_characters))
    parts.append(f"Resume : {event.narrative_summary_fr}")

    base_metadata = {
        "event_id": event.id,
        "year": event.year,
        "location": event.location or "",
        "canonicity": str(event.canonicity),
    }
    chunks: list[Chunk] = [
        Chunk(
            id=f"event:{event.id}",
            text="\n".join(parts),
            type=ChunkType.event,
            source_id=event.id,
            canonicity=str(event.canonicity),
            metadata={**base_metadata, "section": "header"},
        )
    ]
    chunks.extend(_chunk_wiki_sections(
        event.wiki_sections,
        chunk_type=ChunkType.event,
        source_id=event.id,
        canonicity=str(event.canonicity),
        base_metadata=base_metadata,
        title_prefix=f"Event: {event.name_fr}",
    ))
    return chunks


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


def chunk_location(loc: Location) -> list[Chunk]:
    """Chunks pour un lieu : intro + sections wiki (geographie, evenements)."""
    parts = [f"# {loc.name_romaji} ({loc.name_fr})"]
    if loc.country:
        parts.append(f"Pays : {loc.country}")
    if loc.near_village:
        parts.append(f"Village proche : {loc.near_village}")
    if loc.geography_fr:
        parts.append(f"Geographie : {loc.geography_fr}")
    if loc.canonical_events:
        parts.append("Evenements canoniques : " + ", ".join(loc.canonical_events))
    base_metadata = {
        "location_id": loc.id,
        "country": loc.country or "",
        "near_village": loc.near_village or "",
        "canonicity": str(loc.canonicity),
    }
    chunks: list[Chunk] = [
        Chunk(
            id=f"location:{loc.id}",
            text="\n".join(parts),
            type=ChunkType.lore,
            source_id=loc.id,
            canonicity=str(loc.canonicity),
            metadata={**base_metadata, "section": "header"},
        )
    ]
    chunks.extend(_chunk_wiki_sections(
        loc.wiki_sections,
        chunk_type=ChunkType.lore,
        source_id=loc.id,
        canonicity=str(loc.canonicity),
        base_metadata=base_metadata,
        title_prefix=f"Lieu: {loc.name_romaji}",
    ))
    return chunks


def chunk_weapon(weapon: WeaponTool) -> list[Chunk]:
    """Chunks pour une arme/outil."""
    parts = [f"# {weapon.name_romaji} ({weapon.name_fr})"]
    parts.append(f"Type : {weapon.type}")
    if weapon.subcategory:
        parts.append(f"Sous-categorie : {weapon.subcategory}")
    if weapon.abilities_fr:
        parts.append(f"Capacites : {weapon.abilities_fr}")
    parts.append(f"Rarete : {weapon.rarity}")
    if weapon.wielders_canonical:
        parts.append("Utilisateurs canoniques : " + ", ".join(weapon.wielders_canonical))
    base_metadata = {
        "weapon_id": weapon.id,
        "type": weapon.type,
        "rarity": weapon.rarity,
        "canonicity": str(weapon.canonicity),
    }
    chunks: list[Chunk] = [
        Chunk(
            id=f"weapon:{weapon.id}",
            text="\n".join(parts),
            type=ChunkType.lore,
            source_id=weapon.id,
            canonicity=str(weapon.canonicity),
            metadata={**base_metadata, "section": "header"},
        )
    ]
    chunks.extend(_chunk_wiki_sections(
        weapon.wiki_sections,
        chunk_type=ChunkType.lore,
        source_id=weapon.id,
        canonicity=str(weapon.canonicity),
        base_metadata=base_metadata,
        title_prefix=f"Arme: {weapon.name_romaji}",
    ))
    return chunks


def chunk_kekkei(kekkei: KekkeiGenkai) -> list[Chunk]:
    """Chunks pour un kekkei genkai/mora."""
    parts = [f"# {kekkei.name_romaji} ({kekkei.category})"]
    parts.append(f"Type : {kekkei.type}")
    if kekkei.carrier_clans:
        parts.append("Clans porteurs : " + ", ".join(kekkei.carrier_clans))
    if kekkei.activation_conditions_fr:
        parts.append(f"Conditions d'activation : {kekkei.activation_conditions_fr}")
    if kekkei.weaknesses_fr:
        parts.append(f"Faiblesses : {kekkei.weaknesses_fr}")
    base_metadata = {
        "kekkei_id": kekkei.id,
        "category": kekkei.category,
        "kekkei_type": kekkei.type,
        "canonicity": str(kekkei.canonicity),
    }
    chunks: list[Chunk] = [
        Chunk(
            id=f"kekkei:{kekkei.id}",
            text="\n".join(parts),
            type=ChunkType.lore,
            source_id=kekkei.id,
            canonicity=str(kekkei.canonicity),
            metadata={**base_metadata, "section": "header"},
        )
    ]
    chunks.extend(_chunk_wiki_sections(
        kekkei.wiki_sections,
        chunk_type=ChunkType.lore,
        source_id=kekkei.id,
        canonicity=str(kekkei.canonicity),
        base_metadata=base_metadata,
        title_prefix=f"Kekkei {kekkei.category}: {kekkei.name_romaji}",
    ))
    return chunks


def chunk_organization(org: Organization) -> list[Chunk]:
    """Chunks pour une organisation (Akatsuki, Anbu, etc.)."""
    parts = [f"# {org.name_romaji} ({org.name_fr})"]
    if org.headquarters:
        parts.append("QG : " + ", ".join(org.headquarters))
    if org.founders:
        parts.append("Fondateurs : " + ", ".join(org.founders))
    if org.ideology_fr:
        parts.append(f"Ideologie : {org.ideology_fr}")
    base_metadata = {
        "organization_id": org.id,
        "canonicity": str(org.canonicity),
    }
    chunks: list[Chunk] = [
        Chunk(
            id=f"organization:{org.id}",
            text="\n".join(parts),
            type=ChunkType.lore,
            source_id=org.id,
            canonicity=str(org.canonicity),
            metadata={**base_metadata, "section": "header"},
        )
    ]
    chunks.extend(_chunk_wiki_sections(
        org.wiki_sections,
        chunk_type=ChunkType.lore,
        source_id=org.id,
        canonicity=str(org.canonicity),
        base_metadata=base_metadata,
        title_prefix=f"Organisation: {org.name_romaji}",
    ))
    return chunks


def chunk_tailed_beast(beast: TailedBeast) -> list[Chunk]:
    """Chunks pour un bijuu."""
    parts = [f"# {beast.name_romaji} (Bijuu, {beast.tails} queues)"]
    if beast.epithets:
        parts.append("Epithetes : " + ", ".join(beast.epithets))
    if beast.personality_fr:
        parts.append(f"Personnalite : {beast.personality_fr}")
    if beast.abilities_fr:
        parts.append(f"Pouvoirs : {beast.abilities_fr}")
    if beast.current_jinchuuriki_by_era:
        jins = ", ".join(
            f"{j.jinchuuriki} (an {j.from_year})" for j in beast.current_jinchuuriki_by_era
        )
        parts.append(f"Jinchuuriki successifs : {jins}")
    base_metadata = {
        "beast_id": beast.id,
        "tails": beast.tails,
        "canonicity": str(beast.canonicity),
    }
    chunks: list[Chunk] = [
        Chunk(
            id=f"tailed_beast:{beast.id}",
            text="\n".join(parts),
            type=ChunkType.lore,
            source_id=beast.id,
            canonicity=str(beast.canonicity),
            metadata={**base_metadata, "section": "header"},
        )
    ]
    chunks.extend(_chunk_wiki_sections(
        beast.wiki_sections,
        chunk_type=ChunkType.lore,
        source_id=beast.id,
        canonicity=str(beast.canonicity),
        base_metadata=base_metadata,
        title_prefix=f"Bijuu: {beast.name_romaji}",
    ))
    return chunks


def chunk_hiden(hiden: HidenTechnique) -> list[Chunk]:
    """Chunks pour une technique hiden (secrete de clan)."""
    parts = [f"# {hiden.name_romaji} ({hiden.name_fr})"]
    if hiden.owning_clan:
        parts.append(f"Clan possesseur : {hiden.owning_clan}")
    if hiden.owning_village:
        parts.append(f"Village : {hiden.owning_village}")
    parts.append(f"Description : {hiden.description_fr}")
    if hiden.shareable_outside_clan:
        parts.append("Partageable hors clan : oui")
    base_metadata = {
        "hiden_id": hiden.id,
        "owning_clan": hiden.owning_clan or "",
        "canonicity": str(hiden.canonicity),
    }
    chunks: list[Chunk] = [
        Chunk(
            id=f"hiden:{hiden.id}",
            text="\n".join(parts),
            type=ChunkType.lore,
            source_id=hiden.id,
            canonicity=str(hiden.canonicity),
            metadata={**base_metadata, "section": "header"},
        )
    ]
    chunks.extend(_chunk_wiki_sections(
        hiden.wiki_sections,
        chunk_type=ChunkType.lore,
        source_id=hiden.id,
        canonicity=str(hiden.canonicity),
        base_metadata=base_metadata,
        title_prefix=f"Hiden: {hiden.name_romaji}",
    ))
    return chunks


def chunk_all(bundle: CanonBundle) -> list[Chunk]:
    """Genere tous les chunks pour un bundle complet.

    Dedup par chunk.id (premiere occurrence gardee). Necessaire car
    kekkei_genkai.json et kekkei_mora.json peuvent partager des ids
    (ex. `tenseigan`), produisant deux chunks `kekkei:tenseigan` qui
    feraient planter ChromaDB upsert.
    """
    raw: list[Chunk] = []
    for char in bundle.characters.values():
        raw.extend(chunk_character(char))
    for tech in bundle.techniques.values():
        raw.extend(chunk_technique(tech))
    for clan in bundle.clans.values():
        raw.extend(chunk_clan(clan))
    for village in bundle.villages.values():
        raw.extend(chunk_village(village))
    for event in bundle.timeline_events.values():
        raw.extend(chunk_event(event))
    for voice in bundle.voice_profiles.values():
        raw.extend(chunk_voice_profile(voice))
    for loc in bundle.locations.values():
        raw.extend(chunk_location(loc))
    for weapon in bundle.weapons_tools.values():
        raw.extend(chunk_weapon(weapon))
    for kekkei in bundle.kekkei_genkai.values():
        raw.extend(chunk_kekkei(kekkei))
    for kekkei in bundle.kekkei_mora.values():
        raw.extend(chunk_kekkei(kekkei))
    for org in bundle.organizations.values():
        raw.extend(chunk_organization(org))
    for beast in bundle.tailed_beasts.values():
        raw.extend(chunk_tailed_beast(beast))
    for hiden in bundle.hiden.values():
        raw.extend(chunk_hiden(hiden))

    seen: set[str] = set()
    out: list[Chunk] = []
    for chunk in raw:
        if chunk.id in seen:
            continue
        seen.add(chunk.id)
        out.append(chunk)
    return out
