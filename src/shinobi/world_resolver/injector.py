"""SubstituteEventInjector : injecte un SubstituteEvent dans le scheduler + KG.

Spec doc 02 §8.2 :
- 4. Si valide, injection dans scheduler
- (cascade : KG facts mis a jour, world.scheduled_events etendu)

Le scheduler doit pouvoir traiter le substitute comme un event canon
ordinaire (preconditions, outcomes, trigger). Pour cela, on cree :
- 1 ScheduledEvent (event_id = substitute.id) dans world.scheduled_events
- N facts (substitute.id, ...) dans le KG avec source='substitute:<canon_id>'
- 1 Rumor optionnelle si substitute.rumor_template fourni
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from shinobi.engine.world import Rumor, ScheduledEvent, WorldState
from shinobi.kg.schema import Canonicity, Fact, ObjectType
from shinobi.kg.store import KnowledgeGraphStore
from shinobi.logging_setup import get_logger
from shinobi.types import EventStatus
from shinobi.world_resolver.types import SubstituteEvent

logger = get_logger(__name__)


@dataclass(frozen=True)
class InjectionResult:
    """Resultat de l'injection d'un substitute.

    Round 31 : `skipped_collision` distingue le cas idempotent-skip du cas
    injection reussie. Avant, un skip retournait facts_inserted=0/rumor_added
    =False sans drapeau explicite -> le pipeline lisait ca comme un succes
    et marquait status='injected' a tort.
    """

    world: WorldState
    facts_inserted: int
    rumor_added: bool
    skipped_collision: bool = False


class SubstituteEventInjector:
    """Inject un SubstituteEvent dans le scheduler runtime + KG.

    Boucle fermee Phase F : event annule -> substitute genere -> valide ->
    INJECTE -> scheduler trigger normal -> outcomes appliques.
    """

    def __init__(
        self,
        kg: KnowledgeGraphStore,
        *,
        canon=None,  # type: shinobi.canon.models.CanonBundle | None
    ) -> None:
        self.kg = kg
        # Phase H wiring 9.1 : pre-build l'index des narrative_invariants par
        # event_id pour O(1) lookup au moment de l'injection. Le caller passe
        # canon=canon depuis le pipeline. Defensive : si canon None, l'index
        # reste vide et le code continue normalement (pas de facts inherits).
        self._enriched_invariants: dict[str, list[str]] = {}
        if canon is not None:
            for eid, payload in (
                getattr(canon, "timeline_events_enriched", None) or {}
            ).items():
                if not isinstance(payload, dict):
                    continue
                invs = payload.get("narrative_invariants")
                if isinstance(invs, list):
                    self._enriched_invariants[eid] = [
                        str(x) for x in invs if isinstance(x, str) and x
                    ]

    def inject(
        self,
        substitute: SubstituteEvent,
        *,
        world: WorldState,
        current_turn: int = 0,
    ) -> InjectionResult:
        """Injecte le substitute. Retourne le nouveau WorldState.

        Spec §8.2 : KG mis a jour, scheduler etendu, rumor si applicable.

        Round 10 : idempotent. Si un substitute avec le meme id est deja
        injecte (scheduled_events ou substitute_events), skip propre.
        """
        # Idempotence check : si deja present, retourne world inchange
        already_in_scheduled = any(
            e.event_id == substitute.id for e in world.scheduled_events
        )
        already_in_registry = substitute.id in world.substitute_events
        if already_in_scheduled or already_in_registry:
            logger.info(
                "phase_f_substitute_already_injected",
                substitute_id=substitute.id,
                in_scheduled=already_in_scheduled,
                in_registry=already_in_registry,
            )
            return InjectionResult(
                world=world,
                facts_inserted=0,
                rumor_added=False,
                skipped_collision=True,
            )

        # 1. ScheduledEvent dans world (si pas deja completed/cancelled)
        new_scheduled = ScheduledEvent(
            event_id=substitute.id,
            year=substitute.year,
            date=substitute.date,
            status=EventStatus.scheduled,
            notes=(
                f"substitute_for:{substitute.cancelled_canon_event_id} "
                f"strategy:{substitute.cancellation_strategy_type}"
            ),
        )
        scheduled_list = list(world.scheduled_events) + [new_scheduled]

        # 2. Rumor optionnelle
        # Round 14 : meme heuristique radius que les canon events
        # (cf engine.events.tick_scheduler) pour parite.
        # Round 26 : fidelity radius-dependante + expires_at_year, parite
        # complete avec engine.rumors.make_rumor_from_event. Avant : fidelity
        # 0.7 hardcode (toutes diffusions egales) et pas d'expiration ->
        # rumeurs substitut polluaient world.rumors indefiniment.
        rumor_added = False
        rumors_list = list(world.rumors)
        if substitute.rumor_template:
            from shinobi.engine.rumors import _RADIUS_FIDELITY
            radius = self._infer_rumor_radius(substitute.name_fr)
            # Penalite divergence : 15% de moins que la fidelite canon
            # equivalente (un event divergent est par nature moins atteste).
            base_fidelity = _RADIUS_FIDELITY.get(radius, 0.7)
            divergent_fidelity = round(base_fidelity * 0.85, 3)
            rumor = Rumor(
                id=f"rumor_{substitute.id}",
                source_event_id=substitute.id,
                content=substitute.rumor_template,
                fidelity=divergent_fidelity,
                diffusion_radius=radius,
                born_at_year=substitute.year,
                expires_at_year=substitute.year + 5,
            )
            rumors_list = rumors_list + [rumor]
            rumor_added = True

        # Phase F round 5 : enregistre le substitute dans world.substitute_events
        # pour que le scheduler engine.events.process_scheduled puisse le trigger.
        # Sans cette etape, l'event injecte resterait scheduled indefiniment
        # car canon.timeline_events.get(substitute.id) retourne None.
        new_substitutes = dict(world.substitute_events)
        new_substitutes[substitute.id] = substitute.model_dump(mode="json")

        new_world = world.model_copy(update={
            "scheduled_events": scheduled_list,
            "rumors": rumors_list,
            "substitute_events": new_substitutes,
        })

        # 3. KG facts injection
        facts_inserted = self._inject_kg_facts(substitute)

        logger.info(
            "phase_f_substitute_injected",
            substitute_id=substitute.id,
            cancelled_canon=substitute.cancelled_canon_event_id,
            year=substitute.year,
            facts=facts_inserted,
            rumor=rumor_added,
        )

        return InjectionResult(
            world=new_world,
            facts_inserted=facts_inserted,
            rumor_added=rumor_added,
        )

    @staticmethod
    def _infer_rumor_radius(name_fr: str) -> str:
        """Infere le radius diffusion d'une rumeur depuis le nom de l'event.

        Round 14 : meme heuristique que engine.events.tick_scheduler pour
        parite canon/substitute. Events impliquant 'guerre / kage / kyuubi /
        akatsuki / uchiha / konoha' = international ; sinon regional.
        """
        keywords = ("guerre", "kage", "kyuubi", "akatsuki", "uchiha", "konoha")
        text = (name_fr or "").lower()
        return "international" if any(k in text for k in keywords) else "regional"


    def _inject_kg_facts(self, substitute: SubstituteEvent) -> int:
        """Insere les facts KG pour le substitute event.

        Source = 'substitute:<canon_id>' (traceabilite). Canonicity =
        'divergent' (jamais canon_strict, c'est par definition une divergence).
        """
        source = f"substitute:{substitute.cancelled_canon_event_id}"
        facts: list[Fact] = []

        # Type fact
        facts.append(Fact(
            subject=substitute.id, relation="type", object="timeline_event",
            object_type=ObjectType.value,
            source=source, canonicity=Canonicity.divergent,
        ))
        # Lien explicite vers le canon annule
        facts.append(Fact(
            subject=substitute.id, relation="substitutes",
            object=substitute.cancelled_canon_event_id,
            object_type=ObjectType.entity,
            source=source, canonicity=Canonicity.divergent,
        ))
        # Year + name + summary
        f_year = Fact(
            subject=substitute.id, relation="occurs_in_year",
            object=str(substitute.year),
            object_type=ObjectType.value,
            source=source, canonicity=Canonicity.divergent,
        )
        f_year.valid_from_year = substitute.year
        facts.append(f_year)
        facts.append(Fact(
            subject=substitute.id, relation="name_fr",
            object=substitute.name_fr,
            object_type=ObjectType.value,
            source=source, canonicity=Canonicity.divergent,
        ))
        facts.append(Fact(
            subject=substitute.id, relation="narrative_summary_fr",
            object=substitute.narrative_summary_fr,
            object_type=ObjectType.value,
            source=source, canonicity=Canonicity.divergent,
        ))
        if substitute.location:
            f_loc = Fact(
                subject=substitute.id, relation="occurs_at",
                object=substitute.location,
                object_type=ObjectType.entity,
                source=source, canonicity=Canonicity.divergent,
            )
            f_loc.valid_from_year = substitute.year
            facts.append(f_loc)
        # Involves
        for cid in substitute.involved_characters:
            f_inv = Fact(
                subject=substitute.id, relation="involves", object=cid,
                object_type=ObjectType.entity,
                source=source, canonicity=Canonicity.divergent,
            )
            f_inv.valid_from_year = substitute.year
            facts.append(f_inv)
        # Phase H wiring 9.1 : herite des narrative_invariants du canon event
        # annule. Permet aux narrations futures (Narrator + agents) de citer
        # les themes canoniques qu'un substitute doit honorer pour preserver
        # la coherence d'arc. Cap a 3 invariants pour eviter le bloat KG.
        canonical_invariants = self._enriched_invariants.get(
            substitute.cancelled_canon_event_id, [],
        )
        for invariant_text in canonical_invariants[:3]:
            f_inh = Fact(
                subject=substitute.id, relation="inherits_invariant",
                object=invariant_text,
                object_type=ObjectType.value,
                source=source, canonicity=Canonicity.divergent,
            )
            f_inh.valid_from_year = substitute.year
            facts.append(f_inh)
        # Outcomes (cf format event canon round 4)
        # Round 43 : pour les non-entity outcomes, on serialise via json.dumps
        # avec sort_keys=True pour parite avec canon loader (kg/loader.py).
        # Avant, str(dict) produisait "{'a': 1}" (Python repr, single quotes,
        # non-deterministe) - desync avec canon "{"a": 1}". Une query par
        # object_value ne pouvait pas matcher les deux.
        for outcome in substitute.outcomes:
            primary = (
                outcome.parameters.get("character_id")
                or outcome.parameters.get("village_id")
                or outcome.parameters.get("organization_id")
                or outcome.parameters.get("location_id")
            )
            is_entity = primary is not None and isinstance(primary, str)
            if is_entity:
                obj = primary
            elif outcome.parameters:
                obj = json.dumps(
                    outcome.parameters, ensure_ascii=False, sort_keys=True,
                )
            else:
                obj = outcome.type
            f_out = Fact(
                subject=substitute.id, relation=f"outcome:{outcome.type}",
                object=str(obj),
                object_type=ObjectType.entity if is_entity else ObjectType.value,
                source=source, canonicity=Canonicity.divergent,
            )
            f_out.valid_from_year = substitute.year
            facts.append(f_out)
        # Preconditions
        for pre in substitute.preconditions:
            primary = (
                pre.parameters.get("character_id")
                or pre.parameters.get("village_id")
            )
            is_entity = primary is not None and isinstance(primary, str)
            if is_entity:
                obj = primary
            elif pre.parameters:
                obj = json.dumps(
                    pre.parameters, ensure_ascii=False, sort_keys=True,
                )
            else:
                obj = pre.type
            facts.append(Fact(
                subject=substitute.id, relation=f"requires:{pre.type}",
                object=str(obj),
                object_type=ObjectType.entity if is_entity else ObjectType.value,
                source=source, canonicity=Canonicity.divergent,
            ))
        # Cancellation strategy
        facts.append(Fact(
            subject=substitute.id, relation="cancellation_strategy",
            object=substitute.cancellation_strategy_type,
            object_type=ObjectType.value,
            source=source, canonicity=Canonicity.divergent,
        ))

        self.kg.add_facts_batch(facts)
        return len(facts)


__all__ = ["InjectionResult", "SubstituteEventInjector"]
