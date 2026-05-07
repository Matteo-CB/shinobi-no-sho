"""Director : orchestrator central Phase G.

Spec doc 02 §7 : auteur invisible qui s'assure que le monde emergent reste
narrativement interessant et "Naruto-esque". Pas de prescription d'event ;
nudges via contexte LLM uniquement.

Pipeline d'un tick Director :
1. Compose acts depuis TensionList (deterministe)
2. Reconcilie avec acts existants (DirectorState) : retire les expires,
   ajoute les nouveaux
3. Selectionne les invariants Naruto pertinents au contexte
4. Si compaction due : appelle NarrativeCompactor (LLM ou offline fallback)
5. Construit NudgeContext + serialise via build_nudge_text
6. Mute DirectorState (active_acts, last_compaction_year)
7. Retourne DirectorReport

Garantie : un tick Director est idempotent vis-a-vis des inputs identiques
(meme TensionList + meme world state -> meme acts + meme nudge), modulo
le LLM call qui peut produire un summary different.
"""

from __future__ import annotations

from shinobi.canon.models import CanonBundle
from shinobi.director.act_composer import compose_acts, merge_with_existing
from shinobi.director.compactor import (
    DEFAULT_COMPACTION_INTERVAL_MONTHS,
    NarrativeCompactor,
)
from shinobi.director.invariants import (
    select_relevant_invariants,
    select_relevant_patterns,
)
from shinobi.director.nudge_builder import build_nudge
from shinobi.director.scheduler import DirectorState, is_compaction_due
from shinobi.director.types import (
    MONTH_MAX,
    MONTH_MIN,
    YEAR_MAX,
    YEAR_MIN,
    AbstractAct,
    DirectorReport,
    NarrativeInvariant,
)
from shinobi.engine.world import WorldState
from shinobi.llm.client import LLMClient
from shinobi.logging_setup import get_logger
from shinobi.tension.types import TensionList

logger = get_logger(__name__)


# Round G8 : table de mapping explicite TensionType -> contextes invariants.
# Avant : naive split('_') yield-ait des keywords qui matchaient rarement les
# applies_to_contexts (souvent composes : 'forbidden_jutsu', 'kekkei_genkai',
# 'clan_conflict'). 18/21 tension types tombaient au fallback centraux.
# Maintenant : map directe vers les keys exactes des applies_to_contexts.
_TENSION_TYPE_TO_CONTEXTS: dict[str, tuple[str, ...]] = {
    "power_vacuum": ("succession", "alliance"),
    "border_conflict": ("village_war", "war", "alliance"),
    "succession_dispute": ("succession", "clan_conflict"),
    "alliance_breakdown": ("alliance", "war", "clan_conflict"),
    "clan_extinction_threat": ("clan_conflict", "lineage", "trauma"),
    "bloodline_unresolved": ("lineage", "family", "hidden_truth"),
    "factional_revenge": ("vengeance", "war", "clan_conflict"),
    "obsessive_npc_idle": ("trauma", "rivalry"),
    "lone_survivor_obsessed": ("trauma", "vengeance", "redemption"),
    "student_surpasses_master": ("training", "team", "succession"),
    "cursed_hatred": ("vengeance", "war", "trauma", "redemption"),
    "jinchuuriki_unprotected": ("jinchuuriki", "war", "team"),
    "tailed_beast_uncontrolled": ("jinchuuriki", "transformation"),
    "forbidden_jutsu_threat": ("forbidden_jutsu", "training", "transformation"),
    "kekkei_carrier_isolated": ("kekkei_genkai", "lineage", "trauma"),
    "hidden_truth_pending": ("hidden_truth", "trauma", "clan_secret"),
    "chekhovs_gun_unfired": ("hidden_truth", "history"),
    "prophecy_unfulfilled": ("history", "lineage"),
    "death_anniversary": ("death", "trauma", "redemption"),
    "canon_event_pending": ("history", "war"),
    "other": (),  # pas de mapping specifique -> centraux par fallback
}


