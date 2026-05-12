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
from shinobi.i18n import t
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
    """Flux complet de creation. Retourne le save_id cree, ou None si annulation.

    Demande d'abord le mode :
    1. Nouveau perso (random + biais clan/kekkei) - flow original
    2. Incarner un perso canon (Itachi, Naruto, Sasuke, ...) a un age choisi
    """
    console.print(banner(t("cli.character_creation.banner_title"), t("cli.character_creation.banner_subtitle")))

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

    # Mode selection : nouveau perso (random) OU incarner canon
    console.print(t("cli.character_creation.mode.intro"))
    mode = Prompt.ask(
        f"[bold cyan]{t('cli.character_creation.choice_prompt')}[/bold cyan]",
        choices=["1", "2"],
        default="1",
    ).strip()

    if mode == "2":
        return _run_canon_incarnation_flow(canon)
    return _run_original_creation_flow(canon)


def _run_original_creation_flow(canon) -> str | None:
    """Flow original : creation random avec biais clan/village. Garde l'API
    de l'ancien run_character_creation (renommage pour clarte).
    """

    name = (
        Prompt.ask(
            f"[bold cyan]{t('cli.character_creation.name_prompt')}[/bold cyan]",
            default="Endo Aburame",
        ).strip()
        or "Shinobi"
    )

    gender_choice = Prompt.ask(
        f"[bold cyan]{t('cli.character_creation.gender_prompt')}[/bold cyan]",
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

    # Tirage rare : kekkei mora ou jinchuuriki (chances tres faibles)
    kekkei_mora, tailed_beast = _roll_rare_gifts(canon, name, starting_year)

    # Natures
    natures = _pick_natures(clan_id)

    # Age + stats
    age_years = _pick_age_years()
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
        kekkei_mora=kekkei_mora,
        tailed_beast=tailed_beast,
    )

    if not typer.confirm(t("cli.character_creation.confirm_creation"), default=True):
        console.print(f"[yellow]{t('cli.character_creation.creation_cancelled')}[/yellow]")
        return None

    char_id = slugify(name) or "shinobi"
    rank = _rank_from_age(age_years)

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
        kekkei_mora=kekkei_mora,
        tailed_beast=tailed_beast,
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
        t("cli.character_creation.first_objective_prompt"),
        default="",
    ).strip()
    if declared_objective:
        console.print(f"  [green]Objectif enregistre :[/green] {declared_objective}")

    save_id = save_module.create_save(
        character,
        world,
        canonicity_profile="default",
        thumbnail_summary=t(
            "cli.character_creation.thumbnail_summary",
            name=name, age=age_years, rank=rank, village=village_id,
        ) + (f" ({clan_id} clan)" if clan_id else ""),
    )
    console.print(
        Panel.fit(
            t(
                "cli.character_creation.created_panel_intro",
                name=name, village=village_id, year=starting_year,
            )
            + "\n\n"
            + t("cli.character_creation.created_panel_body", save_id=save_id),
            title=t("cli.character_creation.created_panel_title"),
            border_style="green",
        )
    )
    return save_id


