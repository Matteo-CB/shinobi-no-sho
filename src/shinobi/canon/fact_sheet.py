"""Genere des fact sheets canoniques precises pour un NPC a une date donnee.

Permet d'injecter dans le prompt LLM l'etat exact d'un personnage canon
(rang, lieu, age, relations, situation) a l'annee in-game courante.

Empeche le LLM d'inventer 'Naruto a 6 ans avec ses amis' alors que
canoniquement Naruto a 6 ans est ostracise et seul.
"""

from __future__ import annotations

from shinobi.canon.models import CanonBundle, Character

# Notes psychologiques canoniques cles, par ID + tranche d'age.
# Ces faits ne sont pas dans les datasets generes ; ils encodent la connaissance
# editoriale "qu'aurait un fan" pour eviter les inventions du LLM.
_PSYCHO_NOTES_BY_AGE: dict[str, list[tuple[int, int, str]]] = {
    "uzumaki_naruto": [
        (0, 11, "Ostracise par le village (jinchuuriki kyuubi cache). Seul a l'academie. "
                "Pas d'amis, evite par les enfants, deteste par les adultes. Iruka est le seul adulte qui le tolere."),
        (12, 12, "Forme l'equipe 7 avec Sasuke et Sakura. Premier vrai cercle social. Toujours rejete par la majorite du village."),
        (13, 15, "Voyage avec Jiraiya, devient plus mature mais reste exclu socialement."),
        (16, 16, "Retour a Konoha. Reconnu apres avoir vaincu Pain. Heros du village."),
        (17, 99, "Heros et eventuellement Hokage. Marie a Hinata."),
    ],
    "uchiha_sasuke": [
        (0, 7, "Dans l'ombre de son frere Itachi, deja tres talentueux. Famille intacte."),
        (8, 11, "Massacre du clan recent. Survivant traumatise. Solitaire, obsede par la vengeance."),
        (12, 12, "Equipe 7 avec Naruto et Sakura. Tient ses distances."),
        (13, 15, "Deserteur, eleve d'Orochimaru a Otogakure. Missing-nin."),
        (16, 16, "Tue Itachi, decouvre la verite, rejoint puis quitte Akatsuki."),
        (17, 99, "Quete de redemption autour du monde."),
    ],
    "haruno_sakura": [
        (0, 11, "Eleve studieuse, complexee par son grand front. Amie d'Ino jusqu'a leur rivalite pour Sasuke."),
        (12, 12, "Equipe 7. Inutile au combat au debut."),
        (13, 15, "Eleve de Tsunade, devient medic-nin et enorme force physique."),
    ],
    "uchiha_itachi": [
        (0, 6, "Prodige du clan Uchiha. Tres jeune mais deja exceptionnel."),
        (7, 12, "Genin, puis chunin a 10 ans, jonin a 11. Ambivalent face aux ordres du village."),
        (13, 13, "Massacre le clan Uchiha sur ordre du village (an 8 environ). Devient missing-nin."),
        (14, 24, "Akatsuki. Joue le terroriste mais protege Sasuke en secret."),
    ],
    "hatake_kakashi": [
        (0, 5, "Prodige, fils de Sakumo le Croc Blanc."),
        (6, 12, "Chunin a 6 ans, jonin a 12. Eleve de Minato."),
        (13, 25, "Anbu puis enseignant. Connu comme Kakashi du Sharingan."),
        (26, 30, "Sensei de l'equipe 7 (Naruto/Sasuke/Sakura)."),
        (31, 99, "Sixieme Hokage."),
    ],
    "yakushi_kabuto": [
        (0, 9, "Orphelin de guerre adopte par Nono Yakushi."),
        (10, 18, "Eleve de Nono puis espion d'Orochimaru. Apparait comme medic-nin loyal."),
        (19, 99, "Bras droit d'Orochimaru. Deserteur."),
    ],
    "konohamaru_sarutobi": [
        (0, -1, "PAS ENCORE NE. Konohamaru naitra apres l'an 6."),
        (0, 7, "Petit-fils du Sandaime Hokage. Aspirant Hokage des l'enfance."),
        (8, 11, "Disciple informel de Naruto."),
    ],
    "uchiha_madara": [
        (0, 9999, "Mort ou en sommeil pendant l'ere Konoha (n'apparait plus avant les Edo Tensei)."),
    ],
    "senju_hashirama": [
        (0, 9999, "Mort, fondateur de Konoha. N'apparait que via Edo Tensei."),
    ],
    "namikaze_minato": [
        (0, 24, "Vivant. Yondaime Hokage. Mort en l'an 0 lors de l'attaque du Kyuubi sur Konoha."),
        (25, 9999, "Mort. Sceau dans Naruto. Reapparait via Edo Tensei en 4eme guerre."),
    ],
    "uzumaki_kushina": [
        (0, 9999, "Morte en l'an 0 lors de l'attaque du Kyuubi sur Konoha."),
    ],
    "orochimaru": [
        (0, 9999, "Sannin de Konoha jusqu'a sa desertion. Apres : missing-nin a Otogakure, obsede par l'immortalite."),
    ],
    "jiraiya": [
        (0, 9999, "Sannin. Voyage et collecte d'information. Pere spirituel de Naruto plus tard."),
    ],
    "tsunade": [
        (0, 9999, "Sannin. Voyage, jeu, alcool. Devient Godaime Hokage seulement vers l'an 12."),
    ],
}


