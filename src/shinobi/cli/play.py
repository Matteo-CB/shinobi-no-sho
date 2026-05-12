"""Boucle de jeu principale avec UI rich, missions, controles de duree."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
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
    BatchActionSelector,
    EmbeddingsIndex,
    LLMCache,
    Reflector,
    TickEngine,
    apply_actions_to_world_state,
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
from shinobi.i18n import t
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
from shinobi.tension import TensionDetector, TensionScheduler
from shinobi.types import ActionType
from shinobi.utils.time_utils import GameDate

console = Console()


def _meta_help() -> dict[str, str]:
    """Build META_HELP at call time so descriptions reflect current language."""
    return {
        "/status": t("cli.play.help.status"),
        "/techniques": t("cli.play.help.techniques"),
        "/objectives": t("cli.play.help.objectives"),
        "/declare": t("cli.play.help.declare"),
        "/path <goal_id>": t("cli.play.help.path"),
        "/missions": t("cli.play.help.missions"),
        "/buy": t("cli.play.help.buy"),
        "/sell": t("cli.play.help.sell"),
        "/inventory": t("cli.play.help.inventory"),
        "/use <item_id>": t("cli.play.help.use"),
        "/active_missions": t("cli.play.help.active_missions"),
        "/reputation": t("cli.play.help.reputation"),
        "/biography": t("cli.play.help.biography"),
        "/knowledge": t("cli.play.help.knowledge"),
        "/rumors": t("cli.play.help.rumors"),
        "/breadcrumbs": t("cli.play.help.breadcrumbs"),
        "/weapons": t("cli.play.help.weapons"),
        "/summons": t("cli.play.help.summons"),
        "/sign_contract <name>": t("cli.play.help.sign_contract"),
        "/invoke <name>": t("cli.play.help.invoke"),
        "/skip <duree>": t("cli.play.help.skip"),
        "/journal": t("cli.play.help.journal"),
        "/dialogues": t("cli.play.help.dialogues"),
        "/export-vn-dialogues <path>": t("cli.play.help.export_vn"),
        "/personality <npc_id>": t("cli.play.help.personality"),
        "/beliefs <npc_id>": t("cli.play.help.beliefs"),
        "/tensions": t("cli.play.help.tensions"),
        "/tensions-llm": t("cli.play.help.tensions_llm"),
        "/agents": t("cli.play.help.agents"),
        "/agent <npc_id>": t("cli.play.help.agent"),
        "/fast-forward <mois>": t("cli.play.help.fast_forward"),
        "/language": t("cli.play.help.language"),
        "/help": t("cli.play.help.help"),
        "/quit": t("cli.play.help.quit"),
    }


# Backward-compat alias used by tests; lazy-init to current language.
META_HELP: dict[str, str] = {
    "/status": "",
    "/techniques": "",
    "/objectives": "",
    "/declare": "",
    "/path <goal_id>": "",
    "/missions": "",
    "/buy": "",
    "/sell": "",
    "/inventory": "",
    "/use <item_id>": "",
    "/active_missions": "",
    "/reputation": "",
    "/biography": "",
    "/knowledge": "",
    "/rumors": "",
    "/breadcrumbs": "",
    "/weapons": "",
    "/summons": "",
    "/sign_contract <name>": "",
    "/invoke <name>": "",
    "/skip <duree>": "",
    "/journal": "",
    "/dialogues": "",
    "/export-vn-dialogues <path>": "",
    "/personality <npc_id>": "",
    "/beliefs <npc_id>": "",
    "/tensions": "",
    "/tensions-llm": "",
    "/agents": "",
    "/agent <npc_id>": "",
    "/fast-forward <mois>": "",
    "/language": "",
    "/help": "",
    "/quit": "",
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

    # Phase C §5.3 : TensionScheduler pour la boucle de jeu normale.
    # Spec '1 inf/3 mois in-game' s'applique a TOUS les modes (normal +
    # fast-forward). En normal, on tick a chaque tour. Detector tourne
    # chaque tour (gratuit, sync). L'analyst LLM est cree LAZY a chaque
    # tick via `async with LLMClient()` (le contexte HTTP n'est valide
    # qu'a l'interieur du with).
    main_loop_kg = save_module.kg_db_path(save_id)
    main_loop_kg_store = None
    main_loop_social = None
    main_loop_scheduler_state = None
    main_loop_director_state = None
    if main_loop_kg.exists():
        try:
            from shinobi.kg.social import SocialNetwork
            from shinobi.kg.store import KnowledgeGraphStore
            from shinobi.tension import SchedulerState

            main_loop_kg_store = KnowledgeGraphStore(main_loop_kg)
            main_loop_social = SocialNetwork(main_loop_kg_store.conn)

            # Spec §5.3 : scheduler_state persiste entre sessions pour
            # respecter '1 inf/3 mois' au-dela d'une session.
            scheduler_state_path = save_module.tension_scheduler_state_path(save_id)
            main_loop_scheduler_state = SchedulerState()
            if scheduler_state_path.exists():
                try:
                    state_data = json.loads(
                        scheduler_state_path.read_text(encoding="utf-8"),
                    )
                    main_loop_scheduler_state = SchedulerState.from_dict(state_data)
                except (json.JSONDecodeError, OSError):
                    main_loop_scheduler_state = SchedulerState()

            # Phase G §7 : DirectorState persiste entre sessions pour
            # preserver active_acts + last_compaction.
            from shinobi.director import DirectorState

            director_state_path = save_module.director_state_path(save_id)
            main_loop_director_state = DirectorState()
            if director_state_path.exists():
                try:
                    director_data = json.loads(
                        director_state_path.read_text(encoding="utf-8"),
                    )
                    main_loop_director_state = DirectorState.from_dict(
                        director_data,
                    )
                except (json.JSONDecodeError, OSError) as exc:
                    from shinobi.logging_setup import get_logger as _glog
                    _glog(__name__).warning(
                        "main_loop_director_state_load_corrupted",
                        error=type(exc).__name__, msg=str(exc)[:200],
                    )
                    main_loop_director_state = DirectorState()
        except Exception as exc:
            # Audit anti-silent : si l'init main_loop_kg_store crash
            # (DB locked, schema migration, etc), la session continue
            # SANS Phase A KG actif - degradation majeure invisible.
            from shinobi.logging_setup import get_logger as _glog
            _glog(__name__).warning(
                "main_loop_kg_init_failed",
                error=type(exc).__name__, msg=str(exc)[:200],
            )
            main_loop_kg_store = None

    console.print(
        Panel.fit(
            t(
                "cli.play.session_resume",
                name=character.name,
                year=world.current_year,
                date=world.current_date,
            ),
            title=t("cli.play.session_in_progress_title"),
            border_style="cyan",
        )
    )

    while not character.is_dead:
        turn += 1
        print_status(console, character, world)
        if last_proposed:
            action_menu(console, last_proposed)

        intent_text = Prompt.ask(
            t("cli.play.action_prompt"),
            default=t("cli.play.training_default"),
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
                console.print(f"[green]{t('cli.play.save_done')}[/green]")
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
            from shinobi.i18n.catalog import get_active_language
            from shinobi.i18n.player_translator import process_player_input

            description = parsed.parameters.get("description") or intent_text
            active_lang = get_active_language()
            try:
                src_lang, translated, _pending = process_player_input(
                    description,
                    target_lang=active_lang,
                    fallback_source=active_lang,
                )
            except Exception:
                src_lang, translated = active_lang, {}
            goal = _declare(
                description_player=description,
                interpretation_canonical=description,
                declared_at_year=world.current_year,
                declared_at_age=character.age_years,
                description_player_original_language=src_lang,
                description_player_translated=translated,
            )
            save_module.save_goal(save_id, goal)
            console.print(f"[green]Objectif declare : {goal.id[:8]} — {description}[/green]")
            turn -= 1
            continue

        # Route request_objective_path via texte libre : interroge le pathfinder sur le dernier goal actif
        if parsed.action_type == ActionType.request_objective_path:
            goals = [g for g in save_module.load_goals(save_id) if g.status.value == "declared"]
            if not goals:
                console.print(f"[yellow]{t('cli.play.no_objective')}[/yellow]")
                turn -= 1
                continue
            target_goal = goals[-1]
            console.print(t("cli.play.pathfinder_for", description=target_goal.description_player))
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

        # Phase B §5.4 use case : le joueur agit -> KG fact -> propagation
        # rumeur vers les PNJ via cascade sociale. Permet le scenario
        # 'joueur sauve Itachi en year 8 -> Sasuke apprend en year 9, etc.'
        if main_loop_kg_store is not None:
            try:
                fid = _push_player_action_to_kg(
                    main_loop_kg_store, character.name, action, result,
                    world.current_year,
                )
                # Si action notable + target NPC, lance la cascade temporelle
                # (joueur = witness direct, fidelity 1.0 -> cascade rumeur)
                if fid is not None and action.target_id:
                    atype = (
                        action.action_type.value
                        if hasattr(action.action_type, "value")
                        else str(action.action_type)
                    )
                    if atype in _NOTABLE_PLAYER_ACTIONS:
                        from shinobi.kg.belief import BeliefPropagator
                        propagator = BeliefPropagator(
                            main_loop_kg_store.conn, main_loop_social,
                        )
                        propagator.propagate_cascade(
                            witness_npc=character.name,
                            fact_id=fid,
                            year=world.current_year,
                            channel="rumor",
                            max_depth=2,
                            min_fidelity=0.3,
                            initial_fidelity=1.0,
                            year_offset_per_hop=1,
                        )
            except Exception:
                pass

        # Auto-completion des breadcrumbs : verifie si l'action complete une condition
        completed_now = _check_breadcrumb_completions(save_id, character, result, world.current_year)
        for bc_desc in completed_now:
            console.print(t("cli.play.subgoal_completed", description=bc_desc))

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
            console.print(t("cli.play.canon_event_fired", event_id=f.event_id))
        for c in cancelled:
            console.print(t("cli.play.canon_event_cancelled", event_id=c.event_id))
            # Phase F : WorldResolver auto si strategy declenche un substitute.
            # Spec doc 02 §8.2 : 'substitute' (un event prend la place),
            # 'cascade_cancel' (effets domino qui peuvent generer un substitute),
            # et legacy 'narrative_resolution' (alias deprecated).
            canon_ev = canon.timeline_events.get(c.event_id)
            triggers_substitute = canon_ev and canon_ev.cancellation_strategy.type in (
                "substitute", "cascade_cancel", "narrative_resolution",
            )
            if triggers_substitute:
                try:
                    # Spec round 6 : capture new_world pour que le substitute
                    # injecte soit visible au tick suivant du scheduler.
                    new_world = asyncio.run(_world_resolve_cancellation(
                        canon_ev, c.reason, world.current_year, canon,
                        kg_store=main_loop_kg_store, world=world,
                    ))
                    if new_world is not None:
                        world = new_world
                except Exception as exc:
                    # Round 30 : logger au lieu de swallow silencieux.
                    # Round 23 avait logge le bloc Phase F interne, mais
                    # la narration (etape 1) etait hors-couverture - si LLM
                    # crash sur resolve_cancelled_event, l'exception remontait
                    # ici sans trace.
                    from shinobi.logging_setup import get_logger
                    get_logger(__name__).warning(
                        "phase_f_outer_swallow",
                        cancelled_event=c.event_id,
                        error=type(exc).__name__,
                        msg=str(exc)[:200],
                    )

        # Annonce des rumeurs nouvellement nees que le joueur peut entendre
        world = _announce_new_rumors(world, character, fired_event_ids={f.event_id for f in fired}, canon=canon)

        # Phase B §5.4 : Sync les rumeurs world -> KG facts + propage via
        # SocialNetwork (sub-KG par PNJ). Distorsion en chaine via
        # CHANNEL_DECAY (rumor=0.7). Idempotent (skip si fact deja present).
        if main_loop_kg_store is not None and world.rumors:
            try:
                _sync_rumors_to_kg_with_propagation(
                    main_loop_kg_store, main_loop_social, world, canon,
                )
            except Exception:
                pass

        # Phase D : drift de personnalite des PNJ impliques dans les events
        # fired ce tour. Le bridge convertit canon TimelineEvent -> ExperiencedEvent
        # et l'engine applique avec saturation sigmoid + persiste l'historique.
        if fired:
            try:
                _apply_personality_drift_for_fired(
                    save_id, fired, canon, personality_engine,
                )
            except Exception as exc:
                console.print(t("cli.play.drift_phase_d_skipped", error=type(exc).__name__))

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
        completed_goals = _check_goal_completions(
            save_id, character, world.current_year, canon=canon,
        )
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
        # Phase G+H wiring : compose le nudge depuis le DirectorState courant
        # pour l'injecter dans le prompt narrator. Defensive : si le state
        # n'a aucun act actif, on passe None (le narrator ignore le block).
        # Phase G+H wiring : helper unique pour composer le nudge Director.
        # Centralise la logique (contexts FR enrichis + select_relevant_*)
        # qui etait duplique sur 3 call sites avant le refactor.
        from shinobi.director import build_director_nudge_text
        director_nudge_text: str | None = build_director_nudge_text(
            canon=canon,
            director_state=main_loop_director_state,
            current_year=world.current_year,
        ) or None

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
                    director_nudge_text=director_nudge_text,
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
            console.print(
                f"[red]{t('cli.play.save_error', error_type=type(exc).__name__, error=str(exc))}[/red]"
            )

        # Phase C §5.3 : tick le TensionScheduler apres chaque tour normal.
        # Spec '1 inf/3 mois in-game'. Le LLMClient est cree LAZY pour ce
        # tick (le contexte HTTP n'est valide qu'a l'interieur du `async with`).
        if main_loop_kg_store is not None:
            try:
                month = int(world.current_date.split("-")[0])
                tick_result = asyncio.run(
                    _phase_c_tick_with_llm(
                        kg_store=main_loop_kg_store,
                        social=main_loop_social,
                        state=main_loop_scheduler_state,
                        year=world.current_year, month=month,
                        canon=canon,
                    ),
                )
                # Update state in-place pour le prochain tour
                if tick_result is not None and tick_result.new_state is not None:
                    main_loop_scheduler_state = tick_result.new_state
                # Si l'analyst a tourne ce tour : affiche les opportunites
                if (
                    tick_result is not None
                    and tick_result.analyst_ran
                    and tick_result.tensions.tensions
                ):
                    console.print(
                        f"[magenta]>>> Tension Analyst (1 inf/3 mois) : "
                        f"{len(tick_result.tensions.tensions)} opportunites detectees[/magenta]"
                    )
                    for tn in tick_result.tensions.tensions[:3]:
                        console.print(
                            f"  [dim][{tn.severity.value}][/dim] "
                            f"{tn.description[:80]}"
                        )
                    # Persiste le state apres analyst run
                    try:
                        state_path = save_module.tension_scheduler_state_path(save_id)
                        state_path.parent.mkdir(parents=True, exist_ok=True)
                        state_path.write_text(
                            json.dumps(
                                main_loop_scheduler_state.to_dict(),
                                ensure_ascii=False, indent=2,
                            ),
                            encoding="utf-8",
                        )
                    except Exception as exc:
                        from shinobi.logging_setup import get_logger as _glog
                        _glog(__name__).warning(
                            "main_loop_scheduler_state_persist_failed",
                            error=type(exc).__name__, msg=str(exc)[:200],
                        )

                # Phase G §7 : tick Director apres le tension tick.
                # Consume tick_result.tensions et compose des AbstractAct +
                # invariants. Compaction LLM 1x/6 mois in-game.
                if main_loop_director_state is not None and tick_result is not None:
                    try:
                        director_report = asyncio.run(
                            _phase_g_tick_director(
                                canon=canon,
                                tensions=tick_result.tensions,
                                world=world,
                                director_state=main_loop_director_state,
                                year=world.current_year, month=month,
                            ),
                        )
                        if (
                            director_report.new_acts
                            or director_report.compaction_ran
                        ):
                            for act in director_report.new_acts[:2]:
                                console.print(
                                    f"  [dim cyan]Director : nouvel acte "
                                    f"narratif '{act.id}' "
                                    f"(urgency={act.urgency:.2f})[/dim cyan]"
                                )
                            if director_report.compaction_ran:
                                console.print(
                                    "  [dim cyan]Director : compaction "
                                    "narrative regeneree[/dim cyan]"
                                )
                        # Persiste le DirectorState
                        try:
                            d_state_path = save_module.director_state_path(save_id)
                            d_state_path.parent.mkdir(parents=True, exist_ok=True)
                            d_state_path.write_text(
                                json.dumps(
                                    main_loop_director_state.to_dict(),
                                    ensure_ascii=False, indent=2,
                                ),
                                encoding="utf-8",
                            )
                        except Exception as exc:
                            from shinobi.logging_setup import get_logger
                            get_logger(__name__).warning(
                                "phase_g_director_state_persist_failed",
                                error=type(exc).__name__,
                                msg=str(exc)[:200],
                            )
                    except Exception as exc:
                        from shinobi.logging_setup import get_logger
                        get_logger(__name__).warning(
                            "phase_g_director_tick_failed",
                            error=type(exc).__name__,
                            msg=str(exc)[:200],
                        )
            except Exception as exc:
                # Audit anti-silent : Phase C tick principal pouvait crasher
                # silencieusement (signature drift, KG corrompu, etc).
                from shinobi.logging_setup import get_logger as _glog
                _glog(__name__).warning(
                    "phase_c_tick_failed_in_main_loop",
                    error=type(exc).__name__,
                    msg=str(exc)[:200],
                )

    # Cleanup KG store du main loop
    if main_loop_kg_store is not None:
        try:
            main_loop_kg_store.close()
        except Exception:
            pass

    console.print(
        Panel(t("cli.play.death_text", name=character.name), title=t("cli.play.death_panel"), border_style="red")
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
        body_lines.append(Text(t("cli.play.fatigue_delta", sign=sign, delta=result.fatigue_delta), style="yellow"))
    if result.chakra_cost:
        body_lines.append(Text(t("cli.play.chakra_consumed", cost=result.chakra_cost), style="cyan"))

    body = Text("\n").join(body_lines)
    console.print(
        Panel(
            body,
            title=t("cli.play.action_result_title", turn=turn, hours=result.duration_minutes // 60, minutes=result.duration_minutes % 60),
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
    table.add_row("c", t("cli.play.duration_custom"))
    console.print(Panel(table, title=t("cli.play.duration_engagement"), border_style="cyan"))
    choice = (
        Prompt.ask(
            f"[bold cyan]{t('cli.play.duration_prompt')}[/bold cyan]",
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
    if command == "/language":
        from shinobi.cli.language_picker import run_language_reset_menu

        run_language_reset_menu(console=console)
        return True, character, world
    if command == "/help":
        body = "\n".join(f"  [cyan]{cmd}[/cyan] : {desc}" for cmd, desc in _meta_help().items())
        console.print(Panel(body, title=t("cli.play.help.title"), border_style="cyan"))
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
        from shinobi.i18n.catalog import get_active_language
        from shinobi.i18n.player_translator import process_player_input

        text = Prompt.ask("[bold cyan]Decris ton objectif[/bold cyan]")
        if text.strip():
            description = text.strip()
            active_lang = get_active_language()
            try:
                src_lang, translated, _pending = process_player_input(
                    description,
                    target_lang=active_lang,
                    fallback_source=active_lang,
                )
            except Exception:
                src_lang, translated = active_lang, {}
            goal = declare_goal(
                description_player=description,
                interpretation_canonical=description,
                declared_at_year=world.current_year,
                declared_at_age=character.age_years,
                description_player_original_language=src_lang,
                description_player_translated=translated,
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
            console.print(
                f"[cyan]{t('cli.play.path_searching', description=target_goal.description_player)}[/cyan]"
            )
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
            console.print(Panel(t("cli.play.no_active_mission"), title=t("cli.play.missions_title")))
        else:
            lines = []
            for m in items:
                status = (
                    "[green]reussi[/green]" if m["success"]
                    else ("[red]echoue[/red]" if m["success"] is False else "[yellow]en cours[/yellow]")
                )
                lines.append(f"  [{m['rank']}] {m['title']} ({status})")
            console.print(Panel("\n".join(lines), title=t("cli.play.missions_title_count", count=len(items))))
    elif command == "/inventory":
        from shinobi.engine.shop import ITEM_CATALOG, get_inventory_summary, shop_item_name

        items = get_inventory_summary(character.inventory, character.weapons)
        if not items:
            console.print(Panel(t("cli.play.inventory_empty"), title=t("cli.play.inventory_title")))
        else:
            lines = []
            for item_id, qty in items:
                item = ITEM_CATALOG.get(item_id)
                name = shop_item_name(item_id) if item else item_id
                lines.append(f"  {name} (x{qty})")
            console.print(Panel("\n".join(lines), title=t("cli.play.inventory_title_money", money=character.money)))
    elif command == "/reputation":
        if not character.reputation.by_village:
            console.print(Panel(t("cli.play.no_reputation"), title=t("cli.play.reputation_title")))
        else:
            lines = [f"  {e.village_id}: {e.score}" for e in character.reputation.by_village]
            console.print(Panel("\n".join(lines), title=t("cli.play.reputation_subtitle")))
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
        console.print(f"[dim]{t('cli.play.journal_path', save_id=save_id)}[/dim]")
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
    elif command.startswith("/beliefs"):
        parts = command.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            console.print("[red]Usage : /beliefs <npc_id>[/red]")
        else:
            _print_npc_beliefs(save_id, parts[1].strip(), year=world.current_year)
    elif command == "/tensions":
        _print_tensions(save_id, year=world.current_year, canon=canon)
    elif command == "/tensions-llm":
        asyncio.run(_run_tensions_llm_analyst(
            save_id, year=world.current_year, canon=canon,
        ))
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
                # Spec §6.5 : 'le monde tourne sans le joueur'. Le retour
                # actualise character + world in-memory pour que le tour
                # suivant du joueur reprenne dans l'etat advance.
                refreshed = asyncio.run(
                    _run_fast_forward(save_id, world.current_year, months),
                )
                if refreshed is not None:
                    character, world = refreshed
    else:
        console.print(f"[red]Commande inconnue : {command}[/red] (tape [cyan]/help[/cyan])")
    return True, character, world


def _print_dialogue_log(dialogue_log: DialogueLog | None) -> None:
    """Affiche les dernieres lignes capturees du log VN."""
    if dialogue_log is None:
        console.print("[yellow]Log de dialogues VN non initialise.[/yellow]")
        return
    if dialogue_log.size == 0:
        console.print(Panel(t("cli.play.no_dialogue_yet"), title="Dialogues VN"))
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
            f"[yellow]{t('cli.play.no_personality_db')}[/yellow]"
        )
        return
    with PersonalityStore(db_path) as store:
        personality = store.get_personality(npc_id)
    if personality is None:
        console.print(
            f"[yellow]{t('cli.play.no_personality_for_npc', npc_id=npc_id)}[/yellow]"
        )
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
        console.print(f"[yellow]{t('cli.play.no_dialogue_to_export')}[/yellow]")
        return
    try:
        n = export_to_vn_json(dialogue_log.all(), target)
        console.print(
            t("cli.play.export_vn_success", count=n, path=target)
        )
    except Exception as exc:
        console.print(t("cli.play.export_vn_failed", error_type=type(exc).__name__, error=str(exc)))


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
    table.add_column(t("cli.play.missions.col_rank"))
    table.add_column(t("cli.play.missions.col_title"))
    table.add_column(t("cli.play.missions.col_duration"), justify="right")
    table.add_column(t("cli.play.missions.col_reward"), justify="right")
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
    table.add_row("0", "-", t("cli.play.no_mission_choice"), "-", "-", "-")
    console.print(table)

    choice = Prompt.ask(t("cli.play.missions.pick_prompt"), default="0").strip()
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
            + t("cli.play.mission_start", hours=mission.duration_hours),
            title=t("cli.play.missions.accepted_panel_title", rank=mission.rank),
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
        t("cli.play.mission_succeeded", rank=mission.rank, title=mission.title)
        if success
        else t("cli.play.mission_failed", rank=mission.rank, title=mission.title)
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
                title=t("cli.play.success_panel"),
                border_style="green",
            )
        )
    else:
        console.print(
            Panel(
                f"[bold red]{t('cli.play.mission_failed_panel')}[/bold red]\n"
                + t("cli.play.mission_partial_panel") + consequences_block,
                title=t("cli.play.failure_panel"),
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


async def _world_resolve_cancellation(
    canon_ev, reason: str, current_year: int, canon,
    *, kg_store=None, world=None,
):
    """WorldResolver Phase F : narration + boucle creative fermee.

    Spec doc 02 §8 : extension du WorldResolver pour generer un
    SubstituteEvent STRUCTURE (au-dela du texte) + valider + injecter
    dans le scheduler + KG (si kg_store fourni).

    `world` : WorldState live de la session (passe au validator pour les
    checks runtime sherlock-equivalent). Si None, world fallback minimal.

    Returns:
        WorldState : nouveau world avec substitute injecte si Phase F a
        produit un substitute valide. Retourne le world inchange sinon
        (incluant si LLM indispo / kg_store None / silent_cancel).

    Si pas de KG store actif, fallback narration text uniquement.
    """
    from shinobi.llm.client import LLMClient
    from shinobi.llm.narration import WorldResolver

    async with LLMClient() as client:
        if not await client.health():
            return world
        # Etape 1 : narration texte (UX existante - feedback joueur immediat)
        resolver = WorldResolver(client, canon)
        resolution = await resolver.resolve_cancelled_event(
            event_id=canon_ev.id,
            cancellation_reason=reason,
            current_year=current_year,
        )
        console.print(
            Panel(
                t("cli.play.substitute_event", summary=resolution.substitute_event_summary) + "\n"
                + ("\nConsequences :\n" + "\n".join(f"  - {c.get('description', '')}" for c in resolution.consequences) if resolution.consequences else "")
                + (f"\n\nRumeur qui circule : [italic]{resolution.rumor_template}[/italic]" if resolution.rumor_template else ""),
                title=f"Resolution narrative : {canon_ev.name_fr}",
                border_style="yellow",
            )
        )

        # Etape 2 : Phase F boucle fermee (SubstituteEvent structure + KG)
        if kg_store is None:
            return world
        try:
            from shinobi.engine.world import WorldState
            from shinobi.world_resolver import (
                WorldResolverPipeline,
                build_kg_recent_facts,
                build_world_state_summary,
                select_validation_mode,
            )
            pipeline = WorldResolverPipeline(client, canon, kg_store)
            effective_world = world if world is not None else WorldState(
                current_year=current_year,
                current_date=canon_ev.date or "01-01",
            )
            mode = select_validation_mode(kg_store)
            phase_f_resolution, new_world = await pipeline.close_loop(
                cancelled_event_id=canon_ev.id,
                cancellation_reason=reason,
                world=effective_world,
                validation_mode=mode,
                world_state_summary=build_world_state_summary(effective_world),
                kg_recent_facts=build_kg_recent_facts(
                    kg_store, current_year=current_year,
                ),
            )
            if phase_f_resolution.status == "injected":
                console.print(
                    t(
                        "cli.play.phase_f_substitute",
                        sub_id=phase_f_resolution.substitute.id,
                    )
                )
                return new_world
            elif phase_f_resolution.status == "regen_exhausted":
                console.print(t("cli.play.phase_f_silent_cancel"))
        except Exception as exc:
            # Phase F best-effort : ne pas casser le gameplay si la pipeline echoue.
            # Round 23 : on log au lieu de swallow silencieux. Avant, un crash
            # de pipeline (KG corrompu, llama.cpp coupe, Pydantic cassant)
            # disparaissait sans trace - debug impossible.
            from shinobi.logging_setup import get_logger
            get_logger(__name__).warning(
                "phase_f_pipeline_failed_in_cli",
                cancelled_event=canon_ev.id,
                error=type(exc).__name__,
                msg=str(exc)[:200],
            )
        return world


def _shop_buy_flow(character):
    """Affiche la boutique du village et propose un achat."""
    from shinobi.engine.shop import (
        buy_item,
        list_shop_inventory,
        shop_item_description,
        shop_item_name,
    )

    items = list_shop_inventory(character.current_village)
    if not items:
        console.print(
            f"[yellow]{t('cli.play.no_shop_in_village', village_id=character.current_village)}[/yellow]"
        )
        return character
    table = Table(
        title=t("cli.play.shop.title", village=character.current_village, money=character.money),
        header_style=COLOR_TITLE,
    )
    table.add_column("#", style="bold cyan", justify="right")
    table.add_column(t("cli.play.shop.col_item"))
    table.add_column(t("cli.play.shop.col_category"), style="dim")
    table.add_column(t("cli.play.shop.col_price"), justify="right", style="yellow")
    table.add_column(t("cli.play.shop.col_description"), style="dim")
    for i, (item, price) in enumerate(items, start=1):
        table.add_row(
            str(i),
            shop_item_name(item.id),
            item.category,
            f"{price}",
            shop_item_description(item.id)[:60],
        )
    table.add_row("0", t("cli.play.shop.no_buy"), "-", "-", "-")
    console.print(table)
    choice = Prompt.ask(f"[bold cyan]{t('cli.play.shop.item_prompt')}[/bold cyan]", default="0").strip()
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
    color = "green" if new_char is not character else "red"
    console.print(f"[{color}]{msg}[/{color}]")
    return new_char


def _shop_sell_flow(character):
    """Propose la revente d'items de l'inventaire (et des armes)."""
    from shinobi.engine.shop import (
        ITEM_CATALOG,
        SELL_RATIO,
        get_inventory_summary,
        sell_item,
        shop_item_name,
    )

    items = get_inventory_summary(character.inventory, character.weapons)
    if not items:
        console.print(t("cli.play.inventory_empty_yellow"))
        return character
    table = Table(title=t("cli.play.shop.sell_title"), header_style=COLOR_TITLE)
    table.add_column("#", style="bold cyan", justify="right")
    table.add_column(t("cli.play.shop.col_item"))
    table.add_column(t("cli.play.shop.col_quantity"), justify="right")
    table.add_column(t("cli.play.shop.col_sell_price"), justify="right", style="yellow")
    for i, (item_id, qty) in enumerate(items, start=1):
        item = ITEM_CATALOG.get(item_id)
        name = shop_item_name(item_id) if item else item_id
        sell_price = int(item.base_price_ryos * SELL_RATIO) if item else 0
        table.add_row(str(i), name, str(qty), f"{sell_price}")
    table.add_row("0", t("cli.play.shop.no_sell"), "-", "-")
    console.print(table)
    choice = Prompt.ask(f"[bold cyan]{t('cli.play.shop.item_prompt')}[/bold cyan]", default="0").strip()
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
    color = "green" if new_char is not character else "red"
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
        # Phase 5 : transition declared -> in_progress quand le pathfinder
        # produit son 1er indice. Sans ca, le statut reste 'declared' meme
        # apres exploration active. Idempotent (no-op si deja in_progress).
        if response.breadcrumbs:
            from shinobi.goals.declaration import mark_goal_in_progress
            updated_goal = mark_goal_in_progress(goal)
            if updated_goal.status != goal.status:
                save_module.save_goal(save_id, updated_goal)
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
                    title=t("cli.play.indice_panel_title", seq=bc.sequence_index, goal_short=goal.description_player[:40]),
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
        f"[green]{t('cli.play.time_advanced', n=n, unit=unit, year=new_date.year, date=new_date.date_str)}[/green]"
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
                    t(
                        "cli.play.agents_phase_e_initialized",
                        majors=roster.major_count,
                        secondary=roster.secondary_count,
                    )
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
                    t("cli.play.arc_relevant_promoted", ids=", ".join(promoted))
                )


@dataclass
class _PhaseCTickResult:
    """Resultat d'un tick Phase C wrapping TickResult + new_state."""

    tensions: object  # TensionList
    analyst_ran: bool
    new_state: object  # SchedulerState | None


