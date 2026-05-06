"""Boucle de jeu principale avec UI rich, missions, controles de duree."""

from __future__ import annotations

import asyncio
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from shinobi.agents import (
    ActionSelector,
    AgentMemoryStore,
    AgentRoster,
    AgentTier,
    EmbeddingsIndex,
    LLMCache,
    Reflector,
    TickEngine,
    initialize_roster,
    load_eras_data,
    try_load_bge_encoders,
)
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
from shinobi.dialogue import (
    DialogueFormatter,
    DialogueLog,
    DialogueLogConfig,
    export_to_vn_json,
)
from shinobi.engine.actions import (
    Action,
    ActionResult,
    ResolutionInputs,
    apply_action_to_state,
    apply_mission_result,
    resolve_action,
)
from shinobi.engine.economy import cost_of_living_for_period, format_ryos
from shinobi.engine.events import tick_scheduler
from shinobi.engine.interpreter import interpret
from shinobi.engine.locations import travel_minutes
from shinobi.engine.missions import list_available_missions
from shinobi.engine.progression import advance_age, apply_damage, apply_fatigue
from shinobi.engine.relations import add_reputation, decay_affinities, touch_relationship
from shinobi.engine.rng import next_seed
from shinobi.engine.rumors import player_can_hear, receive_rumor
from shinobi.engine.time import advance_time
from shinobi.engine.world import NPCState
from shinobi.llm.client import LLMClient
from shinobi.llm.narration import NarrationRequest, Narrator
from shinobi.persistence import saves as save_module
from shinobi.personality import (
    PersonalityEngine,
    PersonalityStore,
    collect_experienced_events,
    extract_baselines_combined,
)
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
    "/biography": "Affiche le journal biographique (rank-ups, techniques apprises, traumas)",
    "/knowledge": "Affiche ce que tu sais (events canon, secrets reveles)",
    "/rumors": "Affiche les rumeurs entendues",
    "/breadcrumbs": "Affiche les sous-objectifs reveles non encore accomplis",
    "/weapons": "Liste tes armes equipees",
    "/summons": "Liste tes contrats d'invocation",
    "/sign_contract <name>": "Signe un contrat d'invocation (toad, snake, slug, hawk, etc.)",
    "/invoke <name>": "Tente d'invoquer une creature (consomme 30 chakra)",
    "/skip <duree>": "Saute le temps : '/skip 7d' pour 7 jours, '/skip 1m' pour 1 mois",
    "/journal": "Indique ou se trouve le journal",
    "/dialogues": "Affiche les N dernieres lignes de dialogue capturees (style VN)",
    "/export-vn-dialogues <path>": "Exporte le log des dialogues au format JSON VN",
    "/personality <npc_id>": "Affiche le vecteur de personnalite + drift d'un PNJ (Phase D)",
    "/agents": "Liste les agents Phase E (top-15 + secondary 50)",
    "/agent <npc_id>": "Inspecte la memoire 3-niveaux + dernieres actions d'un agent",
    "/fast-forward <mois>": "Simule N mois sans le joueur (digest des events)",
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

    # Bootstrap RAG : telecharge l'index pre-build depuis GitHub Releases si manquant
    # ou desynchronise du canon. Fallback : build local.
    try:
        from shinobi.rag.bootstrap import bootstrap_index

        bootstrap_index(console=console)
    except Exception as exc:
        console.print(f"[dim]Bootstrap RAG echoue : {type(exc).__name__}[/dim]")
    # Bootstrap LLM : assure que llama-server tourne en arriere-plan.
    try:
        from shinobi.llm.server_bootstrap import ensure_llm_server

        ensure_llm_server(console=console)
    except Exception as exc:
        console.print(f"[dim]Bootstrap LLM ignore : {type(exc).__name__}[/dim]")
    store = ChromaStore()

    retriever = Retriever(store, canon)
    turn = meta.total_turns
    last_proposed: list[dict] = []
    pending_missions: list = []

    # Instancie le DialogueLog (rolling window 5000) + DialogueFormatter (parser
    # narrative -> DialogueLines). Le log est persiste dans la save et restaure
    # a la reprise. L'archive offload se declenche automatiquement avant overflow.
    dialogue_log = _load_or_create_dialogue_log(save_id)
    dialogue_formatter = DialogueFormatter()

    # Phase A/B/C : KG dynamique + missions canon. Auto-load idempotent : ne ré-importe
    # que si la base SQLite est manquante ou ne contient aucun fact mission. La base
    # est per-save (kg.sqlite) pour permettre des univers divergents par save.
    try:
        _ensure_kg_initialized(save_id, canon, console=console)
    except Exception as exc:
        console.print(f"[dim]KG init ignore : {type(exc).__name__}[/dim]")

    # Phase D : extrait baselines vectorielles + restaure les drifts
    # accumules en cours de partie (per-save). Idempotent : ne re-extrait
    # baseline que si la base est vide pour ce NPC.
    try:
        _ensure_personality_initialized(save_id, console=console)
    except Exception as exc:
        console.print(f"[dim]Personality init ignore : {type(exc).__name__}[/dim]")
    personality_engine = PersonalityEngine()

    # Phase E : agents multi-agent (top-15 + secondary 50). Idempotent : ne
    # re-initialise pas le roster si la base est deja peuplee. La simulation
    # active reste en mode opt-in : l'utilisateur appelle /fast-forward pour
    # tick le monde sans le joueur.
    try:
        _ensure_agents_initialized(save_id, world.current_year, console=console)
    except Exception as exc:
        console.print(f"[dim]Agents init ignore : {type(exc).__name__}[/dim]")

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
                intent_text, character, world, save_id, canon, pending_missions,
                retriever=retriever,
                dialogue_log=dialogue_log,
            )
            if not should_continue:
                save_module.append_narrative_log(
                    save_id,
                    {"turn": turn, "year": world.current_year, "type": "session_end"},
                )
                # Persiste le log VN avant de quitter pour que /export-vn-dialogues
                # puisse etre invoque depuis l'exterieur sur un dump complet.
                try:
                    dialogue_log.to_jsonl_file(save_module.dialogue_log_path(save_id))
                except Exception:
                    pass
                console.print("[green]Sauvegarde effectuee. Retour au menu.[/green]")
                return
            turn -= 1
            continue

        if intent_text.isdigit() and last_proposed:
            idx = int(intent_text) - 1
            if 0 <= idx < len(last_proposed):
                intent_text = last_proposed[idx].get("label_fr", intent_text)

        # Interpretation de l'intention. Si l'heuristique tombe en custom,
        # on tente une re-interpretation par le LLM character_interpreter
        # (qui maitrise mieux les phrases complexes ou non-canoniques).
        parsed = interpret(intent_text)
        if parsed.action_type == ActionType.custom and not parsed.parameters.get("_desert"):
            parsed = _llm_reinterpret_if_custom(parsed, intent_text, character, world)

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

        # Route declare_goal via texte libre (raccourci sans /declare)
        if parsed.action_type == ActionType.declare_goal:
            from shinobi.goals.declaration import declare_goal as _declare

            description = parsed.parameters.get("description") or intent_text
            goal = _declare(
                description_player=description,
                interpretation_canonical=description,
                declared_at_year=world.current_year,
                declared_at_age=character.age_years,
            )
            save_module.save_goal(save_id, goal)
            console.print(f"[green]Objectif declare : {goal.id[:8]} — {description}[/green]")
            turn -= 1
            continue

        # Route request_objective_path via texte libre : interroge le pathfinder sur le dernier goal actif
        if parsed.action_type == ActionType.request_objective_path:
            goals = [g for g in save_module.load_goals(save_id) if g.status.value == "declared"]
            if not goals:
                console.print("[yellow]Aucun objectif declare. Utilise /declare ou tape \"je declare un objectif: ...\".[/yellow]")
                turn -= 1
                continue
            target_goal = goals[-1]
            console.print(f"[cyan]Pathfinder pour : {target_goal.description_player}[/cyan]")
            try:
                asyncio.run(_pathfinder_flow(target_goal, character, world, canon, retriever, save_id))
            except Exception as exc:
                console.print(f"[dim]Pathfinder LLM indisponible ({type(exc).__name__})[/dim]")
            turn -= 1
            continue

        # Route pay_for_information : tente de reveler un breadcrumb cache du goal le plus prioritaire
        if parsed.action_type == ActionType.pay_for_information:
            amount = int(parsed.parameters.get("amount_ryos") or 100)
            character, world = _pay_for_information_flow(
                character, world, save_id, amount=amount
            )
            turn -= 1
            continue

        # Route desert : abandon du village
        if parsed.parameters.get("_desert"):
            character, world = _desertion_flow(character, world)
            turn -= 1
            continue

        # Route move avec destination : voyage inter-villages
        if parsed.action_type == ActionType.move and parsed.parameters.get("target_location"):
            character, world = _travel_flow(character, world, str(parsed.parameters["target_location"]))
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

        prev_date = GameDate(
            year=world.current_year,
            month=int(world.current_date.split("-")[0]),
            day=int(world.current_date.split("-")[1]),
            hour=world.current_hour,
            minute=world.current_minute,
        )
        new_date = advance_time(prev_date, result.duration_minutes)
        days_passed = max(0, result.duration_minutes // (24 * 60))
        world = world.with_time(
            year=new_date.year,
            date=new_date.date_str,
            hour=new_date.hour,
            minute=new_date.minute,
        )
        seed_after = next_seed(result.seed_after) & 0x7FFFFFFFFFFFFFFF
        world = world.model_copy(update={"seed": seed_after})
        world, fired, cancelled = tick_scheduler(world, canon, turn_number=turn)

        # Cout de vie : prelevement quotidien si l'action a couvert au moins une journee
        character_pre_living = character
        character = _charge_living_cost(character, world, days_passed=days_passed)

        # Knowledge : les events fired ce tour deviennent connus du joueur
        character = _record_fired_events_as_known(character, fired, canon, world.current_year)

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

        # Annonce des rumeurs nouvellement nees que le joueur peut entendre
        world = _announce_new_rumors(world, character, fired_event_ids={f.event_id for f in fired}, canon=canon)

        # Phase D : drift de personnalite des PNJ impliques dans les events
        # fired ce tour. Le bridge convertit canon TimelineEvent -> ExperiencedEvent
        # et l'engine applique avec saturation sigmoid + persiste l'historique.
        if fired:
            try:
                _apply_personality_drift_for_fired(
                    save_id, fired, canon, personality_engine,
                )
            except Exception as exc:
                console.print(f"[dim]Drift Phase D ignore : {type(exc).__name__}[/dim]")

        # Vieillissement : si l'annee a change, remettre l'age en phase et appliquer aging_decay/growth
        character = _age_character_if_needed(character, world)

        # Decay des relations non entretenues (lent, juste a chaque changement d'annee)
        character = decay_affinities(character, current_year=world.current_year)

        # Mise a jour npc_states + touch_relationship pour les PNJ canon mentionnes ce tour
        present_npcs = _detect_present_npcs(intent_text, canon)
        if present_npcs:
            world, character = _touch_present_npcs(world, character, present_npcs, canon)
            # Spec §6.4 : 'eleves au statut d'agent uniquement si le joueur
            # interagit avec eux'. Auto-promote background -> secondary.
            try:
                _promote_npcs_on_player_interaction(
                    save_id, present_npcs,
                    year=world.current_year, tick=turn,
                )
            except Exception:
                pass

        # Verification automatique des Goals declares
        completed_goals = _check_goal_completions(save_id, character, world.current_year)
        for goal_desc in completed_goals:
            console.print(f"  [bold magenta]>>> Objectif accompli :[/bold magenta] {goal_desc}")

        # Biographie : detecte rank-up, technique apprise, blessure grave, mort frolee
        character = _log_biography_milestones(
            character_pre_living, character, world, parsed.action_type, result
        )

        # Auto-trigger : si reputation village trop basse, propose la desertion
        character, world = _maybe_auto_desert(character, world)

        # Pre-injecte les NPCs majeurs plausibles dans la scene (meme village,
        # plage d'age compatible) en plus de ceux nommes dans l'intent. Le LLM
        # recoit ainsi les fact sheets de TOUS les NPCs qu'il pourrait inventer,
        # sait leurs ages et situations, et ne peut plus les sortir n'importe ou.
        from shinobi.canon.fact_sheet import find_contextual_npcs

        scene_npcs = find_contextual_npcs(
            canon,
            current_year=world.current_year,
            player_village=character.current_village,
            player_age=character.age_years,
            extra_ids=present_npcs,
            max_count=8,
        )

        last_proposed = []
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
                    present_npcs=scene_npcs,
                    dialogue_formatter=dialogue_formatter,
                    dialogue_log=dialogue_log,
                    turn_number=turn,
                )
            )
            if narration is not None:
                console.print(Panel(narration.narrative, title="Narration", border_style="cyan"))
                if narration.npc_dialogue:
                    print_dialogue(console, canon, narration.npc_dialogue)
                last_proposed = narration.proposed_actions or []
                for obs in narration.world_observations:
                    console.print(f"  [dim cyan]Observation :[/dim cyan] {obs}")
            else:
                _render_mechanical_narration(result, parsed, character, world)
        except Exception as exc:
            console.print(f"[dim]Narration LLM indisponible ({type(exc).__name__})[/dim]")
            _render_mechanical_narration(result, parsed, character, world)

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
            # Persiste le log VN (rolling window). L'archive est geree automatiquement
            # par DialogueLog.append() quand le seuil est atteint.
            try:
                dialogue_log.to_jsonl_file(save_module.dialogue_log_path(save_id))
            except Exception:
                pass
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


