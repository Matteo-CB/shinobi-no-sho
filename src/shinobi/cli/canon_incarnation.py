"""Mode 'incarner un personnage canon' pour Phase 6.3.

Permet au joueur de selectionner un canon character existant (Naruto, Itachi,
Sasuke, etc.) et de demarrer la partie a un age choisi avec son profil
canonique : clan, village, natures, kekkei genkai, techniques connues filtrees
par age, et stats roll-deterministe biaise selon son canon.

Limitation : `stats_by_era`, `rank_progression`, `key_relationships` sont
partiellement vides dans le canon (gap d'extraction Phase 1). On compense par :
- Stats roll deterministe + bonus selon clan/role canon (pattern _roll_stats)
- Rank derive de l'age via _rank_from_age + boost si prodige (Itachi, Kakashi)
- Relations seedees via canon characters.key_relationships si dispo, sinon
  vides (le joueur les construit en jeu).

Ne mute PAS le canon : on extrait les fields read-only et on construit une
instance Character (Pydantic frozen) a partir.
"""
from __future__ import annotations

import random
from typing import Any

from shinobi.engine.character import (
    Character,
    ChakraState,
    FamilyState,
    KnownTechnique,
    Relationship,
)
from shinobi.engine.stats import CoreStats, ExtendedStats
from shinobi.types import Gender
from shinobi.utils.slug import slugify


# Personnages prodiges canoniques (skip 1 ou 2 paliers de rank pour age).
# Source : observation directe du canon Naruto (Kakashi jonin a 13, Itachi
# capitaine ANBU a 13, etc.).
_PRODIGY_CHARS: frozenset[str] = frozenset({
    "uchiha_itachi",
    "hatake_kakashi",
    "namikaze_minato",
    "uchiha_sasuke",
    "uzumaki_naruto",
    "haku_yuki",
    "uchiha_shisui",
    "hyuuga_neji",
})


def list_playable_canon_characters(
    canon: Any,
    *,
    village_filter: str | None = None,
    alive_at_year: int | None = None,
    require_birth_year: bool = True,
) -> list[Any]:
    """Liste les canon characters jouables, triees par notoriete.

    Filtres :
    - `village_filter` : seul ceux dont village_of_origin = village_filter
    - `alive_at_year` : skip ceux dont death_year <= alive_at_year ou
      birth_year > alive_at_year
    - `require_birth_year` : default True -> seulement les 112 chars avec
      birth_year canon. Sans birth_year on ne peut pas calculer l'age
      proprement.

    Notoriete (heuristique) : presence dans canon.deep_motivations 9.2 puis
    presence dans political_forces.factions[].leader_id puis 9.4 divergence
    points (perso pivot canon) puis ordre alphabetique.
    """
    motivations_keys: set[str] = set()
    if hasattr(canon, "deep_motivations") and canon.deep_motivations:
        motivations_keys = set(canon.deep_motivations.keys())

    leader_ids: set[str] = set()
    if hasattr(canon, "political_forces") and canon.political_forces:
        for fac in canon.political_forces.get("factions", []):
            lid = fac.get("leader_id")
            if isinstance(lid, str):
                leader_ids.add(lid)

    divergence_protagonists: set[str] = set()
    if hasattr(canon, "divergence_points") and canon.divergence_points:
        for dp in canon.divergence_points.get("divergence_points", []):
            for cid in dp.get("involved_canon_ids", []) or []:
                if isinstance(cid, str):
                    divergence_protagonists.add(cid)

    out: list[Any] = []
    for cid, char in canon.characters.items():
        if require_birth_year and char.birth_year is None:
            continue
        if village_filter and char.village_of_origin != village_filter:
            continue
        if alive_at_year is not None:
            if char.birth_year is not None and char.birth_year > alive_at_year:
                continue
            if char.death_year is not None and char.death_year <= alive_at_year:
                continue
        out.append(char)

    def _notoriety(c) -> tuple[int, str]:
        score = 0
        if c.id in motivations_keys:
            score -= 100  # tres connu (Phase H 9.2 covers ~50 chars cles)
        if c.id in leader_ids:
            score -= 50
        if c.id in divergence_protagonists:
            score -= 30
        if c.kekkei_genkai:
            score -= 5  # kekkei_genkai owners sont generalement notables
        return (score, c.id)

    out.sort(key=_notoriety)
    return out


