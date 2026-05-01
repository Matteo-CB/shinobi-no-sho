"""Helpers d'affichage rich pour la CLI."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from shinobi.engine.character import Character
from shinobi.engine.world import WorldState


def print_status(console: Console, character: Character, world: WorldState) -> None:
    """Affiche le panneau status detaille."""
    head = Table.grid(padding=(0, 2))
    head.add_row("Nom", character.name)
    head.add_row("Age", f"{character.age_years} ans")
    head.add_row("Rang", character.rank)
    head.add_row("Village", character.current_village)
    if character.clan:
        head.add_row("Clan", character.clan)
    head.add_row("Date", f"an {world.current_year}, jour {world.current_date}")
    head.add_row("Lieu", character.current_location)
    head.add_row("Chakra", f"{character.chakra.current} / {character.chakra.max}")
    head.add_row("HP", f"{character.health.hp_current} / {character.health.hp_max}")
    head.add_row("Ryos", str(character.money))

    stats = Table(title="Stats databook", show_header=False, box=None)
    s = character.stats
    stats.add_row(
        "Ninjutsu",
        f"{s.ninjutsu:.1f}",
        "Genjutsu",
        f"{s.genjutsu:.1f}",
        "Taijutsu",
        f"{s.taijutsu:.1f}",
    )
    stats.add_row(
        "Intelligence",
        f"{s.intelligence:.1f}",
        "Strength",
        f"{s.strength:.1f}",
        "Speed",
        f"{s.speed:.1f}",
    )
    stats.add_row(
        "Stamina",
        f"{s.stamina:.1f}",
        "Hand seals",
        f"{s.hand_seals:.1f}",
        "Total",
        f"{sum([s.ninjutsu, s.taijutsu, s.genjutsu, s.intelligence, s.strength, s.speed, s.stamina, s.hand_seals]):.1f}",
    )

    console.print(Panel(head, title="Etat du personnage"))
    console.print(stats)


def print_techniques(console: Console, character: Character) -> None:
    table = Table(title="Techniques connues")
    table.add_column("Id")
    table.add_column("Mastery")
    table.add_column("Annee")
    table.add_column("Source")
    for t in character.techniques_known:
        table.add_row(
            t.technique_id,
            f"{t.mastery_level:.1f}",
            str(t.learned_year),
            t.learned_from or "(?)",
        )
    console.print(table)
    if character.techniques_in_progress:
        in_prog = Table(title="Techniques en cours")
        in_prog.add_column("Id")
        in_prog.add_column("Progress")
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
        console.print(Panel("Aucun objectif declare.", title="Objectifs"))
        return
    console.print(Panel("\n".join(f"- {g}" for g in goal_descriptions), title="Objectifs"))


def print_journal(console: Console, lines: list[str]) -> None:
    console.print(Panel("\n".join(lines[-20:]), title="Journal narratif"))