def _handle_meta(
    command: str,
    character,
    world,
    save_id: str,
    canon,
    pending_missions: list,
    retriever=None,
    *,
    dialogue_log: DialogueLog | None = None,
):
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

        items = get_inventory_summary(character.inventory, character.weapons)
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
    elif command == "/biography":
        _print_biography(character)
    elif command == "/knowledge":
        _print_knowledge(character)
    elif command == "/rumors":
        _print_rumors(world, canon)
    elif command == "/breadcrumbs":
        _print_breadcrumbs(save_id)
    elif command == "/weapons":
        _print_weapons(character)
    elif command == "/summons":
        _print_summons(character)
    elif command.startswith("/sign_contract"):
        parts = command.split(maxsplit=1)
        if len(parts) < 2:
            console.print("[red]Usage : /sign_contract <name> (ex: toad, snake, slug, hawk)[/red]")
        else:
            character = _sign_contract_flow(character, parts[1].strip().lower())
    elif command.startswith("/invoke"):
        parts = command.split(maxsplit=1)
        if len(parts) < 2:
            console.print("[red]Usage : /invoke <name>[/red]")
        else:
            character = _invoke_flow(character, parts[1].strip().lower())
    elif command.startswith("/skip"):
        character, world = _skip_time(command, character, world)
    elif command == "/journal":
        console.print(f"[dim]Journal : data/saves/{save_id}/narrative_log.jsonl[/dim]")
    elif command == "/dialogues":
        _print_dialogue_log(dialogue_log)
    elif command.startswith("/export-vn-dialogues"):
        parts = command.split(maxsplit=1)
        # Default path : <saves_dir>/<save_id>/vn_dialogues.json
        from shinobi.config import settings as _s

        default_path = _s.saves_dir / save_id / "vn_dialogues.json"
        target = Path(parts[1].strip()) if len(parts) > 1 and parts[1].strip() else default_path
        _export_vn_dialogues(dialogue_log, target)
    elif command.startswith("/personality"):
        parts = command.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            console.print("[red]Usage : /personality <npc_id>[/red]")
        else:
            _print_personality(save_id, parts[1].strip())
    elif command == "/agents":
        _print_agents_roster(save_id)
    elif command.startswith("/agent "):
        parts = command.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            console.print("[red]Usage : /agent <npc_id>[/red]")
        else:
            _print_agent_detail(save_id, parts[1].strip())
    elif command.startswith("/fast-forward"):
        parts = command.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            console.print("[red]Usage : /fast-forward <mois>[/red]")
        else:
            try:
                months = int(parts[1].strip())
            except ValueError:
                console.print("[red]Mois doit etre un entier[/red]")
            else:
                asyncio.run(_run_fast_forward(save_id, world.current_year, months))
    else:
        console.print(f"[red]Commande inconnue : {command}[/red] (tape [cyan]/help[/cyan])")
    return True, character, world


