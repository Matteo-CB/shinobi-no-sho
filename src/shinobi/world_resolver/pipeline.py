"""Pipeline Phase F : orchestrateur close_substitute_loop.

Spec doc 02 §8.2 :
> 1. LLM analyse le cancelled + etat actuel du KG
> 2. LLM genere un nouvel TimelineEvent qui prend la place
> 3. Validation par triplet_check + sherlock + canon_invariants
> 4. Si valide, injection dans scheduler
> 5. Si invalide, regen avec feedback (max 2 fois)
> 6. Si toujours invalide, fallback en silent_cancel

Boucle fermee : event annule -> event substitut genere -> KG mis a jour
-> nouveau monde -> tick continue.
"""

from __future__ import annotations

from shinobi.canon.models import CanonBundle
from shinobi.engine.world import WorldState
from shinobi.kg.store import KnowledgeGraphStore
from shinobi.llm.client import LLMClient
from shinobi.logging_setup import get_logger
from shinobi.world_resolver.generator import (
    GenerationFailure,
    SubstituteEventGenerator,
)
from shinobi.world_resolver.injector import (
    InjectionResult,
    SubstituteEventInjector,
)
from shinobi.world_resolver.types import (
    SubstituteEvent,
    SubstituteResolution,
    ValidationMode,
    ValidationOutcome,
    ValidationReport,
)
from shinobi.world_resolver.validator import HybridSubstituteValidator

logger = get_logger(__name__)

DEFAULT_MAX_REGEN_ATTEMPTS = 2


