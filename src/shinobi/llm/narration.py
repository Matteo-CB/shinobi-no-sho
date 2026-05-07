"""Orchestrateurs LLM : narrator, character interpreter, world resolver."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shinobi.canon.fact_sheet import context_fact_sheets, fact_sheets_for
from shinobi.canon.models import CanonBundle
from shinobi.config import settings
from shinobi.engine.scene_context import (
    SceneContext,
    format_scene_context_for_prompt,
    looks_like_generic_role,
)
from shinobi.errors import LLMSchemaError, LLMStyleError
from shinobi.llm.claim_validator import (
    format_violations_for_retry,
    validate_narration_claims,
)
from shinobi.llm.client import LLMClient, Message
from shinobi.llm.judge import CanonJudge, format_judge_violations_for_retry
from shinobi.llm.schema import NARRATOR_SCHEMA, build_narrator_schema_with_enum
from shinobi.llm.voices import compose_voice_section
from shinobi.prompts import PersonaContext, build_system_prompt
from shinobi.rag.contextualize import TurnContextRequest, build_turn_context
from shinobi.rag.retriever import Retriever
from shinobi.state.world_state import (
    NarrativeTime,
    PlayerCharacterState,
    RuntimeState,
    SceneContextSnapshot,
)
from shinobi.utils.text import (
    contains_em_dash,
    contains_emoji,
    contains_forbidden_slang,
    sanitize_narrative,
)
from shinobi.validation import (
    AgeCoherenceLayer,
    SherlockRulesLayer,
    TripletCheckLayer,
    Validator,
    format_violations_for_regen,
)
from shinobi.validation import (
    NarrativeAction as ValNarrativeAction,
)
from shinobi.validation import (
    NarrativeDialogue as ValNarrativeDialogue,
)
from shinobi.validation import (
    NarrativeOutput as ValNarrativeOutput,
)

# Estimations heuristiques pour combler les "?" dans le tableau d'actions proposees.
# Le LLM ne fournit pas systematiquement difficulty/duration ; on les derive du label.
_DIFFICULTY_KEYWORDS_FR: dict[str, str] = {
    "combat": "difficile", "attaque": "difficile", "tuer": "tres difficile",
    "voler": "difficile", "espionner": "difficile",
    "intimider": "difficile", "seduire": "difficile",
    "entrain": "modere", "entraine": "modere", "pratique": "modere",
    "etudier": "facile", "lire": "facile", "ecouter": "facile",
    "suivre les cours": "facile", "discuter": "facile", "parler": "facile",
    "demander": "facile", "se reposer": "trivial", "dormir": "trivial",
    "mediter": "trivial", "manger": "trivial",
    "voyager": "modere", "se rendre": "modere", "aller": "facile",
}
_DURATION_KEYWORDS_FR: dict[str, str] = {
    "dormir": "8h", "se reposer": "1h", "mediter": "1h",
    "entrain": "4h", "pratique": "4h",
    "etudier": "2h", "lire": "2h", "ecouter": "1h",
    "suivre les cours": "2h",
    "voyager": "1j+", "se rendre": "1j+",
    "discuter": "30min", "parler": "30min", "demander": "15min",
    "manger": "30min",
    "combat": "1h", "attaque": "30min",
}


def _guess_difficulty(label: str) -> str:
    low = label.lower()
    for kw, diff in _DIFFICULTY_KEYWORDS_FR.items():
        if kw in low:
            return diff
    return "modere"


def _guess_duration(label: str) -> str:
    low = label.lower()
    for kw, dur in _DURATION_KEYWORDS_FR.items():
        if kw in low:
            return dur
    return "1h"


def _enrich_proposed_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remplit difficulty/duration cote Python si le LLM les a omises."""
    out: list[dict[str, Any]] = []
    for a in actions:
        label = a.get("label_fr", "") or a.get("label", "")
        new = dict(a)
        if not new.get("difficulty_fr") and not new.get("difficulty"):
            new["difficulty_fr"] = _guess_difficulty(label)
        if not new.get("duration_fr") and not new.get("duration"):
            new["duration_fr"] = _guess_duration(label)
        out.append(new)
    return out


