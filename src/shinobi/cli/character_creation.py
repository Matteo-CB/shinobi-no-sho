"""Flux de creation de personnage en CLI."""

from __future__ import annotations

import random

import typer
from rich.console import Console
from rich.panel import Panel

from shinobi.canon.loader import load_canon
from shinobi.canon.profiles import CanonicityProfile
from shinobi.canon.queries import list_active_clans_in_village_at, list_villages
from shinobi.constants import YEAR_NARUTO_BIRTH
from shinobi.engine.character import Character
from shinobi.engine.stats import CoreStats, ExtendedStats
from shinobi.engine.world import create_default_world
from shinobi.persistence import saves as save_module
from shinobi.types import Gender
from shinobi.utils.slug import slugify

console = Console()


def run_character_creation() -> str:
    """Flux complet de creation. Retourne le save_id cree."""
    console.print(
        Panel.fit(
            "Creation d'un nouveau personnage de Shinobi no Sho.",
            title="Creation",
        )
    )

    canon = load_canon(
        optional=(
            "characters",
            "techniques",
            "clans",
            "villages",
            "organizations",
            "tailed_beasts",
            "kekkei_genkai",
            "kekkei_mora",
            "hiden",
            "weapons_tools",
            "locations",
            "timeline_events",
            "voice_profiles",
        )
    )

    name = typer.prompt("Nom du personnage").strip()
    if not name:
        name = "Shinobi sans nom"

    gender_str = typer.prompt("Genre (m/f/n)", default="m").strip().lower()
    gender = (
        Gender.male
        if gender_str.startswith("m")
        else Gender.female
        if gender_str.startswith("f")
        else Gender.non_binary
    )

    starting_year = int(
        typer.prompt(
            "Annee de naissance (an 1 = naissance de Naruto)",
            default=str(YEAR_NARUTO_BIRTH),
        )
    )

    villages = list_villages(canon)
    if villages:
        console.print("Villages disponibles :")
        for i, v in enumerate(villages, start=1):
            console.print(f"  {i}. {v.id} ({v.name_fr})")
        v_choice = typer.prompt("Village (numero ou id)", default=villages[0].id).strip()
        try:
            idx = int(v_choice) - 1
            village_id = villages[idx].id
        except ValueError:
            village_id = v_choice
    else:
        village_id = "konohagakure"

    clans = list_active_clans_in_village_at(canon, village_id, starting_year)
    clan_id: str | None = None
    if clans:
        console.print("Clans disponibles dans ce village a cette annee :")
        for i, c in enumerate(clans, start=1):
            console.print(f"  {i}. {c.id} ({c.name_romaji})")
        console.print("  0. Aucun (civil)")
        c_choice = typer.prompt("Clan (numero ou id)", default="0").strip()
        if c_choice not in ("0", ""):
            try:
                idx = int(c_choice) - 1
                clan_id = clans[idx].id if 0 <= idx < len(clans) else c_choice
            except ValueError:
                clan_id = c_choice

    age_years = int(typer.prompt("Age de depart (annees)", default="6"))

    rng = random.Random(name + str(starting_year))
    base_stat = lambda: round(rng.uniform(0.5, 2.5), 1)

    stats = CoreStats(
        ninjutsu=base_stat(),
        taijutsu=base_stat(),
        genjutsu=base_stat(),
        intelligence=base_stat(),
        strength=base_stat(),
        speed=base_stat(),
        stamina=base_stat(),
        hand_seals=base_stat(),
    )
    extended = ExtendedStats(
        chakra_pool_max=int(rng.uniform(80, 200)),
        chakra_control=base_stat(),
        learning_genius=base_stat(),
        social_charisma=base_stat(),
        leadership=base_stat(),
        luck=base_stat(),
        beauty=base_stat(),
        lineage_value=base_stat(),
        willpower=base_stat(),
        perception=base_stat(),
    )

    char_id = slugify(name)
    character = Character(
        id=char_id,
        name=name,
        gender=gender,
        birth_year=starting_year - age_years,
        birth_date=f"{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
        age_years=age_years,
        village_of_origin=village_id,
        current_village=village_id,
        current_location=village_id,
        clan=clan_id,
        rank="academy_student" if age_years < 12 else "genin",
        stats=stats,
        extended_stats=extended,
    )

    profile = CanonicityProfile.from_csv(
        ",".join(
            (canon.eras and ["manga", "boruto_manga", "tbv", "databook", "movie_canon"]) or []
        ),
        label="default",
    )
    world = create_default_world(profile=profile, starting_year=starting_year)

    declared_objective = typer.prompt(
        "Premier objectif de vie (texte libre, ou vide pour passer)",
        default="",
    ).strip()
    if declared_objective:
        console.print(f"Objectif enregistre : {declared_objective}")

    save_id = save_module.create_save(
        character,
        world,
        canonicity_profile="default",
        thumbnail_summary=f"{name}, {age_years} ans, {character.rank} a {village_id}",
    )
    console.print(
        Panel.fit(
            f"Save creee : {save_id}\nLance `shinobi play` pour commencer.",
            title="Pret",
        )
    )
    return save_id
