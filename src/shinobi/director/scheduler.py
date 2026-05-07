"""DirectorScheduler : tick orchestrator pour le Director.

Spec doc 02 §11.1 : "Director (1 fois/10 ticks)". Le scheduler decide
quand le Director doit tourner — il combine :
- Composition des actes (rapide, deterministe)
- Compaction narrative (LLM call, periodique tous les 6 mois in-game)
- Maintenance des invariants (constant)

Design analogue a tension/scheduler.py :
- DirectorState : etat persistant entre ticks (acts actifs, last compaction).
- TickResult : sortie d'un tick.
- is_due_compaction() : faut-il regenerer le summary ce tick ?
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shinobi.director.types import (
    MONTH_MAX,
    MONTH_MIN,
    YEAR_MAX,
    YEAR_MIN,
    AbstractAct,
)
from shinobi.logging_setup import get_logger

logger = get_logger(__name__)

# Round G14 : meme cap que NarrativeCompactor._MAX_SUMMARY_CHARS pour
# enforcer la cap en serialization aussi (saves pre-R G12 ou ecrits par
# une autre source). Eviter dependance circulaire avec compactor.py
# par duplication explicite (les deux constantes doivent rester alignees).
_MAX_LAST_SUMMARY_CHARS: int = 5000

# Round G22 : bornes year/month appliquees a la (de)serialization.
# Round G29 : utilise les constants module-level types.py (single source).
_YEAR_MIN: int = YEAR_MIN
_YEAR_MAX: int = YEAR_MAX
_MONTH_MIN: int = MONTH_MIN
_MONTH_MAX: int = MONTH_MAX


@dataclass
class DirectorState:
    """Etat persistant du Director entre ticks. Serialisable JSON.

    Partie integrale de WorldState pour qu'un save/load preserve les
    actes actifs et le moment de derniere compaction.
    """

    # acts actifs indexes par id pour merge/dedup en O(1)
    active_acts: dict[str, AbstractAct] = field(default_factory=dict)
    last_compaction_year: int | None = None
    last_compaction_month: int | None = None
    last_summary: str | None = None
    tick_count: int = 0
    composer_runs: int = 0
    compactor_runs: int = 0

    @staticmethod
    def _cap_summary(summary: str | None) -> str | None:
        """Round G14 : cap last_summary a _MAX_LAST_SUMMARY_CHARS.

        Symetrique a NarrativeCompactor (R G12). Sans ce cap a la
        (de)serialization, un save pre-R G12 contenant 50K chars passait
        from_dict tel quel, puis to_dict le re-serialisait sans correction.
        Save bloat persistait a travers les sessions.
        """
        if summary is None:
            return None
        if len(summary) <= _MAX_LAST_SUMMARY_CHARS:
            return summary
        return summary[:_MAX_LAST_SUMMARY_CHARS - 4] + "..."

    @staticmethod
    def _clamp_year(year: int | None) -> int | None:
        """Round G22 : clamp last_compaction_year aux bornes Director.

        Save corrompu avec year=99999 -> is_compaction_due retourne False
        indefiniment (elapsed=(current-99999)*12 negatif) -> compactor
        jamais run -> nudge.recent_summary stale a perpetuite.
        Round G23 : exclude bool (subclass de int en Python). Sans `not
        isinstance(year, bool)`, year=True passait isinstance check et
        etait converti en year=1 silencieusement.
        """
        if year is None:
            return None
        if not isinstance(year, int) or isinstance(year, bool):
            return None
        return max(_YEAR_MIN, min(year, _YEAR_MAX))

    @staticmethod
    def _clamp_month(month: int | None) -> int | None:
        """Round G22 : clamp last_compaction_month a [1, 12].

        Save corrompu avec month=13 ou 0 -> arithmetic _months_elapsed
        donne valeurs imprevisibles. Clamp pour garantir math saine.
        Round G23 : exclude bool aussi (mirror _clamp_year).
        """
        if month is None:
            return None
        if not isinstance(month, int) or isinstance(month, bool):
            return None
        return max(_MONTH_MIN, min(month, _MONTH_MAX))

    def to_dict(self) -> dict:
        return {
            "active_acts": {
                aid: act.model_dump(mode="json")
                for aid, act in self.active_acts.items()
            },
            # Round G22 : clamp en serialization (au cas ou state mute
            # via API directe, ou pre-R G22 saves)
            "last_compaction_year": self._clamp_year(self.last_compaction_year),
            "last_compaction_month": self._clamp_month(self.last_compaction_month),
            "last_summary": self._cap_summary(self.last_summary),
            "tick_count": self.tick_count,
            "composer_runs": self.composer_runs,
            "compactor_runs": self.compactor_runs,
        }

    @classmethod
    def from_dict(cls, d: dict) -> DirectorState:
        # Defensive : raw_acts peut etre None ou non-dict (corrupted save)
        raw_acts = d.get("active_acts") or {}
        acts: dict[str, AbstractAct] = {}
        if isinstance(raw_acts, dict):
            for aid, payload in raw_acts.items():
                if not isinstance(aid, str) or not isinstance(payload, dict):
                    continue
                try:
                    acts[aid] = AbstractAct(**payload)
                except Exception:  # noqa: BLE001
                    # Fact malforme dans le save -> skip plutot que crash
                    logger.warning(
                        "director_state_corrupt_act_skipped",
                        act_id=aid,
                    )
                    continue
        return cls(
            active_acts=acts,
            # Round G22 : clamp aussi en deserialization pour protect
            # les saves corrompus.
            last_compaction_year=cls._clamp_year(d.get("last_compaction_year")),
            last_compaction_month=cls._clamp_month(d.get("last_compaction_month")),
            last_summary=cls._cap_summary(d.get("last_summary")),
            tick_count=int(d.get("tick_count", 0)),
            composer_runs=int(d.get("composer_runs", 0)),
            compactor_runs=int(d.get("compactor_runs", 0)),
        )


def _months_elapsed(
    last_year: int | None, last_month: int | None,
    current_year: int, current_month: int,
) -> int:
    """Mois ecoules entre (last_year, last_month) et (current_year, current_month).

    None = jamais run -> returns "infini" via large value (9999) pour
    forcer le run au premier appel.
    """
    if last_year is None or last_month is None:
        return 9999
    return (current_year - last_year) * 12 + (current_month - last_month)


def is_compaction_due(
    state: DirectorState,
    *,
    current_year: int,
    current_month: int = 1,
    interval_months: int,
) -> bool:
    """True si la compaction LLM est due ce tick."""
    elapsed = _months_elapsed(
        state.last_compaction_year, state.last_compaction_month,
        current_year, current_month,
    )
    return elapsed >= interval_months


__all__ = [
    "DirectorState",
    "is_compaction_due",
]
