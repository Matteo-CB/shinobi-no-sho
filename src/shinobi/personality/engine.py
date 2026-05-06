"""PersonalityEngine : orchestrateur d'application des drift rules.

Responsabilites :
- A partir d'un `ExperiencedEvent`, identifier la `DriftRule` correspondante,
  composer les deltas (brut), appliquer la saturation sigmoid, retourner un
  nouveau `NPCPersonality` immuable.
- Maintenir l'historique de drift (append-only).
- Calculer la divergence canon en lecture rapide.

L'engine est stateless et pur : ne touche pas la base de donnees, ne fait pas
d'I/O. La persistance est le job du `store.py`.
"""

from __future__ import annotations

from collections.abc import Iterable

from shinobi.personality.dimensions import ALL_DIMENSIONS, PersonalityDimension
from shinobi.personality.drift_rules import (
    apply_delta_with_saturation,
    compose_drift_for_event,
    get_rule_for_category,
)
from shinobi.personality.types import (
    ExperiencedEvent,
    NPCPersonality,
    PersonalityDrift,
)


class PersonalityEngineError(Exception):
    """Erreur fonctionnelle (rule introuvable, event mal forme, etc.)."""


class PersonalityEngine:
    """Engine deterministe d'application de drifts a des NPCPersonality."""

    def __init__(self, *, sigmoid_sensitivity: float = 4.0) -> None:
        self._sensitivity = sigmoid_sensitivity

    @property
    def sigmoid_sensitivity(self) -> float:
        return self._sensitivity

    # --- single event application ------------------------------------------

    def apply_event(
        self, personality: NPCPersonality, event: ExperiencedEvent,
    ) -> NPCPersonality:
        """Applique un event vecu a une personality. Retourne nouveau objet immuable.

        Si la rule est introuvable, retourne `personality` inchange (defensive).
        """
        if event.npc_id != personality.npc_id:
            raise PersonalityEngineError(
                f"event.npc_id={event.npc_id} != personality.npc_id={personality.npc_id}",
            )
        rule = get_rule_for_category(event.category)
        if rule is None:
            return personality
        if rule.requires_related_npc and event.related_npc_id is None:
            # Soft-skip : event mal-forme, on ne drifte pas
            return personality

        deltas_raw = compose_drift_for_event(
            rule,
            intensity=event.intensity,
            duration_years=event.duration_years,
        )
        new_vector, applied = self._apply_deltas(personality.vector, deltas_raw)
        drift = PersonalityDrift(
            npc_id=event.npc_id,
            rule_name=rule.name,
            year=event.year,
            delta=deltas_raw,
            applied_delta=applied,
            event_category=event.category,
            related_npc_id=event.related_npc_id,
            related_event_id=event.related_event_id,
            related_mission_id=event.related_mission_id,
            notes=event.notes,
        )
        new_history = (*personality.drift_history, drift)
        return personality.model_copy(update={
            "vector": new_vector,
            "drift_history": new_history,
        })

    def apply_events(
        self,
        personality: NPCPersonality,
        events: Iterable[ExperiencedEvent],
    ) -> NPCPersonality:
        """Applique une sequence d'events dans l'ordre. Equivalent a fold."""
        current = personality
        for ev in events:
            current = self.apply_event(current, ev)
        return current

    # --- analyses ----------------------------------------------------------

    def divergence_per_dimension(
        self, personality: NPCPersonality,
    ) -> dict[PersonalityDimension, float]:
        """Pour chaque dimension : abs(vector - canon_baseline).

        Permet d'identifier QUELLE dimension a le plus drifte, pas seulement
        la magnitude globale.
        """
        return {
            dim: abs(personality.vector[dim] - personality.canon_baseline[dim])
            for dim in ALL_DIMENSIONS
        }

    def top_drifted_dimensions(
        self, personality: NPCPersonality, n: int = 3,
    ) -> list[tuple[PersonalityDimension, float]]:
        """Top-N dimensions les plus drifees, ordre decroissant."""
        per_dim = self.divergence_per_dimension(personality)
        items = sorted(per_dim.items(), key=lambda kv: kv[1], reverse=True)
        return items[:n]

    def filter_history_for(
        self,
        personality: NPCPersonality,
        *,
        rule_name: str | None = None,
        year_min: int | None = None,
        year_max: int | None = None,
        related_npc_id: str | None = None,
    ) -> list[PersonalityDrift]:
        """Filtre l'historique de drift selon des criteres."""
        out: list[PersonalityDrift] = []
        for d in personality.drift_history:
            if rule_name is not None and d.rule_name != rule_name:
                continue
            if year_min is not None and d.year < year_min:
                continue
            if year_max is not None and d.year > year_max:
                continue
            if related_npc_id is not None and d.related_npc_id != related_npc_id:
                continue
            out.append(d)
        return out

    # --- internals ---------------------------------------------------------

    def _apply_deltas(
        self,
        vector: dict[PersonalityDimension, float],
        deltas: dict[PersonalityDimension, float],
    ) -> tuple[dict[PersonalityDimension, float], dict[PersonalityDimension, float]]:
        """Applique les deltas avec saturation. Retourne (new_vector, applied_deltas)."""
        new_vector: dict[PersonalityDimension, float] = dict(vector)
        applied: dict[PersonalityDimension, float] = {}
        for dim, raw in deltas.items():
            old = new_vector[dim]
            new_val = apply_delta_with_saturation(
                old, raw, sensitivity=self._sensitivity,
            )
            new_vector[dim] = new_val
            applied[dim] = new_val - old
        return new_vector, applied


__all__ = ["PersonalityEngine", "PersonalityEngineError"]
