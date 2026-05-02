"""Genere des fact sheets canoniques precises pour les 1360 personnages canon.

Strategie : exploiter TOUS les champs canon disponibles pour chaque NPC :
- birth_year/death_year, age calcule a la date courante
- rank_progression complete (toutes les promotions avec dates)
- location_by_year complete (deplacements connus)
- current_village_by_era (changements d'allegeance)
- personality_fr (description canonique de personnalite)
- speech_patterns (verbal_tic, register, vocabulary_traits)
- knowledge_domains (domaines d'expertise)
- teachable_techniques (ce qu'il peut enseigner)
- key_relationships avec les autres canon NPCs (since_year)
- kekkei_genkai, kekkei_mora, tailed_beast, natures
- death_circumstances_fr

Ce module ne hardcode RIEN : tout vient des datasets canon scrapes.
Une whitelist editoriale optionnelle (psycho_notes.json) peut completer pour
les NPCs majeurs avec des nuances 'qu'aurait un fan' non capturees par le scraping.
"""

from __future__ import annotations

import json
import re

from shinobi.canon.models import CanonBundle, Character
from shinobi.config import settings
from shinobi.logging_setup import get_logger

logger = get_logger(__name__)


# Cache du fichier psycho_notes (charge une seule fois par process).
_PSYCHO_NOTES_CACHE: dict[str, list[dict]] | None = None


def _load_psycho_notes() -> dict[str, list[dict]]:
    """Charge data/canonical/psycho_notes.json (optionnel, complete les data canon).

    Format :
      { "uzumaki_naruto": [{"from_age": 0, "to_age": 11, "note": "..."}, ...] }
    """
    global _PSYCHO_NOTES_CACHE
    if _PSYCHO_NOTES_CACHE is not None:
        return _PSYCHO_NOTES_CACHE
    path = settings.canonical_data_dir / "psycho_notes.json"
    if not path.exists():
        _PSYCHO_NOTES_CACHE = {}
        return _PSYCHO_NOTES_CACHE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _PSYCHO_NOTES_CACHE = data.get("notes", {}) if isinstance(data, dict) else {}
        logger.info("psycho_notes_loaded", count=len(_PSYCHO_NOTES_CACHE))
    except Exception as exc:
        logger.warning("psycho_notes_load_failed", error=str(exc))
        _PSYCHO_NOTES_CACHE = {}
    return _PSYCHO_NOTES_CACHE


def reset_psycho_notes_cache() -> None:
    """Vide le cache (utile en tests)."""
    global _PSYCHO_NOTES_CACHE
    _PSYCHO_NOTES_CACHE = None