def _contexts_from_acts(acts: list[AbstractAct]) -> list[str]:
    """Derive les contextes pour la selection d'invariants depuis les acts.

    Round G8 : utilise _TENSION_TYPE_TO_CONTEXTS map directe au lieu d'un
    split naif. Avant, 18/21 tension types ne matchaient aucun invariant
    parce que applies_to_contexts utilise des cles composees ('forbidden_jutsu',
    'clan_conflict') que split('_') casse en parts non-matchantes.
    Resultat avant : tout tombait sur le fallback centraux (R G6) ; les
    invariants ciblés (hatred_breakable pour cursed_hatred par exemple)
    n'etaient jamais selectionnes par scoring.
    """
    contexts: list[str] = []
    for act in acts:
        for tt in act.related_tension_types:
            mapped = _TENSION_TYPE_TO_CONTEXTS.get(tt, ())
            contexts.extend(mapped)
    return contexts


# Phase H 9.5 wiring : keywords FR equivalents pour matching dans les
# patterns. Avant : `_TENSION_TYPE_TO_CONTEXTS` produit `succession`,
# `alliance`, `clan_conflict` (en/snake_case) qui ne matchent jamais les
# `description_fr` / `when_to_apply_fr` des patterns 9.5 (phrases FR
# libres, ex 'Reserver une verite cachee'). Ce mapping enrichit chaque
# context EN -> liste de mots-cles FR susceptibles d'apparaitre dans la
# description du pattern. Sans ca, select_relevant_patterns retombait
# systematiquement sur le fallback (3 premiers patterns canon).
_CONTEXT_TO_FR_KEYWORDS: dict[str, tuple[str, ...]] = {
    "succession": ("succession", "heritier", "successeur", "kage", "chef"),
    "alliance": ("alliance", "allie", "trahison", "pacte"),
    "clan_conflict": ("clan", "rivalite", "famille", "fratricide", "frere"),
    "war": ("guerre", "bataille", "combat", "conflit"),
    "village_war": ("village", "guerre", "siege"),
    "lineage": ("lignee", "heritage", "descendance", "ancetre", "famille"),
    "trauma": ("trauma", "deuil", "mort", "souffrance", "perte"),
    "death": ("mort", "deces", "tue", "perdu", "disparu"),
    "redemption": ("redemption", "pardon", "rachat", "retrouv"),
    "training": ("entrainement", "apprentissage", "maitre", "eleve"),
    "forbidden_jutsu": ("interdit", "kinjutsu", "tabou", "secret"),
    "kekkei_genkai": ("kekkei", "lignee", "heritage", "sang"),
    "evolution": ("evolution", "depasse", "transformation", "puissance"),
    "transformation": ("transformation", "metamorphose", "evolution"),
    "rivalry": ("rival", "rivalite", "competition", "duel"),
    "team": ("equipe", "compagnon", "ensemble"),
    "enemy": ("ennemi", "adversaire", "antagoniste"),
    "dialogue": ("dialogue", "parle", "conversation", "verbe"),
    "jinchuuriki": ("jinchuriki", "biju", "queue", "demon"),
    "history": ("histoire", "passe", "ancien", "souvenir", "flashback"),
    "hidden_truth": ("verite", "cache", "secret", "revelation"),
    "fragmentation": ("fragmente", "scission", "division", "rupture"),
    "war_aftermath": ("guerre", "apres", "consequence", "reconstruction"),
    "hatred": ("haine", "vengeance", "rancune", "fureur"),
    "vengeance": ("vengeance", "vengeur", "revanche"),
    "obsession": ("obsession", "fixation", "tourmente"),
    "isolation": ("seul", "isole", "solitaire", "abandonne"),
    "betrayal": ("trahison", "trahit", "traitre", "duperie"),
    "loss": ("perte", "perdu", "deuil"),
    "borders": ("frontiere", "limite", "siege"),
    "diplomacy": ("diplomatie", "negociation", "pacte", "traite"),
    "geographic_imbalance": ("desequilibre", "geographique", "frontiere"),
}