async def _phase_g_tick_director(
    *,
    canon,
    tensions,
    world,
    director_state,
    year: int,
    month: int,
):
    """Phase G §7 : tick Director apres le tension tick.

    Consume la TensionList, compose des AbstractAct, maintient les invariants
    Naruto, et periodiquement (tous les 6 mois in-game) appelle le LLM pour
    une compaction narrative NexusSum-style.

    Optimisation perf comme phase C : LLM client cree LAZY uniquement si
    is_compaction_due (eviter le setup HTTP pour les ticks ou seul le
    composer deterministe tourne).
    """
    from shinobi.director import Director, is_compaction_due
    from shinobi.director.scheduler import (  # for default interval
        DEFAULT_COMPACTION_INTERVAL_MONTHS,
    )

    needs_llm = is_compaction_due(
        director_state,
        current_year=year, current_month=month,
        interval_months=DEFAULT_COMPACTION_INTERVAL_MONTHS,
    )
    if needs_llm:
        from shinobi.llm.client import LLMClient
        async with LLMClient() as llm:
            director = Director(canon, llm_client=llm)
            return await director.tick(
                tensions=tensions, world=world, state=director_state,
                current_year=year, current_month=month,
            )
    # Pas de compaction due : pas besoin du LLM, fallback offline-only Director.
    director = Director(canon, llm_client=None)
    return await director.tick(
        tensions=tensions, world=world, state=director_state,
        current_year=year, current_month=month,
    )