def _is_canon_npc_invalid(canon: CanonBundle, name_or_id: str, current_year: int) -> bool:
    """True si name_or_id correspond a un NPC canon non vivant a current_year."""
    char = canon.characters.get(name_or_id)
    if char is None:
        # Tente une recherche par nom_romaji (sensible aux variantes)
        target = name_or_id.lower().strip()
        for c in canon.characters.values():
            full = (c.name_romaji or "").lower()
            if full == target or full.replace(" ", "_") == target:
                char = c
                break
            # Match nom de famille seul (uchiha sasuke -> "uchiha")
            parts = full.split()
            if len(parts) >= 2 and (parts[0] == target or parts[-1] == target):
                if len(target) >= 4:
                    char = c
                    break
    if char is None:
        return False
    if char.birth_year is not None and current_year < char.birth_year:
        return True
    return bool(char.death_year is not None and current_year > char.death_year)


def _scan_text_for_invalid_npcs(canon: CanonBundle, text: str, current_year: int) -> list[str]:
    """Retourne la liste des NPCs canon non vivants (pas encore nes / morts) mentionnes."""
    if not text:
        return []
    lower = text.lower()
    bad: list[str] = []
    for cid, char in canon.characters.items():
        full = (char.name_romaji or "").lower()
        if not full:
            continue
        if char.birth_year is not None and current_year < char.birth_year:
            pass  # pas encore ne
        elif char.death_year is not None and current_year > char.death_year:
            pass  # mort
        else:
            continue
        # Match nom complet ou prenom long (>=5 chars pour eviter faux positifs)
        if full in lower:
            bad.append(cid)
            continue
        parts = full.split()
        for p in parts:
            if len(p) >= 5 and f" {p} " in f" {lower} ":
                bad.append(cid)
                break
    return bad


def _scan_text_for_canon_npcs(canon: CanonBundle, text: str) -> list[str]:
    """Retourne TOUS les NPCs canon mentionnes dans le texte (vivants ou non).

    Permet d'enrichir les fact sheets pour le narrator avec n'importe quel NPC
    nomme par le joueur OU par le LLM (pour validation du tour suivant).
    """
    if not text:
        return []
    lower = text.lower()
    found: list[str] = []
    for cid, char in canon.characters.items():
        full = (char.name_romaji or "").lower()
        if not full:
            continue
        if full in lower:
            found.append(cid)
            continue
        parts = full.split()
        for p in parts:
            if len(p) >= 5 and f" {p} " in f" {lower} ":
                found.append(cid)
                break
    return found


def _filter_observations(
    canon: CanonBundle, observations: list[str], current_year: int
) -> list[str]:
    """Rejette les observations qui mentionnent un NPC non vivant a cette date."""
    out = []
    for obs in observations:
        bad = _scan_text_for_invalid_npcs(canon, obs, current_year)
        if not bad:
            out.append(obs)
    return out


def _filter_proposed_actions_by_canon(
    canon: CanonBundle, actions: list[dict[str, Any]], current_year: int
) -> list[dict[str, Any]]:
    """Rejette les actions qui mentionnent un NPC canon non vivant."""
    out = []
    for a in actions:
        label = a.get("label_fr", "") or a.get("label", "")
        bad = _scan_text_for_invalid_npcs(canon, label, current_year)
        if not bad:
            out.append(a)
    return out


@dataclass
class NarrationRequest:
    """Donnees necessaires pour narrer un tour."""

    turn_summary: str
    action_text: str
    action_result_summary: str
    location_id: str | None
    present_npcs: list[str]
    active_breadcrumb_descriptions: list[str]
    character_state_summary: str
    duration_str: str
    scene_context: SceneContext | None = None
    # Contextes optionnels pour enrichir le narrator avec lieu/village/clan/etc
    current_village: str | None = None
    player_clan: str | None = None
    player_kekkei_genkai: list[str] | None = None
    player_tailed_beast: str | None = None
    # Validator anti-invention amitie joueur-NPC
    player_name: str | None = None
    established_npc_friendships: set[str] | None = None
    # Contexte VN dialogue (utilise par DialogueFormatter quand wired)
    turn_number: int | None = None
    in_game_year: int | None = None
    in_game_date: str | None = None
    related_event_id: str | None = None
    related_mission_id: str | None = None
    scene_mood: str | None = None
    # Phase G+H wiring : directives narratives Director (acts + invariants +
    # narrative_patterns 9.5 + recent_summary). Injecte avant les FAITS
    # CANONIQUES dans le user prompt pour conditionner le ton narratif. Cap a
    # ~1200 chars en amont par build_nudge_text.
    director_nudge_text: str | None = None
    # Phase H 9.2 wiring : profils psycho condenses des NPCs presents dans
    # la scene, derives de canon.deep_motivations. Permet au narrator de
    # produire des dialogues en-character (drive principal + red lines)
    # plutot que generiques. Format ~150 chars / NPC, cap 5 NPCs max,
    # build par helper externe (cf cli.play._build_present_npcs_motivations).
    present_npcs_motivations_text: str | None = None
    # Phase H 9.3 wiring : descriptions politiques des factions pertinentes
    # a la scene (location ou members presents). Permet au narrator de
    # contextualiser politiquement la scene canon.
    relevant_factions_text: str | None = None


