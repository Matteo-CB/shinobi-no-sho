"""Flux de creation de personnage en CLI avec choix de clan + kekkei genkai."""

from __future__ import annotations

import random

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from shinobi.canon.loader import load_canon
from shinobi.canon.profiles import CanonicityProfile
from shinobi.canon.queries import list_active_clans_in_village_at, list_villages
from shinobi.cli.display import COLOR_TITLE, banner
from shinobi.constants import YEAR_NARUTO_BIRTH
from shinobi.engine.character import ChakraState, Character, FamilyMember, FamilyState
from shinobi.engine.stats import CoreStats, ExtendedStats
from shinobi.engine.world import create_default_world
from shinobi.persistence import saves as save_module
from shinobi.types import Gender
from shinobi.utils.slug import slugify

console = Console()


# Liens clan -> kekkei genkai canoniques.
CLAN_KEKKEI: dict[str, list[str]] = {
    "uchiha": ["sharingan"],
    "hyuga": ["byakugan"],
    "senju": ["mokuton"],
    "kaguya": ["shikotsumyaku"],
    "yuki": ["hyouton"],
    "hozuki": ["hydrification"],
    "yamanaka": [],
    "nara": [],
    "akimichi": [],
    "inuzuka": [],
    "aburame": [],
    "uzumaki": [],
    "namikaze": [],
    "sarutobi": [],
    "hatake": [],
    "chinoike": ["ketsuryugan"],
    "kurama": ["genjutsu_kekkei"],
    "kohaku": [],
    "fuma": [],
    "iburi": [],
    "shimura": [],
}

# Liens clan -> natures privilegiees.
CLAN_NATURES: dict[str, list[str]] = {
    "uchiha": ["katon"],
    "hyuga": [],
    "senju": ["mokuton"],
    "yuki": ["hyouton"],
    "hozuki": ["suiton"],
    "namikaze": ["fuuton"],
    "uzumaki": ["fuuton", "youton_yang"],
    "kurama": ["inton"],
    "sarutobi": ["katon"],
    "hatake": ["raiton"],
}


def run_character_creation() -> str | None:
    """Flux complet de creation. Retourne le save_id cree, ou None si annulation."""
    console.print(banner("Creation de personnage", "Forge ton shinobi"))

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

    name = (
        Prompt.ask("[bold cyan]Nom du personnage[/bold cyan]", default="Endo Aburame").strip()
        or "Shinobi"
    )

    gender_choice = Prompt.ask(
        "[bold cyan]Genre[/bold cyan]",
        choices=["m", "f", "n"],
        default="m",
    )
    gender = (
        Gender.male
        if gender_choice == "m"
        else Gender.female
        if gender_choice == "f"
        else Gender.non_binary
    )

    starting_year = _pick_starting_year(canon)

    # Village
    villages = sorted(list_villages(canon), key=lambda v: v.id)
    if villages:
        village_id = _pick_village(villages)
    else:
        village_id = "konohagakure"

    # Clan filtre par village + ere ; auto-detection depuis le nom si possible
    clan_hint = _detect_clan_from_name(canon, name)
    clan_id = _pick_clan(canon, village_id, starting_year, hint=clan_hint)

    # Kekkei genkai (si clan en a, sinon optionnel rare)
    kekkei = _pick_kekkei_genkai(clan_id)

    # Natures
    natures = _pick_natures(clan_id)

    # Age + stats
    age_years = int(
        Prompt.ask(
            "[bold cyan]Age de depart[/bold cyan] [dim](annees, 1 = naissance)[/dim]", default="6"
        )
    )
    stats, extended, chakra_state = _roll_stats(name, starting_year, clan_id, kekkei, natures)

    # Famille
    family = _pick_family(clan_id)

    # Apercu
    _show_summary(
        name,
        gender,
        age_years,
        starting_year,
        village_id,
        clan_id,
        kekkei,
        natures,
        stats,
        extended,
    )

    if not typer.confirm("Confirmer la creation ?", default=True):
        console.print("[yellow]Creation annulee.[/yellow]")
        return None

    char_id = slugify(name) or "shinobi"
    rank = "academy_student" if age_years < 12 else "genin"

    character = Character(
        id=char_id,
        name=name,
        gender=gender,
        birth_year=starting_year - age_years,
        birth_date=f"{random.randint(1, 12):02d}-{random.randint(1, 28):02d}",
        age_years=age_years,
        village_of_origin=village_id,
        current_village=village_id,
        current_location=village_id,
        clan=clan_id,
        family=family,
        rank=rank,
        natures=natures,
        kekkei_genkai=kekkei,
        stats=stats,
        extended_stats=extended,
        chakra=chakra_state,
    )

    profile = CanonicityProfile.default()
    world = create_default_world(profile=profile, starting_year=starting_year)
    world = world.with_seed(world.seed & 0x7FFFFFFFFFFFFFFF)
    # Initialize le scheduler avec les events canon a venir
    from shinobi.engine.events import initialize_scheduler

    scheduled = initialize_scheduler(canon, starting_year=starting_year)
    world = world.model_copy(update={"scheduled_events": scheduled})

    declared_objective = Prompt.ask(
        "[bold cyan]Premier objectif[/bold cyan] [dim](texte libre, vide pour passer)[/dim]",
        default="",
    ).strip()
    if declared_objective:
        console.print(f"  [green]Objectif enregistre :[/green] {declared_objective}")

    save_id = save_module.create_save(
        character,
        world,
        canonicity_profile="default",
        thumbnail_summary=f"{name}, {age_years} ans, {rank} a {village_id}"
        + (f" ({clan_id} clan)" if clan_id else ""),
    )
    console.print(
        Panel.fit(
            f"[bold green]{name}[/bold green] vit a [cyan]{village_id}[/cyan] en l'an {starting_year}.\n\n"
            f"Save : [yellow]{save_id}[/yellow]\n\nLance la partie depuis le menu pour commencer.",
            title="Personnage cree",
            border_style="green",
        )
    )
    return save_id