def _print_dialogue_log(dialogue_log: DialogueLog | None) -> None:
    """Affiche les dernieres lignes capturees du log VN."""
    if dialogue_log is None:
        console.print("[yellow]Log de dialogues VN non initialise.[/yellow]")
        return
    if dialogue_log.size == 0:
        console.print(Panel("Aucun dialogue capture pour l'instant.", title="Dialogues VN"))
        return
    last = dialogue_log.last_n(20)
    lines = []
    for d in last:
        prefix = "*" if d.is_thought else ""
        loc = f" @{d.location_id}" if d.location_id else ""
        year = f" an {d.in_game_year}" if d.in_game_year is not None else ""
        lines.append(
            f"  [cyan]{d.speaker_id}[/cyan] [{d.emotion.value}/{d.tone.value}]{year}{loc}: "
            f"{prefix}{d.text}{prefix}"
        )
    console.print(
        Panel(
            "\n".join(lines),
            title=f"Dialogues VN ({dialogue_log.size}/{dialogue_log.max_size})",
            border_style="cyan",
        )
    )


def _print_personality(save_id: str, npc_id: str) -> None:
    """Affiche le vecteur courant + baseline + top-3 drifts d'un NPC."""
    db_path = save_module.personality_db_path(save_id)
    if not db_path.exists():
        console.print(
            "[yellow]Aucune base de personnalite (Phase D non initialisee).[/yellow]"
        )
        return
    with PersonalityStore(db_path) as store:
        personality = store.get_personality(npc_id)
    if personality is None:
        console.print(f"[yellow]Aucune personnalite enregistree pour {npc_id}.[/yellow]")
        return
    engine = PersonalityEngine()
    top_drifted = engine.top_drifted_dimensions(personality, n=5)
    lines = [
        f"  Divergence canon : [bold magenta]{personality.divergence_from_canon():.3f}[/bold magenta]",
        f"  Drifts appliques : {len(personality.drift_history)}",
        "",
        "  [cyan]Top dimensions ayant drifte :[/cyan]",
    ]
    for dim, _mag in top_drifted:
        cur = personality.value(dim)
        base = personality.baseline(dim)
        delta = cur - base
        sign = "+" if delta >= 0 else ""
        lines.append(
            f"    {dim.value:14s} {base:.2f} -> {cur:.2f} ({sign}{delta:.3f})"
        )
    if personality.drift_history:
        lines.append("")
        lines.append("  [cyan]5 derniers drifts :[/cyan]")
        for d in list(personality.drift_history)[-5:]:
            lines.append(
                f"    [dim]an {d.year}[/dim] {d.rule_name}"
                + (f" (re: {d.related_npc_id})" if d.related_npc_id else "")
            )
    console.print(
        Panel("\n".join(lines), title=f"Personnalite : {npc_id}", border_style="magenta")
    )


def _export_vn_dialogues(dialogue_log: DialogueLog | None, target: Path) -> None:
    """Exporte le log au format VN_PAYLOAD_VERSION_1."""
    if dialogue_log is None:
        console.print("[yellow]Log de dialogues VN non initialise.[/yellow]")
        return
    if dialogue_log.size == 0:
        console.print("[yellow]Aucun dialogue a exporter.[/yellow]")
        return
    try:
        n = export_to_vn_json(dialogue_log.all(), target)
        console.print(
            f"[green]Export VN reussi : {n} ligne(s) ecrite(s) dans {target}[/green]"
        )
    except Exception as exc:
        console.print(f"[red]Echec export VN : {type(exc).__name__}: {exc}[/red]")