def resolve_canon_id(canon: Any, query: str) -> tuple[str | None, list[str]]:
    """Resout une chaine utilisateur en canon_id avec recherche fuzzy.

    Strategie :
    1. Match exact sur canon_id (ex: 'uchiha_itachi')
    2. Match exact sur name_romaji (ex: 'Itachi Uchiha')
    3. Match exact sur name_fr (ex: 'Itachi Uchiha')
    4. Match substring sur id, name_romaji, name_fr
    5. Si plusieurs matches, retourne (None, [list_des_matches])

    Returns :
        (canon_id, candidates) : canon_id est None si ambigu ou pas trouve.
        Si pas trouve, candidates est vide. Si ambigu, candidates contient
        les ids potentiels pour disambiguation.
    """
    if not query:
        return None, []
    q = query.strip().lower()

    # 1. Match exact id
    if query in canon.characters:
        return query, [query]

    # 2/3. Match exact name (case insensitive)
    exact_matches: list[str] = []
    for cid, c in canon.characters.items():
        names = [
            getattr(c, "name_romaji", None) or "",
            getattr(c, "name_fr", None) or "",
        ]
        if any(n and n.lower() == q for n in names):
            exact_matches.append(cid)
    if len(exact_matches) == 1:
        return exact_matches[0], exact_matches
    if len(exact_matches) > 1:
        return None, exact_matches

    # 4. Substring OR token-set match : id, name_romaji, name_fr, aliases
    # name_romaji est en ordre occidental ('Kakashi Hatake') mais l'utilisateur
    # peut taper en ordre japonais ('Hatake Kakashi'). On split en tokens et
    # on accepte si tous les tokens du query sont dans le haystack.
    q_tokens = {t for t in q.replace("_", " ").split() if t}
    substr_matches: list[str] = []
    for cid, c in canon.characters.items():
        haystacks = [
            cid.lower().replace("_", " "),
            (getattr(c, "name_romaji", None) or "").lower(),
            (getattr(c, "name_fr", None) or "").lower(),
        ]
        # Aliases
        aliases = getattr(c, "aliases", None) or []
        for a in aliases:
            if isinstance(a, str):
                haystacks.append(a.lower())
        # Match si query est substring d'un haystack OU tous les tokens
        # du query sont dans un haystack (ordre indifferent).
        matched = False
        for h in haystacks:
            if not h:
                continue
            if q in h:
                matched = True
                break
            h_tokens = set(h.replace("_", " ").split())
            if q_tokens and q_tokens.issubset(h_tokens):
                matched = True
                break
        if matched:
            substr_matches.append(cid)
    if len(substr_matches) == 1:
        return substr_matches[0], substr_matches
    if len(substr_matches) > 1:
        # Tri par notoriete + len(id) (preferer matches courts)
        notoriety_keys: set[str] = set()
        if hasattr(canon, "deep_motivations") and canon.deep_motivations:
            notoriety_keys = set(canon.deep_motivations.keys())
        substr_matches.sort(
            key=lambda x: (
                0 if x in notoriety_keys else 1,
                len(x),
                x,
            ),
        )
        return None, substr_matches

    return None, []


def _rank_for_canon_at_age(canon_id: str, age: int) -> str:
    """Rang accelere pour les prodiges canon."""
    if canon_id in _PRODIGY_CHARS:
        if age >= 13:
            return "jonin"  # Kakashi, Itachi etaient jonin/anbu a 13
        if age >= 9:
            return "chunin"
        if age >= 6:
            return "academy_student"
        return "civilian"
    # Default progression standard
    if age < 6:
        return "civilian"
    if age < 12:
        return "academy_student"
    return "genin"