def _run_canon_incarnation_flow(canon) -> str | None:
    """Flow d'incarnation : selectionner un canon character + age et hydrater.

    Phase 6.3 wiring : delegue la construction du Character a
    `canon_incarnation.incarnate_canon_character`. Le world est positionne
    a current_year = canon.birth_year + age choisi, le scheduler initialise
    avec les events canon a cette date.
    """
    from shinobi.cli.canon_incarnation import (
        incarnate_canon_character,
        list_playable_canon_characters,
    )

    console.print(
        Panel.fit(
            t("cli.character_creation.canon_incarnation.intro"),
            title=t("cli.character_creation.canon_incarnation.title"),
            border_style="cyan",
        )
    )

    # Selection optionnelle d'un village pour filter
    village_filter: str | None = None
    village_choice = Prompt.ask(
        f"[cyan]{t('cli.character_creation.filter_village_prompt')}[/cyan]",
        default="",
    ).strip().lower() or None
    if village_choice:
        village_filter = village_choice

    playable = list_playable_canon_characters(
        canon, village_filter=village_filter,
    )
    if not playable:
        console.print(f"[red]{t('cli.character_creation.no_canon_for_filter')}[/red]")
        return None

    # Affiche les top 30 par notoriete
    table = Table(
        title=t("cli.character_creation.canon_table_title"),
        header_style="bold magenta",
    )
    table.add_column("#", style="bold cyan", justify="right")
    table.add_column("Id", style="bold")
    table.add_column("Clan")
    table.add_column("Village")
    table.add_column("Birth", justify="right")
    table.add_column("Death", justify="right")
    for i, c in enumerate(playable[:30], start=1):
        death = str(c.death_year) if c.death_year is not None else "-"
        table.add_row(
            str(i),
            c.id,
            c.clan or "-",
            c.village_of_origin or "-",
            str(c.birth_year),
            death,
        )
    console.print(table)
    console.print(
        f"[dim]({len(playable) - 30} autres disponibles, tape leur id "
        "directement)[/dim]\n"
        if len(playable) > 30 else ""
    )

    selection = Prompt.ask(
        t("cli.character_creation.id_prompt"),
        default="1",
    ).strip()
    canon_id: str | None = None
    # 1. Tente comme numero du top 30
    try:
        idx = int(selection) - 1
        if 0 <= idx < len(playable[:30]):
            canon_id = playable[idx].id
    except ValueError:
        pass

    # 2. Sinon, resolution fuzzy par id/name_romaji/name_fr
    if canon_id is None:
        from shinobi.cli.canon_incarnation import resolve_canon_id
        canon_id, candidates = resolve_canon_id(canon, selection)
        if canon_id is None:
            if candidates:
                # Ambiguite : afficher les matches
                console.print(t("cli.character_creation.ambiguous_match", query=selection))
                for cid in candidates[:10]:
                    char = canon.characters[cid]
                    name = (
                        char.name_romaji or char.name_fr or cid
                    )
                    console.print(f"  - [cyan]{cid}[/cyan] ({name})")
                console.print("\n" + t("cli.character_creation.relaunch_with_id"))
            else:
                console.print(
                    f"[red]{t('cli.character_creation.canon_not_found', selection=selection)}[/red]"
                )
            return None
        # Verifier que le perso choisi est jouable (a un birth_year)
        chosen_check = canon.characters[canon_id]
        if chosen_check.birth_year is None:
            console.print(
                t("cli.character_creation.canon_no_birth_year", canon_id=canon_id)
            )
            return None
        console.print(
            t("cli.character_creation.canon_found", id=canon_id)
        )

    chosen = canon.characters[canon_id]
    age_max = (
        chosen.death_year - chosen.birth_year - 1
        if chosen.death_year is not None
        else 80
    )
    age_default = min(13, max(6, age_max))
    age_str = Prompt.ask(
        t(
            "cli.character_creation.age_prompt_with_meta",
            chosen_birth_year=chosen.birth_year,
            chosen_death_year=chosen.death_year or "?",
        ),
        default=str(age_default),
    ).strip()
    try:
        age_at_start = int(age_str)
    except ValueError:
        console.print(
            f"[red]{t('cli.character_creation.invalid_age', age_str=age_str)}[/red]"
        )
        return None
    if age_at_start < 0:
        age_at_start = 0
    if age_at_start > age_max:
        console.print(
            f"[yellow]{t('cli.character_creation.age_capped', age_max=age_max, chosen_death_year=chosen.death_year)}[/yellow]"
        )
        age_at_start = age_max

    character, current_year = incarnate_canon_character(
        canon, canon_id, age_at_start,
    )

    # Show summary
    console.print(
        Panel.fit(
            f"[bold]{character.name}[/bold] ({canon_id})\n"
            f"  Age : {character.age_years} ans\n"
            f"  Annee in-game : {current_year}\n"
            f"  Village : {character.current_village}\n"
            f"  Clan : {character.clan or 'civil'}\n"
            f"  Rang : {character.rank}\n"
            f"  Natures : {', '.join(character.natures) or 'aucune'}\n"
            f"  Kekkei genkai : "
            f"{', '.join(character.kekkei_genkai) or 'aucun'}\n"
            f"  Techniques connues : {len(character.techniques_known)}\n"
            f"  Stats : ninjutsu={character.stats.ninjutsu:.1f} "
            f"genjutsu={character.stats.genjutsu:.1f} "
            f"taijutsu={character.stats.taijutsu:.1f}",
            title=t("cli.character_creation.canon_recap_title"),
            border_style="green",
        )
    )

    if not typer.confirm(t("cli.character_creation.confirm_incarnation"), default=True):
        console.print(f"[yellow]{t('cli.character_creation.incarnation_cancelled')}[/yellow]")
        return None

    # Build le world a current_year
    profile = CanonicityProfile.default()
    world = create_default_world(profile=profile, starting_year=current_year)
    world = world.with_seed(world.seed & 0x7FFFFFFFFFFFFFFF)
    from shinobi.engine.events import initialize_scheduler
    scheduled = initialize_scheduler(canon, starting_year=current_year)
    world = world.model_copy(update={"scheduled_events": scheduled})

    save_id = save_module.create_save(
        character,
        world,
        canonicity_profile="default",
        thumbnail_summary=(
            f"{character.name} ({canon_id}), {character.age_years} ans, "
            f"{character.rank} a {character.current_village}, "
            f"incarnation canon"
        ),
    )
    console.print(
        Panel.fit(
            t(
                "cli.character_creation.incarnation_summary_panel",
                name=character.name,
                village=character.current_village,
                year=current_year,
            ),
            title=t("cli.character_creation.incarnation_success_title"),
            border_style="green",
        )
    )
    return save_id