async def _phase_c_tick_with_llm(
    *, kg_store, social, state, year: int, month: int, canon=None,
):
    """Spec §5.3 : tick TensionScheduler.

    Optimisation perf : on n'ouvre LLMClient QUE si l'analyst est due
    (chaque 3 mois). Pour les 99% de ticks ou seul le detector tourne
    (gratuit, sync), on evite l'overhead de l'HTTP client setup.

    Phase H wiring 9.3 : `canon` propage au TensionScheduler -> son
    detector interne active la 21eme regle political_alliance_brittle.
    """
    from shinobi.tension import LLMTensionAnalyst

    # 1. Check is_due SANS ouvrir LLMClient
    pre_scheduler = TensionScheduler(
        kg_store, social_network=social, state=state, canon=canon,
    )
    should_run_analyst = pre_scheduler.is_due(year, month)

    if should_run_analyst:
        # 2a. Analyst due : ouvre LLMClient + run avec analyst reel
        from shinobi.llm.client import LLMClient
        async with LLMClient() as llm_client:
            analyst = LLMTensionAnalyst(
                kg_store, llm_client=llm_client, social_network=social,
            )
            scheduler = TensionScheduler(
                kg_store, analyst=analyst,
                social_network=social, state=state, canon=canon,
            )
            tick_result = await scheduler.tick(year, month=month)
        return _PhaseCTickResult(
            tensions=tick_result.tensions,
            analyst_ran=tick_result.analyst_ran,
            new_state=scheduler.state,
        )
    # 2b. Analyst pas due : detector seulement, pas d'HTTP client
    tick_result = await pre_scheduler.tick(year, month=month)
    return _PhaseCTickResult(
        tensions=tick_result.tensions,
        analyst_ran=tick_result.analyst_ran,
        new_state=pre_scheduler.state,
    )