def _filter_techniques_at_age(canon_char: Any, age: int) -> list[KnownTechnique]:
    """Extrait les techniques connues canoniquement a un age donne.

    Strategie tolerante au gap d'extraction canon :

    Cas 1 : techniques_known_by_era a plusieurs entries avec years distincts
    (extraction Phase 1 complete) -> filtre exact par era_year <= birth+age.

    Cas 2 : 1 seule entry avec era_year (cas courant : extraction Phase 1
    a aggrege toutes les techniques au last_year). On infere une fraction
    proportionnelle a l'age :
      ratio = max(0.1, min(1.0, age / age_at_era_year))
    Garde les `len(techs) * ratio` premieres (ordre canon = approx ordre
    d'apprentissage).

    Skip aussi les "techniques" qui sont en realite des refs fichiers wiki
    (file_*, *_png).
    """
    out: list[KnownTechnique] = []
    if not hasattr(canon_char, "techniques_known_by_era"):
        return out
    birth = canon_char.birth_year
    if birth is None:
        return out
    eras = list(canon_char.techniques_known_by_era or [])
    if not eras:
        return out

    seen: set[str] = set()

    # Cas 1 : multi-era avec years distincts
    if len(eras) >= 2:
        threshold_year = birth + age
        for era in eras:
            era_year = getattr(era, "year", None)
            techs = getattr(era, "techniques", None) or []
            if era_year is not None and era_year > threshold_year:
                continue
            for tech_id in techs:
                if (
                    not isinstance(tech_id, str)
                    or tech_id in seen
                    or tech_id.startswith("file_")
                    or tech_id.endswith("_png")
                ):
                    continue
                seen.add(tech_id)
                out.append(KnownTechnique(
                    technique_id=tech_id,
                    mastery_level=1.0,
                    learned_year=era_year if era_year is not None else birth + age,
                    learned_from=None,
                ))
        return out

    # Cas 2 : 1 era unique - inference par ratio d'age
    era = eras[0]
    era_year = getattr(era, "year", None)
    techs = [
        t for t in (getattr(era, "techniques", None) or [])
        if isinstance(t, str)
        and not t.startswith("file_")
        and not t.endswith("_png")
    ]
    if not techs:
        return out

    if era_year is None:
        # Pas de year era -> assume "always known" mais cap a 50% des techs
        # pour les ages enfant
        ratio = 0.5 if age < 12 else 1.0
    else:
        # age effectif que represente l'era_year
        canon_age_at_era = era_year - birth
        if canon_age_at_era <= 0:
            ratio = 1.0
        else:
            ratio = max(0.1, min(1.0, age / canon_age_at_era))
    n = max(1, int(len(techs) * ratio))

    # learned_year heuristique : echelonne lineairement sur birth..era_year
    # pour les techniques retenues.
    for i, tech_id in enumerate(techs[:n]):
        if tech_id in seen:
            continue
        seen.add(tech_id)
        if era_year is not None and n > 0:
            learned = birth + int((i / max(1, n)) * (era_year - birth))
        else:
            learned = birth + age
        out.append(KnownTechnique(
            technique_id=tech_id,
            mastery_level=1.0,
            learned_year=learned,
            learned_from=None,
        ))
    return out


def _seed_relationships(canon_char: Any, current_year: int) -> list[Relationship]:
    """Hydrate les relations depuis canon.Character.key_relationships.

    Tolerant : si key_relationships est vide (cas frequent canon partiel),
    retourne []. Le joueur construit ses relations en jeu via les rencontres
    (`talk`, `befriend`, etc.).
    """
    out: list[Relationship] = []
    rels = getattr(canon_char, "key_relationships", None) or []
    for rel in rels:
        with_id = getattr(rel, "with_character_id", None) or getattr(
            rel, "target_id", None,
        )
        affinity = getattr(rel, "affinity", None) or 0
        nature = getattr(rel, "nature", None) or "ally"
        if not isinstance(with_id, str) or not with_id:
            continue
        out.append(Relationship(
            with_character_id=with_id,
            affinity=int(affinity),
            relationship_type=nature,
            last_updated_year=current_year,
        ))
    return out


