"""Boucle de jeu principale avec UI rich, missions, controles de duree."""

from __future__ import annotations

import asyncio

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from shinobi.canon.loader import load_canon
from shinobi.cli.display import (
    COLOR_TITLE,
    action_menu,
    outcome_color,
    print_objectives,
    print_status,
    print_techniques,
)
from shinobi.engine.actions import (
    Action,
    ActionResult,
    ResolutionInputs,
    apply_action_to_state,
    apply_mission_result,
    resolve_action,
)
from shinobi.engine.events import tick_scheduler
from shinobi.engine.interpreter import interpret
from shinobi.engine.missions import list_available_missions
from shinobi.engine.rng import next_seed
from shinobi.engine.time import advance_time
from shinobi.llm.client import LLMClient
from shinobi.llm.narration import NarrationRequest, Narrator
from shinobi.persistence import saves as save_module
from shinobi.rag.retriever import Retriever
from shinobi.rag.store import ChromaStore
from shinobi.types import ActionType
from shinobi.utils.time_utils import GameDate

console = Console()


META_HELP = {
    "/status": "Affiche le panneau de statut detaille",
    "/techniques": "Liste tes techniques connues et en cours",
    "/objectives": "Liste tes objectifs declares",
    "/missions": "Liste les missions disponibles",
    "/skip <duree>": "Saute le temps : '/skip 7d' pour 7 jours, '/skip 1m' pour 1 mois",
    "/journal": "Indique ou se trouve le journal",
    "/help": "Affiche cette aide",
    "/quit": "Sauvegarde et retourne au menu principal",
}

# Choix de duree pour une action.
DURATION_PRESETS = [
    ("Action breve (~30 minutes)", 0),
    ("Demi-journee (4h)", 4),
    ("Journee complete (8h)", 8),
    ("3 jours intensifs", 24),
    ("Semaine d'entrainement (7 jours)", 56),
    ("Mois d'entrainement (30 jours)", 240),
]