async def _run_tensions_llm_analyst(
    save_id: str, *, year: int, canon=None,
) -> None:
    """Spec §5.3 : force LLM analyst Qwen3-4B sur snapshot KG.

    Affiche les opportunites narratives identifiees par l'analyst.
    """
    from shinobi.kg.social import SocialNetwork
    from shinobi.kg.store import KnowledgeGraphStore

    kg_db = save_module.kg_db_path(save_id)
    if not kg_db.exists():
        console.print(
            t("cli.play.kg_not_initialized_a")
        )
        return
    console.print("[cyan]Snapshot KG + analyst LLM en cours...[/cyan]")
    from shinobi.llm.client import LLMClient
    from shinobi.tension import LLMTensionAnalyst, SchedulerState

    with KnowledgeGraphStore(kg_db) as kg:
        social = SocialNetwork(kg.conn)
        # Spec §5.3 : LLM client REEL pour que l'analyst soit actif
        async with LLMClient() as llm:
            analyst = LLMTensionAnalyst(
                kg, llm_client=llm, social_network=social,
            )
            state_path = save_module.tension_scheduler_state_path(save_id)
            initial_state = SchedulerState()
            if state_path.exists():
                try:
                    initial_state = SchedulerState.from_dict(
                        json.loads(state_path.read_text(encoding="utf-8")),
                    )
                except (json.JSONDecodeError, OSError):
                    initial_state = SchedulerState()
            scheduler = TensionScheduler(
                kg, analyst=analyst,
                social_network=social, state=initial_state,
                canon=canon,
            )
            try:
                result = await scheduler.tick(
                    year, month=1, force_analyst=True,
                )
            except Exception as exc:
                console.print(
                    f"[red]Analyst LLM echoue : {type(exc).__name__}: {exc}[/red]"
                )
                return
            # Persiste le state apres tick (force_analyst => analyst tourne toujours)
            try:
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(
                    json.dumps(
                        scheduler.state.to_dict(),
                        ensure_ascii=False, indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass

    if not result.tensions.tensions:
        console.print(
            Panel(
                f"[dim]{t('cli.play.no_narrative_opportunity')}[/dim]",
                title="LLM Analyst", border_style="magenta",
            )
        )
        return
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    sorted_t = sorted(
        result.tensions.tensions,
        key=lambda t: sev_order.get(t.severity.value, 99),
    )
    lines = []
    for tn in sorted_t[:15]:
        sev_color = {
            "critical": "red", "high": "magenta",
            "medium": "yellow", "low": "dim",
        }.get(tn.severity.value, "white")
        lines.append(
            f"  [{sev_color}]{tn.severity.value:8s}[/{sev_color}] "
            f"[{tn.type.value}] {tn.description[:80]}"
        )
        if tn.involved_entities:
            lines.append(
                f"    [dim]impliques : {', '.join(tn.involved_entities[:5])}[/dim]"
            )
    console.print(
        Panel(
            "\n".join(lines),
            title=(
                f"LLM Analyst : {len(sorted_t)} opportunites (an {year})"
            ),
            border_style="magenta",
        )
    )


# Mapping ActionType -> relation KG pour push_player_action_to_kg
_PLAYER_ACTION_RELATIONS: dict[str, str] = {
    "move": "moved_to",
    "talk": "talked_to",
    "fight": "fought",
    "spy": "spied_on",
    "steal": "stole_from",
    "challenge": "challenged",
    "seduce": "seduced",
    "intimidate": "intimidated",
    "bribe": "bribed",
    "use_technique": "used_jutsu",
    "submit_mission": "completed_mission",
    "accept_mission": "accepted_mission",
    # Actions privees / sans target visible : pas de fact public
    "rest": None,
    "meditate": None,
    "train_stat": None,
    "train_technique": None,
    "research": None,
    "declare_goal": None,
    "request_objective_path": None,
    "pay_for_information": None,
    "buy": None,
    "sell": None,
    "work": None,
    "pray": None,
    "wait": None,
    "custom": None,
}

# Actions importantes (importance 0.7+) qui peuvent generer une rumeur.
# Spec §5.4 use case : actions dramatiquement notables propagent via cascade.
# Inclut combat, conflit, et actions sociales 'scandaleuses' qui sont
# typiquement rapportees par les temoins (potin / rumeur).
_NOTABLE_PLAYER_ACTIONS: frozenset[str] = frozenset({
    "fight", "challenge", "steal", "spy", "submit_mission",
    "use_technique",
    # Actions sociales qui se propagent comme rumeurs canon
    "seduce", "bribe", "intimidate",
})


def _push_player_action_to_kg(
    kg_store, character_name: str, action, result, year: int,
) -> int | None:
    """Spec §5.4 use case : 'le joueur sauve Itachi en year 8'.

    Convertit une Action joueur en Fact KG pour que la propagation rumeur
    puisse l'amener aux PNJ. Retourne fact_id ou None (action privee).

    Spec §5.4 implique que l'OUTCOME affecte le fact : 'Naruto a vaincu
    Pain' est different de 'Naruto a echoue contre Pain'. La relation
    canonique reflete le resultat (full_success vs failure).
    """
    from shinobi.kg.schema import Canonicity, Fact, ObjectType

    atype = action.action_type.value if hasattr(
        action.action_type, "value",
    ) else str(action.action_type)
    base_relation = _PLAYER_ACTION_RELATIONS.get(atype)
    if base_relation is None:
        return None  # action privee/triviale, pas de fact

    # Spec §5.4 : outcome-aware relation. Un combat gagne != combat perdu.
    relation = base_relation
    success_factor = 1.0
    if result is not None:
        outcome = getattr(result, "outcome", None)
        outcome_value = (
            outcome.value if hasattr(outcome, "value") else str(outcome) if outcome else ""
        )
        if atype == "fight":
            if outcome_value == "full_success":
                relation = "defeated"
            elif outcome_value == "partial_success":
                relation = "wounded"
            elif outcome_value in ("minor_failure", "catastrophic_failure"):
                relation = "lost_against"
                success_factor = 0.5
        elif atype == "challenge":
            if outcome_value == "full_success":
                relation = "challenged_and_defeated"
            elif outcome_value in ("minor_failure", "catastrophic_failure"):
                relation = "challenged_and_lost"
                success_factor = 0.5
        elif atype == "spy":
            if outcome_value in ("full_success", "partial_success"):
                relation = "spied_on_successfully"
            else:
                relation = "spy_attempt_failed"
                success_factor = 0.5
        elif atype == "steal":
            if outcome_value in ("full_success", "partial_success"):
                relation = "stole_from"
            else:
                relation = "theft_attempt_failed"
                success_factor = 0.5
        elif atype == "seduce":
            if outcome_value == "full_success":
                relation = "seduced"
            else:
                relation = "seduction_failed_against"
                success_factor = 0.5
        elif atype == "bribe":
            if outcome_value in ("full_success", "partial_success"):
                relation = "bribed"
            else:
                relation = "bribe_refused_by"
                success_factor = 0.5
        elif atype == "intimidate":
            if outcome_value == "full_success":
                relation = "intimidated"
            else:
                relation = "intimidation_failed_against"
                success_factor = 0.5

    target = action.target_id or action.parameters.get("target_id") or ""
    if not target:
        # Pour les actions sans target explicite, fact = sujet+verbe
        target = action.summary[:100] if action.summary else atype
        obj_type = ObjectType.value
    else:
        obj_type = ObjectType.entity

    base_importance = 0.8 if atype in _NOTABLE_PLAYER_ACTIONS else 0.5
    # Echec dramatique = aussi notable qu'un succes (pour rumeurs)
    importance = base_importance * success_factor

    fact = Fact(
        subject=character_name,
        relation=relation,
        object=target,
        object_type=obj_type,
        valid_from_year=year,
        valid_to_year=year,  # action ponctuelle
        source=f"player_action:{atype}",
        canonicity=Canonicity.divergent,
        confidence=importance,
        # Le joueur lui-meme connait le fait (sub-KG perso)
        known_by_npc_ids=[character_name],
    )
    return kg_store.add_fact(fact)


def _print_npc_beliefs(save_id: str, npc_id: str, *, year: int) -> None:
    """Spec §5.4 : affiche le sub-KG (beliefs) d'un PNJ.

    Liste les facts que ce PNJ connait + fidelity (channel decay).
    """
    from shinobi.kg.store import KnowledgeGraphStore

    kg_db = save_module.kg_db_path(save_id)
    if not kg_db.exists():
        console.print(t("cli.play.kg_not_initialized_a"))
        return
    with KnowledgeGraphStore(kg_db) as kg:
        # Facts dans known_to(npc) (sub-KG personnel)
        known_facts = kg.known_to(npc_id, year=year)

        # Beliefs depuis kg_beliefs table
        try:
            row_count = kg.conn.execute(
                "SELECT COUNT(*) AS c FROM kg_beliefs WHERE npc_id = ?",
                (npc_id,),
            ).fetchone()
            beliefs_count = int(row_count["c"]) if row_count else 0
        except Exception:
            beliefs_count = 0

    if not known_facts and beliefs_count == 0:
        console.print(
            Panel(
                f"[dim]{t('cli.play.no_fact_no_belief', npc_id=npc_id)}[/dim]",
                title=f"Sub-KG : {npc_id}",
                border_style="cyan",
            )
        )
        return

    lines = [
        f"  Facts connus (sub-KG) : {len(known_facts)}",
        f"  Beliefs enregistres : {beliefs_count}",
        "",
    ]
    if known_facts:
        lines.append("  [cyan]Top 10 facts connus :[/cyan]")
        for f in known_facts[:10]:
            obj_str = f.object[:30] if f.object else "-"
            lines.append(
                f"    [dim]conf={f.confidence:.2f}[/dim] "
                f"{f.subject} {f.relation} {obj_str}"
            )
    console.print(
        Panel(
            "\n".join(lines),
            title=t("cli.play.beliefs_subkg_title", npc_id=npc_id),
            border_style="cyan",
        )
    )


def _sync_rumors_to_kg_with_propagation(
    kg_store, social, world, canon,
) -> None:
    """Phase B §5.4 : sync les rumeurs en facts KG + propage via social network.

    Pour chaque nouvelle rumeur :
    1. Insere comme Fact KG (idempotent via source='rumor:<id>')
    2. Pour chaque NPC dans le radius event_location, ajoute Belief avec
       fidelity = rumor.fidelity * CHANNEL_DECAY['rumor'] (=0.7)
    3. Propage via BeliefPropagator.propagate_cascade aux liens sociaux
       avec distorsion en chaine
    """
    from shinobi.engine.rumors import player_can_hear
    from shinobi.kg.belief import BeliefPropagator
    from shinobi.kg.rumor_bridge import sync_world_rumors_to_kg

    propagator = BeliefPropagator(kg_store, social)

    # Pour chaque rumeur, identifie les NPCs qui peuvent l'entendre par
    # proximite location (radius)
    npcs_per_rumor: dict[str, list[str]] = {}
    canon_chars = list(canon.characters.keys()) if hasattr(canon, "characters") else []
    for rumor in world.rumors:
        if rumor.id in npcs_per_rumor:
            continue
        # NPCs qui peuvent entendre : top NPCs canon dans la radius region
        # Heuristique simple : on prend les NPCs lies a l'event_location
        event_location = None
        if rumor.source_event_id:
            ev = canon.timeline_events.get(rumor.source_event_id)
            if ev and ev.location:
                event_location = ev.location
        if event_location is None:
            npcs_per_rumor[rumor.id] = []
            continue
        # Limit aux 10 premiers (heuristique perf)
        listening_npcs: list[str] = []
        for cid in canon_chars[:50]:
            npc_state = world.npc_states.get(cid)
            if npc_state is None:
                continue
            if player_can_hear(
                rumor,
                player_location=npc_state.current_location,
                event_location=event_location,
                current_year=world.current_year,
            ):
                listening_npcs.append(cid)
                if len(listening_npcs) >= 10:
                    break
        npcs_per_rumor[rumor.id] = listening_npcs

    # Sync rumors -> KG facts + initial beliefs
    sync_world_rumors_to_kg(
        kg_store, propagator, world, npcs_per_rumor=npcs_per_rumor,
    )

    # Propagation cascade : pour chaque NPC qui a entendu, propage aux
    # voisins sociaux avec distorsion (channel='rumor')
    for rumor_id, npcs in npcs_per_rumor.items():
        existing_facts = kg_store.get_facts(source_prefix=f"rumor:{rumor_id}")
        if not existing_facts:
            continue
        fact_id = existing_facts[0].id
        if fact_id is None:
            continue
        for npc in npcs[:3]:  # Limit cascade depth
            try:
                # Spec §5.4 : year_offset_per_hop=1 -> chaque hop ajoute
                # 1 annee au learned_at_year, modelisant la propagation
                # temporelle ('Sasuke year+1, Madara year+2, ...').
                propagator.propagate_cascade(
                    witness_npc=npc,
                    fact_id=fact_id,
                    year=world.current_year,
                    channel="rumor",
                    max_depth=2,
                    min_fidelity=0.3,
                    initial_fidelity=0.7,
                    year_offset_per_hop=1,
                )
            except Exception:
                continue


def _print_tensions(save_id: str, *, year: int, canon=None) -> None:
    """Spec §5.3 : detecte les opportunites narratives via les 21 invariants
    sur le KG courant. Affichage ordre severity descendant.

    Phase H wiring 9.3 : `canon` (optionnel) permet d'activer la 21eme regle
    `political_alliance_brittle_via_dead_leader` qui croise political_forces
    et death_year pour signaler des alliances fragilisees.
    """
    from shinobi.kg.store import KnowledgeGraphStore

    kg_db = save_module.kg_db_path(save_id)
    if not kg_db.exists():
        console.print(
            t("cli.play.tensions_kg_required")
        )
        return
    with KnowledgeGraphStore(kg_db) as kg:
        detector = TensionDetector(kg, canon=canon)
        result = detector.detect(year)
    if not result.tensions.tensions:
        console.print(
            Panel(
                t("cli.play.no_tension_at_year", year=year),
                title=t("cli.play.tensions_panel_title"),
                border_style="cyan",
            )
        )
        return
    # Trie par severity
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    sorted_tensions = sorted(
        result.tensions.tensions,
        key=lambda t: (
            severity_order.get(t.severity.value, 99),
            -t.score,
        ),
    )
    lines = []
    for tn in sorted_tensions[:15]:
        sev_color = {
            "critical": "red", "high": "magenta",
            "medium": "yellow", "low": "dim",
        }.get(tn.severity.value, "white")
        lines.append(
            f"  [{sev_color}]{tn.severity.value:8s}[/{sev_color}] "
            f"[{tn.type.value}] {tn.description[:80]}"
        )
        if tn.involved_entities:
            lines.append(
                f"    [dim]impliques : {', '.join(tn.involved_entities[:5])}[/dim]"
            )
    if len(sorted_tensions) > 15:
        lines.append(t("cli.play.more_count", count=len(sorted_tensions) - 15))
    console.print(
        Panel(
            "\n".join(lines),
            title=(
                t("cli.play.tensions_panel_count_year", count=len(sorted_tensions), year=year)
            ),
            border_style="cyan",
        )
    )


def _print_agents_roster(save_id: str) -> None:
    """Liste les agents top-15 / secondary-50 avec last_active."""
    db_path = save_module.agents_db_path(save_id)
    if not db_path.exists():
        console.print(t("cli.play.roster_phase_e_not_init"))
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
    lines.append(t("cli.play.secondary_header", count=len(secondary)))
    for e in secondary[:10]:
        lines.append(f"  {e.npc_id}")
    if len(secondary) > 10:
        lines.append(t("cli.play.more_count", count=len(secondary) - 10))
    console.print(
        Panel("\n".join(lines), title=t("cli.play.agents_roster_title"), border_style="cyan")
    )


def _print_agent_detail(save_id: str, npc_id: str) -> None:
    """Affiche memoire 3-niveaux + dernieres actions d'un agent."""
    db_path = save_module.agents_db_path(save_id)
    if not db_path.exists():
        console.print(t("cli.play.roster_phase_e_not_init"))
        return
    with AgentMemoryStore(db_path) as store:
        entry = store.get_roster_entry(npc_id)
        memory = store.load_memory(npc_id)
        actions = store.list_actions(npc_id, limit=10)
    if entry is None and memory.size == 0:
        console.print(f"[yellow]{t('cli.play.no_agent_for_npc', npc_id=npc_id)}[/yellow]")
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
):
    """Simule N mois sans player input. Le MONDE tourne (spec §6.5).

    Spec §6.5 : 'le monde tourne sans le joueur ... events canon se
    declenchent ou s'annulent selon les actions agents'. Implementation :
    - Charge la save (world + character + canon)
    - A chaque tick agent : advance world time (1 semaine), tick canon
      scheduler, age character si annee passe
    - Persiste la save mise a jour a la fin
    - Retourne (aged_character, final_world) pour refresh in-memory du
      play_session, ou None si erreur.
    """
    if months <= 0 or months > 60:
        console.print("[red]Mois doit etre dans [1, 60][/red]")
        return None
    db_path = save_module.agents_db_path(save_id)
    cache_path = save_module.llm_cache_db_path(save_id)
    emb_path = save_module.agents_embeddings_db_path(save_id)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Charge state pour faire avancer le monde (spec §6.5)
    character, world, _meta = save_module.load_save(save_id)
    canon = load_canon(
        optional=(
            "characters", "techniques", "clans", "villages", "organizations",
            "tailed_beasts", "kekkei_genkai", "kekkei_mora", "hiden",
            "weapons_tools", "locations", "timeline_events", "voice_profiles",
        ),
    )

    console.print(
        t("cli.play.fast_forward_in_progress", months=months)
    )
    # Spec §6.1 : BGE-M3 wired si dispo. Fallback gracieux Jaccard sinon.
    bge_encoder, bge_query = try_load_bge_encoders()
    embeddings_idx = EmbeddingsIndex(
        emb_path, encoder=bge_encoder, query_encoder=bge_query,
    )
    if bge_encoder is not None:
        console.print("[dim]BGE-M3 actif pour retrieval semantique[/dim]")

    # Spec §6.3 : 'Son vecteur de personnalite actuel'. Le PersonalityStore
    # Phase D doit etre passe au TickEngine pour que les agents recoivent
    # leur vector dans SelectionContext.
    personality_db = save_module.personality_db_path(save_id)

    # Spec §6.3 : KG + SocialNetwork pour auto-fill world_summary +
    # relations_summary dans SelectionContext de chaque agent.
    from shinobi.kg.social import SocialNetwork
    from shinobi.kg.store import KnowledgeGraphStore
    kg_db = save_module.kg_db_path(save_id)
    kg_store_ref = (
        KnowledgeGraphStore(kg_db) if kg_db.exists() else None
    )
    social_net = (
        SocialNetwork(kg_store_ref.conn) if kg_store_ref is not None else None
    )

    # Phase D : engine de drift pour les canon events fired pendant fast-forward
    personality_engine = PersonalityEngine()

    # Phase C §5.3 : TensionScheduler - 1 inf LLM analyst tous les 3 mois.
    # Le LLMClient est cree LAZY dans canon_scheduler_callable via async
    # with (HTTP context valide seulement la). On charge le state initial
    # ici pour respecter le throttling entre sessions.
    ff_state_path = save_module.tension_scheduler_state_path(save_id)
    ff_scheduler_state = None
    if kg_store_ref is not None:
        try:
            from shinobi.tension import SchedulerState
            ff_scheduler_state = SchedulerState()
            if ff_state_path.exists():
                try:
                    ff_scheduler_state = SchedulerState.from_dict(
                        json.loads(ff_state_path.read_text(encoding="utf-8")),
                    )
                except (json.JSONDecodeError, OSError):
                    ff_scheduler_state = SchedulerState()
        except Exception:
            ff_scheduler_state = None
    # Accumulateur de tensions analyst pour le digest
    analyst_tensions_acc: list = []

    # Phase G §7 : DirectorState charge en amont pour que canon_scheduler_callable
    # puisse muter le state au gre des ticks Director (sinon nonlocal binding
    # echoue car defini apres la closure).
    ff_director_state = None
    try:
        from shinobi.director import DirectorState as _DState
        d_state_path = save_module.director_state_path(save_id)
        ff_director_state = _DState()
        if d_state_path.exists():
            try:
                ff_director_state = _DState.from_dict(
                    json.loads(d_state_path.read_text(encoding="utf-8")),
                )
            except (json.JSONDecodeError, OSError) as exc:
                from shinobi.logging_setup import get_logger as _glog
                _glog(__name__).warning(
                    "ff_director_state_load_corrupted",
                    path=str(d_state_path),
                    error=type(exc).__name__, msg=str(exc)[:200],
                )
                ff_director_state = _DState()
    except Exception as exc:
        # Audit anti-silent : si l'import DirectorState casse (ex refactor
        # path) ou autre, on log au lieu de continuer en mode degraded
        # silencieux (FF tournerait sans Director).
        from shinobi.logging_setup import get_logger as _glog
        _glog(__name__).warning(
            "ff_director_state_init_failed",
            error=type(exc).__name__, msg=str(exc)[:200],
        )
        ff_director_state = None

    # State partage entre les ticks pour faire avancer le monde
    world_ref = [world]

    async def canon_scheduler_callable(state, year, tick, *, actions=()):
        """Tick canon scheduler + advance world time. Spec §6.5.

        Spec §6.5 'events canon ... selon les actions agents' :
        - Les actions high-impact mutent world.npc_states avant tick_scheduler
        - tick_scheduler lit world.npc_states pour evaluer preconditions
        - Le link causal agents -> canon est ainsi etabli

        Phase D ↔ Phase E : les canon events fired declenchent le drift de
        personnalite des PNJ impliques (idem main loop joueur).
        """
        from shinobi.engine.time import advance_time
        from shinobi.utils.time_utils import GameDate

        cur_world = world_ref[0]
        # Spec §6.5 : applique les mutations world des actions agents
        # de ce tick AVANT le tick scheduler.
        if actions:
            cur_world = apply_actions_to_world_state(actions, cur_world)
        # Advance time : 1 semaine par tick
        prev_date = GameDate(
            year=cur_world.current_year,
            month=int(cur_world.current_date.split("-")[0]),
            day=int(cur_world.current_date.split("-")[1]),
            hour=cur_world.current_hour,
            minute=cur_world.current_minute,
        )
        new_date = advance_time(prev_date, 7 * 24 * 60)  # 1 week
        cur_world = cur_world.with_time(
            year=new_date.year,
            date=new_date.date_str,
            hour=new_date.hour,
            minute=new_date.minute,
        )
        # Tick canon scheduler (lit npc_states deja mutes par actions agents)
        cur_world, fired, cancelled = tick_scheduler(
            cur_world, canon, turn_number=tick,
        )
        world_ref[0] = cur_world

        # Phase D ↔ Phase E : drift de personnalite sur les events fired
        # (idem main loop joueur). Les NPCs impliques voient leur vecteur
        # bouger selon les rules deterministes (event_bridge).
        if fired:
            try:
                _apply_personality_drift_for_fired(
                    save_id, fired, canon, personality_engine,
                )
            except Exception:
                pass

        # Phase C §5.3 : TensionScheduler tick avec LLMClient cree LAZY
        # (HTTP context valide seulement dans `async with`).
        nonlocal ff_scheduler_state, ff_director_state
        tensions_for_director = None
        if kg_store_ref is not None and ff_scheduler_state is not None:
            try:
                month = int(cur_world.current_date.split("-")[0])
                tick_result = await _phase_c_tick_with_llm(
                    kg_store=kg_store_ref,
                    social=social_net,
                    state=ff_scheduler_state,
                    year=year, month=month, canon=canon,
                )
                if tick_result.new_state is not None:
                    ff_scheduler_state = tick_result.new_state
                if tick_result.analyst_ran and tick_result.tensions.tensions:
                    analyst_tensions_acc.extend(tick_result.tensions.tensions)
                tensions_for_director = tick_result.tensions
            except Exception as exc:
                from shinobi.logging_setup import get_logger as _glog
                _glog(__name__).warning(
                    "phase_c_tick_failed_in_ff",
                    error=type(exc).__name__,
                    msg=str(exc)[:200],
                    year=year, tick=tick,
                )

        # Phase G §7 : tick Director avec les tensions fraichement detectees.
        # Avant : Director ne tickait pas en fast-forward -> acts figes a t0,
        # nouveaux tensions jamais composees en acts. Avec ce tick, les acts
        # evoluent au fil des mois et le _refresh_nudge du between_ticks_fn
        # (cf engine.fast_forward) compose un nudge a jour pour les agents.
        if (
            ff_director_state is not None
            and tensions_for_director is not None
        ):
            try:
                month = int(cur_world.current_date.split("-")[0])
                director_report = await _phase_g_tick_director(
                    canon=canon,
                    tensions=tensions_for_director,
                    world=cur_world,
                    director_state=ff_director_state,
                    year=year, month=month,
                )
                # _phase_g_tick_director mute le state in-place via Director.tick
                _ = director_report
            except Exception as exc:
                # Audit anti-silent : log au lieu de pass nu (un bug de
                # signature ou import casse Phase G en FF se voyait pas
                # avant). Defensive : on continue la simulation.
                from shinobi.logging_setup import get_logger as _glog
                _glog(__name__).warning(
                    "phase_g_tick_director_failed_in_ff",
                    error=type(exc).__name__,
                    msg=str(exc)[:200],
                    year=year, tick=tick,
                )

        return state, fired, cancelled

    with AgentMemoryStore(db_path) as store, \
            LLMCache(cache_path) as cache, \
            embeddings_idx as emb_idx, \
            PersonalityStore(personality_db) as p_store:

        roster = AgentRoster(store)
        if roster.major_count == 0:
            initialize_roster(store, included_since_year=current_year)
            roster = AgentRoster(store)
        # LLM call=None : utilise le fallback deterministe (frugal)
        # Spec §6.4 : BatchActionSelector pour le tier secondary
        # Spec §6.3 : KG + SocialNetwork pour auto-fill summaries
        engine = TickEngine(
            roster=roster, memory_store=store,
            selector=ActionSelector(cache=cache),
            reflector=Reflector(cache=cache),
            cache=cache,
            embeddings_index=emb_idx,
            personality_store=p_store,
            batch_selector=BatchActionSelector(cache=cache, batch_size=5),
            kg_store=kg_store_ref,
            social_network=social_net,
            # Phase H wiring 9.2 : deep_motivations injecte dans les
            # SelectionContext de chaque agent via _build_inputs.
            deep_motivations_dataset=canon.deep_motivations or None,
            # Phase H 9.2 fallback : canon.characters pour deriver un
            # profil minimal aux 1310 NPCs sans entry 9.2 enrichie.
            canon_characters=canon.characters,
        )
        # Phase G+E wiring : compose le nudge initial depuis le DirectorState
        # deja charge en amont (cf init avant canon_scheduler_callable). Le
        # state evolue dans canon_scheduler_callable via _phase_g_tick_director
        # entre les ticks agents.
        # Phase G+H wiring : nudge initial via helper unifie.
        from shinobi.director import build_director_nudge_text
        nudge_text_init = build_director_nudge_text(
            canon=canon,
            director_state=ff_director_state,
            current_year=current_year,
        )
        if nudge_text_init:
            engine.set_director_nudge_text(nudge_text_init)

        # Phase G+E wiring : hook between_ticks_fn pour re-builder le nudge
        # apres chaque tick canon scheduler. Cap a 1x par mois (= ticks_per_month)
        # pour amortir le cout de build_nudge_text : sans ce throttle,
        # 4 builds/tick * 4 tick/mois = 16 builds/mois pour le meme nudge.
        ticks_per_month = engine._ticks_per_month
        last_nudge_built_tick = [-1]

        def _refresh_nudge(eng, cur_year: int, cur_tick: int) -> None:
            if cur_tick - last_nudge_built_tick[0] < ticks_per_month:
                return
            try:
                # Phase G+H wiring : helper unifie, meme logique que main loop
                # et FF init. Les acts evoluent via _phase_g_tick_director
                # appele depuis canon_scheduler_callable.
                nudge_text_n = build_director_nudge_text(
                    canon=canon,
                    director_state=ff_director_state,
                    current_year=cur_year,
                )
                if nudge_text_n:
                    eng.set_director_nudge_text(nudge_text_n)
                    last_nudge_built_tick[0] = cur_tick
            except Exception:
                pass

        digest = await engine.fast_forward(
            from_year=current_year, months=months,
            canon_scheduler_fn=canon_scheduler_callable,
            canon_scheduler_state={},  # opaque, used as placeholder
            between_ticks_fn=_refresh_nudge,
        )

    # Persiste world state mis a jour + age character
    final_world = world_ref[0]
    aged_character = _age_character_if_needed(character, final_world)
    try:
        save_module.save_passive_state(
            save_id,
            turn_number=_meta.total_turns + digest.ticks_simulated,
            new_character=aged_character,
            new_world=final_world,
            seed_state=int(final_world.seed),
        )
        console.print(
            f"[dim]Etat sauvegarde : an {final_world.current_year} "
            f"date {final_world.current_date}[/dim]",
        )
    except Exception as exc:
        console.print(
            f"[red]Persistance fast-forward echouee : {type(exc).__name__}: {exc}[/red]"
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
            lines.append(t("cli.play.more_count", count=len(digest.entries) - 20))
    else:
        lines.append("")
        lines.append(t("cli.play.no_marking_event"))

    # Phase C §5.3 : afficher les tensions LLM analyst capturees
    if analyst_tensions_acc:
        lines.append("")
        lines.append(t("cli.play.tensions_analyst_header", count=len(analyst_tensions_acc)))
        # Trie par severity descendant
        sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        sorted_t = sorted(
            analyst_tensions_acc,
            key=lambda t: sev_order.get(t.severity.value, 99),
        )
        for tn in sorted_t[:8]:
            lines.append(
                f"  [{tn.severity.value}] [{tn.type.value}] {tn.description[:80]}"
            )

    # Phase C §5.3 : afficher aussi les tensions detector finales (apres
    # fast-forward, l'etat KG a evolue -> nouvelles configurations
    # detectees par les 20 invariants statiques).
    if kg_store_ref is not None:
        try:
            from shinobi.tension import TensionDetector
            # Phase H wiring 9.3 : canon passe pour activer la 21eme regle
            # political_alliance_brittle_via_dead_leader.
            final_detector = TensionDetector(kg_store_ref, canon=canon)
            final_det = final_detector.detect(final_world.current_year)
            if final_det.tensions:
                # Filtre severity >= medium pour eviter spam
                sev_order_d = {"critical": 0, "high": 1, "medium": 2, "low": 3}
                medium_plus = [
                    tn for tn in final_det.tensions
                    if sev_order_d.get(tn.severity.value, 99) <= 2
                ]
                if medium_plus:
                    lines.append("")
                    lines.append(t("cli.play.tensions_detector_finals_header", count=len(medium_plus)))
                    medium_plus.sort(
                        key=lambda t: sev_order_d.get(t.severity.value, 99),
                    )
                    for tn in medium_plus[:8]:
                        lines.append(
                            f"  [{tn.severity.value}] [{tn.type.value}] "
                            f"{tn.description[:80]}"
                        )
        except Exception:
            pass

    console.print(
        Panel("\n".join(lines), title=f"Fast-forward {months} mois", border_style="magenta")
    )

    # Spec §5.3 : persiste le scheduler state apres fast-forward pour
    # preserver le throttling 3 mois entre sessions.
    if ff_scheduler_state is not None:
        try:
            ff_state_path.parent.mkdir(parents=True, exist_ok=True)
            ff_state_path.write_text(
                json.dumps(
                    ff_scheduler_state.to_dict(),
                    ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            from shinobi.logging_setup import get_logger as _glog
            _glog(__name__).warning(
                "ff_scheduler_state_persist_failed",
                error=type(exc).__name__, msg=str(exc)[:200],
            )

    # Phase G+E wiring : persiste le DirectorState apres fast-forward.
    # Avant : le state evoluait dans canon_scheduler_callable mais n'etait
    # jamais ecrit sur disque -> reload de la save ramenait l'etat pre-FF
    # et les acts composes pendant le FF disparaissaient.
    if ff_director_state is not None:
        try:
            d_state_path = save_module.director_state_path(save_id)
            d_state_path.parent.mkdir(parents=True, exist_ok=True)
            d_state_path.write_text(
                json.dumps(
                    ff_director_state.to_dict(),
                    ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            # Audit anti-silent : si la persistence DirectorState echoue,
            # le reload de save aura un state stale -> acts composes pendant
            # FF perdus. On log pour exposer (ex permission denied, JSON
            # serialization error sur acts mal formes).
            from shinobi.logging_setup import get_logger as _glog
            _glog(__name__).warning(
                "ff_director_state_persist_failed",
                error=type(exc).__name__, msg=str(exc)[:200],
            )

    # Cleanup KG store
    if kg_store_ref is not None:
        try:
            kg_store_ref.close()
        except Exception:
            pass

    # Spec §6.5 : retourne (character, world) refreshes pour que le caller
    # puisse mettre a jour son state in-memory.
    return aged_character, final_world


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
    director_nudge_text: str | None = None,
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
            t("cli.play.present_npcs_summary", npcs=", ".join(present_npcs))
            if present_npcs
            else t("cli.play.no_canon_npc_in_scene")
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
            # Phase G+H wiring : directives Director (acts + invariants +
            # narrative_patterns 9.5). Build par build_nudge_text en amont.
            director_nudge_text=director_nudge_text,
            # Phase H 9.2 wiring : profils psycho des NPCs presents pour
            # dialogues en-character (drive principal + 1 red line).
            present_npcs_motivations_text=(
                _build_present_npcs_motivations_text(canon, present_npcs)
                if present_npcs else None
            ),
            # Phase H 9.3 wiring : descriptions politiques des factions
            # pertinentes (village current + clans des NPCs presents).
            relevant_factions_text=_build_relevant_factions_text(
                canon, character.current_location, present_npcs,
            ),
        )
        return await narrator.narrate(request)


def _build_relevant_factions_text(
    canon, location_id: str | None, present_npcs: list[str],
) -> str | None:
    """Helper : 9.3 description_fr des factions pertinentes a la scene."""
    try:
        from shinobi.agents.context_builder import (
            build_faction_descriptions_block,
        )
        text = build_faction_descriptions_block(
            political_forces=canon.political_forces or None,
            location_id=location_id,
            present_npc_ids=tuple(present_npcs or ()),
        )
        return text or None
    except Exception:
        from shinobi.logging_setup import get_logger as _glog
        _glog(__name__).warning(
            "narrator_relevant_factions_failed",
            location_id=location_id,
        )
        return None


def _build_present_npcs_motivations_text(
    canon, present_npcs: list[str],
) -> str | None:
    """Helper : compose le block 9.2 motivations pour le narrator. Retourne
    None si rien a injecter (pour omettre le block dans le prompt).
    """
    try:
        from shinobi.agents.context_builder import (
            build_present_npcs_motivations_block,
        )
        text = build_present_npcs_motivations_block(
            deep_motivations_dataset=canon.deep_motivations or None,
            present_npc_ids=present_npcs,
        )
        return text or None
    except Exception:
        from shinobi.logging_setup import get_logger as _glog
        _glog(__name__).warning(
            "narrator_present_npcs_motivations_failed",
            n_npcs=len(present_npcs),
        )
        return None


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


def _check_goal_completions(save_id: str, character, current_year: int, *, canon=None) -> list[str]:
    """Verifie tous les goals declares et marque ceux qui sont accomplis.

    Renvoie la liste des descriptions accomplies ce tour.
    """
    from shinobi.goals.completion import check_goal_by_target, check_goal_completion
    from shinobi.goals.declaration import complete_goal, detect_goal_failure, fail_goal

    goals = save_module.load_goals(save_id)
    breadcrumbs = save_module.load_breadcrumbs(save_id)
    completed_now: list[str] = []
    canon_chars = canon.characters if canon is not None else {}
    player_dead = getattr(character, "is_dead", False)
    for goal in goals:
        # Phase 5 : on traite declared ET in_progress (un goal active reste
        # eligible a completion via target_match meme apres pathfinder).
        if goal.status.value not in ("declared", "in_progress"):
            continue
        # Phase 5 : detection auto-failure (target mort, joueur mort).
        # Prend precedence sur completion : un goal sur Itachi ne peut pas
        # etre complete si Itachi est mort entre temps.
        fail_reason = detect_goal_failure(
            goal,
            canon_characters=canon_chars,
            current_year=current_year,
            player_is_dead=player_dead,
        )
        if fail_reason is not None:
            failed_goal = fail_goal(goal, current_year, reason=fail_reason)
            save_module.save_goal(save_id, failed_goal)
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
        console.print(t("cli.play.not_enough_ryos_pocket", amount=amount, money=character.money))
        return character, world
    goals = [g for g in save_module.load_goals(save_id) if g.status.value == "declared"]
    if not goals:
        console.print(f"[yellow]{t('cli.play.no_objective_pay')}[/yellow]")
        return character, world
    target_goal = goals[-1]
    breadcrumbs = save_module.load_breadcrumbs(save_id, parent_goal_id=target_goal.id)
    hidden = [bc for bc in breadcrumbs if not bc.revealed]
    if not hidden:
        console.print(t("cli.play.contact_no_more_to_say"))
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
        f"  [yellow]{t('cli.play.not_enough_ryos', short=short)} "
        f"{t('cli.play.malnutrition_msg')}[/yellow]"
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
                summary=t(
                    "engine.biography.summary.promotion",
                    old=char_before.rank,
                    new=char_after.rank,
                ),
                category="rank_promotion",
            )
        )

    before_techs = {tk.technique_id for tk in char_before.techniques_known}
    after_techs = {tk.technique_id for tk in char_after.techniques_known}
    for tid in after_techs - before_techs:
        events.append(
            BiographyEvent(
                year=year,
                age=age,
                summary=t("engine.biography.summary.technique_learned", technique_id=tid),
                category="technique_learned",
            )
        )

    if not char_before.is_dead and char_after.is_dead:
        events.append(
            BiographyEvent(
                year=year,
                age=age,
                summary=char_after.death_circumstances or t("cli.play.death_default"),
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
                    summary=t(
                        "engine.biography.summary.severe_injury",
                        hp=char_after.health.hp_current,
                        hp_max=char_after.health.hp_max,
                    ),
                    category="trauma",
                )
            )

    if not char_before.is_missing_nin and char_after.is_missing_nin:
        events.append(
            BiographyEvent(
                year=year,
                age=age,
                summary=t(
                    "engine.biography.summary.becomes_nukenin",
                    village=char_before.current_village,
                ),
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
        console.print(f"[yellow]{t('cli.play.already_at_destination')}[/yellow]")
        return character, world
    minutes = travel_minutes(character.current_village, target_village)
    days = max(1, minutes // (24 * 60))
    fatigue_cost = min(80, 8 * days)
    console.print(
        Panel.fit(
            t(
                "cli.play.travel_panel",
                from_village=character.current_village,
                to_village=target_village,
            )
            + "\n"
            + t("cli.play.travel_estimate", days=days, fatigue=fatigue_cost),
            title=t("cli.play.travel_title"),
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
        console.print(t("cli.play.already_nukenin"))
        return character, world
    if not Prompt.ask(
        f"[bold red]{t('cli.play.confirm_desertion')}[/bold red]",
        default="non",
    ).strip().lower().startswith("o"):
        console.print(t("cli.play.desertion_aborted"))
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
            t(
                "cli.play.desertion_panel",
                village=village,
                bingo=t("cli.play.bingo_book_warning"),
            ),
            title=t("cli.play.nukenin_title"),
            border_style="red",
        )
    )
    new_rep = new_char.reputation.model_copy(update={"bingo_book_entry": True})
    new_char = new_char.model_copy(update={"reputation": new_rep})
    return new_char, world


def _print_biography(character) -> None:
    """Affiche le journal biographique du personnage."""
    if not character.biography_log:
        console.print(Panel(t("cli.play.no_biography"), title=t("cli.play.biography_panel_title")))
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
            title=t("cli.play.biography_panel_count", name=character.name, count=len(character.biography_log)),
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
        console.print(Panel(t("cli.play.knowledge_empty"), title=t("cli.play.knowledge_title")))
        return
    body = "\n\n".join(f"{title}\n{content}" for title, content in sections)
    console.print(Panel(body, title="Connaissances", border_style="blue"))


def _print_rumors(world, canon) -> None:
    """Affiche les rumeurs reçues par le joueur."""
    received = [r for r in world.rumors if r.received_by_player]
    if not received:
        console.print(Panel(t("cli.play.no_rumor_yet"), title=t("cli.play.rumors_panel_title")))
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
        Panel("\n".join(lines), title=t("cli.play.rumors_panel_count", count=len(received)), border_style="yellow")
    )


def _print_breadcrumbs(save_id: str) -> None:
    """Affiche les sous-objectifs reveles non encore accomplis."""
    bcs = save_module.load_breadcrumbs(save_id)
    if not bcs:
        console.print(Panel(t("cli.play.no_breadcrumb"), title=t("cli.play.pistes_title")))
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
        console.print(Panel(t("cli.play.no_breadcrumb"), title=t("cli.play.pistes_title")))
        return
    console.print(Panel("\n\n".join(blocks), title=t("cli.play.pistes_title"), border_style="magenta"))


def _print_weapons(character) -> None:
    """Affiche les armes equipees."""
    if not character.weapons:
        console.print(
            Panel(
                t("cli.play.no_weapon"),
                title=t("cli.play.weapons_title"),
            )
        )
        return
    lines = []
    for w in character.weapons:
        marker = " [dim](x" + str(w.quantity) + ")[/dim]" if w.quantity > 1 else ""
        lines.append(f"  [cyan]{w.weapon_id}[/cyan] [dim]({w.quality})[/dim]{marker}")
    console.print(Panel("\n".join(lines), title=t("cli.play.weapons_title_count", count=len(character.weapons)), border_style="cyan"))


def _print_summons(character) -> None:
    """Affiche les contrats d'invocation signes."""
    if not character.summons:
        console.print(
            Panel(
                t("cli.play.no_summon_contract"),
                title="Invocations",
            )
        )
        return
    lines = [f"  [magenta]*[/magenta] {s}" for s in character.summons]
    console.print(
        Panel("\n".join(lines), title=f"Contrats d'invocation ({len(character.summons)})", border_style="magenta")
    )


CANONICAL_SUMMONS_IDS: tuple[str, ...] = (
    "toad",
    "snake",
    "slug",
    "hawk",
    "monkey",
    "ninken",
    "weasel",
    "crow",
    "dragon",
)


def _summon_label(contract_id: str) -> str:
    """Resout le libelle localise d'un contrat canonique."""
    return t(f"cli.play.summons.{contract_id}.label")


def _sign_contract_flow(character, contract_name: str):
    """Signe un contrat d'invocation. Heuristique : ouvert a tout nom canonique connu."""
    if contract_name not in CANONICAL_SUMMONS_IDS:
        console.print(
            t(
                "cli.play.contract_unknown",
                name=contract_name,
                available=", ".join(CANONICAL_SUMMONS_IDS),
            )
        )
        return character
    canonical = _summon_label(contract_name)
    if contract_name in character.summons:
        console.print(t("cli.play.contract_already_signed", contract=contract_name))
        return character
    new_summons = [*character.summons, contract_name]
    new_char = character.model_copy(update={"summons": new_summons})
    console.print(
        Panel.fit(
            t(
                "cli.play.contract_signed_panel",
                contract=contract_name,
                canonical=canonical,
            ),
            title=t("cli.play.contract_signed_title"),
            border_style="magenta",
        )
    )
    return new_char


def _invoke_flow(character, contract_name: str):
    """Invocation : consomme 30 chakra, succes selon ninjutsu + chakra_control."""
    if contract_name not in character.summons:
        console.print(t("cli.play.contract_not_signed", contract=contract_name))
        return character
    if character.chakra.current < 30:
        console.print(
            f"[red]{t('cli.play.summon_insufficient_chakra', current=character.chakra.current, required=30)}[/red]"
        )
        return character
    new_chakra = character.chakra.model_copy(update={"current": character.chakra.current - 30})
    new_char = character.with_chakra(new_chakra)
    skill = (character.stats.ninjutsu + character.extended_stats.chakra_control) / 2
    if skill < 1.5:
        console.print(
            Panel(
                t("cli.play.summon_failed_msg"),
                title=t("cli.play.summon_failed_title"),
                border_style="red",
            )
        )
        return new_char
    if skill < 3.0:
        console.print(
            Panel(
                t("cli.play.summon_minor_text", contract=contract_name),
                title=t("cli.play.summon_minor_title"),
                border_style="cyan",
            )
        )
    else:
        console.print(
            Panel(
                t("cli.play.summon_major_text", contract=contract_name),
                title=t("cli.play.summon_major_title"),
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
                t("cli.play.action_reinterpreted", action_type=new_parsed.action_type.value)
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
            "[bold yellow]"
            + t("cli.play.reputation_intenable", village=village, score=score)
            + " "
            + t("cli.play.flee_warning")
            + "[/bold yellow]",
            title=t("cli.play.warning_title"),
            border_style="yellow",
        )
    )
    return _desertion_flow(character, world)