def _enrich_contexts_with_fr(contexts: list[str]) -> list[str]:
    """Phase H 9.5 wiring : enrichit les contexts EN/snake_case avec leurs
    equivalents FR pour permettre le matching dans patterns FR.

    Garde les contexts originaux (compat invariants en/snake_case) et
    ajoute les keywords FR. Dedup via dict.fromkeys pour preserver l'ordre.
    """
    enriched: list[str] = list(contexts)
    for c in contexts:
        for kw in _CONTEXT_TO_FR_KEYWORDS.get(c, ()):
            enriched.append(kw)
    return list(dict.fromkeys(enriched))


class Director:
    """Drama Manager Phase G.

    Combine act composer + invariants + compactor en un orchestrateur unique.
    Stateless : tout l'etat est dans DirectorState (passe en argument).

    Usage typique (boucle CLI) :

        director = Director(canon, llm_client=client)
        report = await director.tick(
            tensions=tension_list,
            world=world,
            state=director_state,
            current_year=world.current_year,
            current_month=...,
        )
        # state est mute en place ; report contient nudge_text pour
        # le prompt narrator.
    """

    def __init__(
        self,
        canon: CanonBundle,
        *,
        llm_client: LLMClient | None = None,
        compaction_interval_months: int = DEFAULT_COMPACTION_INTERVAL_MONTHS,
        max_active_acts: int = 10,
        composer_top_n: int = 5,
        composer_min_score: float = 0.5,
    ) -> None:
        # Round G17 : valide les params pour eviter mode degrade silencieux.
        # compaction_interval_months <= 0 -> is_compaction_due toujours True
        # -> 1 LLM call par tick au lieu d'1 tous les 6 mois (-> coût x10-20).
        # max_active_acts <= 0 -> aucun act jamais conserve.
        # composer_top_n <= 0 -> aucun act jamais propose.
        # composer_min_score hors [0, 1] -> filtrage degenere.
        if compaction_interval_months < 1:
            raise ValueError(
                f"compaction_interval_months doit etre >= 1, got "
                f"{compaction_interval_months}",
            )
        if max_active_acts < 1:
            raise ValueError(
                f"max_active_acts doit etre >= 1, got {max_active_acts}",
            )
        if composer_top_n < 1:
            raise ValueError(
                f"composer_top_n doit etre >= 1, got {composer_top_n}",
            )
        if not (0.0 <= composer_min_score <= 1.0):
            raise ValueError(
                f"composer_min_score doit etre dans [0, 1], got "
                f"{composer_min_score}",
            )
        self.canon = canon
        self.llm_client = llm_client
        self.compactor = NarrativeCompactor(client=llm_client, canon=canon)
        self.compaction_interval_months = compaction_interval_months
        self.max_active_acts = max_active_acts
        self.composer_top_n = composer_top_n
        self.composer_min_score = composer_min_score

    # Round G21 : bornes des fields year (mirror R G18 Pydantic constraints).
    # Sert au clamp defensif a l'entree de tick().
    # Round G29 : import depuis types.py (single source of truth).
    _YEAR_MIN: int = YEAR_MIN
    _YEAR_MAX: int = YEAR_MAX

    async def tick(
        self,
        *,
        tensions: TensionList,
        world: WorldState,
        state: DirectorState,
        current_year: int,
        current_month: int = 1,
    ) -> DirectorReport:
        """Execute un tick complet du Director. Mute `state` en place.

        Round G21 : clamp current_year aux bornes Pydantic [-10000, 10000].
        Avant : current_year=20000 (corrupted save / extreme alternate
        timeline) faisait que compose_acts skip via R G19 mais build_nudge
        crashait avec ValidationError -> Director.tick raise -> CLI catch
        outer mais perd le report (pas de state persist, pas de log).
        Maintenant : log warning + clamp -> tick complete normalement.
        """
        if not (self._YEAR_MIN <= current_year <= self._YEAR_MAX):
            logger.warning(
                "phase_g_director_year_clamped",
                original=current_year,
                clamped_min=self._YEAR_MIN,
                clamped_max=self._YEAR_MAX,
            )
            current_year = max(self._YEAR_MIN, min(current_year, self._YEAR_MAX))
        # Round G25 : clamp current_month aussi. CLI parse world.current_date
        # comme int(date.split('-')[0]) ; un date='00-01' produit month=0,
        # '13-01' produit month=13. Sans clamp, _months_elapsed donne des
        # valeurs off-by-one ou month fantome -> is_compaction_due imprevisible.
        if not (MONTH_MIN <= current_month <= MONTH_MAX):
            logger.warning(
                "phase_g_director_month_clamped",
                original=current_month,
            )
            current_month = max(MONTH_MIN, min(current_month, MONTH_MAX))
        # Round G24 : clamp aussi state.last_compaction_year/month en memoire.
        # R G22 clamp seulement a la (de)serialization. Si state est construit
        # directement avec last_compaction_year=99999 (caller bug, mute apres
        # load), is_compaction_due lit la valeur absurde -> elapsed negatif
        # -> compactor jamais run. Defense au point de lecture (tick()).
        if state.last_compaction_year is not None and not (
            self._YEAR_MIN <= state.last_compaction_year <= self._YEAR_MAX
        ):
            logger.warning(
                "phase_g_state_year_clamped",
                original=state.last_compaction_year,
            )
            state.last_compaction_year = max(
                self._YEAR_MIN, min(state.last_compaction_year, self._YEAR_MAX),
            )
        if state.last_compaction_month is not None and not (
            MONTH_MIN <= state.last_compaction_month <= MONTH_MAX
        ):
            logger.warning(
                "phase_g_state_month_clamped",
                original=state.last_compaction_month,
            )
            state.last_compaction_month = max(
                MONTH_MIN, min(state.last_compaction_month, MONTH_MAX),
            )
        # Round G26 : si state.last_compaction_year > current_year apres clamp,
        # incoherence temporelle ("compacte dans le futur"). R G24 transforme
        # une corruption type 99999 en clamp a 10000, mais current_year=10 ->
        # is_compaction_due calcule elapsed=(10-10000)*12 negatif -> compactor
        # jamais run. Reset state a None pour forcer un fresh start.
        # Round G28 : extension a (year, month) tuple comparison. R G26 manquait
        # le cas year egal mais month retrograde : last=(10, 12), current=(10, 1)
        # via save/load -> elapsed=-11 -> never compaction jusqu'a annee 11.
        if state.last_compaction_year is not None:
            last_y = state.last_compaction_year
            last_m = state.last_compaction_month or 1
            if (last_y, last_m) > (current_year, current_month):
                logger.warning(
                    "phase_g_state_reset_temporal_incoherence",
                    last_year=last_y, last_month=last_m,
                    current_year=current_year, current_month=current_month,
                )
                state.last_compaction_year = None
                state.last_compaction_month = None
        state.tick_count += 1

        # 1. Compose nouveaux acts.
        # Phase H wiring : pass divergence_event_ids depuis CanonBundle
        # pour que les tensions liees aux moments charnieres recoivent un
        # urgency boost x1.3 (capped a 1.0).
        divergence_ids: set[str] = set()
        for dp in (
            self.canon.divergence_points.get("divergence_points", [])
            if self.canon.divergence_points else []
        ):
            eid = dp.get("event_id")
            if isinstance(eid, str) and eid:
                divergence_ids.add(eid)

        new_acts = compose_acts(
            tensions,
            current_year=current_year,
            top_n=self.composer_top_n,
            min_score=self.composer_min_score,
            divergence_event_ids=divergence_ids,
        )
        state.composer_runs += 1

        # 2. Reconcilie avec existing
        added, retired, merged = merge_with_existing(
            new_acts, state.active_acts, current_year=current_year,
        )
        # Cap dur sur active_acts pour eviter accumulation sur longue partie.
        # Round G7 : les acts evinces (low urgency) etaient silencieusement
        # supprimes. Maintenant, on les ajoute a retired avec status='expired'
        # pour traceability dans DirectorReport.retired_acts. Sans ca,
        # impossible de logger ni debugger pourquoi un act narratif disparait.
        if len(merged) > self.max_active_acts:
            sorted_items = sorted(
                merged.items(), key=lambda kv: -kv[1].urgency,
            )
            kept = dict(sorted_items[:self.max_active_acts])
            for _, evicted in sorted_items[self.max_active_acts:]:
                retired.append(evicted.model_copy(update={"status": "expired"}))
            merged = kept
        state.active_acts = merged

        active_list = list(merged.values())

        # 3. Invariants pertinents au contexte courant
        contexts = _contexts_from_acts(active_list)
        invariants = select_relevant_invariants(contexts, max_invariants=5)

        # 4. Compaction si due
        compaction_ran = False
        compaction_summary: str | None = None
        if is_compaction_due(
            state,
            current_year=current_year, current_month=current_month,
            interval_months=self.compaction_interval_months,
        ):
            # Round G4 : 1er run, derive period_start depuis le min year
            # des events completed/cancelled. Avant : current_year - 1
            # ratait toute l'histoire pre-1ere-compaction (ex player a 5 ans
            # de game avant 1er Director tick). Si aucun event, fallback
            # a current_year - 1 (premier tick d'une partie neuve).
            if state.last_compaction_year is not None:
                period_start = state.last_compaction_year
            else:
                event_years: list[int] = [
                    ev.triggered_at_year for ev in world.completed_events
                ] + [
                    ev.cancelled_at_year for ev in world.cancelled_events
                ]
                period_start = min(event_years) if event_years else (current_year - 1)
            try:
                compaction_summary = await self.compactor.compact(
                    world,
                    period_start_year=period_start,
                    period_end_year=current_year,
                )
                state.last_summary = compaction_summary
                state.last_compaction_year = current_year
                state.last_compaction_month = current_month
                state.compactor_runs += 1
                compaction_ran = True
            except Exception as exc:  # noqa: BLE001
                # Defensive : compactor crash ne doit pas casser le tick
                logger.warning(
                    "director_compactor_crashed",
                    error=type(exc).__name__,
                    msg=str(exc)[:200],
                )

        # 5. Build nudge avec recent_summary depuis le state
        # Phase H 9.5 : selection des patterns par pertinence au contexte
        # courant (vs ordre canon-arbitrary). Sur 14 patterns disponibles,
        # seuls les 3 plus pertinents aux tensions du tick sont transmis.
        all_patterns = (
            self.canon.narrative_patterns.get("patterns", [])
            if self.canon.narrative_patterns else []
        )
        # Phase H 9.5 : enrichit les contexts avec leurs equivalents FR
        # pour permettre le matching dans patterns FR (sinon fallback
        # constant sur les 3 premiers patterns canon).
        fr_enriched_contexts = _enrich_contexts_with_fr(contexts)
        relevant_patterns = select_relevant_patterns(
            all_patterns, contexts=fr_enriched_contexts, max_patterns=3,
        )
        nudge = build_nudge(
            active_acts=active_list,
            active_invariants=invariants,
            recent_summary=state.last_summary,
            current_year=current_year,
            narrative_patterns=relevant_patterns,
        )

        logger.info(
            "phase_g_director_tick",
            current_year=current_year,
            new_acts=len(added),
            retired_acts=len(retired),
            active_acts=len(active_list),
            invariants=len(invariants),
            compaction_ran=compaction_ran,
        )

        return DirectorReport(
            new_acts=added,
            retired_acts=retired,
            active_acts=active_list,
            nudge=nudge,
            compaction_ran=compaction_ran,
            compaction_summary=compaction_summary,
            tick_year=current_year,
            tick_month=current_month,
        )


__all__ = ["Director"]