def _persona_context_from_request(request: NarrationRequest) -> PersonaContext:
    """Derive un PersonaContext minimal depuis un NarrationRequest.

    L'arc est laisse a "(non défini)" : le re-tagging temporel par arc viendra
    avec le pilier 5 (cf. research/anti-hallucination-rag-narratif-v2.md §5).
    """
    sc = request.scene_context
    if sc is None:
        return PersonaContext(player_name=request.player_name or "(non défini)")
    return PersonaContext(
        player_name=request.player_name or "(non défini)",
        rank=sc.player_rank or "(non défini)",
        village=sc.player_village or "(non défini)",
        age=sc.player_age,
        year=sc.current_year,
    )


@dataclass
class NarrationResponse:
    """Reponse structuree du narrator."""

    narrative: str
    npc_dialogue: list[dict[str, Any]]
    proposed_actions: list[dict[str, Any]]
    world_observations: list[str]
    clarification_request: str | None


def _build_anti_hallu_validator() -> Validator:
    """Validator pilier 6B : sherlock A + triplet B + age C."""
    return Validator([
        SherlockRulesLayer(),
        TripletCheckLayer(),
        AgeCoherenceLayer(),
    ])


def _narration_to_validator_output(parsed: NarrationResponse) -> ValNarrativeOutput:
    """Convertit une NarrationResponse legacy en NarrativeOutput du validator."""
    dialogues = []
    for d in parsed.npc_dialogue or []:
        if not isinstance(d, dict):
            continue
        dialogues.append(ValNarrativeDialogue(
            character_id=d.get("character_id", ""),
            line=d.get("line", "") or d.get("text", ""),
            tone=d.get("tone"),
        ))
    proposed = []
    for a in parsed.proposed_actions or []:
        if not isinstance(a, dict):
            continue
        proposed.append(ValNarrativeAction(
            actor=a.get("actor"),
            type=a.get("type") or a.get("action_type"),
            location=a.get("location") or a.get("location_id"),
            jutsu=a.get("jutsu") or a.get("jutsu_id"),
            target=a.get("target") or a.get("target_id"),
            label_fr=a.get("label_fr") or a.get("label"),
        ))
    return ValNarrativeOutput(
        narrative=parsed.narrative or "",
        npc_dialogue=dialogues,
        proposed_actions=proposed,
        world_observations=parsed.world_observations or [],
    )


def _build_runtime_state_from_request(request: NarrationRequest) -> RuntimeState | None:
    """Construit un RuntimeState minimal pour le validator anti-hallu.

    Necessite scene_context.current_year et un nom de joueur. Retourne None
    si insuffisant : le validator anti-hallu sera alors skip pour ce tour.
    """
    sc = request.scene_context
    if sc is None or sc.current_year is None:
        return None
    return RuntimeState(
        narrative_time=NarrativeTime(
            arc="(non défini)",
            approximate_year=sc.current_year,
        ),
        player_character=PlayerCharacterState(
            name=request.player_name or "(non défini)",
            village=sc.player_village or "(non défini)",
            rank=sc.player_rank or "academy_student",
            location=request.location_id or "(non défini)",
        ),
        scene_context=SceneContextSnapshot(
            location=request.location_id,
            present_characters=list(request.present_npcs or []),
        ),
    )