def fact_sheet_for(canon: CanonBundle, character_id: str, *, current_year: int) -> str | None:
    """Retourne un resume canonique de l'etat d'un NPC a current_year, ou None.

    Exploite TOUS les champs canon disponibles dans Character. Marche pour les
    1360 NPCs sans configuration manuelle.
    """
    char = canon.characters.get(character_id)
    if char is None:
        return None

    lines: list[str] = []
    name = char.name_fr or char.name_romaji
    aliases = ""
    if char.aliases:
        aliases = f" (alias: {', '.join(char.aliases[:3])})"
    lines.append(f"NPC: {character_id} ({name}){aliases}")

    # ========== STATUT VITAL ==========
    if char.birth_year is not None and current_year < char.birth_year:
        lines.append(
            f"  STATUT: PAS ENCORE NE (naissance prevue an {char.birth_year}). "
            "Ne peut PAS apparaitre dans la scene."
        )
        return "\n".join(lines)
    if char.death_year is not None and current_year > char.death_year:
        lines.append(f"  STATUT: MORT (en l'an {char.death_year}). Ne peut PAS apparaitre vivant.")
        if char.death_circumstances_fr:
            lines.append(f"  Circonstances: {char.death_circumstances_fr}")
        return "\n".join(lines)

    age = current_year - char.birth_year if char.birth_year is not None else None
    if age is not None:
        lines.append(f"  Age: {age} ans en l'an {current_year}")

    # ========== RANG (canonique > derive de l'age) ==========
    rank = _rank_at(char, current_year)
    if rank:
        # Mentionne aussi le rang precedent si transition recente
        prev = _previous_rank(char, current_year)
        if prev and prev != rank:
            lines.append(f"  Rang: {rank} (anterieurement {prev})")
        else:
            lines.append(f"  Rang: {rank}")
    elif age is not None:
        derived = _rank_from_age(age)
        if derived:
            lines.append(f"  Rang derive: {derived} (estime selon l'age, pas dans le canon)")

    # ========== LIEU + VILLAGE ==========
    location = _location_at(char, current_year)
    village = _village_at(char, current_year)
    if location and village and location != village:
        lines.append(f"  Lieu courant: {location} (rattachement: {village})")
    elif location:
        lines.append(f"  Lieu courant: {location}")
    elif village:
        lines.append(f"  Village: {village}")

    # ========== HERITAGE GENETIQUE ==========
    if char.clan:
        clan_str = char.clan
        if char.secondary_clan:
            clan_str += f" + {char.secondary_clan} (secondaire)"
        lines.append(f"  Clan: {clan_str}")
    if char.kekkei_genkai:
        lines.append(f"  Kekkei genkai: {', '.join(char.kekkei_genkai)}")
    if char.kekkei_mora:
        lines.append(f"  Kekkei mora: {', '.join(char.kekkei_mora)}")
    if char.tailed_beast:
        lines.append(f"  Jinchuuriki de: {char.tailed_beast}")
    if char.natures:
        lines.append(f"  Natures de chakra: {', '.join(char.natures)}")

    # ========== PERSONNALITE CANONIQUE (donnee scrapee) ==========
    if char.personality_fr:
        perso = _clean_wikitext(char.personality_fr)
        if perso:
            if len(perso) > 400:
                perso = perso[:397] + "..."
            lines.append(f"  Personnalite canon: {perso}")

    # ========== PATTERNS DE LANGAGE (donnee scrapee) ==========
    if char.speech_patterns:
        sp = char.speech_patterns
        sp_parts: list[str] = []
        if sp.verbal_tic:
            sp_parts.append(f"tic verbal '{sp.verbal_tic}'")
        if sp.register_label:
            sp_parts.append(f"registre '{sp.register_label}'")
        if sp.vocabulary_traits:
            sp_parts.append(f"vocabulaire: {', '.join(sp.vocabulary_traits[:3])}")
        if sp_parts:
            lines.append(f"  Parle: {' | '.join(sp_parts)}")

    # ========== EXPERTISE / DOMAINES ==========
    if char.knowledge_domains:
        lines.append(f"  Domaines d'expertise: {', '.join(char.knowledge_domains[:5])}")
    if char.teachable_techniques:
        techs = char.teachable_techniques[:5]
        more = len(char.teachable_techniques) - len(techs)
        suffix = f" (+{more} autres)" if more > 0 else ""
        lines.append(f"  Peut enseigner: {', '.join(techs)}{suffix}")
    if char.teaching_conditions_fr:
        cond = char.teaching_conditions_fr.strip()
        if len(cond) > 200:
            cond = cond[:197] + "..."
        lines.append(f"  Conditions d'enseignement: {cond}")

    # ========== TECHNIQUES CONNUES A CETTE DATE ==========
    techs_known = _techniques_known_at(char, current_year)
    if techs_known:
        sample = techs_known[:5]
        more = len(techs_known) - len(sample)
        suffix = f" (+{more} autres connues)" if more > 0 else ""
        lines.append(f"  Techniques maitrisees a cette date: {', '.join(sample)}{suffix}")

    # ========== STATS A CETTE DATE ==========
    stats_at = _stats_at(char, current_year)
    if stats_at:
        s = stats_at
        lines.append(
            f"  Stats canon a cette date: NIN {s.ninjutsu:.1f} TAI {s.taijutsu:.1f} "
            f"GEN {s.genjutsu:.1f} INT {s.intelligence:.1f} STR {s.strength:.1f} "
            f"SPD {s.speed:.1f} STA {s.stamina:.1f} HS {s.hand_seals:.1f} "
            f"(chakra {s.chakra_pool})"
        )

    # ========== NOTES PSYCHOLOGIQUES EDITORIALES (optionnel, JSON externe) ==========
    psycho_entry = _psycho_entry_at(character_id, age)
    if psycho_entry:
        if psycho_entry.get("note"):
            lines.append(f"  Situation editoriale: {psycho_entry['note']}")
        allowed = psycho_entry.get("allowed_relations") or []
        if allowed:
            lines.append(
                f"  RELATIONS CANON AUTORISEES a cet age: {', '.join(allowed[:8])}"
            )
        forbidden = psycho_entry.get("forbidden_relations") or []
        if forbidden:
            lines.append(
                f"  >>> RELATIONS CANON INTERDITES a cet age (n'invente JAMAIS d'interaction "
                f"sociale entre ce NPC et les suivants) : {', '.join(forbidden[:8])}"
            )
    elif age is not None and not char.personality_fr:
        # Fallback generique seulement si AUCUNE info perso n'est disponible
        fallback = _generic_age_situation(age)
        if fallback:
            lines.append(f"  Situation generale (extrapolee): {fallback}")

    # ========== RELATIONS CANONIQUES ACTIVES ==========
    active_rels = [r for r in char.key_relationships if r.since_year <= current_year]
    if active_rels:
        rel_strs = [f"{r.with_character}({r.type})" for r in active_rels[:6]]
        more = len(active_rels) - len(rel_strs)
        suffix = f" +{more}" if more > 0 else ""
        lines.append(f"  Relations canoniques actives: {', '.join(rel_strs)}{suffix}")
    elif age is not None and age < 4:
        lines.append(
            "  Relations actives: aucune relation canonique connue. A cet age, "
            "interactions limitees (parents, fratrie). Pas d'amis, pas d'ennemis."
        )

    # ========== AFFILIATIONS / VILLAGES PASSES ==========
    if len(char.current_village_by_era) > 1:
        # Indique le parcours (utile pour Itachi missing-nin, Sasuke deserteur, etc.)
        parcours_parts = []
        for entry in char.current_village_by_era:
            end = entry.to_year if entry.to_year is not None else "present"
            parcours_parts.append(f"{entry.village} (an {entry.from_year} a {end})")
        lines.append(f"  Parcours village: {' -> '.join(parcours_parts[:4])}")

    return "\n".join(lines)


