"""Boucle de jeu principale avec UI rich et narration LLM optionnelle."""

from __future__ import annotations

import asyncio

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

from shinobi.canon.loader import load_canon
from shinobi.cli.display import (
    action_menu,
    outcome_color,
    print_objectives,
    print_status,
    print_techniques,
)
from shinobi.engine.actions import (
    Action,
    ResolutionInputs,
    apply_action_to_state,
    resolve_action,
)
from shinobi.engine.events import tick_scheduler
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
    "/journal": "Affiche les 20 derniers tours",
    "/help": "Affiche cette aide",
    "/save": "Force un snapshot complet (auto deja active)",
    "/quit": "Sauvegarde et retourne au menu principal",
}


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

        intent = Prompt.ask(
            "[bold cyan]Action[/bold cyan] [dim](numero, texte libre, ou /help)[/dim]",
            default="je m'entraine",
        ).strip()

        if intent.startswith("/"):
            if not _handle_meta(intent, character, world):
                save_module.append_narrative_log(
                    save_id,
                    {"turn": turn, "year": world.current_year, "type": "session_end"},
                )
                console.print("[green]Sauvegarde effectuee. Retour au menu.[/green]")
                return
            turn -= 1  # n'a pas consomme de tour reel
            continue

        # Si le joueur a tape un numero correspondant a une action proposee, on prend son label.
        if intent.isdigit() and last_proposed:
            idx = int(intent) - 1
            if 0 <= idx < len(last_proposed):
                intent = last_proposed[idx].get("label_fr", intent)

        action = Action(
            action_type=ActionType.custom,
            summary=intent,
            declared_text=intent,
        )
        result = resolve_action(
            ResolutionInputs(
                character=character,
                world=world,
                action=action,
                seed=world.seed & 0x7FFFFFFFFFFFFFFF,
            )
        )
        character, world = apply_action_to_state(character, world, result)
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

        outcome_text = Text(result.summary_fr, style=outcome_color(result.outcome.value))
        console.print(
            Panel(
                outcome_text,
                title=f"Resultat (tour {turn})",
                border_style=outcome_color(result.outcome.value),
            )
        )
        for f in fired:
            console.print(f"  [yellow]>>> Evenement canon declenche : {f.event_id}[/yellow]")
        for c in cancelled:
            console.print(f"  [red]>>> Evenement canon annule : {c.event_id}[/red]")

        last_proposed = []
        try:
            narration = asyncio.run(
                _attempt_narration(character, world, canon, retriever, result, intent)
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
                    "intent": intent,
                },
            )
        except Exception as exc:
            console.print(f"[red]Erreur de sauvegarde : {type(exc).__name__}: {exc}[/red]")

    console.print(
        Panel(f"[red]Fin de la vie de {character.name}.[/red]", title="Mort", border_style="red")
    )


def _handle_meta(command: str, character, world) -> bool:
    """Traite une commande meta. Retourne False si on doit quitter."""
    if command in ("/quit", "/exit"):
        return False
    if command == "/help":
        body = "\n".join(f"  [cyan]{cmd}[/cyan] : {desc}" for cmd, desc in META_HELP.items())
        console.print(Panel(body, title="Commandes meta", border_style="cyan"))
    elif command == "/status":
        print_status(console, character, world)
    elif command == "/techniques":
        print_techniques(console, character)
    elif command == "/objectives":
        descriptions = []
        for goal in character.declared_goals:
            descriptions.append(f"{goal.goal_id} : {goal.status.value}")
        print_objectives(console, descriptions)
    elif command == "/journal":
        console.print("[dim]Journal narratif : voir data/saves/<save_id>/narrative_log.jsonl[/dim]")
    elif command == "/save":
        console.print("[green]Snapshot deja effectue automatiquement chaque tour.[/green]")
    else:
        console.print(f"[red]Commande inconnue : {command}[/red] (tape [cyan]/help[/cyan])")
    return True


async def _attempt_narration(character, world, canon, retriever, result, intent: str):
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
                f"natures {', '.join(character.natures) or 'aucune'}"
            ),
            duration_str=f"{result.duration_minutes} minutes",
        )
        return await narrator.narrate(request)