class Narrator:
    """Orchestrateur du role NARRATOR.

    Si un `DialogueFormatter` et un `DialogueLog` sont fournis, chaque sortie
    LLM (narrative + npc_dialogue) est convertie en sequence de DialogueLines
    indexables et appendee au log automatiquement (Phase VN integration).
    Cela permet l'export ulterieur vers une application Visual Novel sans
    modifier le pipeline de narration.
    """

    def __init__(
        self,
        client: LLMClient,
        canon: CanonBundle,
        retriever: Retriever,
        *,
        enable_anti_hallu_validation: bool | None = None,
        dialogue_formatter=None,  # DialogueFormatter | None
        dialogue_log=None,        # DialogueLog | None
    ) -> None:
        self.client = client
        self.canon = canon
        self.retriever = retriever
        # Default = settings.enable_anti_hallu_validation (True par defaut).
        # Surchargeable per-instance pour debug ou tests.
        self.enable_anti_hallu_validation = (
            enable_anti_hallu_validation
            if enable_anti_hallu_validation is not None
            else settings.enable_anti_hallu_validation
        )
        self._anti_hallu_validator = (
            _build_anti_hallu_validator()
            if self.enable_anti_hallu_validation
            else None
        )
        # Integration VN (optionnelle). Si les deux sont fournis, append-auto.
        self._dialogue_formatter = dialogue_formatter
        self._dialogue_log = dialogue_log

    @property
    def dialogue_log(self):
        return self._dialogue_log

    def _capture_dialogues(
        self, response: NarrationResponse, request: NarrationRequest,
    ) -> int:
        """Convertit la sortie LLM en DialogueLines et appende au log si configure.

        Retourne le nombre de lignes ajoutees (0 si VN integration disabled).
        """
        if self._dialogue_formatter is None or self._dialogue_log is None:
            return 0
        location_id = (
            request.location_id
            or (request.scene_context.location if request.scene_context is not None else None)
        )
        in_game_year = request.in_game_year or (
            request.scene_context.current_year if request.scene_context is not None else None
        )
        lines = self._dialogue_formatter.format(
            narrative=response.narrative,
            npc_dialogue=response.npc_dialogue,
            in_game_year=in_game_year,
            in_game_date=request.in_game_date,
            location_id=location_id,
            turn_number=request.turn_number,
            related_event_id=request.related_event_id,
            related_mission_id=request.related_mission_id,
            scene_mood=request.scene_mood,
        )
        return self._dialogue_log.append_many(lines)

    async def narrate(self, request: NarrationRequest) -> NarrationResponse:
        """Narration robuste avec retry x2 :

        1. Premier appel : grammar JSON dynamique + claim validator + LLM-as-judge
        2. Si violations -> retry avec corrections explicites
        3. Si encore violations -> retry x2
        4. Apres x2 echecs : retourne la meilleure narration disponible (ne lance pas)
        """
        rag_context = build_turn_context(
            self.retriever,
            TurnContextRequest(
                action_text=request.action_text,
                location_id=request.location_id,
                present_npcs=request.present_npcs,
                active_breadcrumb_descriptions=request.active_breadcrumb_descriptions,
            ),
        )
        voices = compose_voice_section(self.canon, request.present_npcs)

        # Fact sheets canoniques pour les NPCs presents : etat exact a l'annee in-game.
        current_year = (
            request.scene_context.current_year if request.scene_context is not None else None
        )
        fact_sheets = ""
        if current_year is not None and request.present_npcs:
            fact_sheets = fact_sheets_for(
                self.canon, request.present_npcs, current_year=current_year
            )

        # Construit le user_message de base (reutilise pour tous les retries)
        base_user_message = self._build_user_message(
            request=request,
            fact_sheets=fact_sheets,
            voices=voices,
            rag_context=rag_context,
        )

        # Grammar dynamique : restreint character_id et target_id aux NPCs des fact sheets
        dynamic_schema = (
            build_narrator_schema_with_enum(request.present_npcs)
            if request.present_npcs
            else NARRATOR_SCHEMA
        )

        judge = CanonJudge(self.client)
        last_response: NarrationResponse | None = None
        retry_correction = ""
        max_attempts = 3  # 1 essai + 2 retries

        system_prompt = build_system_prompt(_persona_context_from_request(request))

        for attempt in range(max_attempts):
            user_message = base_user_message
            if retry_correction:
                user_message += "\n\n[CORRECTION REQUISE]\n" + retry_correction

            response = await self.client.generate(
                messages=[
                    Message(role="system", content=system_prompt),
                    Message(role="user", content=user_message),
                ],
                schema=dynamic_schema,
            )
            if response.parsed_json is None:
                raise LLMSchemaError("Reponse narrator vide")

            # Post-process raw response (style + filters)
            parsed = self._post_process_response(response.parsed_json, current_year)
            last_response = parsed

            if current_year is None:
                self._capture_dialogues(parsed, request)
                return parsed

            # ===== VALIDATION ETAGEE =====
            # Etape 1 : claim validator deterministe (rapide, pas d'appel LLM)
            claim_violations = validate_narration_claims(
                self.canon,
                narrative=parsed.narrative,
                observations=parsed.world_observations,
                npc_dialogue=parsed.npc_dialogue,
                proposed_actions=parsed.proposed_actions,
                current_year=current_year,
                player_name=request.player_name,
                established_friendships=request.established_npc_friendships,
            )

            # Etape 2 : LLM-as-judge (filet de secours pour nuances)
            judge_verdict = await judge.judge(
                fact_sheets=fact_sheets,
                narrative=parsed.narrative,
                observations=parsed.world_observations,
                npc_dialogue=parsed.npc_dialogue,
                proposed_actions=parsed.proposed_actions,
            )

            # Etape 3 : validator anti-hallu pilier 6B (A+B+C). Optionnel.
            anti_hallu_results = self._run_anti_hallu_validator(parsed, request)
            anti_hallu_violations = [
                r for r in anti_hallu_results if not r.is_valid
            ]

            if (
                not claim_violations
                and judge_verdict.ok
                and not anti_hallu_violations
            ):
                self._capture_dialogues(parsed, request)
                return parsed  # SUCCES

            # Sinon : compose la correction pour le prochain retry
            corrections: list[str] = []
            if claim_violations:
                corrections.append(format_violations_for_retry(claim_violations))
            if not judge_verdict.ok and judge_verdict.violations:
                corrections.append(format_judge_violations_for_retry(judge_verdict.violations))
            if anti_hallu_violations:
                corrections.append(format_violations_for_regen(anti_hallu_violations))
            retry_correction = "\n\n".join(corrections)

            if attempt == max_attempts - 1:
                # Dernier retry : on retourne la meilleure narration meme imparfaite,
                # avec un tag d'avertissement dans la narrative.
                tags = []
                if claim_violations:
                    tags.append(
                        f"violations validator: {len(claim_violations)}"
                    )
                if not judge_verdict.ok:
                    tags.append(f"violations judge: {len(judge_verdict.violations)}")
                parsed = NarrationResponse(
                    narrative=parsed.narrative + (
                        f"\n\n[Note interne : narration retournee apres {max_attempts} "
                        f"tentatives, {' / '.join(tags)} non resolues. A lire avec recul.]"
                    ),
                    npc_dialogue=parsed.npc_dialogue,
                    proposed_actions=parsed.proposed_actions,
                    world_observations=parsed.world_observations,
                    clarification_request=parsed.clarification_request,
                )
                self._capture_dialogues(parsed, request)
                return parsed

        # Defensif : ne devrait jamais arriver (le loop retourne toujours)
        assert last_response is not None
        self._capture_dialogues(last_response, request)
        return last_response

    def _build_user_message(
        self,
        *,
        request: NarrationRequest,
        fact_sheets: str,
        voices: str,
        rag_context: str,
    ) -> str:
        """Compose le user_message du narrator (extrait pour reutilisation aux retries)."""
        user_blocks: list[str] = []
        if fact_sheets:
            user_blocks.append("############### LIRE EN PREMIER ###############")
            user_blocks.append(fact_sheets)
            user_blocks.append(
                "\nIMPORTANT : ces faits ci-dessus sont la verite ABSOLUE pour ce tour. "
                "Tu ne dois JAMAIS contredire un fact sheet. Si la situation dit 'seul, "
                "pas d'amis', tu n'invites AUCUN autre PNJ canon a interagir socialement "
                "avec lui (pas d'amis, pas de bande, pas de groupe). Si tu mentionnes un "
                "PNJ canon non liste ci-dessus en relation positive avec le joueur OU avec "
                "un PNJ liste, ta sortie sera REJETEE."
            )
            user_blocks.append("###############################################\n")
        # Contexte canon : lieu, village, clan, kekkei, bijuu du joueur
        ctx_sheets = context_fact_sheets(
            self.canon,
            current_village=request.current_village,
            current_location=request.location_id,
            player_clan=request.player_clan,
            player_kekkei_genkai=request.player_kekkei_genkai,
            player_tailed_beast=request.player_tailed_beast,
        )
        if ctx_sheets:
            user_blocks.append(ctx_sheets)
            user_blocks.append("")
        if request.scene_context is not None:
            user_blocks.append(format_scene_context_for_prompt(request.scene_context))
            user_blocks.append("")
        user_blocks.append("[ETAT DU PERSONNAGE]")
        user_blocks.append(request.character_state_summary)
        # Phase G+H wiring : directives Director injectees AVANT le RAG
        # canon. Le LLM lit les patterns Kishimoto + acts + invariants
        # avant de plonger dans les faits canoniques, qui conditionne le ton.
        if request.director_nudge_text:
            user_blocks.append("\n" + request.director_nudge_text)
        # Phase H 9.2 wiring : profils psycho condenses des NPCs presents
        # pour que dialogues + actions du narrator restent en-character
        # canon (Naruto, Sasuke, etc.). Sans ce block, le narrator avait
        # juste fact_sheets (clan/rank) et inventait les motivations.
        if request.present_npcs_motivations_text:
            user_blocks.append(
                "\n[PROFILS PSYCHO NPCS PRESENTS]\n"
                + request.present_npcs_motivations_text
            )
        # Phase H 9.3 wiring : contexte politique des factions pertinentes
        # (description_fr du village + clans des NPCs presents).
        if request.relevant_factions_text:
            user_blocks.append(
                "\n[CONTEXTE POLITIQUE]\n"
                + request.relevant_factions_text
            )
        if voices:
            user_blocks.append("\n" + voices)
        user_blocks.append("\n" + rag_context)
        user_blocks.append(
            "\n[ACTION DU JOUEUR]\n"
            f"Texte de l'intention : {request.action_text}\n"
            f"Resultat mecanique : {request.action_result_summary}\n"
            f"Duree ecoulee : {request.duration_str}"
        )
        user_blocks.append(
            "\n[INSTRUCTION FINALE]\n"
            "1. Relis les FAITS CANONIQUES NPC ci-dessus.\n"
            "2. Narre ce tour SANS contredire un seul de ces faits.\n"
            "3. Tout PNJ que tu nommes doit etre soit dans le fact sheet, soit un role "
            "generique snake_case (sensei_academie, marchand_taverne).\n"
            "4. world_observations et proposed_actions sont SOUMIS aux memes regles.\n"
            "5. Reponds en JSON conforme."
        )
        return "\n".join(user_blocks)

    def _post_process_response(
        self, data: dict[str, Any], current_year: int | None
    ) -> NarrationResponse:
        """Post-traitement de la reponse LLM : style, filtres, enrichissement."""
        narrative = data.get("narrative", "")
        if (
            contains_em_dash(narrative)
            or contains_emoji(narrative)
            or contains_forbidden_slang(narrative)
        ):
            cleaned = sanitize_narrative(narrative)
            if contains_forbidden_slang(cleaned):
                raise LLMStyleError("Argot otaku detecte dans la narration")
            narrative = cleaned

        proposed_actions = data.get("proposed_actions", []) or []
        npc_dialogue = data.get("npc_dialogue", []) or []
        observations = data.get("world_observations", []) or []

        if current_year is not None:
            # Filter NPCs non vivants
            npc_dialogue = [
                d
                for d in npc_dialogue
                if looks_like_generic_role(d.get("character_id", ""))
                or self._npc_is_alive(d.get("character_id", ""), current_year)
            ]
            observations = _filter_observations(self.canon, observations, current_year)
            proposed_actions = _filter_proposed_actions_by_canon(
                self.canon, proposed_actions, current_year
            )

        # Enrichit difficulty + duration cote Python
        proposed_actions = _enrich_proposed_actions(proposed_actions)

        return NarrationResponse(
            narrative=narrative,
            npc_dialogue=npc_dialogue,
            proposed_actions=proposed_actions,
            world_observations=observations,
            clarification_request=data.get("clarification_request"),
        )

    def _run_anti_hallu_validator(
        self, parsed: NarrationResponse, request: NarrationRequest,
    ) -> list:
        """Execute le validator anti-hallu (A+B+C) si active. Retourne []
        si desactive ou si le RuntimeState est insuffisant."""
        if self._anti_hallu_validator is None:
            return []
        runtime_state = _build_runtime_state_from_request(request)
        if runtime_state is None:
            return []
        narrative_output = _narration_to_validator_output(parsed)
        return self._anti_hallu_validator.validate(
            narrative_output=narrative_output,
            state=runtime_state,
            canon=self.canon,
            short_circuit=False,
        )

    def _npc_is_alive(self, character_id: str, year: int) -> bool:
        """True si le NPC canon est vivant a l'annee donnee."""
        if not character_id:
            return True
        char = self.canon.characters.get(character_id)
        if char is None:
            return True  # NPC inconnu : on le laisse passer
        if char.birth_year is not None and year < char.birth_year:
            return False
        return not (char.death_year is not None and year > char.death_year)