def _pick_starting_year(canon) -> int:
    """Selection d'une ere canonique ou d'une annee personnalisee."""
    eras = sorted(canon.eras.values(), key=lambda e: e.year_start)
    if not eras:
        return int(
            Prompt.ask(
                f"[bold cyan]{t('cli.character_creation.birth_year_prompt')}[/bold cyan]",
                default=str(YEAR_NARUTO_BIRTH),
            )
        )

    table = Table(title="Eres disponibles", header_style=COLOR_TITLE)
    table.add_column("#", style="bold cyan", justify="right")
    table.add_column(t("cli.character_creation.era.col_era"), style="bold")
    table.add_column(t("cli.character_creation.era.col_period"), justify="right")
    table.add_column(t("cli.character_creation.era.col_description"), style="dim")
    for i, era in enumerate(eras, start=1):
        end = str(era.year_end) if era.year_end is not None else "present"
        table.add_row(
            str(i),
            era.name_fr,
            t("cli.character_creation.era_period_value", start=era.year_start, end=end),
            era.description_fr[:80] + ("..." if len(era.description_fr) > 80 else ""),
        )
    table.add_row("c", t("cli.character_creation.era.custom_year_marker"), "[any]", "Saisis directement une annee")
    console.print(table)

    while True:
        choice = (
            Prompt.ask(
                f"[bold cyan]{t('cli.character_creation.era.prompt')}[/bold cyan]",
                default=str(_default_era_index(eras)),
            )
            .strip()
            .lower()
        )
        if choice in ("c", "custom"):
            return int(
                Prompt.ask(
                    f"[bold cyan]{t('cli.character_creation.birth_year_prompt')}[/bold cyan]",
                    default=str(YEAR_NARUTO_BIRTH),
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
        console.print(f"[red]{t('cli.character_creation.invalid_choice', choice=choice)}[/red]")


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
            t("cli.character_creation.precise_year_prompt"),
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
    table.add_column(t("cli.character_creation.village.col_id"))
    table.add_column(t("cli.character_creation.village.col_name"))
    for i, v in enumerate(villages, start=1):
        table.add_row(str(i), v.id, v.name_fr or v.name_romaji)
    console.print(table)
    while True:
        choice = Prompt.ask(
            f"[bold cyan]{t('cli.character_creation.village.prompt')}[/bold cyan]",
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
        console.print(f"[red]{t('cli.character_creation.invalid_choice', choice=choice)}[/red]")


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
            t("cli.character_creation.no_active_clan", village_id=village_id, starting_year=starting_year)
        )
        return None

    if hint:
        console.print(t("cli.character_creation.clan_hint_from_name", clan=hint))
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
            f"[bold cyan]{t('cli.character_creation.clan.prompt')}[/bold cyan]",
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
        console.print(f"[red]{t('cli.character_creation.invalid_choice', choice=choice)}[/red]")


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
        body_lines.append(t("cli.character_creation.clan_disadvantages", disadvantages=clan.key_disadvantages_fr))
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
            t("cli.character_creation.clan_inheritance", clan_id=clan_id, candidates=", ".join(candidates)),
            title="Kekkei genkai",
            border_style="magenta",
        )
    )
    inherit = typer.confirm(
        t("cli.character_creation.inheritance_question"),
        default=True,
    )
    return candidates if inherit else []


