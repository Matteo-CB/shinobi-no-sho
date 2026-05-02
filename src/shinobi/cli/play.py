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
    print_dialogue,
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
    "/declare": "Declare un nouvel objectif (texte libre)",
    "/path <goal_id>": "Demande au pathfinder LLM le prochain pas vers un objectif",
    "/missions": "Liste les missions disponibles",
    "/buy": "Ouvrir la boutique du village",
    "/sell": "Vendre un item de ton inventaire",
    "/inventory": "Affiche ton inventaire",
    "/use <item_id>": "Consomme un item (soldier_pill, ramen_bowl, antidote, ...)",
    "/active_missions": "Liste les missions acceptees",
    "/reputation": "Affiche ta reputation par village",
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
    # Filtre canon selon le profil de canonicite de la save
    from shinobi.canon.profiles import CanonicityProfile

    profile_csv = ",".join(world.canonicity_profile) if world.canonicity_profile else "manga,boruto_manga,tbv,databook,movie_canon"
    profile = CanonicityProfile.from_csv(profile_csv, label=meta.canonicity_profile)
    canon = load_canon(
        optional=(
            "characters", "techniques", "clans", "villages", "organizations",
            "tailed_beasts", "kekkei_genkai", "kekkei_mora", "hiden",
            "weapons_tools", "locations", "timeline_events", "voice_profiles",
        ),
        profile=profile,
    )

    store = ChromaStore()
    # Auto-indexation au premier lancement si l'index ChromaDB est vide
    try:
        if store.count("crossdomain") == 0:
            console.print("[dim]Premiere utilisation : indexation RAG en cours...[/dim]")
            from shinobi.rag.chunker import chunk_all
            from shinobi.rag.embedder import embed_texts

            chunks = chunk_all(canon)
            total = len(chunks)
            for i in range(0, total, 64):
                batch = chunks[i : i + 64]
                vecs = embed_texts([c.text for c in batch], batch_size=64)
                store.add_chunks(batch, vecs)
            console.print(f"[green]RAG indexe : {total} chunks.[/green]")
    except Exception as exc:
        console.print(f"[dim]Indexation RAG echouee : {type(exc).__name__}[/dim]")

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
                intent_text, character, world, save_id, canon, pending_missions, retriever=retriever
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

        # Routes special : /buy /sell /use sont declenches via interpreter aussi
        if parsed.action_type == ActionType.buy:
            character = _shop_buy_flow(character)
            turn -= 1
            continue
        if parsed.action_type == ActionType.sell:
            character = _shop_sell_flow(character)
            turn -= 1
            continue
        use_item_id = parsed.parameters.get("_use_item")
        if use_item_id:
            from shinobi.engine.items import use_item

            character, effect = use_item(character, use_item_id)
            color = "green" if effect.success else "yellow"
            console.print(f"  [{color}]{effect.summary_fr}[/{color}]")
            turn -= 1
            continue

        # Choix de duree pour les actions longues
        duration_param = parsed.parameters.get("duration_hours")
        if isinstance(duration_param, int) and parsed.action_type in {
            ActionType.train_stat,
            ActionType.train_technique,
            ActionType.research,
            ActionType.work,
        }:
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

        # Auto-completion des breadcrumbs : verifie si l'action complete une condition
        completed_now = _check_breadcrumb_completions(save_id, character, result, world.current_year)
        for bc_desc in completed_now:
            console.print(f"  [bold green]>>> Sous-objectif accompli :[/bold green] {bc_desc}")

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
            # WorldResolver auto si strategy=narrative_resolution
            canon_ev = canon.timeline_events.get(c.event_id)
            if canon_ev and canon_ev.cancellation_strategy.type == "narrative_resolution":
                try:
                    asyncio.run(_world_resolve_cancellation(canon_ev, c.reason, world.current_year, canon))
                except Exception:
                    pass

        last_proposed = []
        present_npcs = _detect_present_npcs(intent_text, canon)
        try:
            narration = asyncio.run(
                _attempt_narration(
                    character,
                    world,
                    canon,
                    retriever,
                    result,
                    intent_text,
                    parsed,
                    present_npcs=present_npcs,
                )
            )
            if narration is not None:
                console.print(Panel(narration.narrative, title="Narration", border_style="cyan"))
                if narration.npc_dialogue:
                    print_dialogue(console, canon, narration.npc_dialogue)
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
    """Affiche le panneau de resultat avec stat changes + consequences justifiees."""
    body_lines = [Text(result.summary_fr, style=outcome_color(result.outcome.value))]

    # Index : stat -> justification (pour annoter les stat_changes)
    why_by_stat: dict[str, str] = {}
    for cons in result.consequences or []:
        why_by_stat[cons.get("stat", "")] = cons.get("why_fr", "")

    if result.stat_changes:
        body_lines.append(Text(""))
        for ch in result.stat_changes:
            sign = "+" if ch["delta"] > 0 else ""
            line = f"  {ch['stat']:<20} {ch['old']:.2f} -> {ch['new']:.2f} ({sign}{ch['delta']:.3f})"
            text = Text(line, style="bold green" if ch["delta"] > 0 else "dim")
            why = why_by_stat.get(ch["stat"], "")
            if why:
                text.append(f"   [{why}]", style="dim italic")
            body_lines.append(text)
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
    choice = (
        Prompt.ask(
            "[bold cyan]Duree[/bold cyan]",
            default=str(_default_duration_index(default_hours)),
        )
        .strip()
        .lower()
    )
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