# ---------------------------------------------------------------------------
# Character Interpreter LLM (fallback de l'heuristique engine.interpreter)
# ---------------------------------------------------------------------------


@dataclass
class InterpretedIntent:
    """Resultat de l'interpretation LLM d'une action libre."""

    action_type: str
    summary: str
    parameters: dict[str, Any]
    target_id: str | None
    clarification_questions: list[str]


class CharacterInterpreter:
    """LLM-driven interpretation des actions joueur ambigues."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def interpret(self, free_text: str, *, context_summary: str = "") -> InterpretedIntent:
        from shinobi.llm.prompts import CHARACTER_INTERPRETER_SYSTEM_PROMPT
        from shinobi.llm.schema import CHARACTER_INTERPRETER_SCHEMA

        user_msg = (
            f"[CONTEXTE]\n{context_summary}\n\n"
            f"[ACTION DU JOUEUR]\n{free_text}\n\n"
            f"[INSTRUCTION]\nClassifie cette action et reponds en JSON conforme."
        )
        response = await self.client.generate(
            messages=[
                Message(role="system", content=CHARACTER_INTERPRETER_SYSTEM_PROMPT),
                Message(role="user", content=user_msg),
            ],
            schema=CHARACTER_INTERPRETER_SCHEMA,
        )
        if response.parsed_json is None:
            return InterpretedIntent(
                action_type="custom",
                summary=free_text,
                parameters={},
                target_id=None,
                clarification_questions=[],
            )
        intent = response.parsed_json.get("intention", {})
        return InterpretedIntent(
            action_type=intent.get("action_type", "custom"),
            summary=intent.get("summary", free_text),
            parameters=intent.get("parameters", {}),
            target_id=intent.get("target_id"),
            clarification_questions=response.parsed_json.get("clarification_questions", []),
        )


# ---------------------------------------------------------------------------
# World Resolver LLM (resout les divergences canoniques complexes)
# ---------------------------------------------------------------------------


@dataclass
class WorldResolution:
    """Resultat du WorldResolver pour un evenement annule."""

    substitute_event_summary: str
    consequences: list[dict[str, Any]]
    rumor_template: str | None


class WorldResolver:
    """LLM qui propose des consequences narratives quand un event canon est annule."""

    def __init__(self, client: LLMClient, canon: CanonBundle) -> None:
        self.client = client
        self.canon = canon

    async def resolve_cancelled_event(
        self,
        *,
        event_id: str,
        cancellation_reason: str,
        current_year: int,
    ) -> WorldResolution:
        from shinobi.llm.prompts import WORLD_RESOLVER_SYSTEM_PROMPT
        from shinobi.llm.schema import WORLD_RESOLVER_SCHEMA

        ev = self.canon.timeline_events.get(event_id)
        if ev is None:
            return WorldResolution(
                substitute_event_summary="Evenement inconnu, aucun substitut.",
                consequences=[],
                rumor_template=None,
            )
        user_msg = (
            f"[EVENEMENT ANNULE]\n"
            f"Id : {event_id}\n"
            f"Nom : {ev.name_fr}\n"
            f"Date prevue : an {ev.year}{', ' + ev.date if ev.date else ''}\n"
            f"Resume canon : {ev.narrative_summary_fr}\n"
            f"Raison annulation : {cancellation_reason}\n"
            f"Annee courante in-game : {current_year}\n\n"
            f"[INSTRUCTION]\nProduis un substitut narratif et liste les consequences en cascade."
        )
        response = await self.client.generate(
            messages=[
                Message(role="system", content=WORLD_RESOLVER_SYSTEM_PROMPT),
                Message(role="user", content=user_msg),
            ],
            schema=WORLD_RESOLVER_SCHEMA,
        )
        if response.parsed_json is None:
            return WorldResolution(
                substitute_event_summary="Le canon est devie, mais aucune narration n'a pu etre generee.",
                consequences=[],
                rumor_template=None,
            )
        data = response.parsed_json
        return WorldResolution(
            substitute_event_summary=data.get("substitute_event_summary", ""),
            consequences=data.get("consequences", []),
            rumor_template=data.get("rumor_template"),
        )