def _roll_rare_gifts(canon, seed_text: str, year: int) -> tuple[list[str], str | None]:
    """Tirage extremement rare : kekkei mora (~0.3%) ou jinchuuriki (~0.5% si bijuu libre).

    Si tirage favorable, propose au joueur d'accepter le don avec sa malediction.
    """
    rng = random.Random(f"rare|{seed_text}|{year}")
    kekkei_mora: list[str] = []
    tailed_beast: str | None = None

    mora_pool = list(canon.kekkei_mora.keys()) if canon.kekkei_mora else []
    if mora_pool and rng.random() < 0.003:
        candidate = rng.choice(mora_pool)
        console.print(
            Panel.fit(
                t("cli.character_creation.kekkei_mora_detected", candidate=candidate)
                + "\n"
                + t("cli.character_creation.kekkei_mora_intro"),
                title=t("cli.character_creation.rare_draw_title"),
                border_style="magenta",
            )
        )
        if typer.confirm(t("cli.character_creation.accept_gift_prompt"), default=True):
            kekkei_mora = [candidate]

    if canon.tailed_beasts and rng.random() < 0.005:
        # Cherche un bijuu libre (pas de jinchuuriki canon assigne a cette annee)
        free_beasts: list[str] = []
        for beast_id, beast in canon.tailed_beasts.items():
            held = False
            for entry in beast.current_jinchuuriki_by_era:
                if entry.from_year <= year and (
                    entry.to_year is None or year < entry.to_year
                ):
                    if entry.jinchuuriki:
                        held = True
                        break
            if not held:
                free_beasts.append(beast_id)
        if free_beasts:
            chosen_beast = rng.choice(free_beasts)
            console.print(
                Panel.fit(
                    t("cli.character_creation.jinchuuriki_intro", beast=chosen_beast),
                    title="Destin lourd",
                    border_style="red",
                )
            )
            if typer.confirm("Accepter ce destin ?", default=True):
                tailed_beast = chosen_beast

    return kekkei_mora, tailed_beast


def _pick_age_years() -> int:
    """Choix d'age avec warning si choix narrativement absurde (<6 = avant l'academie)."""
    while True:
        raw = Prompt.ask(
            t("cli.character_creation.start_age_prompt"),
            default="8",
        ).strip()
        try:
            age = int(raw)
        except ValueError:
            console.print(f"[red]{t('cli.character_creation.invalid_age_raw', raw=raw)}[/red]")
            continue
        if age < 0 or age > 100:
            console.print(f"[red]{t('cli.character_creation.age_unrealistic')}[/red]")
            continue
        if age < 6:
            console.print(
                Panel.fit(
                    t(
                        "cli.character_creation.too_young_warn",
                        age=age,
                        plural="s" if age > 1 else "",
                    ),
                    title=t("cli.character_creation.unusual_choice_title"),
                    border_style="yellow",
                )
            )
            if not typer.confirm(t("cli.character_creation.confirm_choice"), default=False):
                continue
        return age


def _rank_from_age(age_years: int) -> str:
    """Rang canonique impose par l'age. Le joueur monte ensuite via les missions."""
    if age_years < 6:
        return "civilian"
    if age_years < 12:
        return "academy_student"
    return "genin"


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
        f"[bold cyan]{t('cli.character_creation.family.status_prompt')}[/bold cyan]",
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
    *,
    kekkei_mora: list[str] | None = None,
    tailed_beast: str | None = None,
) -> None:
    """Recap de la creation avant validation."""
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", justify="right")
    table.add_column()
    table.add_row(t("cli.character_creation.summary.col_label_name"), name)
    table.add_row(t("cli.character_creation.summary.col_label_gender"), gender.value)
    table.add_row(
        t("cli.character_creation.summary.col_label_age"),
        t("cli.display.label.age_with_value", age=age),
    )
    table.add_row(t("cli.character_creation.summary.col_label_year"), f"an {year}")
    table.add_row(t("cli.display.label.village"), village)
    if clan:
        table.add_row(t("cli.display.label.clan"), clan)
    if kekkei:
        table.add_row(t("cli.display.label.kekkei_genkai"), ", ".join(kekkei))
    if kekkei_mora:
        table.add_row("Kekkei mora", ", ".join(kekkei_mora))
    if tailed_beast:
        table.add_row("Tailed beast", tailed_beast)
    table.add_row(t("cli.display.label.natures"), ", ".join(natures))
    table.add_row(
        t("cli.character_creation.summary.stats_label"),
        f"NIN {stats.ninjutsu:.1f} TAI {stats.taijutsu:.1f} GEN {stats.genjutsu:.1f} "
        f"INT {stats.intelligence:.1f} STR {stats.strength:.1f} SPD {stats.speed:.1f} "
        f"STA {stats.stamina:.1f} HS {stats.hand_seals:.1f}",
    )
    table.add_row(t("cli.character_creation.summary.chakra_max"), str(extended.chakra_pool_max))
    table.add_row(t("cli.character_creation.summary.lineage"), f"{extended.lineage_value:.1f}")
    console.print(
        Panel(
            table,
            title=t("cli.character_creation.summary.title"),
            border_style="green",
        )
    )