def _stats_for_canon_character(
    canon_char: Any, age: int, current_year: int,
) -> tuple[CoreStats, ExtendedStats, ChakraState]:
    """Roll deterministe biaise selon clan + kekkei + role canon.

    Reutilise la formule de base de _roll_stats (character_creation) avec
    seed = canon_id (deterministe : meme perso au meme age = memes stats).
    Bonus prodige : +0.5 sur les axes principaux pour les chars _PRODIGY_CHARS.
    """
    canon_id = canon_char.id
    natures = list(canon_char.natures or [])
    kekkei = list(canon_char.kekkei_genkai or [])
    clan_id = canon_char.clan
    rng = random.Random(f"canon|{canon_id}|{current_year}")

    def _b() -> float:
        return round(rng.uniform(1.0, 2.8), 1)  # range plus haut que civils

    ninjutsu = _b()
    taijutsu = _b()
    genjutsu = _b()
    intelligence = _b()
    strength = _b()
    speed = _b()
    stamina = _b()
    hand_seals = _b()
    chakra_pool_max = int(rng.uniform(120, 250))
    chakra_control = _b()
    learning_genius = _b()
    social_charisma = _b()
    leadership = _b()
    luck = _b()
    beauty = _b()
    lineage_value = round(rng.uniform(2.0, 4.5), 1) if clan_id else 1.5
    willpower = _b()
    perception = _b()

    # Bias clan (memes que character_creation._roll_stats)
    if clan_id == "uchiha":
        ninjutsu += 0.5
        genjutsu += 0.5
        intelligence += 0.3
    elif clan_id == "hyuuga":
        taijutsu += 0.6
        perception += 0.5
        chakra_control += 0.4
    elif clan_id == "senju":
        stamina += 0.5
        chakra_pool_max = int(chakra_pool_max * 1.3)
        lineage_value += 0.5
    elif clan_id == "uzumaki":
        chakra_pool_max = int(chakra_pool_max * 1.5)
        stamina += 0.4
    elif clan_id == "nara":
        intelligence += 0.6
    elif clan_id == "akimichi":
        strength += 0.6
        stamina += 0.4
    elif clan_id == "inuzuka":
        speed += 0.4
        perception += 0.4
    elif clan_id == "yamanaka":
        genjutsu += 0.4
        social_charisma += 0.4

    if "sharingan" in kekkei or "byakugan" in kekkei:
        perception += 0.5
    if "rinnegan" in kekkei:
        ninjutsu += 0.7
        chakra_pool_max = int(chakra_pool_max * 1.4)

    # Bonus prodige
    if canon_id in _PRODIGY_CHARS:
        ninjutsu += 0.5
        speed += 0.4
        learning_genius += 0.7
        willpower += 0.4

    # Scaling par age : enfants (age<10) ont des stats reduites
    # (le canon a dim a l'age adulte ; on regle le current snapshot).
    if age < 10:
        scale = 0.6 + (age / 25.0)  # ~0.6 a age=0, ~1.0 a age=10
        for var in (
            "ninjutsu", "taijutsu", "genjutsu", "strength", "speed",
            "stamina", "hand_seals",
        ):
            pass  # variables locales, on apply scale ci-dessous
        ninjutsu *= scale
        taijutsu *= scale
        genjutsu *= scale
        strength *= scale
        speed *= scale
        stamina *= scale
        hand_seals *= scale
        chakra_pool_max = int(chakra_pool_max * scale)

    return (
        CoreStats(
            ninjutsu=min(5.0, ninjutsu),
            taijutsu=min(5.0, taijutsu),
            genjutsu=min(5.0, genjutsu),
            intelligence=min(5.0, intelligence),
            strength=min(5.0, strength),
            speed=min(5.0, speed),
            stamina=min(5.0, stamina),
            hand_seals=min(5.0, hand_seals),
        ),
        ExtendedStats(
            chakra_pool_max=chakra_pool_max,
            chakra_control=min(5.0, chakra_control),
            learning_genius=min(5.0, learning_genius),
            social_charisma=min(5.0, social_charisma),
            leadership=min(5.0, leadership),
            luck=min(5.0, luck),
            beauty=min(5.0, beauty),
            lineage_value=min(5.0, lineage_value),
            willpower=min(5.0, willpower),
            perception=min(5.0, perception),
        ),
        ChakraState(
            current=chakra_pool_max,
            max=chakra_pool_max,
            natures_unlocked=natures,
        ),
    )