def play_session(save_id: str) -> None:
    """Charge une save et lance la boucle de jeu."""
    character, world, meta = save_module.load_save(save_id)
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

    store = ChromaStore()
    retriever = Retriever(store, canon)
    turn = meta.total_turns
    last_proposed: list[dict] = []
    pending_missions: list = []

    console.print(
        Panel.fit(
            f"Tu reprends [bold yellow]{character.name}[/bold yellow] a l'an {world.current_year}, "
            f"jour {world.current_date}.\nTape [cyan]/help[/cyan] pour les commandes.",
            title="Partie en cours",
            border_style="cyan",
        )
    )

    while not character.is_dead:
        turn += 1
        print_status(console, character, world)
        if last_proposed:
            action_menu(console, last_proposed)

        intent_text = Prompt.ask(
            "[bold cyan]Action[/bold cyan] [dim](numero, texte libre, ou /help)[/dim]",
            default="je m'entraine au taijutsu",
        ).strip()

        if intent_text.startswith("/"):
            should_continue, character, world = _handle_meta(
                intent_text, character, world, save_id, canon, pending_missions
            )
            if not should_continue:
                save_module.append_narrative_log(
                    save_id,
                    {"turn": turn, "year": world.current_year, "type": "session_end"},
                )
                console.print("[green]Sauvegarde effectuee. Retour au menu.[/green]")
                return
            turn -= 1
            continue

        if intent_text.isdigit() and last_proposed:
            idx = int(intent_text) - 1
            if 0 <= idx < len(last_proposed):
                intent_text = last_proposed[idx].get("label_fr", intent_text)

        # Interpretation de l'intention
        parsed = interpret(intent_text)

        # Choix de duree pour les actions longues
        duration_param = parsed.parameters.get("duration_hours")
        if (
            isinstance(duration_param, int)
            and parsed.action_type in {ActionType.train_stat, ActionType.train_technique, ActionType.research, ActionType.work}
        ):
            chosen = _pick_duration(default_hours=duration_param)
            parsed.parameters["duration_hours"] = chosen

        action = Action(
            action_type=parsed.action_type,
            summary=parsed.summary,
            parameters=parsed.parameters,
            declared_text=intent_text,
        )

        result = resolve_action(
            ResolutionInputs(
                character=character,
                world=world,
                action=action,
                seed=world.seed & 0x7FFFFFFFFFFFFFFF,
            )
        )
        character, world, result = apply_action_to_state(character, world, result)

        new_date = GameDate(
            year=world.current_year,
            month=int(world.current_date.split("-")[0]),
            day=int(world.current_date.split("-")[1]),
            hour=world.current_hour,
            minute=world.current_minute,
        )
        new_date = advance_time(new_date, result.duration_minutes)
        world = world.with_time(
            year=new_date.year,
            date=new_date.date_str,
            hour=new_date.hour,
            minute=new_date.minute,
        )
        seed_after = next_seed(result.seed_after) & 0x7FFFFFFFFFFFFFFF
        world = world.model_copy(update={"seed": seed_after})
        world, fired, cancelled = tick_scheduler(world, canon, turn_number=turn)

        _print_result(result, parsed.action_type, turn)
        for f in fired:
            console.print(f"  [yellow]>>> Evenement canon declenche : {f.event_id}[/yellow]")
        for c in cancelled:
            console.print(f"  [red]>>> Evenement canon annule : {c.event_id}[/red]")

        last_proposed = []
        try:
            narration = asyncio.run(
                _attempt_narration(character, world, canon, retriever, result, intent_text, parsed)
            )
            if narration is not None:
                console.print(Panel(narration.narrative, title="Narration", border_style="cyan"))
                for d in narration.npc_dialogue:
                    console.print(
                        f"  [bold magenta]{d.get('character_id', '?')}[/bold magenta] : "
                        f"[italic]{d.get('line', '')}[/italic]"
                    )
                last_proposed = narration.proposed_actions or []
                for obs in narration.world_observations:
                    console.print(f"  [dim cyan]Observation :[/dim cyan] {obs}")
        except Exception as exc:
            console.print(f"[dim]Narration LLM indisponible ({type(exc).__name__})[/dim]")

        try:
            save_module.save_turn(
                save_id,
                turn_number=turn,
                action_result=result,
                new_character=character,
                new_world=world,
                seed_state=seed_after,
            )
            save_module.append_narrative_log(
                save_id,
                {
                    "turn": turn,
                    "year": world.current_year,
                    "date": world.current_date,
                    "type": "narration",
                    "content": result.summary_fr,
                    "intent": intent_text,
                    "action_type": parsed.action_type.value,
                },
            )
        except Exception as exc:
            console.print(f"[red]Erreur de sauvegarde : {type(exc).__name__}: {exc}[/red]")

    console.print(
        Panel(f"[red]Fin de la vie de {character.name}.[/red]", title="Mort", border_style="red")
    )


def _print_result(result: ActionResult, action_type: ActionType, turn: int) -> None:
    """Affiche le panneau de resultat avec stat changes + recompenses."""
    body_lines = [Text(result.summary_fr, style=outcome_color(result.outcome.value))]

    if result.stat_changes:
        body_lines.append(Text(""))
        for ch in result.stat_changes:
            sign = "+" if ch["delta"] > 0 else ""
            body_lines.append(
                Text(
                    f"  {ch['stat']:<20} {ch['old']:.2f} -> {ch['new']:.2f} ({sign}{ch['delta']:.3f})",
                    style="bold green" if ch["delta"] > 0 else "dim",
                )
            )
    if result.money_delta:
        sign = "+" if result.money_delta > 0 else ""
        body_lines.append(
            Text(
                f"  Ryos : {sign}{result.money_delta}",
                style="bold yellow" if result.money_delta > 0 else "red",
            )
        )
    if result.hp_delta:
        body_lines.append(Text(f"  HP : {result.hp_delta}", style="bold red"))
    if result.fatigue_delta:
        sign = "+" if result.fatigue_delta > 0 else ""
        body_lines.append(Text(f"  Fatigue : {sign}{result.fatigue_delta}", style="yellow"))
    if result.chakra_cost:
        body_lines.append(Text(f"  Chakra consomme : -{result.chakra_cost}", style="cyan"))

    body = Text("\n").join(body_lines)
    console.print(
        Panel(
            body,
            title=f"Resultat (tour {turn}, {result.duration_minutes // 60}h{result.duration_minutes % 60:02d})",
            border_style=outcome_color(result.outcome.value),
        )
    )


