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
from shinobi.i18n import t

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
        return t("engine.scene_context.role.clan_member", clan=canon_char.clan)
    return t("engine.scene_context.role.shinobi")


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
            out.append(t("engine.scene_context.event_year_label", year=ev.year, name=ev.name_fr))
    return sorted(out)[:10]


def _build_constraints_fr(
    character: Character, age: int, can_leave: bool, combat_capable: bool
) -> list[str]:
    """Liste lisible des contraintes physiques/sociales actuelles."""
    out: list[str] = []
    out.append(
        t(
            "engine.scene_context.constraint.player_summary",
            age=age,
            rank=character.rank,
            village=character.current_village,
        )
    )
    if age < 4:
        out.append(t("engine.scene_context.constraint.too_young_complex"))
    elif age < 6:
        out.append(t("engine.scene_context.constraint.too_young_combat"))
    elif age < 12 and character.rank == "academy_student":
        out.append(t("engine.scene_context.constraint.academy_student"))
    if not can_leave:
        out.append(t("engine.scene_context.constraint.cannot_leave_village"))
    if not combat_capable:
        out.append(t("engine.scene_context.constraint.not_combat_capable"))
    if character.health.fatigue >= 75:
        out.append(t("engine.scene_context.constraint.too_fatigued"))
    if character.chakra.current < character.chakra.max * 0.2:
        out.append(t("engine.scene_context.constraint.low_chakra"))
    return out


def format_scene_context_for_prompt(ctx: SceneContext) -> str:
    """Formate le contexte de scene pour injection dans le system prompt LLM."""
    lines: list[str] = []
    lines.append(t("engine.scene_context.prompt.header"))
    lines.append("")
    lines.append(
        t(
            "engine.scene_context.prompt.date_line",
            year=ctx.current_year,
            date=ctx.current_date,
        )
    )
    lines.append(
        t(
            "engine.scene_context.prompt.player_line",
            age=ctx.player_age,
            rank=ctx.player_rank,
            village=ctx.player_village,
            location=ctx.player_location,
        )
    )
    lines.append("")
    lines.append(t("engine.scene_context.prompt.constraints_header"))
    for c in ctx.constraints_fr:
        lines.append(f"  - {c}")
    lines.append("")
    lines.append(t("engine.scene_context.prompt.accessible_npcs_header"))
    if ctx.accessible_npcs:
        for npc in ctx.accessible_npcs:
            same = (
                t("engine.scene_context.prompt.same_village")
                if npc.is_in_same_village
                else t("engine.scene_context.prompt.other_village", village=npc.village)
            )
            rank = npc.rank_in_canon or t("engine.scene_context.prompt.rank_unknown")
            lines.append(
                t(
                    "engine.scene_context.prompt.npc_entry",
                    name=npc.name,
                    npc_id=npc.character_id,
                    age=npc.age,
                    rank=rank,
                    same=same,
                    role=npc.role_label,
                )
            )
    else:
        lines.append("  " + t("engine.scene_context.prompt.no_accessible_npc"))
    lines.append("")
    lines.append(t("engine.scene_context.prompt.accessible_locations_header"))
    lines.append("  " + ", ".join(ctx.accessible_locations))
    if ctx.notable_events_imminent:
        lines.append("")
        lines.append(t("engine.scene_context.prompt.notable_events_header"))
        for ev in ctx.notable_events_imminent:
            lines.append(f"  - {ev}")
    lines.append("")
    lines.append(t("engine.scene_context.prompt.rules_header"))
    lines.append("  - " + t("engine.scene_context.prompt.rule_only_mention_npcs"))
    lines.append("  - " + t("engine.scene_context.prompt.rule_no_other_village_npc"))
    lines.append("  - " + t("engine.scene_context.prompt.rule_age_appropriate"))
    lines.append("  - " + t("engine.scene_context.prompt.rule_incoherent_action"))
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


def build_inaccessible_canon_tokens(canon: CanonBundle, ctx: SceneContext) -> set[str]:
    """Construit l'ensemble des tokens de noms de PNJ canon NON accessibles.

    Un token = mot du nom (au moins 5 caracteres) qui n'est pas un nom de clan,
    village, ou mot generique. Sert a detecter dans les labels les references a
    des persos hors scene.

    Exclus :
    - tous les noms de clan (Uchiha, Senju, Hyuga...) qui apparaissent dans plein
      de noms et dans des contextes legitimes (quartier Uchiha, clan Hyuga...)
    - tous les noms de village
    - les mots generiques (sensei, hokage, ninja...)
    - les noms de PNJ accessibles (sinon on bloquerait des actions valides)
    """
    accessible_ids = ctx.npc_ids()
    clan_names = {cid.lower() for cid in canon.clans}
    village_names = {vid.lower() for vid in canon.villages}
    village_names |= {vid.lower().replace("gakure", "") for vid in canon.villages}
    accessible_name_parts: set[str] = set()
    for npc in ctx.accessible_npcs:
        for part in (npc.name or "").lower().split():
            if len(part) >= 4:
                accessible_name_parts.add(part.strip(".,;:!?'\"()-"))
    common_words = {
        "shinobi", "ninja", "hokage", "kage", "sensei", "clan", "village",
        "sannin", "anbu", "genin", "chunin", "jonin", "kunoichi", "academie",
        "academy", "konoha", "naruto",  # naruto = nom de la serie + perso, ambigu
        "quartier", "domaine", "complexe", "famille", "frere", "soeur",
        "pere", "mere", "fils", "fille", "ami", "rival", "voisin", "marchand",
    }
    excluded = clan_names | village_names | common_words | accessible_name_parts

    tokens: set[str] = set()
    for char_id, char in canon.characters.items():
        if char_id in accessible_ids:
            continue
        full = (char.name_romaji or "").lower()
        if len(full) < 5:
            continue
        # Nom complet (ex: "uchiha itachi") : tres specifique, on l'ajoute si suffisamment long
        if " " in full and len(full) >= 8:
            tokens.add(full)
        # Composantes uniques de 5+ chars
        for part in full.split():
            p = part.strip(".,;:!?'\"()-")
            if len(p) >= 5 and p not in excluded:
                tokens.add(p)
    return tokens


def action_references_inaccessible_npc(
    action: dict,
    ctx: SceneContext,
    inaccessible_tokens: set[str],
) -> bool:
    """Detecte si une action proposee mentionne un PNJ canon hors scene."""
    accessible_ids = ctx.npc_ids()
    params = action.get("parameters") or {}
    cid = params.get("character_id") or params.get("target_id")
    if cid:
        if cid not in accessible_ids and not looks_like_generic_role(cid):
            return True
    label = (action.get("label_fr") or "").lower()
    if not label:
        return False
    # Scanner pour des tokens inaccessibles
    label_padded = " " + label + " "
    for token in inaccessible_tokens:
        if " " in token:
            if token in label:
                return True
        else:
            if f" {token} " in label_padded or f" {token}." in label or f" {token}," in label:
                return True
    return False


def filter_proposed_actions(
    actions: list[dict],
    ctx: SceneContext,
    *,
    canon: CanonBundle | None = None,
) -> list[dict]:
    """Retire les proposed_actions qui referencent des PNJ canon hors scene.

    Strategie en 2 couches :
    - parameters.character_id : doit etre accessible, ou un id role-based generique
    - label_fr : ne doit pas contenir de nom de PNJ canon non accessible
      (necessite que canon soit fourni)
    """
    inaccessible = build_inaccessible_canon_tokens(canon, ctx) if canon is not None else set()
    return [
        a for a in actions
        if not action_references_inaccessible_npc(a, ctx, inaccessible)
    ]