def incarnate_canon_character(
    canon: Any,
    canon_id: str,
    age_at_start: int,
    *,
    assumed_birth_year: int | None = None,
) -> tuple[Character, int]:
    """Construit un Character a partir du canon character + age choisi.

    Retourne (character, current_year) : le current_year est calcule depuis
    canon.birth_year + age_at_start. Le caller utilise current_year pour
    initialiser le WorldState.

    Args:
        canon: CanonBundle
        canon_id: id du canon character
        age_at_start: age auquel le joueur veut commencer
        assumed_birth_year: si canon.birth_year est None (gap d'extraction
            Phase 1), accepter une birth_year inferee fournie par le caller
            (ex: derive de l'ere ou demandee a l'utilisateur). Si None et
            que canon.birth_year est None, leve ValueError.

    Raises:
        KeyError: si canon_id absent de canon.characters
        ValueError: si canon.birth_year est None ET assumed_birth_year aussi
    """
    if canon_id not in canon.characters:
        raise KeyError(
            f"Canon character {canon_id} introuvable dans canon.characters",
        )
    canon_char = canon.characters[canon_id]
    effective_birth_year = canon_char.birth_year
    if effective_birth_year is None:
        if assumed_birth_year is None:
            raise ValueError(
                f"Canon character {canon_id} has no birth_year defined in "
                f"canon. Provide assumed_birth_year to proceed."
            )
        effective_birth_year = assumed_birth_year
    current_year = effective_birth_year + age_at_start
    # Si le perso est canon-mort avant cet age, on warn mais on continue
    # (le joueur peut choisir de jouer un perso "ramene a la vie" en mode
    # alternate timeline).
    if (
        canon_char.death_year is not None
        and canon_char.death_year <= current_year
    ):
        # On accepte mais current_year est cape juste avant death_year
        current_year = canon_char.death_year - 1
        age_at_start = current_year - canon_char.birth_year

    # Seed deterministe : meme canon_id + meme age = meme perso roll.
    rng = random.Random(f"canon|{canon_id}|{age_at_start}")
    birth_month = rng.randint(1, 12)
    birth_day = rng.randint(1, 28)

    name = canon_char.name_romaji or canon_char.name_fr or canon_id
    char_id = slugify(canon_id)

    rank = _rank_for_canon_at_age(canon_id, age_at_start)
    natures = list(canon_char.natures or [])
    kekkei_genkai = list(canon_char.kekkei_genkai or [])
    kekkei_mora = list(canon_char.kekkei_mora or [])
    tailed_beast = canon_char.tailed_beast

    stats, extended_stats, chakra = _stats_for_canon_character(
        canon_char, age_at_start, current_year,
    )

    techniques = _filter_techniques_at_age(canon_char, age_at_start)
    relationships = _seed_relationships(canon_char, current_year)

    # Gender map : canon utilise 'male'/'female'/'unknown', Character.Gender
    # accepte les memes valeurs.
    raw_gender = getattr(canon_char, "gender", None)
    if raw_gender == "male":
        gender = Gender.male
    elif raw_gender == "female":
        gender = Gender.female
    else:
        gender = Gender.non_binary

    village = canon_char.village_of_origin or "konohagakure"

    character = Character(
        id=char_id,
        name=name,
        gender=gender,
        birth_year=canon_char.birth_year,
        birth_date=f"{birth_month:02d}-{birth_day:02d}",
        age_years=age_at_start,
        village_of_origin=village,
        current_village=village,
        current_location=village,
        clan=canon_char.clan,
        secondary_clan=canon_char.secondary_clan,
        family=FamilyState(),
        rank=rank,
        natures=natures,
        kekkei_genkai=kekkei_genkai,
        kekkei_mora=kekkei_mora,
        tailed_beast=tailed_beast,
        stats=stats,
        extended_stats=extended_stats,
        chakra=chakra,
        techniques_known=techniques,
        relationships=relationships,
    )
    return character, current_year


__all__ = [
    "incarnate_canon_character",
    "list_playable_canon_characters",
]