def _missions_flow(character, world, save_id: str, canon):
    """Affiche les missions disponibles, propose d'en accepter une."""
    missions = list_available_missions(
        player_rank=character.rank,
        count=5,
        seed=int(world.seed) % 100000,
        global_tension=world.political_climate.global_tension,
        inflation_factor=world.economy.inflation_factor,
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
    # Trace la mission dans la biographie (succes ou echec significatif)
    from shinobi.engine.character import BiographyEvent

    summary = (
        f"Mission {mission.rank} reussie : {mission.title}"
        if success
        else f"Mission {mission.rank} echouee : {mission.title}"
    )
    bio = BiographyEvent(
        year=world.current_year,
        age=new_char.age_years,
        summary=summary,
        category="achievement" if success else "trauma",
    )
    new_char = new_char.add_biography_event(bio)

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
    """Propose la revente d'items de l'inventaire (et des armes)."""
    from shinobi.engine.shop import ITEM_CATALOG, SELL_RATIO, get_inventory_summary, sell_item

    items = get_inventory_summary(character.inventory, character.weapons)
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
    # Le perso vieillit avec aging_decay/growth (pas juste +N ans naif)
    character = _age_character_if_needed(character, new_world)
    console.print(
        f"[green]Temps avance de {n}{unit}. Nouvelle date : an {new_date.year}, jour {new_date.date_str}[/green]"
    )
    return character, new_world


from shinobi.canon.fact_sheet import PRIMARY_NPC_NAMES  # noqa: E402  (re-export)


def _detect_present_npcs(intent_text: str, canon) -> list[str]:
    """Detecte les PNJ canon mentionnes dans l'intention du joueur.

    Strategie :
    1. Tokenise l'intent en mots (word boundaries).
    2. Match prioritaire via PRIMARY_NPC_NAMES (noms canoniques usuels) -> id
       principal direct, evite les ambiguites (Naruto, Sasuke, etc.).
    3. Fallback : scan generique avec word boundary stricte. Si un nom court
       comme 'naruto' a deja matche un primary, on skip les variants
       (Naruto Musasabi, Nine-Tailed Naruto Clone) qui contiendraient le meme.
    """
    import re as _re

    lower = intent_text.lower()
    # Tokenise pour des matches mot-a-mot reels
    tokens = set(_re.findall(r"[a-z]+(?:[\'-][a-z]+)*", lower))
    matches: list[str] = []
    primary_short_names_used: set[str] = set()

    # Passe 1 : noms canoniques usuels (strict, mot complet)
    for short, canonical_id in PRIMARY_NPC_NAMES.items():
        # Match si le short est present comme mot/groupe dans les tokens
        # (gere "killer bee" en deux mots aussi)
        if " " in short:
            if short in lower:
                matched = True
            else:
                matched = False
        else:
            matched = short in tokens
        if matched and canonical_id in canon.characters and canonical_id not in matches:
            matches.append(canonical_id)
            primary_short_names_used.add(short)
            if len(matches) >= 5:
                return matches

    # Passe 2 : fallback generique (filtre les NPCs courts ou auxiliaires)
    banned_as_name = {"sensei", "maitre", "sage", "ami", "amis", "anko"}
    for char_id, char in canon.characters.items():
        if char_id in matches:
            continue
        if not char.name_romaji:
            continue
        full_name = char.name_romaji.lower()
        # Skip noms trop courts (C, J, K, Emi, Sen) qui generent faux positifs
        if len(full_name) < 5:
            continue
        # Skip si le nom contient un primary deja matche (variants/clones)
        if any(s in full_name for s in primary_short_names_used):
            continue
        # Skip noms communs (sensei_kabutos, etc.)
        if any(bw in full_name.split() for bw in banned_as_name):
            continue
        # Match strict mot-a-mot
        full_tokens = set(_re.findall(r"[a-z]+(?:[' -][a-z]+)*", full_name))
        # Le nom complet doit avoir au moins un token de >= 5 chars en commun
        common_long = {t for t in full_tokens & tokens if len(t) >= 5}
        if common_long:
            matches.append(char_id)
            if len(matches) >= 5:
                break
    return matches


def _load_or_create_dialogue_log(save_id: str) -> DialogueLog:
    """Charge le DialogueLog persiste pour cette save, ou en cree un neuf.

    L'archive_path est branche sur la save : quand le rolling window approche
    de sa borne (5000 lignes), DialogueLog.append() offload les anciennes
    vers `dialogues_archive.jsonl` automatiquement, evitant la perte memoire.
    """
    archive_path = save_module.dialogue_archive_path(save_id)
    config = DialogueLogConfig(archive_path=archive_path)
    log_path = save_module.dialogue_log_path(save_id)
    if log_path.exists():
        try:
            return DialogueLog.from_jsonl_file(log_path, config=config)
        except Exception:
            # Corruption tolerable : on repart d'un log vide plutot que crasher
            return DialogueLog(config=config)
    return DialogueLog(config=config)


def _ensure_kg_initialized(save_id: str, canon, *, console=None) -> None:
    """Initialise le KG dynamique si la base est vide (idempotent).

    - Importe les datasets canon (characters, techniques, clans, ...) une fois
    - Importe missions.json s'il existe et qu'aucun fact mission n'est present
    Bootstrap idempotent : detecte deja-importe via comptage des facts.
    """
    from shinobi.config import settings as _s
    from shinobi.kg.loader import import_canon_to_kg
    from shinobi.kg.store import KnowledgeGraphStore
    from shinobi.missions.catalog import MissionCatalog
    from shinobi.missions.kg_integration import import_missions_to_kg

    db_path = save_module.kg_db_path(save_id)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with KnowledgeGraphStore(db_path) as store:
        # Canon : on importe si la base est vide
        existing_canon_facts = store.count(source_prefix="canon")
        if existing_canon_facts == 0:
            stats = import_canon_to_kg(store, _s.canonical_data_dir, clear_first=False)
            if console is not None:
                console.print(
                    f"[dim]KG canon importe : {stats.get('total', 0)} facts[/dim]"
                )

        # Missions : auto-load idempotent
        missions_path = _s.canonical_data_dir / "missions.json"
        if missions_path.exists():
            existing_mission_facts = store.count(source_prefix="mission:")
            if existing_mission_facts == 0:
                catalog = MissionCatalog.from_json_file(missions_path)
                if catalog.count > 0:
                    stats = import_missions_to_kg(
                        store, catalog.all(), clear_first=False,
                    )
                    if console is not None:
                        console.print(
                            f"[dim]KG missions importees : "
                            f"{stats.get('missions_imported', 0)} missions, "
                            f"{stats.get('facts_inserted', 0)} facts[/dim]"
                        )


def _promote_npcs_on_player_interaction(
    save_id: str, npc_ids: list[str], *, year: int, tick: int,
) -> None:
    """Spec §6.4 : promote chaque NPC cite par le joueur (background -> secondary)."""
    db_path = save_module.agents_db_path(save_id)
    if not db_path.exists():
        return
    with AgentMemoryStore(db_path) as store:
        roster = AgentRoster(store)
        for nid in npc_ids:
            roster.on_player_interaction(nid, year=year, tick=tick)


def _ensure_agents_initialized(
    save_id: str, current_year: int, *, console=None,
) -> None:
    """Initialise le roster Phase E (top-15 + secondary 50 + arc dynamique).
    Idempotent. Spec §6.1 'top-15 + dynamique selon arc'."""
    from shinobi.config import settings as _s

    db_path = save_module.agents_db_path(save_id)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with AgentMemoryStore(db_path) as store:
        existing = len(store.list_roster())
        if existing == 0:
            roster = initialize_roster(store, included_since_year=current_year)
            if console is not None:
                console.print(
                    f"[dim]Agents Phase E initialises : "
                    f"{roster.major_count} majors + "
                    f"{roster.secondary_count} secondary[/dim]"
                )
        else:
            roster = AgentRoster(store)

        # Spec §6.1 'dynamique selon arc' : promote les key_figures de l'ere
        # courante en secondary (s'ils ne sont pas deja major).
        eras_path = _s.canonical_data_dir / "eras.json"
        eras_data = load_eras_data(eras_path)
        if eras_data:
            promoted = roster.promote_arc_relevant(current_year, eras_data)
            if promoted and console is not None:
                console.print(
                    f"[dim]Arc-relevant NPCs promus : "
                    f"{', '.join(promoted)}[/dim]"
                )


def _print_agents_roster(save_id: str) -> None:
    """Liste les agents top-15 / secondary-50 avec last_active."""
    db_path = save_module.agents_db_path(save_id)
    if not db_path.exists():
        console.print("[yellow]Roster Phase E non initialise.[/yellow]")
        return
    with AgentMemoryStore(db_path) as store:
        majors = store.list_roster(tier=AgentTier.major)
        secondary = store.list_roster(tier=AgentTier.secondary)

    lines = ["[cyan]Top-15 (simulation active chaque tick) :[/cyan]"]
    for e in majors:
        last = (
            f" last={e.last_active_year}/{e.last_active_tick}"
            if e.last_active_year is not None else ""
        )
        lines.append(f"  {e.npc_id}{last}")
    lines.append("")
    lines.append(f"[cyan]Secondary ({len(secondary)}, par lot tous les 10 ticks) :[/cyan]")
    for e in secondary[:10]:
        lines.append(f"  {e.npc_id}")
    if len(secondary) > 10:
        lines.append(f"  ... ({len(secondary) - 10} de plus)")
    console.print(
        Panel("\n".join(lines), title="Roster agents Phase E", border_style="cyan")
    )


def _print_agent_detail(save_id: str, npc_id: str) -> None:
    """Affiche memoire 3-niveaux + dernieres actions d'un agent."""
    db_path = save_module.agents_db_path(save_id)
    if not db_path.exists():
        console.print("[yellow]Roster Phase E non initialise.[/yellow]")
        return
    with AgentMemoryStore(db_path) as store:
        entry = store.get_roster_entry(npc_id)
        memory = store.load_memory(npc_id)
        actions = store.list_actions(npc_id, limit=10)
    if entry is None and memory.size == 0:
        console.print(f"[yellow]Aucun agent enregistre pour {npc_id}.[/yellow]")
        return
    tier = entry.tier.value if entry else "background"
    lines = [
        f"  Tier : [bold]{tier}[/bold]",
        f"  Memoire : {len(memory.observations)} obs + "
        f"{len(memory.reflections)} refl + {len(memory.plans)} plans",
        "",
    ]
    if memory.observations:
        lines.append("[cyan]3 dernieres observations :[/cyan]")
        for o in list(memory.observations)[-3:]:
            txt = o.text[:80]
            lines.append(f"  [dim]an {o.year}[/dim] {txt}")
    if memory.reflections:
        lines.append("")
        lines.append("[cyan]3 dernieres reflections :[/cyan]")
        for r in list(memory.reflections)[-3:]:
            lines.append(f"  [dim]an {r.year}[/dim] {r.gist or r.text[:80]}")
    if actions:
        lines.append("")
        lines.append(f"[cyan]Dernieres {len(actions)} actions :[/cyan]")
        for a in actions[-5:]:
            content = a.content[:60] if a.content else ""
            lines.append(f"  [dim]an {a.year}[/dim] {a.type.value}: {content}")
    console.print(
        Panel("\n".join(lines), title=f"Agent : {npc_id}", border_style="cyan")
    )


async def _run_fast_forward(
    save_id: str, current_year: int, months: int,
) -> None:
    """Simule N mois sans player input. Affiche le digest."""
    if months <= 0 or months > 60:
        console.print("[red]Mois doit etre dans [1, 60][/red]")
        return
    db_path = save_module.agents_db_path(save_id)
    cache_path = save_module.llm_cache_db_path(save_id)
    emb_path = save_module.agents_embeddings_db_path(save_id)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    console.print(
        f"[cyan]Fast-forward {months} mois en cours (mode passif)...[/cyan]"
    )
    # Spec §6.1 : BGE-M3 wired si dispo. Fallback gracieux Jaccard sinon.
    bge_encoder, bge_query = try_load_bge_encoders()
    embeddings_idx = EmbeddingsIndex(
        emb_path, encoder=bge_encoder, query_encoder=bge_query,
    )
    if bge_encoder is not None:
        console.print("[dim]BGE-M3 actif pour retrieval semantique[/dim]")

    with AgentMemoryStore(db_path) as store, LLMCache(cache_path) as cache, \
            embeddings_idx as emb_idx:
        from shinobi.agents import AgentRoster

        roster = AgentRoster(store)
        if roster.major_count == 0:
            initialize_roster(store, included_since_year=current_year)
            roster = AgentRoster(store)
        # LLM call=None : utilise le fallback deterministe (frugal)
        engine = TickEngine(
            roster=roster, memory_store=store,
            selector=ActionSelector(cache=cache),
            reflector=Reflector(cache=cache),
            cache=cache,
            embeddings_index=emb_idx,
        )
        digest = await engine.fast_forward(
            from_year=current_year, months=months,
        )

    lines = [
        f"  Annees : {digest.from_year} -> {digest.to_year}",
        f"  Ticks simules : {digest.ticks_simulated}",
        f"  Actions totales : {digest.actions_total}",
        f"  Agents actifs : {len(digest.npcs_active)}",
        f"  Cache hit rate : {digest.cache_hit_rate:.1%}",
    ]
    if digest.entries:
        lines.append("")
        lines.append(f"[cyan]Digest ({len(digest.entries)} events importants) :[/cyan]")
        for e in digest.entries[:20]:
            lines.append(f"  [dim]an {e.year}[/dim] {e.headline[:90]}")
        if len(digest.entries) > 20:
            lines.append(f"  ... ({len(digest.entries) - 20} de plus)")
    else:
        lines.append("")
        lines.append("[dim]Aucun event marquant detecte (importance < seuil).[/dim]")

    console.print(
        Panel("\n".join(lines), title=f"Fast-forward {months} mois", border_style="magenta")
    )


def _ensure_personality_initialized(save_id: str, *, console=None) -> None:
    """Initialise le store de personnalite si vide : extrait baselines pour
    TOUS les NPCs presents dans psycho_notes.json + characters.json
    (wiki_sections + personality_fr). Idempotent.

    Si la base est deja peuplee, on ne fait rien (les drifts deja accumules
    en cours de partie sont preserves).
    """
    from shinobi.config import settings as _s

    db_path = save_module.personality_db_path(save_id)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    psycho_path = _s.canonical_data_dir / "psycho_notes.json"
    chars_path = _s.canonical_data_dir / "characters.json"
    if not psycho_path.exists() and not chars_path.exists():
        return

    with PersonalityStore(db_path) as store:
        existing = {p.npc_id for p in store.list_personalities()}
        baselines = extract_baselines_combined(
            psycho_notes_path=psycho_path if psycho_path.exists() else None,
            characters_path=chars_path if chars_path.exists() else None,
        )
        new_count = 0
        for npc_id, p in baselines.items():
            if npc_id in existing:
                continue
            store.upsert_personality(p)
            new_count += 1
        if new_count > 0 and console is not None:
            console.print(
                f"[dim]Personality baselines extraits : {new_count} PNJ "
                f"(wiki_sections + psycho_notes)[/dim]",
            )


def _apply_personality_drift_for_fired(
    save_id: str,
    fired,
    canon,
    engine: PersonalityEngine,
) -> None:
    """Applique le drift de personnalite aux PNJ impliques dans les events
    fired ce tour. Le bridge transcrit canon -> ExperiencedEvent.
    """
    if not fired:
        return
    canon_events = []
    for f in fired:
        ev = canon.timeline_events.get(f.event_id)
        if ev is not None:
            canon_events.append(ev)
    if not canon_events:
        return
    experienced = collect_experienced_events(timeline_events=canon_events)
    if not experienced:
        return

    db_path = save_module.personality_db_path(save_id)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Group by npc pour minimiser les ouvertures
    per_npc: dict[str, list] = {}
    for e in experienced:
        per_npc.setdefault(e.npc_id, []).append(e)
    with PersonalityStore(db_path) as store:
        for npc_id, events in per_npc.items():
            personality = store.get_personality(npc_id)
            if personality is None:
                # Pas de baseline pour ce NPC : on skip (vector neutre n'est
                # pas utile a drifter sans canon_baseline informatif)
                continue
            personality = engine.apply_events(personality, events)
            store.save_personality_with_history(personality)


async def _attempt_narration(
    character, world, canon, retriever, result, intent: str, parsed, *,
    present_npcs: list[str],
    dialogue_formatter=None,
    dialogue_log=None,
    turn_number: int | None = None,
):
    async with LLMClient() as client:
        if not await client.health():
            return None
        narrator = Narrator(
            client, canon, retriever,
            dialogue_formatter=dialogue_formatter,
            dialogue_log=dialogue_log,
        )
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
            current_village=character.current_village,
            player_clan=character.clan,
            player_kekkei_genkai=list(character.kekkei_genkai),
            player_tailed_beast=character.tailed_beast,
            player_name=character.name,
            # Amities etablies = relations canon dont l'affinity > 30 (seuil amical)
            established_npc_friendships={
                rel.with_character_id for rel in character.relationships if rel.affinity > 30
            },
            # Contexte VN : permet a Narrator._capture_dialogues() d'attribuer
            # year/turn/date corrects a chaque DialogueLine produit.
            turn_number=turn_number,
            in_game_year=world.current_year,
            in_game_date=world.current_date,
        )
        return await narrator.narrate(request)


