"""Boucle de jeu principale (sans LLM en fallback)."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.panel import Panel

from shinobi.canon.loader import load_canon
from shinobi.cli.display import print_status, print_techniques
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

    console.print(
        Panel.fit(
            f"Tu reprends la partie de {character.name} a l'an {world.current_year}, jour {world.current_date}.",
            title="Reprise",
        )
    )

    while not character.is_dead:
        turn += 1
        print_status(console, character, world)
        intent = typer.prompt(
            "Action libre (ou /quit pour sauvegarder et sortir)",
            default="je m'entraine",
        ).strip()

        if intent.startswith("/"):
            if intent == "/quit":
                console.print("Sauvegarde finale...")
                break
            if intent == "/status":
                print_status(console, character, world)
                continue
            if intent == "/techniques":
                print_techniques(console, character)
                continue
            console.print("Commande inconnue.")
            continue

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
                seed=world.seed,
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
        world = world.model_copy(update={"seed": next_seed(result.seed_after)})
        world, fired, cancelled = tick_scheduler(world, canon, turn_number=turn)

        console.print(Panel(result.summary_fr, title="Resultat"))
        for f in fired:
            console.print(f"[yellow]Evenement canon declenche : {f.event_id}[/yellow]")
        for c in cancelled:
            console.print(f"[red]Evenement canon annule : {c.event_id}[/red]")

        try:
            asyncio.run(_attempt_narration(character, world, canon, retriever, result, intent))
        except Exception as exc:
            console.print(
                f"[dim]Narration LLM indisponible ({type(exc).__name__}: {str(exc)[:80]})[/dim]"
            )

        save_module.save_turn(
            save_id,
            turn_number=turn,
            action_result=result,
            new_character=character,
            new_world=world,
            seed_state=world.seed,
        )
        save_module.append_narrative_log(
            save_id,
            {
                "turn": turn,
                "year": world.current_year,
                "type": "narration",
                "content": result.summary_fr,
            },
        )

    console.print("Fin de session.")


async def _attempt_narration(character, world, canon, retriever, result, intent: str) -> None:
    async with LLMClient() as client:
        if not await client.health():
            return
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
                f"chakra {character.chakra.current}/{character.chakra.max}"
            ),
            duration_str=f"{result.duration_minutes} minutes",
        )
        try:
            narration = await narrator.narrate(request)
            console.print(Panel(narration.narrative, title="Narration"))
            for d in narration.npc_dialogue:
                console.print(f"  [cyan]{d.get('character_id', '?')}[/cyan] : {d.get('line', '')}")
        except Exception as exc:
            console.print(f"[dim]Narration: {type(exc).__name__}[/dim]")