def _pick_starting_year(canon) -> int:
    """Selection d'une ere canonique ou d'une annee personnalisee."""
    eras = sorted(canon.eras.values(), key=lambda e: e.year_start)
    if not eras:
        return int(
            Prompt.ask("[bold cyan]Annee de naissance[/bold cyan]", default=str(YEAR_NARUTO_BIRTH))
        )

    table = Table(title="Eres disponibles", header_style=COLOR_TITLE)
    table.add_column("#", style="bold cyan", justify="right")
    table.add_column("Ere", style="bold")
    table.add_column("Periode", justify="right")
    table.add_column("Description", style="dim")
    for i, era in enumerate(eras, start=1):
        end = str(era.year_end) if era.year_end is not None else "present"
        table.add_row(
            str(i),
            era.name_fr,
            f"an {era.year_start} a {end}",
            era.description_fr[:80] + ("..." if len(era.description_fr) > 80 else ""),
        )
    table.add_row("c", "[Annee personnalisee]", "[any]", "Saisis directement une annee")
    console.print(table)

    while True:
        choice = (
            Prompt.ask(
                "[bold cyan]Ere[/bold cyan] [dim](numero, id, ou 'c' pour annee custom)[/dim]",
                default=str(_default_era_index(eras)),
            )
            .strip()
            .lower()
        )
        if choice in ("c", "custom"):
            return int(
                Prompt.ask(
                    "[bold cyan]Annee de naissance[/bold cyan]", default=str(YEAR_NARUTO_BIRTH)
                )
            )
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(eras):
                return _year_within_era(eras[idx])
        except ValueError:
            pass
        for era in eras:
            if era.id == choice:
                return _year_within_era(era)
        console.print(f"[red]Choix invalide : {choice}[/red]")


def _default_era_index(eras) -> int:
    """Index de l'ere par defaut (l'ere de l'academie de Naruto)."""
    for i, era in enumerate(eras, start=1):
        if era.id == "naruto_academy_era":
            return i
    return 1


def _year_within_era(era) -> int:
    """Choisit une annee dans une ere : random ou saisie."""
    end = era.year_end if era.year_end is not None else era.year_start + 5
    console.print(
        f"  [cyan]{era.name_fr}[/cyan] couvre an {era.year_start} a "
        f"{end if era.year_end is not None else 'present'}."
    )
    sub = (
        Prompt.ask(
            "[bold cyan]Annee precise[/bold cyan] [dim](nombre, ou 'r' pour aleatoire dans l'ere)[/dim]",
            default="r",
        )
        .strip()
        .lower()
    )
    if sub in ("r", "random"):
        return random.randint(era.year_start, end)
    try:
        year = int(sub)
        return year
    except ValueError:
        console.print(f"[yellow]Saisie invalide, debut d'ere ({era.year_start}) utilise.[/yellow]")
        return era.year_start


