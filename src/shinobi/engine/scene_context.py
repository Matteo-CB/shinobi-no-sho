"""Calcule le contexte factuel STRICT d'une scene de jeu.

Determine de maniere deterministe :
- Qui est vivant a la date courante
- Qui est physiquement accessible au joueur (meme village, ou voyage realiste)
- Quels lieux sont atteignables compte tenu du rang et de l'age
- Quelles ressources canoniques (techniques, kekkei genkai) le joueur peut esperer
  acquerir dans son contexte immediat

Ce module est la garde-fou de coherence : ce qu'il ne renvoie PAS ne doit pas
etre propose par le LLM dans les actions.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from shinobi.canon.models import CanonBundle
from shinobi.canon.models import Character as CanonCharacter
from shinobi.engine.character import Character
from shinobi.engine.world import WorldState

# Nombre max de PNJ accessibles a injecter (eviter le surcharge prompt).
MAX_ACCESSIBLE_NPCS = 25


@dataclass
class AccessibleNPC:
    """PNJ canoniquement accessible au joueur dans la scene actuelle."""

    character_id: str
    name: str
    age: int
    village: str
    clan: str | None
    rank_in_canon: str | None
    role_label: str  # ex: "Hokage", "academy_student", "marchand", "rival"
    is_in_same_village: bool


@dataclass
class SceneContext:
    """Contexte factuel complet de la scene actuelle, source de verite."""

    current_year: int
    current_date: str
    player_age: int
    player_rank: str
    player_village: str
    player_location: str
    player_can_leave_village: bool
    player_combat_capable: bool

    accessible_npcs: list[AccessibleNPC] = field(default_factory=list)
    accessible_locations: list[str] = field(default_factory=list)
    notable_events_imminent: list[str] = field(default_factory=list)

    constraints_fr: list[str] = field(default_factory=list)

    def npc_ids(self) -> set[str]:
        return {n.character_id for n in self.accessible_npcs}


def compute_scene_context(
    character: Character,
    world: WorldState,
    canon: CanonBundle,
) -> SceneContext:
    """Calcule le contexte factuel de la scene."""
    year = world.current_year
    age = character.age_years
    village = character.current_village

    can_leave_village = _can_leave_village(character, age)
    combat_capable = _combat_capable(character, age)

    accessible = list(_gather_accessible_npcs(character, world, canon, year=year, village=village))

    constraints = _build_constraints_fr(character, age, can_leave_village, combat_capable)

    locations = _accessible_locations(canon, character, can_leave_village)

    imminent_events = _imminent_events(canon, world, horizon_days=180)

    return SceneContext(
        current_year=year,
        current_date=world.current_date,
        player_age=age,
        player_rank=character.rank,
        player_village=village,
        player_location=character.current_location,
        player_can_leave_village=can_leave_village,
        player_combat_capable=combat_capable,
        accessible_npcs=accessible[:MAX_ACCESSIBLE_NPCS],
        accessible_locations=locations,
        notable_events_imminent=imminent_events,
        constraints_fr=constraints,
    )


def _can_leave_village(character: Character, age: int) -> bool:
    """Un perso peut sortir de son village seul si genin ET 12+ minimum."""
    if character.is_missing_nin:
        return True
    rank_levels = {
        "academy_student": 0,
        "civilian": 0,
        "genin": 1,
        "chunin": 2,
        "tokubetsu_jonin": 3,
        "jonin": 4,
        "anbu": 4,
        "kage": 5,
        "sannin": 4,
    }
    level = rank_levels.get(character.rank, 0)
    if age < 6:
        return False
    if age < 12:
        return level >= 2  # exception : enfant prodige genin a 8 ans
    return level >= 1


def _combat_capable(character: Character, age: int) -> bool:
    """Un perso peut s'engager dans un combat reel si >= 6 ans + entraine."""
    if age < 4:
        return False
    if age < 6 and character.rank == "academy_student":
        return False
    if character.health.hp_current <= 0:
        return False
    return True