class WorldResolverPipeline:
    """Pipeline Phase F complete.

    Spec §8 + §13 (quick win 3) : close_substitute_loop oriente la
    creativite emergente sur les events canon annules. Mode hybride
    canon_strict (defaut) ou alternate_timeline selon contexte.
    """

    def __init__(
        self,
        client: LLMClient,
        canon: CanonBundle,
        kg: KnowledgeGraphStore,
        *,
        max_regen_attempts: int = DEFAULT_MAX_REGEN_ATTEMPTS,
    ) -> None:
        self.generator = SubstituteEventGenerator(client, canon)
        # Phase H wiring 9.1 : enforce actor overlap en pipeline production.
        # Les tests unitaires construisent le validator directement et
        # gardent le default False pour back-compat (cf validator.__init__).
        self.validator = HybridSubstituteValidator(
            canon, kg, enforce_phase_h_actor_overlap=True,
        )
        # Phase H wiring 9.1 : injector recoit canon pour heriter les
        # narrative_invariants du canon event annule au moment de l'injection.
        self.injector = SubstituteEventInjector(kg, canon=canon)
        self.max_regen_attempts = max_regen_attempts

    async def close_loop(
        self,
        *,
        cancelled_event_id: str,
        cancellation_reason: str,
        world: WorldState,
        validation_mode: ValidationMode = ValidationMode.canon_strict,
        world_state_summary: str = "",
        kg_recent_facts: str = "",
    ) -> tuple[SubstituteResolution, WorldState]:
        """Execute la boucle complete.

        Returns:
            (resolution, new_world) : resolution decrit ce qui s'est passe.
            new_world peut etre = world inchange (silent_cancel) ou modifie
            (substitute injecte).
        """
        attempts: list[ValidationReport] = []
        feedback: str | None = None

        # Round 32 : fail-fast si cancelled_event_id absent du canon. Sans
        # ce check, le generator echouait deterministiquement a chaque
        # regen (l'id est en argument fixe, pas LLM) -> 3 LLM calls
        # gachees pour une erreur de programmation cote caller.
        if cancelled_event_id not in self.generator.canon.timeline_events:
            attempts.append(ValidationReport(
                outcome=ValidationOutcome.invalid_schema,
                mode=validation_mode,
                is_valid=False,
                reason=(
                    f"cancelled_event_id '{cancelled_event_id}' absent du canon."
                    " Aucune regen LLM ne peut corriger ca (caller bug)."
                ),
            ))
            logger.warning(
                "phase_f_close_loop_unknown_canon_event",
                cancelled=cancelled_event_id,
            )
            return (
                SubstituteResolution(
                    cancelled_canon_event_id=cancelled_event_id,
                    status="silent_cancel",
                    substitute=None,
                    validation_attempts=attempts,
                ),
                world,
            )

        for attempt_n in range(self.max_regen_attempts + 1):
            gen = await self.generator.generate(
                cancelled_event_id=cancelled_event_id,
                cancellation_reason=cancellation_reason,
                current_year=world.current_year,
                world_state_summary=world_state_summary,
                kg_recent_facts=kg_recent_facts,
                feedback=feedback,
            )
            if isinstance(gen, GenerationFailure):
                # LLM offline ou schema casse : fallback silent_cancel
                attempts.append(ValidationReport(
                    outcome=ValidationOutcome.invalid_schema,
                    mode=validation_mode,
                    is_valid=False,
                    reason=f"generation_failed: {gen.reason}",
                ))
                # Round 12 : feedback inclut un extrait de la raw_response
                # pour aider le LLM a corriger son output.
                feedback = (
                    "Tentative precedente : reponse LLM invalide. "
                    f"Detail : {gen.reason}. Reponds STRICTEMENT en JSON "
                    "conforme au schema."
                )
                if gen.raw_response:
                    feedback += (
                        f"\nExtrait de ta reponse precedente (a corriger) : "
                        f"{gen.raw_response[:200]}"
                    )
                continue

            assert isinstance(gen, SubstituteEvent)
            # Spec §8.3 : valide aussi contre le WorldState runtime
            report = self.validator.validate(gen, mode=validation_mode, world=world)
            attempts.append(report)
            if report.is_valid:
                # SUCCES : injecte
                injection = self.injector.inject(gen, world=world)
                # Round 31 : si l'injector skip (id_suffix collision avec un
                # substitute deja injecte), traite ca comme un echec et regen
                # avec feedback. Avant, status='injected' etait retourne avec
                # facts_inserted=0 -> faux positif silencieux.
                if injection.skipped_collision:
                    collision_report = ValidationReport(
                        outcome=ValidationOutcome.invalid_schema,
                        mode=validation_mode,
                        is_valid=False,
                        reason=f"id_suffix '{gen.id}' deja utilise (collision)",
                        failing_facts=[f"substitute.id={gen.id} deja present"],
                    )
                    attempts.append(collision_report)
                    feedback = (
                        f"Tentative precedente : l'id_suffix produit un substitute.id"
                        f" '{gen.id}' qui existe deja dans le monde. Choisis un"
                        f" id_suffix DIFFERENT (plus specifique a cet event canon)."
                    )
                    continue
                logger.info(
                    "phase_f_close_loop_success",
                    cancelled=cancelled_event_id,
                    substitute_id=gen.id,
                    attempt=attempt_n + 1,
                    facts=injection.facts_inserted,
                )
                return (
                    SubstituteResolution(
                        cancelled_canon_event_id=cancelled_event_id,
                        status="injected",
                        substitute=gen,
                        validation_attempts=attempts,
                        rumor_template=gen.rumor_template,
                    ),
                    injection.world,
                )

            # INVALIDE : regen avec feedback
            feedback = self._build_feedback(report)

        # Toutes tentatives epuisees -> silent_cancel
        logger.info(
            "phase_f_close_loop_exhausted",
            cancelled=cancelled_event_id,
            attempts=len(attempts),
        )
        return (
            SubstituteResolution(
                cancelled_canon_event_id=cancelled_event_id,
                status="regen_exhausted",
                substitute=None,
                validation_attempts=attempts,
            ),
            world,  # inchange
        )

    # Round 25 : hints cibles par outcome au lieu d'un hint generique.
    # Avant, on conseillait toujours "evite les morts impossibles" meme
    # quand l'erreur etait un year hors plage ou un schema casse.
    _OUTCOME_HINTS: dict[ValidationOutcome, str] = {
        ValidationOutcome.invalid_triplet: (
            " Corrige : utilise UNIQUEMENT des personnages, lieux et techniques"
            " presents dans le canon. Verifie aussi que le triplet"
            " (character, technique) figure dans canonical_users."
        ),
        ValidationOutcome.invalid_plausibility: (
            " Corrige : assure que tous les personnages mentionnes existent"
            " dans le canon ou ont ete introduits via fact divergent KG."
            " Pas d'invention de personnages."
        ),
        ValidationOutcome.invalid_dead_character: (
            " Corrige : un des personnages est mort avant cet event. Choisis"
            " un personnage encore vivant a cette annee, ou recuperes un"
            " perso vivant via les outcomes."
        ),
        ValidationOutcome.invalid_temporal: (
            " Corrige : 'year' doit etre un entier dans [-1000, 200]."
            " Reutilise l'annee canon prevue ou une annee proche."
        ),
        ValidationOutcome.invalid_schema: (
            " Corrige : reponds STRICTEMENT en JSON conforme au schema."
            " Verifie chaque champ obligatoire (id_suffix, name_fr, year,"
            " outcomes, narrative_summary_fr)."
        ),
        ValidationOutcome.invalid_style: (
            " Corrige : retire tirets cadratins (— ou –) et emoji des"
            " champs narratifs (name_fr, narrative_summary_fr,"
            " rumor_template). Francais standard avec accents OK."
        ),
    }

    @classmethod
    def _build_feedback(cls, report: ValidationReport) -> str:
        """Construit un feedback structure pour la regen LLM."""
        base = (
            f"Tentative precedente rejetee. Mode: {report.mode.value}. "
            f"Outcome: {report.outcome.value}."
        )
        if report.reason:
            base += f" Raison: {report.reason}."
        if report.failing_facts:
            # Round 28 : limite remontee a 10 (etait 3) pour exposer tous les
            # echecs batches par round 27. Avec 3 morts sur 5 visibles, le LLM
            # fixait 3, regen revenait avec 2 morts -> regen brulee. 10 couvre
            # les cas realistes (typiquement 1-5 facts) sans exploser le prompt.
            shown = report.failing_facts[:10]
            base += f" Faits qui posent probleme: {shown}"
            if len(report.failing_facts) > 10:
                base += f" (+{len(report.failing_facts) - 10} autre(s) tronque(s))"
        base += cls._OUTCOME_HINTS.get(
            report.outcome,
            " Corrige : reste fidele au canon Naruto et au schema.",
        )
        # Phase H wiring 9.1 : si le rejet est un actor_overlap=0, ajoute un
        # hint specifique listant les protagonistes canoniques attendus. Sans
        # ce hint, le LLM voyait juste 'overlap=0' dans failing_facts et ne
        # comprenait pas qu'il devait inclure au moins 1 acteur canon.
        for f in report.failing_facts:
            if isinstance(f, str) and f.startswith("canon_subjects="):
                base += (
                    " Corrige aussi : ton substitute remplace un evenement"
                    " canon implicant precisement ces protagonistes - tu dois"
                    f" inclure au moins un d'eux dans involved_characters ({f})."
                )
                break
        return base


def silent_cancel_resolution(
    cancelled_event_id: str,
    *,
    reason: str = "no_substitute_attempted",
) -> SubstituteResolution:
    """Helper : resolution silent_cancel quand pas de LLM disponible.

    Round 20 : `reason` est trace dans validation_attempts via une
    ValidationReport synthetique (mode canon_strict, outcome invalid_schema)
    pour garder la traceabilite de pourquoi on a saute la generation.
    Avant, reason etait silencieusement ignore.
    """
    trace = ValidationReport(
        outcome=ValidationOutcome.invalid_schema,
        mode=ValidationMode.canon_strict,
        is_valid=False,
        reason=f"silent_cancel: {reason}",
    )
    return SubstituteResolution(
        cancelled_canon_event_id=cancelled_event_id,
        status="silent_cancel",
        substitute=None,
        validation_attempts=[trace],
    )


__all__ = [
    "DEFAULT_MAX_REGEN_ATTEMPTS",
    "WorldResolverPipeline",
    "silent_cancel_resolution",
]