def fact_sheets_for(
    canon: CanonBundle, character_ids: list[str], *, current_year: int
) -> str:
    """Compose un bloc 'FAITS CANONIQUES PNJ PRESENTS' pour le prompt."""
    blocks: list[str] = []
    for cid in character_ids:
        sheet = fact_sheet_for(canon, cid, current_year=current_year)
        if sheet:
            blocks.append(sheet)
    if not blocks:
        return ""
    return (
        "[FAITS CANONIQUES NPC]\n"
        "Ces faits sont la VERITE absolue, derives directement du canon scrape "
        "(personnalite, parcours, techniques, relations). Tu DOIS les respecter en TOTALITE.\n\n"
        + "\n\n".join(blocks)
    )


def find_contextual_npcs(
    canon: CanonBundle,
    *,
    current_year: int,
    player_village: str | None,
    player_age: int | None,
    extra_ids: list[str] | None = None,
    max_count: int = 8,
) -> list[str]:
    """Trouve les NPCs canon plausiblement presents dans la scene du joueur.

    Critere : meme village courant + age compatible (joueur enfant -> camarades
    enfants ; joueur adulte -> autres adultes). Toujours inclut extra_ids
    (les NPCs explicitement mentionnes dans l'intent ou le label).
    """
    selected: list[str] = list(extra_ids or [])

    if player_age is None or player_village is None:
        return selected[:max_count]

    # Plage d'age plausible pour camarades : +/- 4 ans pour enfants, +/- 8 pour adultes
    age_window = 4 if player_age < 14 else 8

    candidates: list[tuple[int, str]] = []
    for cid, char in canon.characters.items():
        if cid in selected:
            continue
        if char.birth_year is None:
            continue
        age = current_year - char.birth_year
        if age < 0:
            continue  # pas encore ne
        if char.death_year is not None and current_year > char.death_year:
            continue  # deja mort
        # Meme village a cette date
        village = _village_at(char, current_year)
        if village != player_village:
            continue
        # Filtre d'age
        if abs(age - player_age) > age_window:
            continue
        # Score : proche en age + a une note psycho (= NPC majeur)
        psycho = _load_psycho_notes().get(cid)
        score = abs(age - player_age) - (3 if psycho else 0)
        candidates.append((score, cid))

    candidates.sort()
    for _score, cid in candidates:
        if cid in selected:
            continue
        selected.append(cid)
        if len(selected) >= max_count:
            break
    return selected[:max_count]


# Helpers internes : extraction temporelle des donnees canon -----------------


def _rank_at(char: Character, year: int) -> str | None:
    """Rang du personnage a l'annee donnee (le plus recent <= year)."""
    if not char.rank_progression:
        return None
    valid = [r for r in char.rank_progression if r.year <= year]
    if not valid:
        return None
    return max(valid, key=lambda r: r.year).rank


def _previous_rank(char: Character, year: int) -> str | None:
    """Rang precedent le rang courant (utile pour signaler une promotion recente)."""
    if not char.rank_progression:
        return None
    valid = sorted([r for r in char.rank_progression if r.year <= year], key=lambda r: r.year)
    if len(valid) < 2:
        return None
    return valid[-2].rank


def _location_at(char: Character, year: int) -> str | None:
    """Localisation precise a l'annee donnee."""
    if not char.location_by_year:
        return None
    valid = [loc for loc in char.location_by_year if loc.year <= year]
    if not valid:
        return None
    return max(valid, key=lambda loc: loc.year).location


