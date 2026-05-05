"""Helpers d'affichage rich pour la CLI : panels, tables, barres de progression."""

from __future__ import annotations

from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from shinobi.engine.character import Character
from shinobi.engine.world import WorldState

# Palette de couleurs coherentes pour la CLI.
COLOR_TITLE = "bold magenta"
COLOR_OK = "bold green"
COLOR_WARN = "bold yellow"
COLOR_BAD = "bold red"
COLOR_INFO = "cyan"
COLOR_DIM = "grey50"


def banner(title: str, subtitle: str = "") -> Panel:
    """Panneau ban titre."""
    text = Text()
    text.append(title, style=COLOR_TITLE)
    if subtitle:
        text.append("\n" + subtitle, style=COLOR_DIM)
    return Panel(Align.center(text), border_style="magenta", padding=(1, 4))


def _bar(current: int, maximum: int, width: int = 20, color: str = "green") -> Text:
    """Barre de progression compacte."""
    if maximum <= 0:
        ratio = 0.0
    else:
        ratio = max(0.0, min(1.0, current / maximum))
    filled = round(ratio * width)
    text = Text()
    text.append("[", style=COLOR_DIM)
    text.append("#" * filled, style=color)
    text.append("." * (width - filled), style=COLOR_DIM)
    text.append("]", style=COLOR_DIM)
    text.append(f" {current}/{maximum}", style="white")
    return text


def status_panel(character: Character, world: WorldState) -> Panel:
    """Panneau global de statut : header + stats + chakra/hp + ressources."""
    header = Table.grid(padding=(0, 2), expand=False)
    header.add_column(style=COLOR_DIM, justify="right")
    header.add_column(style="bold")
    header.add_row("Nom", character.name)
    header.add_row("Age", f"{character.age_years} ans")
    header.add_row("Rang", character.rank)
    header.add_row("Village", character.current_village)
    if character.clan:
        header.add_row("Clan", character.clan)
    if character.kekkei_genkai:
        header.add_row("Kekkei genkai", ", ".join(character.kekkei_genkai))
    if character.natures:
        header.add_row("Natures", ", ".join(character.natures))
    header.add_row(
        "Date",
        f"an {world.current_year}, jour {world.current_date}, {world.current_hour:02d}:{world.current_minute:02d}",
    )
    header.add_row("Lieu", character.current_location)

    bars = Table.grid(padding=(0, 2), expand=False)
    bars.add_column(style=COLOR_DIM, justify="right")
    bars.add_column()
    bars.add_row("Chakra", _bar(character.chakra.current, character.chakra.max, color="cyan"))
    bars.add_row("HP", _bar(character.health.hp_current, character.health.hp_max, color="green"))
    bars.add_row("Fatigue", _bar(character.health.fatigue, 100, color="yellow"))
    bars.add_row("Ryos", Text(f"{character.money:,}".replace(",", " "), style="bold yellow"))

    s = character.stats
    es = character.extended_stats
    stats_table = Table(
        show_header=False,
        box=None,
        title="Stats databook",
        title_style=COLOR_TITLE,
        padding=(0, 1),
    )
    stats_table.add_column(style="bold cyan", justify="right")
    stats_table.add_column(justify="left")
    stats_table.add_column(style="bold cyan", justify="right")
    stats_table.add_column(justify="left")
    stats_table.add_column(style="bold cyan", justify="right")
    stats_table.add_column(justify="left")
    stats_table.add_row(
        "Ninjutsu",
        f"{s.ninjutsu:.1f}",
        "Genjutsu",
        f"{s.genjutsu:.1f}",
        "Taijutsu",
        f"{s.taijutsu:.1f}",
    )
    stats_table.add_row(
        "Intelligence",
        f"{s.intelligence:.1f}",
        "Strength",
        f"{s.strength:.1f}",
        "Speed",
        f"{s.speed:.1f}",
    )
    stats_table.add_row(
        "Stamina",
        f"{s.stamina:.1f}",
        "Hand seals",
        f"{s.hand_seals:.1f}",
        "Total",
        f"{(s.ninjutsu + s.taijutsu + s.genjutsu + s.intelligence + s.strength + s.speed + s.stamina + s.hand_seals):.1f}",
    )

    ext_table = Table(
        show_header=False,
        box=None,
        title="Stats etendues",
        title_style=COLOR_TITLE,
        padding=(0, 1),
    )
    ext_table.add_column(style="bold cyan", justify="right")
    ext_table.add_column(justify="left")
    ext_table.add_column(style="bold cyan", justify="right")
    ext_table.add_column(justify="left")
    ext_table.add_row("Genie", f"{es.learning_genius:.1f}", "Charisma", f"{es.social_charisma:.1f}")
    ext_table.add_row("Willpower", f"{es.willpower:.1f}", "Perception", f"{es.perception:.1f}")
    ext_table.add_row("Lineage", f"{es.lineage_value:.1f}", "Beauty", f"{es.beauty:.1f}")

    body = Group(header, Text(""), bars, Text(""), stats_table, Text(""), ext_table)
    return Panel(body, title=f"[{COLOR_TITLE}]{character.name}", border_style="magenta")