def _pick_village(villages) -> str:
    """Selection de village avec table compacte."""
    table = Table(title="Villages disponibles", header_style=COLOR_TITLE, show_lines=False)
    table.add_column("#", style="bold cyan", justify="right")
    table.add_column("Id")
    table.add_column("Nom")
    for i, v in enumerate(villages, start=1):
        table.add_row(str(i), v.id, v.name_fr or v.name_romaji)
    console.print(table)
    while True:
        choice = Prompt.ask(
            "[bold cyan]Village[/bold cyan] [dim](numero ou id)[/dim]",
            default="konohagakure",
        ).strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(villages):
                return villages[idx].id
        except ValueError:
            pass
        if any(v.id == choice for v in villages):
            return choice
        console.print(f"[red]Choix invalide : {choice}[/red]")


def _detect_clan_from_name(canon, name: str) -> str | None:
    """Si le nom contient un nom de clan canonique, le retourne."""
    lower = name.lower()
    for clan_id in canon.clans:
        token = clan_id.replace("_", " ")
        if token in lower:
            return clan_id
    return None


def _pick_clan(
    canon, village_id: str, starting_year: int, *, hint: str | None = None
) -> str | None:
    """Selection de clan dans le village a cette ere."""
    clans = sorted(
        list_active_clans_in_village_at(canon, village_id, starting_year), key=lambda c: c.id
    )
    if not clans:
        console.print(
            f"[dim]Aucun clan actif a {village_id} en l'an {starting_year}. Tu seras civil.[/dim]"
        )
        return None

    if hint:
        console.print(f"[dim]Indice : ton nom suggere le clan [magenta]{hint}[/magenta][/dim]")
    default_choice = "0"
    if hint:
        for i, c in enumerate(clans, start=1):
            if c.id == hint:
                default_choice = str(i)
                break

    table = Table(
        title=f"Clans actifs a {village_id} (an {starting_year})", header_style=COLOR_TITLE
    )
    table.add_column("#", style="bold cyan", justify="right")
    table.add_column("Id")
    table.add_column("Kekkei genkai cle", style="magenta")
    table.add_column("Natures", style="cyan")
    for i, c in enumerate(clans, start=1):
        marker = " [yellow](*)[/yellow]" if c.id == hint else ""
        table.add_row(
            str(i),
            c.id + marker,
            ", ".join(c.key_kekkei_genkai) if c.key_kekkei_genkai else "(aucun)",
            ", ".join(c.key_natures) if c.key_natures else "(aucune)",
        )
    table.add_row("0", "[civil]", "(aucun)", "(aucune)")
    console.print(table)

    while True:
        choice = Prompt.ask(
            "[bold cyan]Clan[/bold cyan] [dim](numero, id, 0 pour civil, '?<n>' pour details)[/dim]",
            default=default_choice,
        ).strip()
        if choice in ("0", ""):
            return None
        if choice.startswith("?"):
            try:
                idx = int(choice[1:]) - 1
                if 0 <= idx < len(clans):
                    _show_clan_details(clans[idx])
                    continue
            except ValueError:
                pass
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(clans):
                return clans[idx].id
        except ValueError:
            pass
        if any(c.id == choice for c in clans):
            return choice
        console.print(f"[red]Choix invalide : {choice}[/red]")


def _show_clan_details(clan) -> None:
    """Affiche un panneau detaille avec avantages, inconvenients, techniques cles."""
    body_lines: list[str] = []
    if clan.history_summary_fr:
        excerpt = clan.history_summary_fr[:400]
        body_lines.append(
            f"[dim]{excerpt}{'...' if len(clan.history_summary_fr) > 400 else ''}[/dim]"
        )
        body_lines.append("")
    if clan.key_kekkei_genkai:
        body_lines.append(f"[magenta]Kekkei genkai :[/magenta] {', '.join(clan.key_kekkei_genkai)}")
    if clan.key_natures:
        body_lines.append(f"[cyan]Natures :[/cyan] {', '.join(clan.key_natures)}")
    if clan.key_advantages_fr:
        body_lines.append("")
        body_lines.append(f"[green]Avantages :[/green] {clan.key_advantages_fr}")
    if clan.key_disadvantages_fr:
        body_lines.append("")
        body_lines.append(f"[red]Inconvenients :[/red] {clan.key_disadvantages_fr}")
    if clan.key_techniques:
        body_lines.append("")
        sample = clan.key_techniques[:8]
        more = len(clan.key_techniques) - len(sample)
        body_lines.append(
            f"[yellow]Techniques cles :[/yellow] {', '.join(sample)}"
            + (f" [dim](et {more} autres)[/dim]" if more > 0 else "")
        )
    console.print(
        Panel("\n".join(body_lines), title=f"Clan {clan.name_romaji}", border_style="magenta")
    )