def _village_at(char: Character, year: int) -> str | None:
    """Village d'appartenance a l'annee donnee."""
    if not char.current_village_by_era:
        return char.village_of_origin
    for entry in char.current_village_by_era:
        if entry.from_year <= year and (entry.to_year is None or year < entry.to_year):
            return entry.village
    return char.village_of_origin


def _techniques_known_at(char: Character, year: int) -> list[str]:
    """Techniques connues du NPC a la date donnee (cumul jusqu'a la derniere snapshot)."""
    if not char.techniques_known_by_era:
        return []
    valid = sorted(
        [t for t in char.techniques_known_by_era if t.year <= year],
        key=lambda t: t.year,
    )
    if not valid:
        return []
    # Le canon snapshot par ere : on prend le plus recent (cumulatif implicite)
    return valid[-1].techniques


def _stats_at(char: Character, year: int):
    """Snapshot des stats canon a la date donnee."""
    if not char.stats_by_era:
        return None
    valid = [s for s in char.stats_by_era if s.year <= year]
    if not valid:
        return None
    return max(valid, key=lambda s: s.year)


def _psycho_entry_at(character_id: str, age: int | None) -> dict | None:
    """Entree complete (note + allowed_relations + forbidden_relations) pour un NPC a un age."""
    if age is None:
        return None
    notes = _load_psycho_notes().get(character_id)
    if not notes:
        return None
    for entry in notes:
        try:
            low = int(entry.get("from_age", 0))
            high = int(entry.get("to_age", 99))
            if low <= age <= high:
                return entry
        except (TypeError, ValueError):
            continue
    return None


_WIKITEXT_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_WIKITEXT_LINK = re.compile(r"\[\[(?:[^\]|]+\|)?([^\]]+)\]\]")
_WIKITEXT_REF = re.compile(r"<ref[^>]*>.*?</ref>", flags=re.DOTALL)
_WIKITEXT_HTML_TAG = re.compile(r"<[^>]+>")
_MULTI_WHITESPACE = re.compile(r"\s+")


def _clean_wikitext(text: str) -> str:
    """Strippe le markup Mediawiki residuel des champs scrapes (templates,
    liens, refs, balises HTML, espaces multiples)."""
    if not text:
        return ""
    cleaned = text
    # Strip ref blocks
    cleaned = _WIKITEXT_REF.sub("", cleaned)
    # Strip nested templates (passes successives)
    for _ in range(5):
        new = _WIKITEXT_TEMPLATE.sub("", cleaned)
        if new == cleaned:
            break
        cleaned = new
    # Liens [[A|B]] -> B, [[A]] -> A
    cleaned = _WIKITEXT_LINK.sub(r"\1", cleaned)
    # Balises HTML residuelles
    cleaned = _WIKITEXT_HTML_TAG.sub("", cleaned)
    # Whitespaces multiples
    cleaned = _MULTI_WHITESPACE.sub(" ", cleaned).strip()
    return cleaned


def _rank_from_age(age: int) -> str | None:
    """Rang typique derive de l'age (heuristique canon Naruto)."""
    if age < 4:
        return "nourrisson (porte par un adulte, pas autonome)"
    if age < 6:
        return "jeune enfant (pas encore a l'academie)"
    if age < 12:
        return "academy_student potentiel (academie 6-12 ans)"
    if age < 14:
        return "genin probable"
    if age < 18:
        return "chunin possible"
    if age < 30:
        return "jonin / specialiste possible"
    return "adulte experimente / kage / sage selon parcours"


def _generic_age_situation(age: int) -> str | None:
    """Note de situation generique selon l'age, quand aucune donnee perso n'est dispo."""
    if age < 1:
        return "Nourrisson de moins d'un an. Ne parle pas, depend totalement de ses parents."
    if age < 4:
        return (
            f"Tres jeune enfant ({age} ans). Pas a l'academie, ne participe pas aux "
            "interactions sociales adultes. Reste avec ses parents / nourrice."
        )
    if age < 6:
        return f"Enfant de {age} ans, joue, decouvre le village, pas encore eleve a l'academie."
    if age < 12:
        return f"Eleve a l'academie ({age} ans), apprend les bases du chakra et du combat."
    if age < 16:
        return (
            f"Adolescent shinobi ({age} ans). Genin ou chunin selon parcours canonique. "
            "Vit en equipe, prend des missions."
        )
    if age < 30:
        return f"Adulte shinobi ({age} ans). Chunin/jonin selon le canon."
    if age < 60:
        return f"Adulte mature ({age} ans). Possible jonin senior, instructeur, ou commandant."
    return f"Ancien ({age} ans). Souvent en retrait, conseiller ou kage en exercice."
