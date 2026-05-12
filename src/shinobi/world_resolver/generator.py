"""SubstituteEventGenerator : invoque le LLM pour produire un SubstituteEvent.

Phase F doc 02 §8.2 : extension du WorldResolver actuel pour generer un
EVENT STRUCTURE (Pydantic-validated) au lieu d'un simple texte.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from shinobi.canon.models import CanonBundle
from shinobi.i18n.prompts_loader import load_prompt
from shinobi.llm.client import LLMClient, Message
from shinobi.logging_setup import get_logger
from shinobi.world_resolver.prompts import (
    build_substitute_user_message,
)
from shinobi.world_resolver.schema import SUBSTITUTE_EVENT_SCHEMA
from shinobi.world_resolver.types import (
    ALLOWED_CANCELLATION_STRATEGIES,
    SubstituteEvent,
    SubstituteOutcome,
    SubstitutePrecondition,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class GenerationFailure:
    """Raison structuree d'un echec de generation (LLM offline, schema, ...)."""

    reason: str
    raw_response: str | None = None


class SubstituteEventGenerator:
    """Generateur LLM de SubstituteEvent structures.

    Spec §8.2 : prompt + schema JSON force le LLM a produire la structure
    requise. Pydantic round-trip valide la coherence (id format, year int,
    outcomes >=1, narrative non-vide). Echec -> GenerationFailure.
    """

    def __init__(self, client: LLMClient, canon: CanonBundle) -> None:
        self.client = client
        self.canon = canon
        # Phase H wiring 9.4 : pre-build l'index des divergence_points par
        # event_id pour O(1) lookup. Defensive : tolere absence ou format
        # inattendu.
        self._divergence_index: dict[str, dict] = {}
        for dp in (canon.divergence_points or {}).get(
            "divergence_points", [],
        ):
            if not isinstance(dp, dict):
                continue
            eid = dp.get("event_id")
            if isinstance(eid, str) and eid:
                self._divergence_index[eid] = dp

    async def generate(
        self,
        *,
        cancelled_event_id: str,
        cancellation_reason: str,
        current_year: int,
        world_state_summary: str = "",
        kg_recent_facts: str = "",
        feedback: str | None = None,
    ) -> SubstituteEvent | GenerationFailure:
        """Genere un SubstituteEvent. Si feedback fourni, c'est une regen.

        Spec §8.2 : si la 1ere generation echoue (validation invalide),
        on rappelle le LLM avec un feedback structure pour qu'il corrige.
        Max 2 regens orchestres par la pipeline (round 0 + 2 retries).
        """
        ev = self.canon.timeline_events.get(cancelled_event_id)
        if ev is None:
            return GenerationFailure(
                reason=f"cancelled_event {cancelled_event_id} introuvable dans canon"
            )

        # Phase H wiring 9.1 : recupere narrative_invariants + alternative_seeds
        # depuis canon.timeline_events_enriched pour guider la generation LLM.
        # Defensive : tolere absence de l'enrichissement (back-compat canon
        # partiellement enrichi).
        enriched_payload = (
            self.canon.timeline_events_enriched.get(cancelled_event_id)
            if self.canon.timeline_events_enriched else None
        )
        enriched_invariants: list[str] | None = None
        enriched_seeds: list[str] | None = None
        if isinstance(enriched_payload, dict):
            invs = enriched_payload.get("narrative_invariants")
            if isinstance(invs, list):
                enriched_invariants = [
                    str(x) for x in invs if isinstance(x, str) and x
                ]
            seeds = enriched_payload.get("alternative_seeds")
            if isinstance(seeds, list):
                enriched_seeds = [
                    str(x) for x in seeds if isinstance(x, str) and x
                ]

        # Phase H wiring 9.4 : si l'event annule est un divergence_point,
        # extrait severity / why_pivotal / consequences pour le prompt.
        divergence_payload = self._divergence_index.get(cancelled_event_id)
        div_severity: str | None = None
        div_why: str | None = None
        div_consequences: list[str] | None = None
        if isinstance(divergence_payload, dict):
            sev = divergence_payload.get("cascade_severity")
            if isinstance(sev, str) and sev:
                div_severity = sev
            why = divergence_payload.get("why_pivotal_fr")
            if isinstance(why, str) and why:
                div_why = why
            cons = divergence_payload.get("if_altered_consequences")
            if isinstance(cons, list):
                div_consequences = [
                    str(x) for x in cons if isinstance(x, str) and x
                ]

        user_msg = build_substitute_user_message(
            cancelled_event_id=cancelled_event_id,
            cancelled_event_name=ev.name_fr,
            cancelled_event_year=ev.year,
            cancellation_reason=cancellation_reason,
            current_year=current_year,
            world_state_summary=world_state_summary or "(non fourni)",
            kg_recent_facts=kg_recent_facts or "(aucun)",
            enriched_narrative_invariants=enriched_invariants,
            enriched_alternative_seeds=enriched_seeds,
            divergence_severity=div_severity,
            divergence_why_pivotal=div_why,
            divergence_consequences=div_consequences,
        )
        if feedback:
            user_msg += f"\n\n[FEEDBACK SUR TENTATIVE PRECEDENTE]\n{feedback}"

        try:
            response = await self.client.generate(
                messages=[
                    Message(role="system", content=load_prompt("world_resolver")),
                    Message(role="user", content=user_msg),
                ],
                schema=SUBSTITUTE_EVENT_SCHEMA,
            )
        except Exception as exc:
            logger.warning(
                "phase_f_llm_failed",
                cancelled_event=cancelled_event_id,
                error=type(exc).__name__,
                msg=str(exc)[:200],
            )
            return GenerationFailure(reason=f"llm_unavailable: {exc}")

        if response.parsed_json is None:
            return GenerationFailure(
                reason="schema_invalid",
                raw_response=response.text[:500] if response.text else None,
            )

        return self._build_substitute_from_json(
            data=response.parsed_json,
            cancelled_event_id=cancelled_event_id,
            fallback_year=ev.year,
        )

    def _build_substitute_from_json(
        self,
        *,
        data: dict[str, Any],
        cancelled_event_id: str,
        fallback_year: int,
    ) -> SubstituteEvent | GenerationFailure:
        """Construit un SubstituteEvent valide depuis le JSON LLM.

        Force le prefixe id 'substitute_' et le rattachement au canon.
        """
        # Round 15 : defensive, si id_suffix est None (LLM peut produire null
        # explicite) ou autre type non-string, traite comme manquant.
        raw_suffix = data.get("id_suffix")
        suffix = raw_suffix.strip() if isinstance(raw_suffix, str) else ""
        if not suffix:
            return GenerationFailure(reason="id_suffix manquant ou vide")
        # Sanitize : suffixe lowercase ASCII snake_case (regex Pydantic
        # `^substitute_[a-z0-9_]+$` rejette les unicode). Strip accents et
        # caracteres speciaux. Round 7 : LLM FR produit souvent des accents.
        import unicodedata
        normalized = unicodedata.normalize("NFKD", suffix.lower())
        ascii_only = normalized.encode("ascii", errors="ignore").decode("ascii")
        safe = "".join(c for c in ascii_only if c.isalnum() or c == "_")
        if not safe:
            return GenerationFailure(reason="id_suffix non-alphanumeric")
        full_id = f"substitute_{safe}"

        try:
            preconditions = [
                SubstitutePrecondition(
                    type=p.get("type", ""),
                    parameters=p.get("parameters") or {},
                )
                for p in (data.get("preconditions") or [])
                if isinstance(p, dict) and p.get("type")
            ]
            outcomes = [
                SubstituteOutcome(
                    type=o.get("type", ""),
                    parameters=o.get("parameters") or {},
                )
                for o in (data.get("outcomes") or [])
                if isinstance(o, dict) and o.get("type")
            ]
            if not outcomes:
                return GenerationFailure(reason="outcomes vide apres parsing")

            # Round 15 : defensive str() sur les optional string fields qui
            # pourraient etre None dans le LLM output. `.strip() if isinstance`
            # protege contre AttributeError.
            def _str_field(key: str, default: str) -> str:
                raw = data.get(key)
                if isinstance(raw, str) and raw.strip():
                    return raw.strip()
                return default

            def _opt_str_field(key: str) -> str | None:
                raw = data.get(key)
                if isinstance(raw, str) and raw:
                    return raw
                return None

            # Round 16 : valide cancellation_strategy_type contre les valeurs
            # acceptees par Pydantic Literal. Fallback "substitute" si LLM
            # produit une valeur inconnue.
            # Round 59 : single source of truth depuis types.py.
            raw_strategy = _str_field("cancellation_strategy_type", "substitute")
            if raw_strategy not in ALLOWED_CANCELLATION_STRATEGIES:
                raw_strategy = "substitute"

            return SubstituteEvent(
                id=full_id,
                cancelled_canon_event_id=cancelled_event_id,
                name_fr=_str_field("name_fr", "(sans titre)"),
                year=int(data.get("year", fallback_year)),
                date=_opt_str_field("date"),
                location=_opt_str_field("location"),
                involved_characters=[
                    c for c in (data.get("involved_characters") or [])
                    if isinstance(c, str) and c
                ],
                preconditions=preconditions,
                outcomes=outcomes,
                narrative_summary_fr=_str_field(
                    "narrative_summary_fr", "(pas de narration generee)",
                ),
                cancellation_strategy_type=raw_strategy,
                rumor_template=_opt_str_field("rumor_template"),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            return GenerationFailure(
                reason=f"pydantic_invalid: {exc}",
                raw_response=str(data)[:500],
            )


__all__ = ["GenerationFailure", "SubstituteEventGenerator"]