def _announce_new_rumors(world, character, *, fired_event_ids: set[str], canon):
    """Affiche les rumeurs nouvellement nees que le joueur peut entendre.

    Marque chaque rumeur affichee comme received_by_player pour eviter le doublon.
    """
    if not world.rumors:
        return world
    new_world = world
    for rumor in world.rumors:
        if rumor.received_by_player:
            continue
        if rumor.born_at_year != world.current_year:
            # On ne propage que les rumeurs fraiches (sinon on deverserait tout au load).
            continue
        # Localise l'evenement pour determiner le radius effectif
        event_location = character.current_location  # fallback : meme lieu
        if rumor.source_event_id:
            ev = canon.timeline_events.get(rumor.source_event_id)
            if ev and ev.location:
                event_location = ev.location
        if not player_can_hear(
            rumor,
            player_location=character.current_location,
            event_location=event_location,
            current_year=world.current_year,
        ):
            continue
        is_canon_fired = rumor.source_event_id in fired_event_ids
        prefix = "[bold yellow]Rumeur" + (" canon" if is_canon_fired else "") + " :[/bold yellow]"
        console.print(
            Panel(
                f"{prefix} {rumor.content}\n[dim](fidelite {rumor.fidelity:.2f}, diffusion {rumor.diffusion_radius})[/dim]",
                border_style="yellow",
            )
        )
        new_world = receive_rumor(new_world, rumor.id, year=world.current_year)
    return new_world