def fact_sheet_for(canon: CanonBundle, character_id: str, *, current_year: int) -> str | None:
    """Retourne un resume canonique de l'etat d'un NPC a current_year, ou None.

    Inclut : age, statut vital, rang, lieu, situation psychologique cle.
    Generique : marche pour TOUS les NPCs canon, avec fallback dynamique
    base sur l'age si pas de note psycho hardcodee.
    """
    char = canon.characters.get(character_id)
    if char is None:
        return None

    lines: list[str] = []
    name = char.name_fr or char.name_romaji
    lines.append(f"NPC: {character_id} ({name})")

    # Statut vital
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

    # Rang a cette date (canon rank_progression > fallback derive de l'age)
    rank = _rank_at(char, current_year)
    if rank:
        lines.append(f"  Rang: {rank}")
    elif age is not None:
        derived = _rank_from_age(age)
        if derived:
            lines.append(f"  Rang derive: {derived} (estime selon l'age)")

    # Lieu courant
    location = _location_at(char, current_year)
    if location:
        lines.append(f"  Lieu: {location}")
    else:
        village = _village_at(char, current_year)
        if village:
            lines.append(f"  Village: {village}")

    if char.clan:
        lines.append(f"  Clan: {char.clan}")

    # Notes psychologiques canoniques (priorite : whitelist editoriale > fallback age-based)
    psycho = _psycho_at(character_id, age)
    if psycho:
        lines.append(f"  Situation: {psycho}")
    elif age is not None:
        fallback = _generic_age_situation(age)
        if fallback:
            lines.append(f"  Situation generale: {fallback}")

    # Relations canoniques actives a cette date
    active_rels = [
        r for r in char.key_relationships
        if r.since_year <= current_year
    ]
    if active_rels:
        rel_strs = [f"{r.with_character}({r.type})" for r in active_rels[:5]]
        lines.append(f"  Relations actives: {', '.join(rel_strs)}")
    elif age is not None and age < 4:
        lines.append(
            "  Relations actives: aucune relation canonique connue. A cet age, "
            "interactions limitees (parents, fratrie). Pas d'amis, pas d'ennemis."
        )

    return "\n".join(lines)


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
    """Note de situation generique selon l'age, quand aucune psycho note hardcodee."""
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
    return "[FAITS CANONIQUES NPC]\nCes faits sont la VERITE absolue. Tu DOIS les respecter sans en inventer d'autres :\n\n" + "\n\n".join(blocks)


# Helpers internes ------------------------------------------------------------


def _rank_at(char: Character, year: int) -> str | None:
    """Rang du personnage a l'annee donnee (le plus recent <= year)."""
    if not char.rank_progression:
        return None
    valid = [r for r in char.rank_progression if r.year <= year]
    if not valid:
        return None
    return max(valid, key=lambda r: r.year).rank


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


def _psycho_at(character_id: str, age: int | None) -> str | None:
    """Note psychologique canonique selon l'age."""
    if age is None:
        return None
    notes = _PSYCHO_NOTES_BY_AGE.get(character_id)
    if notes is None:
        return None
    for low, high, text in notes:
        if low <= age <= high:
            return text
    return None