def _pick_duration(default_hours: int) -> int:
    """Demande au joueur la duree d'engagement pour une action longue."""
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", justify="right")
    table.add_column()
    for i, (label, _hours) in enumerate(DURATION_PRESETS, start=1):
        table.add_row(str(i), label)
    table.add_row("c", "Duree personnalisee (en heures)")
    console.print(Panel(table, title="Duree d'engagement", border_style="cyan"))
    choice = Prompt.ask(
        "[bold cyan]Duree[/bold cyan]",
        default=str(_default_duration_index(default_hours)),
    ).strip().lower()
    if choice in ("c", "custom"):
        try:
            return int(Prompt.ask("[bold cyan]Heures[/bold cyan]", default=str(default_hours)))
        except ValueError:
            return default_hours
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(DURATION_PRESETS):
            return DURATION_PRESETS[idx][1] or default_hours
    except ValueError:
        pass
    return default_hours


def _default_duration_index(target_hours: int) -> int:
    """Retourne l'index du preset le plus proche."""
    best_i = 1
    best_diff = 99999
    for i, (_label, hours) in enumerate(DURATION_PRESETS, start=1):
        diff = abs(hours - target_hours)
        if diff < best_diff:
            best_diff = diff
            best_i = i
    return best_i


def _handle_meta(command: str, character, world, save_id: str, canon, pending_missions: list):
    """Traite une commande meta. Retourne (continue, character, world)."""
    if command in ("/quit", "/exit"):
        return False, character, world
    if command == "/help":
        body = "\n".join(f"  [cyan]{cmd}[/cyan] : {desc}" for cmd, desc in META_HELP.items())
        console.print(Panel(body, title="Commandes meta", border_style="cyan"))
    elif command == "/status":
        print_status(console, character, world)
    elif command == "/techniques":
        print_techniques(console, character)
    elif command == "/objectives":
        descriptions = [f"{g.goal_id} : {g.status.value}" for g in character.declared_goals]
        print_objectives(console, descriptions)
    elif command == "/missions":
        character, world = _missions_flow(character, world, save_id, canon)
    elif command.startswith("/skip"):
        character, world = _skip_time(command, character, world)
    elif command == "/journal":
        console.print(f"[dim]Journal : data/saves/{save_id}/narrative_log.jsonl[/dim]")
    else:
        console.print(f"[red]Commande inconnue : {command}[/red] (tape [cyan]/help[/cyan])")
    return True, character, world