def _gather_accessible_npcs(
    character: Character,
    world: WorldState,
    canon: CanonBundle,
    *,
    year: int,
    village: str,
) -> Iterable[AccessibleNPC]:
    """Liste des PNJ canon accessibles au joueur dans la scene."""
    seen: set[str] = set()
    # Priorite 1 : famille du joueur
    for member in character.family.members:
        if member.character_id in seen:
            continue
        canon_char = canon.characters.get(member.character_id)
        if canon_char is None:
            yield AccessibleNPC(
                character_id=member.character_id,
                name=member.character_id.replace("_", " ").title(),
                age=age_at(canon_char, year)
                if canon_char
                else max(20, year - character.birth_year + 25),
                village=village,
                clan=character.clan,
                rank_in_canon=None,
                role_label=member.relationship_label,
                is_in_same_village=True,
            )
            seen.add(member.character_id)
            continue
        yield _make_accessible(
            canon_char, year, village=village, role_label=member.relationship_label
        )
        seen.add(member.character_id)

    # Priorite 2 : Hokage / chef du village au moment courant
    kage_id = _kage_at(canon, village, year)
    if kage_id and kage_id not in seen:
        canon_char = canon.characters.get(kage_id)
        if canon_char is not None:
            yield _make_accessible(canon_char, year, village=village, role_label="Hokage")
            seen.add(kage_id)

    # Priorite 3 : tous les chars vivants dans le meme village a cette annee
    for canon_char in canon.characters.values():
        if canon_char.id in seen:
            continue
        if not _is_alive_at(canon_char, year):
            continue
        if not _is_in_village_at(canon_char, year, village):
            continue
        # Pour academy_student/genin tres jeunes : on prefere les chars du meme age + ou - 5 ans + adultes notables
        ch_age = age_at(canon_char, year)
        if character.age_years < 8 and not (abs(ch_age - character.age_years) <= 5 or ch_age >= 18):
            continue
        yield _make_accessible(
            canon_char, year, village=village, role_label=_role_label(canon_char)
        )
        seen.add(canon_char.id)


def _make_accessible(
    canon_char: CanonCharacter, year: int, *, village: str, role_label: str
) -> AccessibleNPC:
    return AccessibleNPC(
        character_id=canon_char.id,
        name=canon_char.name_romaji,
        age=age_at(canon_char, year),
        village=_village_at(canon_char, year) or village,
        clan=canon_char.clan,
        rank_in_canon=_current_rank(canon_char, year),
        role_label=role_label,
        is_in_same_village=(_village_at(canon_char, year) == village),
    )


def age_at(canon_char: CanonCharacter | None, year: int) -> int:
    if canon_char is None or canon_char.birth_year is None:
        return 0
    return max(0, year - canon_char.birth_year)


def _is_alive_at(canon_char: CanonCharacter, year: int) -> bool:
    if canon_char.birth_year is not None and year < canon_char.birth_year:
        return False
    if canon_char.death_year is not None and year >= canon_char.death_year:
        return False
    return True


def _village_at(canon_char: CanonCharacter, year: int) -> str | None:
    """Determine le village ou se trouve canoniquement le perso a une date."""
    for entry in canon_char.current_village_by_era:
        if entry.from_year <= year and (entry.to_year is None or year < entry.to_year):
            return entry.village
    return canon_char.village_of_origin


def _is_in_village_at(canon_char: CanonCharacter, year: int, village: str) -> bool:
    located = _village_at(canon_char, year)
    if located is None:
        # Sans donnee, on suppose dans son village d'origine si le canon ne dit rien d'autre.
        return canon_char.village_of_origin == village
    return located == village


def _current_rank(canon_char: CanonCharacter, year: int) -> str | None:
    """Determine le rang canonique du perso a cette annee."""
    last_rank: str | None = None
    for entry in sorted(canon_char.rank_progression, key=lambda r: r.year):
        if entry.year <= year:
            last_rank = entry.rank
    return last_rank


def _role_label(canon_char: CanonCharacter) -> str:
    if canon_char.clan:
        return f"membre du clan {canon_char.clan}"
    return "shinobi"


def _kage_at(canon: CanonBundle, village_id: str, year: int) -> str | None:
    village = canon.villages.get(village_id)
    if village is None:
        return None
    for entry in sorted(village.kage_lineage, key=lambda k: k.from_year):
        if entry.from_year <= year and (entry.to_year is None or year < entry.to_year):
            return entry.character_id
    return None


def _accessible_locations(canon: CanonBundle, character: Character, can_leave: bool) -> list[str]:
    """Lieux que le joueur peut visiter realistement."""
    out = [character.current_village, character.current_location]
    if not can_leave:
        return list(set(out))
    # Si peut sortir, ajoute villages voisins ou tous les villages selon rang
    for v in canon.villages.values():
        if v.id != character.current_village:
            out.append(v.id)
    return list(set(out))[:30]