def _age_character_if_needed(character, world):
    """Aligne age_years sur birth_year vs current_year et applique aging_decay/growth.

    Appele apres chaque tour : si le tour a fait passer un anniversaire,
    le personnage vieillit d'une annee et ses stats sont ajustees.
    """
    expected_age = world.current_year - character.birth_year
    if expected_age <= character.age_years:
        return character
    # advance_age applique aging_decay/growth interne
    return advance_age(character, expected_age)


def _check_goal_completions(save_id: str, character, current_year: int) -> list[str]:
    """Verifie tous les goals declares et marque ceux qui sont accomplis.

    Renvoie la liste des descriptions accomplies ce tour.
    """
    from shinobi.goals.completion import check_goal_by_target, check_goal_completion
    from shinobi.goals.declaration import complete_goal

    goals = save_module.load_goals(save_id)
    breadcrumbs = save_module.load_breadcrumbs(save_id)
    completed_now: list[str] = []
    for goal in goals:
        if goal.status.value != "declared":
            continue
        is_done = check_goal_completion(goal, breadcrumbs) or check_goal_by_target(goal, character)
        if is_done:
            updated = complete_goal(goal, current_year)
            save_module.save_goal(save_id, updated)
            completed_now.append(goal.description_player)
    return completed_now


def _touch_present_npcs(world, character, present_npcs: list[str], canon):
    """Met a jour npc_states + touche les relations correspondantes."""
    new_world = world
    for npc_id in present_npcs:
        canon_npc = canon.characters.get(npc_id)
        if canon_npc is None:
            continue
        # Determine localisation et rang courant a partir du canon
        loc = character.current_location
        rank = "unknown"
        if canon_npc.rank_progression:
            rank = canon_npc.rank_progression[-1].rank
        existing = new_world.npc_states.get(npc_id)
        is_alive = True
        if canon_npc.death_year is not None and world.current_year >= canon_npc.death_year:
            is_alive = False
        npc_age = (
            world.current_year - canon_npc.birth_year if canon_npc.birth_year else 25
        )
        new_world = new_world.with_npc_state(
            NPCState(
                character_id=npc_id,
                is_alive=is_alive if existing is None else existing.is_alive,
                current_location=loc,
                current_year=world.current_year,
                current_age=max(0, npc_age),
                current_rank=rank,
                last_updated_year=world.current_year,
            )
        )
        character = touch_relationship(character, with_id=npc_id, year=world.current_year)
    return new_world, character


def _render_mechanical_narration(result, parsed, character, world):
    """Fallback : affiche un Panel narratif mecanique quand le LLM est indisponible."""
    bits = [result.summary_fr]
    duration_str = f"{result.duration_minutes // 60}h{result.duration_minutes % 60:02d}"
    bits.append(f"\n[dim]Action : {parsed.action_type.value} ({duration_str})[/dim]")
    if result.stat_changes:
        bits.append("\n[dim]Effets : " + ", ".join(
            f"{ch['stat']} {ch['old']:.2f}→{ch['new']:.2f}" for ch in result.stat_changes[:3]
        ) + "[/dim]")
    bits.append(
        f"\n[dim]Lieu : {character.current_location} | An {world.current_year}, jour {world.current_date}[/dim]"
    )
    console.print(
        Panel(
            "\n".join(bits),
            title="Narration (mode mecanique)",
            border_style="dim cyan",
        )
    )


def _pay_for_information_flow(character, world, save_id: str, *, amount: int):
    """Le joueur paie pour reveler le prochain breadcrumb cache d'un objectif actif."""
    if character.money < amount:
        console.print(
            f"[red]Tu n'as pas {amount} ryos en poche (tu as {character.money}).[/red]"
        )
        return character, world
    goals = [g for g in save_module.load_goals(save_id) if g.status.value == "declared"]
    if not goals:
        console.print("[yellow]Aucun objectif declare. Personne n'a rien a te vendre.[/yellow]")
        return character, world
    target_goal = goals[-1]
    breadcrumbs = save_module.load_breadcrumbs(save_id, parent_goal_id=target_goal.id)
    hidden = [bc for bc in breadcrumbs if not bc.revealed]
    if not hidden:
        console.print(
            "[yellow]Le contact te repond qu'il n'a rien de plus a t'apprendre pour cet objectif.[/yellow]"
        )
        return character, world
    next_bc = hidden[0]
    from shinobi.goals.breadcrumbs import BreadcrumbPrice, mark_revealed

    price = BreadcrumbPrice(
        type="money",
        description=f"information payee {amount} ryos",
        amount=float(amount),
        paid=True,
        paid_at_year=world.current_year,
    )
    revealed = mark_revealed(next_bc, year=world.current_year, price_paid=price)
    save_module.save_breadcrumb(save_id, revealed)
    new_char = character.with_money(-amount)
    # Le secret revele entre dans la connaissance du joueur
    new_secrets = [*new_char.knowledge.secrets_uncovered, revealed.description]
    new_knowledge = new_char.knowledge.model_copy(update={"secrets_uncovered": new_secrets})
    new_char = new_char.model_copy(update={"knowledge": new_knowledge})
    console.print(
        Panel(
            f"[bold]Information acquise :[/bold] {revealed.description}\n"
            f"  [dim]Base canonique : {revealed.canonical_basis}[/dim]\n"
            f"  [dim]Cout : {amount} ryos[/dim]",
            title=f"Indice debloque pour '{target_goal.description_player[:40]}'",
            border_style="magenta",
        )
    )
    return new_char, world