def _pick_kekkei_genkai(clan_id: str | None) -> list[str]:
    """Si clan a un kekkei genkai latent, propose au joueur."""
    if not clan_id:
        return []
    candidates = CLAN_KEKKEI.get(clan_id, [])
    if not candidates:
        return []
    console.print(
        Panel.fit(
            f"Le clan [cyan]{clan_id}[/cyan] possede un don hereditaire : [magenta]{', '.join(candidates)}[/magenta]",
            title="Kekkei genkai",
            border_style="magenta",
        )
    )
    inherit = typer.confirm(
        "Hereditairement, le don coule dans tes veines (latent ou actif). Veux-tu l'avoir ?",
        default=True,
    )
    return candidates if inherit else []


def _pick_natures(clan_id: str | None) -> list[str]:
    """Tirage des natures de chakra (1 ou 2 maximum a la naissance)."""
    pool = ["katon", "suiton", "fuuton", "doton", "raiton"]
    inherited = CLAN_NATURES.get(clan_id or "", []) if clan_id else []
    rng = random.Random()
    primary = inherited[0] if inherited else rng.choice(pool)
    natures = [primary]
    if rng.random() < 0.15:  # 15% chance d'avoir une seconde nature de naissance
        secondary = rng.choice([n for n in pool if n != primary])
        natures.append(secondary)
    console.print(f"  [green]Affinite naturelle :[/green] {', '.join(natures)}")
    return natures


def _pick_family(clan_id: str | None) -> FamilyState:
    """Famille : typique du clan ou orphelin."""
    choice = Prompt.ask(
        "[bold cyan]Statut familial[/bold cyan]",
        choices=["typique", "orphelin", "lignee"],
        default="typique",
    )
    if choice == "orphelin":
        return FamilyState(members=[])
    if choice == "lignee" and clan_id:
        return FamilyState(
            members=[
                FamilyMember(
                    relationship_label="pere", character_id=f"{clan_id}_father", is_alive=True
                ),
                FamilyMember(
                    relationship_label="mere", character_id=f"{clan_id}_mother", is_alive=True
                ),
                FamilyMember(
                    relationship_label="ancetre", character_id=f"{clan_id}_elder", is_alive=True
                ),
            ]
        )
    return FamilyState(
        members=[
            FamilyMember(
                relationship_label="pere",
                character_id=f"{clan_id or 'civilian'}_father",
                is_alive=True,
            ),
            FamilyMember(
                relationship_label="mere",
                character_id=f"{clan_id or 'civilian'}_mother",
                is_alive=True,
            ),
        ]
    )


def _roll_stats(
    seed_text: str,
    year: int,
    clan_id: str | None,
    kekkei: list[str],
    natures: list[str],
) -> tuple[CoreStats, ExtendedStats, ChakraState]:
    """Tirage des stats avec biais selon clan / kekkei genkai."""
    rng = random.Random(f"{seed_text}|{year}|{clan_id}")
    base = lambda: round(rng.uniform(0.8, 2.5), 1)

    ninjutsu = base()
    taijutsu = base()
    genjutsu = base()
    intelligence = base()
    strength = base()
    speed = base()
    stamina = base()
    hand_seals = base()
    chakra_pool_max = int(rng.uniform(80, 200))
    chakra_control = base()
    learning_genius = base()
    social_charisma = base()
    leadership = base()
    luck = base()
    beauty = base()
    lineage_value = round(rng.uniform(1.0, 4.0), 1) if clan_id else 1.0
    willpower = base()
    perception = base()

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


def _show_summary(
    name: str,
    gender: Gender,
    age: int,
    year: int,
    village: str,
    clan: str | None,
    kekkei: list[str],
    natures: list[str],
    stats: CoreStats,
    extended: ExtendedStats,
) -> None:
    """Recap de la creation avant validation."""
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", justify="right")
    table.add_column()
    table.add_row("Nom", name)
    table.add_row("Genre", gender.value)
    table.add_row("Age", f"{age} ans")
    table.add_row("Annee", f"an {year}")
    table.add_row("Village", village)
    if clan:
        table.add_row("Clan", clan)
    if kekkei:
        table.add_row("Kekkei genkai", ", ".join(kekkei))
    table.add_row("Natures", ", ".join(natures))
    table.add_row(
        "Stats",
        f"NIN {stats.ninjutsu:.1f} TAI {stats.taijutsu:.1f} GEN {stats.genjutsu:.1f} "
        f"INT {stats.intelligence:.1f} STR {stats.strength:.1f} SPD {stats.speed:.1f} "
        f"STA {stats.stamina:.1f} HS {stats.hand_seals:.1f}",
    )
    table.add_row("Chakra max", str(extended.chakra_pool_max))
    table.add_row("Lignee", f"{extended.lineage_value:.1f}")
    console.print(Panel(table, title="Recap", border_style="green"))