def _missions_flow(character, world, save_id: str, canon):
    """Affiche les missions disponibles, propose d'en accepter une."""
    missions = list_available_missions(player_rank=character.rank, count=5, seed=int(world.seed) % 100000)
    table = Table(title=f"Missions disponibles (rang {character.rank})", header_style=COLOR_TITLE)
    table.add_column("#", style="bold cyan", justify="right")
    table.add_column("Rang")
    table.add_column("Titre")
    table.add_column("Duree", justify="right")
    table.add_column("Recompense", justify="right")
    table.add_column("DC", justify="right")
    for i, m in enumerate(missions, start=1):
        table.add_row(
            str(i),
            m.rank,
            m.title,
            f"{m.duration_hours}h",
            f"{m.reward_ryos:,} r".replace(",", " "),
            str(m.difficulty_dc),
        )
    table.add_row("0", "-", "[ne pas accepter de mission]", "-", "-", "-")
    console.print(table)

    choice = Prompt.ask("[bold cyan]Mission[/bold cyan] [dim](numero)[/dim]", default="0").strip()
    if choice in ("0", ""):
        return character, world
    try:
        idx = int(choice) - 1
        if not (0 <= idx < len(missions)):
            return character, world
    except ValueError:
        return character, world

    mission = missions[idx]
    console.print(
        Panel(
            f"[bold]{mission.title}[/bold]\n[dim]{mission.description_fr}[/dim]\n\n"
            f"Tu pars en mission : duree {mission.duration_hours}h.",
            title=f"Mission {mission.rank} acceptee",
            border_style="magenta",
        )
    )

    # Resolution rapide : un jet contre la difficulte
    from shinobi.engine.rng import roll
    from shinobi.engine.stats import average_combat_stat

    stat = average_combat_stat(character.stats)
    r = roll(world.seed & 0x7FFFFFFFFFFFFFFF, "1d20", modifier=int(stat * 4))
    success = r.total >= mission.difficulty_dc
    new_char, ryos = apply_mission_result(character, mission, success=success)
    if success:
        console.print(
            Panel(
                f"[bold green]Mission accomplie ![/bold green]\n"
                f"Recompense : [yellow]+{ryos:,}[/yellow] ryos.".replace(",", " "),
                title="Succes",
                border_style="green",
            )
        )
    else:
        console.print(
            Panel(
                "[bold red]Mission echouee.[/bold red]\n"
                "Tu rentres blesse au village. Reputation legerement entamee.",
                title="Echec",
                border_style="red",
            )
        )

    # Avance le temps de la duree de la mission
    new_date = GameDate(
        year=world.current_year,
        month=int(world.current_date.split("-")[0]),
        day=int(world.current_date.split("-")[1]),
        hour=world.current_hour,
        minute=world.current_minute,
    )
    new_date = advance_time(new_date, mission.duration_hours * 60)
    new_world = world.with_time(
        year=new_date.year, date=new_date.date_str, hour=new_date.hour, minute=new_date.minute
    )
    new_world = new_world.model_copy(update={"seed": next_seed(r.seed_after) & 0x7FFFFFFFFFFFFFFF})
    return new_char, new_world


def _skip_time(command: str, character, world):
    """/skip 7d, /skip 1m, /skip 24h"""
    import re

    m = re.search(r"/skip\s+(\d+)\s*([dhwmDHWM])", command)
    if not m:
        console.print("[red]Format : /skip <nombre><d|h|w|m>  (ex: /skip 7d)[/red]")
        return character, world
    n = int(m.group(1))
    unit = m.group(2).lower()
    minutes = {"h": n * 60, "d": n * 24 * 60, "w": n * 7 * 24 * 60, "m": n * 30 * 24 * 60}[unit]
    new_date = GameDate(
        year=world.current_year,
        month=int(world.current_date.split("-")[0]),
        day=int(world.current_date.split("-")[1]),
        hour=world.current_hour,
        minute=world.current_minute,
    )
    new_date = advance_time(new_date, minutes)
    new_world = world.with_time(
        year=new_date.year, date=new_date.date_str, hour=new_date.hour, minute=new_date.minute
    )
    # Le perso vieillit si on saute beaucoup
    if minutes > 30 * 24 * 60:
        years_passed = minutes // (365 * 24 * 60)
        if years_passed > 0:
            new_age = character.age_years + years_passed
            character = character.with_age(new_age)
    console.print(f"[green]Temps avance de {n}{unit}. Nouvelle date : an {new_date.year}, jour {new_date.date_str}[/green]")
    return character, new_world


async def _attempt_narration(character, world, canon, retriever, result, intent: str, parsed):
    async with LLMClient() as client:
        if not await client.health():
            return None
        narrator = Narrator(client, canon, retriever)
        request = NarrationRequest(
            turn_summary=intent,
            action_text=intent,
            action_result_summary=result.summary_fr,
            location_id=character.current_location,
            present_npcs=[],
            active_breadcrumb_descriptions=[],
            character_state_summary=(
                f"{character.name}, {character.age_years} ans, "
                f"{character.rank} a {character.current_village}, "
                f"chakra {character.chakra.current}/{character.chakra.max}, "
                f"clan {character.clan or 'civil'}, "
                f"natures {', '.join(character.natures) or 'aucune'}, "
                f"action interpretee : {parsed.action_type.value}"
            ),
            duration_str=f"{result.duration_minutes // 60}h{result.duration_minutes % 60:02d}",
        )
        return await narrator.narrate(request)