def _charge_living_cost(character, world, *, days_passed: int):
    """Prelevement quotidien : nourriture + logement modeste, ajuste pour l'inflation."""
    if days_passed <= 0:
        return character
    cost = cost_of_living_for_period(
        days=days_passed, inflation_factor=world.economy.inflation_factor
    )
    if cost <= 0:
        return character
    if character.money >= cost:
        new_char = character.with_money(-cost)
        console.print(
            f"  [dim]Cout de vie ({days_passed}j) : {format_ryos(cost)} preleve.[/dim]"
        )
        return new_char
    # Pas assez d'argent : -fatigue, -hp, mais pas de dette negative
    short = cost - character.money
    new_char = character.with_money(-character.money)
    fatigue_penalty = min(40, short // 20)
    if fatigue_penalty > 0:
        new_char = apply_fatigue(new_char, fatigue_penalty)
    if short > 200:
        new_char = apply_damage(new_char, min(15, short // 100), description="malnutrition")
    console.print(
        f"  [yellow]Pas assez de ryos pour vivre ({short} ryos manquants). "
        f"Tu dors dehors, mal nourri.[/yellow]"
    )
    return new_char


def _record_fired_events_as_known(character, fired, canon, current_year: int):
    """Ajoute les events declenches ce tour a knowledge.known_events."""
    if not fired:
        return character
    new_known = dict(character.knowledge.known_events)
    added = False
    for ev in fired:
        canon_ev = canon.timeline_events.get(ev.event_id)
        if canon_ev is None or ev.event_id in new_known:
            continue
        new_known[ev.event_id] = (
            f"an {current_year} : {canon_ev.name_fr} ({canon_ev.narrative_summary_fr[:120]})"
        )
        added = True
    if not added:
        return character
    new_knowledge = character.knowledge.model_copy(update={"known_events": new_known})
    return character.model_copy(update={"knowledge": new_knowledge})


def _log_biography_milestones(char_before, char_after, world, action_type, result):
    """Detecte transitions notables (rank, learn, near-death) et ajoute des BiographyEvent."""
    from shinobi.engine.character import BiographyEvent

    events: list[BiographyEvent] = []
    year = world.current_year
    age = char_after.age_years

    if char_before.rank != char_after.rank:
        events.append(
            BiographyEvent(
                year=year,
                age=age,
                summary=f"Promotion : {char_before.rank} -> {char_after.rank}",
                category="rank_promotion",
            )
        )

    before_techs = {t.technique_id for t in char_before.techniques_known}
    after_techs = {t.technique_id for t in char_after.techniques_known}
    for tid in after_techs - before_techs:
        events.append(
            BiographyEvent(
                year=year,
                age=age,
                summary=f"Technique apprise : {tid}",
                category="technique_learned",
            )
        )

    if not char_before.is_dead and char_after.is_dead:
        events.append(
            BiographyEvent(
                year=year,
                age=age,
                summary=char_after.death_circumstances or "Mort",
                category="trauma",
            )
        )
    else:
        # Frole la mort : descend sous 20% des HP
        ratio_after = char_after.health.hp_current / max(1, char_after.health.hp_max)
        ratio_before = char_before.health.hp_current / max(1, char_before.health.hp_max)
        if ratio_after < 0.2 <= ratio_before:
            events.append(
                BiographyEvent(
                    year=year,
                    age=age,
                    summary=f"Blessure grave (hp {char_after.health.hp_current}/{char_after.health.hp_max})",
                    category="trauma",
                )
            )

    if not char_before.is_missing_nin and char_after.is_missing_nin:
        events.append(
            BiographyEvent(
                year=year,
                age=age,
                summary=f"Devient nukenin de {char_before.current_village}",
                category="other",
            )
        )

    if not events:
        return char_after
    log = [*char_after.biography_log, *events]
    return char_after.model_copy(update={"biography_log": log})


def _travel_flow(character, world, target_village: str):
    """Voyage inter-village : consomme du temps reel + fatigue, peut declencher des events au passage."""
    if target_village == character.current_village:
        console.print("[yellow]Tu es deja a destination.[/yellow]")
        return character, world
    minutes = travel_minutes(character.current_village, target_village)
    days = max(1, minutes // (24 * 60))
    fatigue_cost = min(80, 8 * days)
    console.print(
        Panel.fit(
            f"Tu pars de [cyan]{character.current_village}[/cyan] vers [cyan]{target_village}[/cyan].\n"
            f"Voyage estime : [yellow]{days} jours[/yellow]. Fatigue accumulee : +{fatigue_cost}.",
            title="Voyage",
            border_style="cyan",
        )
    )
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
    new_char = apply_fatigue(character, fatigue_cost)
    new_char = new_char.model_copy(
        update={"current_village": target_village, "current_location": target_village}
    )
    # Cout de vie en route
    new_char = _charge_living_cost(new_char, new_world, days_passed=days)
    return new_char, new_world


def _desertion_flow(character, world):
    """Le joueur abandonne son village. Reputation chute, status nukenin, location = wilderness."""
    if character.is_missing_nin:
        console.print("[yellow]Tu es deja un nukenin. Inutile de redecla la fuite.[/yellow]")
        return character, world
    if not Prompt.ask(
        "[bold red]Tu vas tout abandonner et devenir nukenin. Confirmer ? (oui/non)[/bold red]",
        default="non",
    ).strip().lower().startswith("o"):
        console.print("[dim]Abandonne. Tu restes loyal.[/dim]")
        return character, world
    village = character.current_village
    new_char = character.model_copy(
        update={
            "is_missing_nin": True,
            "rank": "missing_nin",
            "current_location": "wilderness",
            "current_village": "wilderness",
        }
    )
    new_char = add_reputation(new_char, village, -100)
    console.print(
        Panel.fit(
            f"[bold red]Tu desertes {village}.[/bold red]\n"
            "[dim]Tu brises ton bandeau. Le village te traque desormais. "
            "Le bingo book va parler de toi.[/dim]",
            title="Nukenin",
            border_style="red",
        )
    )
    new_rep = new_char.reputation.model_copy(update={"bingo_book_entry": True})
    new_char = new_char.model_copy(update={"reputation": new_rep})
    return new_char, world


def _print_biography(character) -> None:
    """Affiche le journal biographique du personnage."""
    if not character.biography_log:
        console.print(Panel("Aucun evenement biographique enregistre.", title="Biographie"))
        return
    lines = []
    for ev in character.biography_log[-50:]:
        cat_color = {
            "rank_promotion": "bold green",
            "technique_learned": "cyan",
            "trauma": "red",
            "achievement": "yellow",
            "key_relationship": "magenta",
            "encounter": "blue",
            "birth": "dim",
        }.get(ev.category, "white")
        lines.append(f"  [dim]An {ev.year} (a {ev.age} ans)[/dim] [{cat_color}]{ev.summary}[/{cat_color}]")
    console.print(
        Panel(
            "\n".join(lines),
            title=f"Biographie de {character.name} ({len(character.biography_log)} evenements)",
            border_style="cyan",
        )
    )


def _print_knowledge(character) -> None:
    """Affiche ce que le joueur sait : events canon, secrets, techniques connues."""
    sections = []
    if character.knowledge.known_events:
        items = sorted(character.knowledge.known_events.items(), key=lambda kv: kv[0])
        body = "\n".join(f"  [dim]{eid}[/dim] : {desc}" for eid, desc in items[-30:])
        sections.append(("[bold]Events canon connus[/bold]", body))
    if character.knowledge.secrets_uncovered:
        body = "\n".join(f"  [magenta]*[/magenta] {s}" for s in character.knowledge.secrets_uncovered[-20:])
        sections.append(("[bold magenta]Secrets reveles[/bold magenta]", body))
    if character.knowledge.known_techniques_existence:
        body = ", ".join(character.knowledge.known_techniques_existence[-15:])
        sections.append(("[bold cyan]Techniques entendues[/bold cyan]", body))
    if character.knowledge.known_locations:
        body = ", ".join(character.knowledge.known_locations[-15:])
        sections.append(("[bold blue]Lieux visites/connus[/bold blue]", body))
    if not sections:
        console.print(Panel("Tu ne sais encore rien d'important.", title="Connaissances"))
        return
    body = "\n\n".join(f"{title}\n{content}" for title, content in sections)
    console.print(Panel(body, title="Connaissances", border_style="blue"))


def _print_rumors(world, canon) -> None:
    """Affiche les rumeurs reçues par le joueur."""
    received = [r for r in world.rumors if r.received_by_player]
    if not received:
        console.print(Panel("Aucune rumeur entendue pour le moment.", title="Rumeurs"))
        return
    lines = []
    for r in received[-20:]:
        ev = canon.timeline_events.get(r.source_event_id) if r.source_event_id else None
        canon_label = f" [dim]({ev.name_fr})[/dim]" if ev else ""
        lines.append(
            f"  [yellow]An {r.born_at_year}[/yellow]{canon_label} : {r.content} "
            f"[dim](fid {r.fidelity:.2f}, {r.diffusion_radius})[/dim]"
        )
    console.print(
        Panel("\n".join(lines), title=f"Rumeurs ({len(received)})", border_style="yellow")
    )


def _print_breadcrumbs(save_id: str) -> None:
    """Affiche les sous-objectifs reveles non encore accomplis."""
    bcs = save_module.load_breadcrumbs(save_id)
    if not bcs:
        console.print(Panel("Aucun sous-objectif revele.", title="Pistes"))
        return
    revealed = [b for b in bcs if b.revealed]
    pending = [b for b in revealed if not b.completed]
    completed = [b for b in revealed if b.completed]
    blocks = []
    if pending:
        block = "\n".join(
            f"  [yellow]>[/yellow] {b.description} [dim]({b.canonical_basis})[/dim]"
            for b in pending[-15:]
        )
        blocks.append(f"[bold yellow]En cours ({len(pending)})[/bold yellow]\n{block}")
    if completed:
        block = "\n".join(f"  [green]v[/green] [dim]{b.description}[/dim]" for b in completed[-10:])
        blocks.append(f"[bold green]Accomplis ({len(completed)})[/bold green]\n{block}")
    if not blocks:
        console.print(Panel("Aucun sous-objectif revele.", title="Pistes"))
        return
    console.print(Panel("\n\n".join(blocks), title="Pistes", border_style="magenta"))


def _print_weapons(character) -> None:
    """Affiche les armes equipees."""
    if not character.weapons:
        console.print(
            Panel(
                "Aucune arme equipee. Achete-en au shop ou recupere-en sur des adversaires.",
                title="Armes",
            )
        )
        return
    lines = []
    for w in character.weapons:
        marker = " [dim](x" + str(w.quantity) + ")[/dim]" if w.quantity > 1 else ""
        lines.append(f"  [cyan]{w.weapon_id}[/cyan] [dim]({w.quality})[/dim]{marker}")
    console.print(Panel("\n".join(lines), title=f"Armes ({len(character.weapons)})", border_style="cyan"))


def _print_summons(character) -> None:
    """Affiche les contrats d'invocation signes."""
    if not character.summons:
        console.print(
            Panel(
                "Aucun contrat d'invocation signe. Cherche un sannin ou un sage pour en obtenir un.",
                title="Invocations",
            )
        )
        return
    lines = [f"  [magenta]*[/magenta] {s}" for s in character.summons]
    console.print(
        Panel("\n".join(lines), title=f"Contrats d'invocation ({len(character.summons)})", border_style="magenta")
    )


CANONICAL_SUMMONS = {
    "toad": "Crapauds du Mont Myoboku (lignee Jiraiya/Naruto/Minato)",
    "snake": "Serpents du Mont Ryuchi (lignee Orochimaru/Sasuke)",
    "slug": "Limaces du Mont Shikkotsu (lignee Tsunade/Sakura)",
    "hawk": "Faucons (lignee Sasuke post-revolt)",
    "monkey": "Singes (Hiruzen Sarutobi)",
    "ninken": "Meute de chiens ninja (lignee Hatake)",
    "weasel": "Belettes (Temari)",
    "crow": "Corbeaux (Itachi, Shisui)",
    "dragon": "Dragons (Kakuzu, lignee rare)",
}


def _sign_contract_flow(character, contract_name: str):
    """Signe un contrat d'invocation. Heuristique : ouvert a tout nom canonique connu."""
    canonical = CANONICAL_SUMMONS.get(contract_name)
    if canonical is None:
        console.print(
            f"[yellow]Contrat '{contract_name}' inconnu. "
            f"Liste : {', '.join(CANONICAL_SUMMONS.keys())}[/yellow]"
        )
        return character
    if contract_name in character.summons:
        console.print(f"[dim]Tu as deja signe le contrat des {contract_name}.[/dim]")
        return character
    new_summons = [*character.summons, contract_name]
    new_char = character.model_copy(update={"summons": new_summons})
    console.print(
        Panel.fit(
            f"[bold magenta]Contrat signe : {contract_name}[/bold magenta]\n"
            f"[dim]{canonical}[/dim]\n"
            "[dim]Tu peux desormais invoquer une creature de cette lignee avec /invoke <name>.[/dim]",
            title="Kuchiyose no Jutsu",
            border_style="magenta",
        )
    )
    return new_char


def _invoke_flow(character, contract_name: str):
    """Invocation : consomme 30 chakra, succes selon ninjutsu + chakra_control."""
    if contract_name not in character.summons:
        console.print(f"[red]Tu n'as pas signe le contrat des {contract_name}.[/red]")
        return character
    if character.chakra.current < 30:
        console.print(
            f"[red]Pas assez de chakra ({character.chakra.current}/30 requis pour Kuchiyose).[/red]"
        )
        return character
    new_chakra = character.chakra.model_copy(update={"current": character.chakra.current - 30})
    new_char = character.with_chakra(new_chakra)
    skill = (character.stats.ninjutsu + character.extended_stats.chakra_control) / 2
    if skill < 1.5:
        console.print(
            Panel(
                "Ton invocation rate. Le chakra se dissipe. Tes mains tremblent.",
                title="Kuchiyose echoue",
                border_style="red",
            )
        )
        return new_char
    if skill < 3.0:
        console.print(
            Panel(
                f"Une petite creature de la lignee des {contract_name} apparait. "
                "Modeste mais fidele.",
                title="Kuchiyose mineur",
                border_style="cyan",
            )
        )
    else:
        console.print(
            Panel(
                f"Une creature majeure de la lignee des {contract_name} apparait dans un nuage de fumee. "
                "Elle attend tes ordres.",
                title="Kuchiyose majeur",
                border_style="magenta",
            )
        )
    return new_char


def _llm_reinterpret_if_custom(parsed, intent_text: str, character, world):
    """Si l'heuristique tombe en custom, demande au LLM character_interpreter de classifier.

    Permet de transformer 'je m'occupe de mes affaires' / 'je flane au marche' etc.
    en train_stat / move / talk / etc. au lieu de tomber en fallback inerte.
    Si le LLM est down ou retourne aussi custom, on garde le parsed original.
    """
    from shinobi.engine.interpreter import ParsedIntent

    try:
        from shinobi.llm.client import LLMClient
        from shinobi.llm.narration import CharacterInterpreter

        async def _do() -> ParsedIntent | None:
            async with LLMClient() as client:
                if not await client.health():
                    return None
                interp = CharacterInterpreter(client)
                ctx = (
                    f"{character.name}, {character.age_years} ans, {character.rank} "
                    f"a {character.current_village}. Date in-game : an {world.current_year}."
                )
                result = await interp.interpret(intent_text, context_summary=ctx)
                # Convertit string -> ActionType si valide
                try:
                    new_type = ActionType(result.action_type)
                except ValueError:
                    return None
                if new_type == ActionType.custom:
                    return None
                params = dict(result.parameters or {})
                if result.target_id and "target_id" not in params:
                    params["target_id"] = result.target_id
                return ParsedIntent(
                    action_type=new_type,
                    parameters=params,
                    summary=result.summary or intent_text,
                )

        new_parsed = asyncio.run(_do())
        if new_parsed is not None:
            console.print(
                f"  [dim italic]Action interpretee comme : {new_parsed.action_type.value}[/dim italic]"
            )
            return new_parsed
    except Exception as exc:
        console.print(f"[dim]Re-interpretation LLM ignoree ({type(exc).__name__})[/dim]")
    return parsed


def _maybe_auto_desert(character, world):
    """Si la reputation village descend tres bas, propose la fuite."""
    if character.is_missing_nin:
        return character, world
    village = character.current_village
    score = 0
    for entry in character.reputation.by_village:
        if entry.village_id == village:
            score = entry.score
            break
    if score > -100:
        return character, world
    console.print(
        Panel.fit(
            f"[bold yellow]Ta reputation a {village} est devenue intenable ({score}). "
            "Tu peux choisir de fuir avant que la garde ne t'arrete.[/bold yellow]",
            title="Mise en garde",
            border_style="yellow",
        )
    )
    return _desertion_flow(character, world)
