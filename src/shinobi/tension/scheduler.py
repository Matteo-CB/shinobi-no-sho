"""TensionScheduler : declenche l'analyste LLM a intervalle regulier (3 mois in-game).

Spec doc 02 §5.3.B :
> Tous les 3 mois in-game, un Qwen3-4B recoit un snapshot synthetique du KG
> et identifie les fils narratifs en suspens, configurations critiques,
> anniversaires d'events.

Le scheduler ENCAPSULE la logique d'intervalle : il sait quand le dernier
appel analyste a eu lieu et decide si un nouveau est necessaire. Il combine :
- TensionDetector (deterministe, appele a chaque turn ou tick)
- LLMTensionAnalyst (LLM, appele tous les N mois in-game)

L'orchestrateur du jeu (boucle CLI / world tick) appelle simplement
`scheduler.tick(year, month)` apres chaque avancee temporelle. Le scheduler
decide :
- Toujours executer le detecteur deterministe (rapide, ms)
- Executer l'analyste LLM seulement si l'intervalle est ecoule
- Merge les deux outputs en TensionList unique

Mode offline : si le client LLM est None, l'analyste est skip silencieusement.
Le detecteur deterministe continue de tourner (aucune dependance LLM).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shinobi.kg.store import KnowledgeGraphStore
from shinobi.logging_setup import get_logger
from shinobi.tension.detector import TensionDetector
from shinobi.tension.llm_analyst import LLMAnalystConfig, LLMTensionAnalyst
from shinobi.tension.types import TensionList

logger = get_logger(__name__)


@dataclass
class SchedulerState:
    """Etat persistant du scheduler entre les ticks. Serialisable JSON."""

    last_analyst_year: int | None = None
    last_analyst_month: int | None = None
    analyst_runs_count: int = 0
    detector_runs_count: int = 0
    skipped_runs: list[tuple[int, int, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "last_analyst_year": self.last_analyst_year,
            "last_analyst_month": self.last_analyst_month,
            "analyst_runs_count": self.analyst_runs_count,
            "detector_runs_count": self.detector_runs_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SchedulerState:
        return cls(
            last_analyst_year=d.get("last_analyst_year"),
            last_analyst_month=d.get("last_analyst_month"),
            analyst_runs_count=int(d.get("analyst_runs_count", 0)),
            detector_runs_count=int(d.get("detector_runs_count", 0)),
        )


@dataclass(frozen=True)
class TickResult:
    """Resultat d'un tick scheduler."""

    tensions: TensionList
    detector_ran: bool
    analyst_ran: bool
    reason_analyst_skipped: str | None = None


def _months_elapsed(
    last_year: int | None, last_month: int | None,
    current_year: int, current_month: int,
) -> int:
    """Calcule les mois ecoules depuis last (None = infini)."""
    if last_year is None or last_month is None:
        return 999_999  # premier appel
    return (current_year - last_year) * 12 + (current_month - last_month)


class TensionScheduler:
    """Orchestrateur temporel du Tension Detector.

    Appel-le `scheduler.tick(year, month)` apres chaque avancee in-game.
    Il execute le detecteur deterministe a chaque tick, et l'analyste LLM
    seulement si interval_months_in_game ecoule depuis le dernier appel.
    """

    def __init__(
        self,
        store: KnowledgeGraphStore,
        *,
        detector: TensionDetector | None = None,
        analyst: LLMTensionAnalyst | None = None,
        config: LLMAnalystConfig | None = None,
        state: SchedulerState | None = None,
        social_network=None,  # type: SocialNetwork | None
        canon=None,  # type: CanonBundle | None
    ) -> None:
        self._store = store
        self._config = config or LLMAnalystConfig()
        # Phase H wiring 9.3 : propage canon au detector auto-cree pour
        # activer la 21eme regle political_alliance_brittle_via_dead_leader.
        # Si un detector externe est fourni, il porte deja son canon.
        self._detector = detector or TensionDetector(store, canon=canon)
        # Spec §5.3 : LLMTensionAnalyst recoit social_network pour
        # inclure les relations dans le snapshot
        self._analyst = analyst or LLMTensionAnalyst(
            store, llm_client=None, config=self._config,
            social_network=social_network,
        )
        self._state = state or SchedulerState()

    @property
    def state(self) -> SchedulerState:
        return self._state

    @property
    def config(self) -> LLMAnalystConfig:
        return self._config

    def is_due(self, year: int, month: int = 1) -> bool:
        """True si l'analyste doit tourner ce tick."""
        elapsed = _months_elapsed(
            self._state.last_analyst_year, self._state.last_analyst_month,
            year, month,
        )
        return elapsed >= self._config.interval_months_in_game

    async def tick(
        self,
        year: int,
        month: int = 1,
        *,
        ctx: dict | None = None,
        force_analyst: bool = False,
    ) -> TickResult:
        """Avance d'un pas. Lance toujours le detecteur, l'analyste si due.

        force_analyst : court-circuite l'intervalle (tests / debug).
        """
        # 1. Detector deterministe a chaque tick
        det_tensions = self._detector.detect(year, ctx=ctx)
        self._state.detector_runs_count += 1

        # 2. Analyste LLM si intervalle ecoule
        analyst_should_run = force_analyst or self.is_due(year, month)
        analyst_tensions = TensionList(detected_at_year=year)
        analyst_ran = False
        skip_reason: str | None = None

        if not analyst_should_run:
            elapsed = _months_elapsed(
                self._state.last_analyst_year, self._state.last_analyst_month,
                year, month,
            )
            skip_reason = (
                f"interval_not_elapsed ({elapsed} < "
                f"{self._config.interval_months_in_game} mois)"
            )
        else:
            try:
                analyst_tensions = await self._analyst.analyze(year)
                self._state.last_analyst_year = year
                self._state.last_analyst_month = month
                self._state.analyst_runs_count += 1
                analyst_ran = True
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "tension_scheduler_analyst_failed",
                    error=type(exc).__name__, msg=str(exc)[:200],
                )
                skip_reason = f"analyst_error:{type(exc).__name__}"
                self._state.skipped_runs.append((year, month, skip_reason))

        # Merge deterministe + LLM
        merged = det_tensions.merge(analyst_tensions)
        return TickResult(
            tensions=merged,
            detector_ran=True,
            analyst_ran=analyst_ran,
            reason_analyst_skipped=skip_reason,
        )

    def reset(self) -> None:
        """Reinitialise l'etat (tests / nouvelle partie)."""
        self._state = SchedulerState()


__all__ = ["SchedulerState", "TensionScheduler", "TickResult"]