def _handle_meta(command: str, character, world, save_id: str, canon, pending_missions: list, retriever=None):
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
        goals = save_module.load_goals(save_id)
        descriptions = [
            f"[{g.id[:8]}] {g.description_player} - {g.status.value}" for g in goals
        ]
        print_objectives(console, descriptions)
    elif command == "/declare":
        from shinobi.goals.declaration import declare_goal

        text = Prompt.ask("[bold cyan]Decris ton objectif[/bold cyan]")
        if text.strip():
            goal = declare_goal(
                description_player=text.strip(),
                interpretation_canonical=text.strip(),
                declared_at_year=world.current_year,
                declared_at_age=character.age_years,
            )
            save_module.save_goal(save_id, goal)
            console.print(f"[green]Objectif declare : {goal.id[:8]}[/green]")
    elif command.startswith("/path"):
        parts = command.split(maxsplit=1)
        goal_id_partial = parts[1].strip() if len(parts) > 1 else ""
        goals = save_module.load_goals(save_id)
        target_goal = next((g for g in goals if g.id.startswith(goal_id_partial)), None)
        if target_goal is None:
            console.print(f"[red]Objectif introuvable : {goal_id_partial}[/red]")
        else:
            console.print(f"[cyan]Recherche du chemin pour : {target_goal.description_player}[/cyan]")
            try:
                asyncio.run(_pathfinder_flow(target_goal, character, world, canon, retriever, save_id))
            except Exception as exc:
                console.print(f"[dim]Pathfinder LLM indisponible ({type(exc).__name__})[/dim]")
    elif command == "/buy":
        character = _shop_buy_flow(character)
    elif command == "/sell":
        character = _shop_sell_flow(character)
    elif command.startswith("/use"):
        parts = command.split(maxsplit=1)
        if len(parts) < 2:
            console.print("[red]Usage : /use <item_id>[/red]")
        else:
            from shinobi.engine.items import use_item

            character, effect = use_item(character, parts[1].strip())
            color = "green" if effect.success else "yellow"
            console.print(f"  [{color}]{effect.summary_fr}[/{color}]")
    elif command == "/active_missions":
        items = save_module.load_active_missions(save_id)
        if not items:
            console.print(Panel("Aucune mission acceptee.", title="Missions"))
        else:
            lines = []
            for m in items:
                status = (
                    "[green]reussi[/green]" if m["success"]
                    else ("[red]echoue[/red]" if m["success"] is False else "[yellow]en cours[/yellow]")
                )
                lines.append(f"  [{m['rank']}] {m['title']} ({status})")
            console.print(Panel("\n".join(lines), title=f"Missions ({len(items)})"))
    elif command == "/inventory":
        from shinobi.engine.shop import ITEM_CATALOG, get_inventory_summary

        items = get_inventory_summary(character.inventory)
        if not items:
            console.print(Panel("Inventaire vide.", title="Inventaire"))
        else:
            lines = []
            for item_id, qty in items:
                item = ITEM_CATALOG.get(item_id)
                name = item.name_fr if item else item_id
                lines.append(f"  {name} (x{qty})")
            console.print(Panel("\n".join(lines), title=f"Inventaire ({character.money} ryos)"))
    elif command == "/reputation":
        if not character.reputation.by_village:
            console.print(Panel("Aucune reputation enregistree.", title="Reputation"))
        else:
            lines = [f"  {e.village_id}: {e.score}" for e in character.reputation.by_village]
            console.print(Panel("\n".join(lines), title="Reputation par village"))
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
    missions = list_available_missions(
        player_rank=character.rank, count=5, seed=int(world.seed) % 100000
    )
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
    save_module.save_active_mission(save_id, mission, year=world.current_year)
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
    new_char, ryos, mission_changes = apply_mission_result(character, mission, success=success)
    save_module.mark_mission_completed(save_id, mission.id, year=world.current_year, success=success)

    # Construit les lignes de stat changes avec justification
    consequence_lines = []
    for ac in mission_changes:
        sign = "+" if ac.change.delta > 0 else ""
        consequence_lines.append(
            f"  [bold green]{ac.change.stat_name}[/bold green] "
            f"{ac.change.old:.2f} -> {ac.change.new:.2f} ({sign}{ac.change.delta:.3f}) "
            f"[dim italic][{ac.why_fr}][/dim italic]"
        )
    consequences_block = ("\n\n" + "\n".join(consequence_lines)) if consequence_lines else ""

    if success:
        console.print(
            Panel(
                f"[bold green]Mission accomplie ![/bold green]\n"
                f"Recompense : [yellow]+{ryos:,}[/yellow] ryos.".replace(",", " ") + consequences_block,
                title="Succes",
                border_style="green",
            )
        )
    else:
        console.print(
            Panel(
                "[bold red]Mission echouee.[/bold red]\n"
                "Tu rentres blesse au village. Reputation legerement entamee." + consequences_block,
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


def _check_breadcrumb_completions(save_id: str, character, action_result, current_year: int) -> list[str]:
    """Apres chaque action, verifie si des breadcrumbs reveles sont completes.

    Retourne la liste des descriptions de breadcrumbs complettes ce tour.
    """
    from shinobi.goals.breadcrumbs import mark_completed
    from shinobi.goals.completion import check_breadcrumb_completion

    breadcrumbs = save_module.load_breadcrumbs(save_id)
    completed_descriptions: list[str] = []
    for bc in breadcrumbs:
        if bc.completed or not bc.revealed:
            continue
        if check_breadcrumb_completion(bc, action_result=action_result, character=character):
            updated = mark_completed(bc, current_year)
            save_module.save_breadcrumb(save_id, updated)
            completed_descriptions.append(bc.description)
    return completed_descriptions


async def _world_resolve_cancellation(canon_ev, reason: str, current_year: int, canon):
    """Appelle WorldResolver LLM quand un event canon est annule narrativement."""
    from shinobi.llm.client import LLMClient
    from shinobi.llm.narration import WorldResolver

    async with LLMClient() as client:
        if not await client.health():
            return
        resolver = WorldResolver(client, canon)
        resolution = await resolver.resolve_cancelled_event(
            event_id=canon_ev.id,
            cancellation_reason=reason,
            current_year=current_year,
        )
        console.print(
            Panel(
                f"[bold]A la place :[/bold] {resolution.substitute_event_summary}\n"
                + ("\nConsequences :\n" + "\n".join(f"  - {c.get('description', '')}" for c in resolution.consequences) if resolution.consequences else "")
                + (f"\n\nRumeur qui circule : [italic]{resolution.rumor_template}[/italic]" if resolution.rumor_template else ""),
                title=f"Resolution narrative : {canon_ev.name_fr}",
                border_style="yellow",
            )
        )


def _shop_buy_flow(character):
    """Affiche la boutique du village et propose un achat."""
    from shinobi.engine.shop import buy_item, list_shop_inventory

    items = list_shop_inventory(character.current_village)
    if not items:
        console.print(f"[yellow]Aucune boutique a {character.current_village}.[/yellow]")
        return character
    table = Table(title=f"Boutique de {character.current_village} ({character.money} ryos en poche)", header_style=COLOR_TITLE)
    table.add_column("#", style="bold cyan", justify="right")
    table.add_column("Item")
    table.add_column("Categorie", style="dim")
    table.add_column("Prix", justify="right", style="yellow")
    table.add_column("Description", style="dim")
    for i, (item, price) in enumerate(items, start=1):
        table.add_row(str(i), item.name_fr, item.category, f"{price}", item.description_fr[:60])
    table.add_row("0", "[ne rien acheter]", "-", "-", "-")
    console.print(table)
    choice = Prompt.ask("[bold cyan]Item[/bold cyan]", default="0").strip()
    if choice in ("0", ""):
        return character
    try:
        idx = int(choice) - 1
        if not (0 <= idx < len(items)):
            return character
    except ValueError:
        return character
    item, price = items[idx]
    new_char, msg = buy_item(character, item, price)
    color = "green" if "Achete" in msg else "red"
    console.print(f"[{color}]{msg}[/{color}]")
    return new_char


def _shop_sell_flow(character):
    """Propose la revente d'items de l'inventaire."""
    from shinobi.engine.shop import ITEM_CATALOG, SELL_RATIO, get_inventory_summary, sell_item

    items = get_inventory_summary(character.inventory)
    if not items:
        console.print("[yellow]Inventaire vide.[/yellow]")
        return character
    table = Table(title="Revente (40% du prix d'achat)", header_style=COLOR_TITLE)
    table.add_column("#", style="bold cyan", justify="right")
    table.add_column("Item")
    table.add_column("Quantite", justify="right")
    table.add_column("Prix vente", justify="right", style="yellow")
    for i, (item_id, qty) in enumerate(items, start=1):
        item = ITEM_CATALOG.get(item_id)
        name = item.name_fr if item else item_id
        sell_price = int(item.base_price_ryos * SELL_RATIO) if item else 0
        table.add_row(str(i), name, str(qty), f"{sell_price}")
    table.add_row("0", "[ne rien vendre]", "-", "-")
    console.print(table)
    choice = Prompt.ask("[bold cyan]Item[/bold cyan]", default="0").strip()
    if choice in ("0", ""):
        return character
    try:
        idx = int(choice) - 1
        if not (0 <= idx < len(items)):
            return character
    except ValueError:
        return character
    item_id, _qty = items[idx]
    new_char, msg = sell_item(character, item_id)
    color = "green" if "Vendu" in msg else "red"
    console.print(f"[{color}]{msg}[/{color}]")
    return new_char


async def _pathfinder_flow(goal, character, world, canon, retriever, save_id: str) -> None:
    """Demande au pathfinder LLM le prochain pas vers un objectif."""
    from shinobi.goals.pathfinder import GoalPathfinder, PathfinderRequest
    from shinobi.llm.client import LLMClient

    async with LLMClient() as client:
        if not await client.health():
            console.print("[dim]Serveur LLM hors ligne[/dim]")
            return
        pathfinder = GoalPathfinder(client, canon, retriever)
        existing_breadcrumbs = save_module.load_breadcrumbs(save_id, parent_goal_id=goal.id)
        seq = len(existing_breadcrumbs) + 1
        request = PathfinderRequest(
            goal=goal,
            character_state_summary=(
                f"{character.name}, {character.age_years} ans, {character.rank} a {character.current_village}, "
                f"clan {character.clan or 'civil'}"
            ),
            current_year=world.current_year,
            sequence_index=seq,
        )
        response = await pathfinder.find_path(request)
        console.print(Panel(response.interpretation or "(pas d'interpretation)", title="Interpretation", border_style="cyan"))
        for bc in response.breadcrumbs:
            save_module.save_breadcrumb(save_id, bc)
            price_str = ""
            if bc.price_paid and bc.price_paid.type != "none":
                amt = f" ({bc.price_paid.amount})" if bc.price_paid.amount else ""
                price_str = f"\n  Prix : [{bc.price_paid.type}{amt}] {bc.price_paid.description}"
            console.print(
                Panel(
                    f"[bold]{bc.description}[/bold]\n"
                    f"  Base canonique : {bc.canonical_basis}"
                    + price_str,
                    title=f"Indice {bc.sequence_index} pour '{goal.description_player[:40]}'",
                    border_style="magenta",
                )
            )


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
    console.print(
        f"[green]Temps avance de {n}{unit}. Nouvelle date : an {new_date.year}, jour {new_date.date_str}[/green]"
    )
    return character, new_world


def _detect_present_npcs(intent_text: str, canon) -> list[str]:
    """Detecte les PNJ canon mentionnes dans l'intention du joueur.

    Cherche les noms exacts dans la description. Match insensible a la casse.
    Retourne max 5 PNJ pour ne pas surcharger le contexte.
    """
    lower = intent_text.lower()
    matches: list[str] = []
    for char_id, char in canon.characters.items():
        if not char.name_romaji:
            continue
        full_name = char.name_romaji.lower()
        if full_name in lower:
            matches.append(char_id)
            continue
        # Premier nom ou nom de famille separement (au moins 4 lettres)
        parts = full_name.split()
        for p in parts:
            if len(p) >= 4 and f" {p} " in f" {lower} ":
                matches.append(char_id)
                break
        if len(matches) >= 5:
            break
    return matches


async def _attempt_narration(
    character, world, canon, retriever, result, intent: str, parsed, *, present_npcs: list[str]
):
    async with LLMClient() as client:
        if not await client.health():
            return None
        narrator = Narrator(client, canon, retriever)
        npc_summary = (
            f"PNJ canon presents : {', '.join(present_npcs)}"
            if present_npcs
            else "Aucun PNJ canon nomme dans la scene. Si la situation implique des"
            " interlocuteurs (sensei, parent, marchand, etc.), invente leur id role-based"
            " (snake_case, ex: sensei_academie, marchand_taverne) et fais-les parler."
        )
        request = NarrationRequest(
            turn_summary=intent,
            action_text=intent,
            action_result_summary=result.summary_fr,
            location_id=character.current_location,
            present_npcs=present_npcs,
            active_breadcrumb_descriptions=[],
            character_state_summary=(
                f"{character.name}, {character.age_years} ans, "
                f"{character.rank} a {character.current_village}, "
                f"chakra {character.chakra.current}/{character.chakra.max}, "
                f"clan {character.clan or 'civil'}, "
                f"natures {', '.join(character.natures) or 'aucune'}, "
                f"action interpretee : {parsed.action_type.value}\n"
                f"{npc_summary}"
            ),
            duration_str=f"{result.duration_minutes // 60}h{result.duration_minutes % 60:02d}",
        )
        return await narrator.narrate(request)