def _imminent_events(canon: CanonBundle, world: WorldState, *, horizon_days: int) -> list[str]:
    """Evenements canon prevus dans les prochains mois."""
    out: list[str] = []
    target_year_max = world.current_year + (horizon_days // 365 + 1)
    for ev in canon.timeline_events.values():
        if world.current_year <= ev.year <= target_year_max:
            out.append(f"an {ev.year} : {ev.name_fr}")
    return sorted(out)[:10]


def _build_constraints_fr(
    character: Character, age: int, can_leave: bool, combat_capable: bool
) -> list[str]:
    """Liste lisible des contraintes physiques/sociales actuelles."""
    out: list[str] = []
    out.append(f"Le joueur a {age} ans, rang {character.rank}, dans {character.current_village}.")
    if age < 4:
        out.append(
            "Trop jeune pour des actions complexes (entrainement leger, observation, parler aux parents)."
        )
    elif age < 6:
        out.append(
            "Trop jeune pour combattre ou se deplacer seul. Limite a son foyer et au quartier proche."
        )
    elif age < 12 and character.rank == "academy_student":
        out.append(
            "Eleve de l'academie : restreint au village, journee partagee entre cours et famille."
        )
    if not can_leave:
        out.append(
            "Ne peut PAS quitter le village seul. Toute action a l'exterieur necessite un tuteur."
        )
    if not combat_capable:
        out.append("Pas en etat de combat (trop jeune, blesse, ou non entraine).")
    if character.health.fatigue >= 75:
        out.append("Tres fatigue, doit se reposer avant d'entreprendre une action exigeante.")
    if character.chakra.current < character.chakra.max * 0.2:
        out.append("Chakra presque vide, techniques tres limitees jusqu'a recuperation.")
    return out


def format_scene_context_for_prompt(ctx: SceneContext) -> str:
    """Formate le contexte de scene pour injection dans le system prompt LLM."""
    lines: list[str] = []
    lines.append("[CONTEXTE FACTUEL DE LA SCENE - SOURCE DE VERITE]")
    lines.append("")
    lines.append(f"Date in-game : an {ctx.current_year}, {ctx.current_date}")
    lines.append(
        f"Joueur : {ctx.player_age} ans, {ctx.player_rank}, dans {ctx.player_village} ({ctx.player_location})"
    )
    lines.append("")
    lines.append("Contraintes actuelles (a RESPECTER ABSOLUMENT) :")
    for c in ctx.constraints_fr:
        lines.append(f"  - {c}")
    lines.append("")
    lines.append("PNJ canoniquement accessibles au joueur en ce moment :")
    if ctx.accessible_npcs:
        for npc in ctx.accessible_npcs:
            same = "meme village" if npc.is_in_same_village else f"autre village ({npc.village})"
            rank = npc.rank_in_canon or "rang inconnu"
            lines.append(
                f"  - {npc.name} (id: {npc.character_id}, {npc.age} ans, {rank}, {same}) - role: {npc.role_label}"
            )
    else:
        lines.append(
            "  (aucun PNJ canon accessible : les seules interactions sont avec des PNJ generiques)"
        )
    lines.append("")
    lines.append("Lieux accessibles au joueur :")
    lines.append("  " + ", ".join(ctx.accessible_locations))
    if ctx.notable_events_imminent:
        lines.append("")
        lines.append("Evenements canon a l'horizon (informatif, ne pas spoiler au joueur) :")
        for ev in ctx.notable_events_imminent:
            lines.append(f"  - {ev}")
    lines.append("")
    lines.append("REGLES STRICTES :")
    lines.append(
        "  - Tu ne dois PROPOSER ou MENTIONNER que des PNJ presents dans la liste ci-dessus."
    )
    lines.append(
        "  - Tu ne dois PAS proposer d'aller voir un perso situe dans un autre village si le joueur ne peut pas quitter."
    )
    lines.append(
        "  - Tu ne dois PAS proposer d'actions inadaptees a l'age et au rang du joueur (un eleve de 1 an ne peut pas se battre)."
    )
    lines.append(
        "  - Si le joueur tente une action incoherente, narre-la comme une impossibilite contextuelle, pas comme un succes."
    )
    return "\n".join(lines)


# Prefixes de roles generiques que le LLM peut inventer.
_GENERIC_ROLE_PREFIXES = (
    "marchand", "garde", "sensei", "etranger", "voisin", "client", "civilien",
    "moine", "soldat", "messager", "informateur", "anbu_anonyme", "bandit",
    "pere_", "mere_", "ami_", "rival_", "frere_", "soeur_", "oncle_", "tante_",
    "generic", "anonyme", "cousin_", "cousine_", "professeur", "instructeur",
)


def looks_like_generic_role(character_id: str) -> bool:
    """Vrai si l'id ressemble a un role generique invente par le LLM."""
    if not character_id:
        return False
    lower = character_id.lower()
    return any(lower.startswith(p) for p in _GENERIC_ROLE_PREFIXES)


def filter_proposed_actions(
    actions: list[dict],
    ctx: SceneContext,
) -> list[dict]:
    """Retire les proposed_actions qui referencent des PNJ canon inaccessibles.

    Strategie :
    - Si character_id est un PNJ accessible : ok.
    - Si character_id ressemble a un role generique (mere_du_perso, marchand_taverne) : ok.
    - Sinon : rejette (PNJ canon hors scene).
    """
    accessible_ids = ctx.npc_ids()
    out: list[dict] = []
    for action in actions:
        params = action.get("parameters") or {}
        cid = params.get("character_id") or params.get("target_id")
        if cid:
            if cid in accessible_ids or looks_like_generic_role(cid):
                out.append(action)
            # sinon on rejette silencieusement
            continue
        out.append(action)
    return out