def print_status(console: Console, character: Character, world: WorldState) -> None:
    console.print(status_panel(character, world))


def print_techniques(console: Console, character: Character) -> None:
    table = Table(title="Techniques connues", header_style=COLOR_TITLE)
    table.add_column("Id")
    table.add_column("Maitrise", justify="right")
    table.add_column("Annee", justify="right")
    table.add_column("Source")
    if not character.techniques_known:
        console.print(Panel("Aucune technique connue.", title="Techniques", border_style="dim"))
        return
    for t in character.techniques_known:
        table.add_row(
            t.technique_id,
            f"{t.mastery_level:.1f}",
            str(t.learned_year),
            t.learned_from or "(?)",
        )
    console.print(table)
    if character.techniques_in_progress:
        in_prog = Table(title="Techniques en cours", header_style=COLOR_WARN)
        in_prog.add_column("Id")
        in_prog.add_column("Progress", justify="right")
        in_prog.add_column("Mentor")
        for t in character.techniques_in_progress:
            in_prog.add_row(
                t.technique_id,
                f"{t.progress_hours} / {t.progress_required} h",
                t.teacher_id or "(autodidacte)",
            )
        console.print(in_prog)


def print_objectives(console: Console, goal_descriptions: list[str]) -> None:
    if not goal_descriptions:
        console.print(Panel("Aucun objectif declare.", title="Objectifs", border_style="dim"))
        return
    body = "\n".join(f"  {i}. {g}" for i, g in enumerate(goal_descriptions, start=1))
    console.print(Panel(body, title="Objectifs", border_style="magenta"))


def print_journal(console: Console, lines: list[str]) -> None:
    text = "\n".join(lines[-20:]) if lines else "(rien encore)"
    console.print(Panel(text, title="Journal narratif", border_style="cyan"))


def action_menu(console: Console, options: list[dict]) -> None:
    """Affiche les actions proposees par le LLM."""
    if not options:
        return
    table = Table(title="Actions proposees", header_style=COLOR_TITLE, show_lines=False)
    table.add_column("#", justify="right", style="bold cyan", width=3)
    table.add_column("Action")
    table.add_column("Difficulte", style=COLOR_DIM, justify="right")
    table.add_column("Duree", style=COLOR_DIM, justify="right")
    for i, opt in enumerate(options, start=1):
        # Cherche dans plusieurs cles possibles (LLM peut emettre difficulty_fr,
        # estimated_difficulty, ou rien) ; le narrator backfill avec heuristique.
        difficulty = (
            opt.get("difficulty_fr")
            or opt.get("estimated_difficulty")
            or opt.get("difficulty")
            or "modere"
        )
        duration = (
            opt.get("duration_fr")
            or opt.get("estimated_duration")
            or opt.get("duration")
            or "1h"
        )
        table.add_row(
            str(i),
            opt.get("label_fr", "?"),
            difficulty,
            duration,
        )
    console.print(table)


def outcome_color(outcome: str) -> str:
    if "full_success" in outcome:
        return COLOR_OK
    if "partial" in outcome:
        return COLOR_WARN
    if "catastrophic" in outcome:
        return COLOR_BAD
    if "impossibility" in outcome:
        return COLOR_DIM
    return "white"


def format_speaker(canon, character_id: str) -> str:
    """Compose un libelle de locuteur : 'Nom (Clan, Village)' ou role generique.

    Si character_id correspond a un personnage canon, on utilise nom + clan + village.
    Sinon on traite l'id comme un role (ex: marchand_taverne -> 'Marchand Taverne').
    """
    char = canon.characters.get(character_id) if canon is not None else None
    if char is None:
        return character_id.replace("_", " ").title()
    parts: list[str] = []
    if char.clan:
        parts.append(char.clan.title())
    if char.village_of_origin:
        parts.append(char.village_of_origin.replace("gakure", "").title())
    role = ", ".join(parts) if parts else "civil"
    return f"{char.name_romaji} ({role})"


def print_dialogue(console: Console, canon, dialogue_entries: list[dict]) -> None:
    """Affiche les repliques avec le nom complet du locuteur (ou role)."""
    for d in dialogue_entries:
        cid = d.get("character_id", "?")
        line = d.get("line", "")
        tone = d.get("tone", "")
        speaker = format_speaker(canon, cid)
        tone_part = f" [dim]({tone})[/dim]" if tone else ""
        console.print(f"  [bold magenta]{speaker}[/bold magenta]{tone_part}")
        console.print(f"     [italic]{line}[/italic]")
