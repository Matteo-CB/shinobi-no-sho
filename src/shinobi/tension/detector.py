"""TensionDetector : orchestre les 20 invariants deterministes.

Usage typique :
    detector = TensionDetector(store)
    tensions = detector.detect(year=12)
    top = tensions.top(5)

L'invocation est purement deterministe (~ms par invariant). Le LLM analyste
est dans un module separe (llm_analyst.py).
"""

from __future__ import annotations

from collections.abc import Sequence

from shinobi.kg.store import KnowledgeGraphStore
from shinobi.logging_setup import get_logger
from shinobi.tension.invariants import INVARIANTS, TensionInvariant
from shinobi.tension.types import Tension, TensionList

logger = get_logger(__name__)


class TensionDetector:
    """Orchestre les invariants. Stateless (instance reutilisable a chaque tour)."""

    def __init__(
        self,
        store: KnowledgeGraphStore,
        *,
        invariants: Sequence[TensionInvariant] = INVARIANTS,
    ) -> None:
        self._store = store
        self._invariants = list(invariants)

    @property
    def invariants(self) -> Sequence[TensionInvariant]:
        return tuple(self._invariants)

    def detect(
        self,
        year: int,
        *,
        ctx: dict | None = None,
        skip_invariants: Sequence[str] = (),
    ) -> TensionList:
        """Lance toutes les regles non skippees. Retourne TensionList agreg.

        ctx peut etre injecte par le caller pour overrider des constantes
        (ex: liste de great_villages, anniversary_cycles, etc.).
        """
        ctx = ctx or {}
        skipped = set(skip_invariants)
        all_tensions: list[Tension] = []
        for inv in self._invariants:
            if inv.name in skipped:
                continue
            try:
                detected = inv.detect(self._store, year, ctx)
            except Exception as exc:
                logger.warning(
                    "tension_invariant_failed",
                    invariant=inv.name, error=str(exc),
                )
                continue
            all_tensions.extend(detected)
        return TensionList(
            tensions=all_tensions,
            detected_at_year=year,
        )

    def detect_with_top(
        self, year: int, n: int = 10,
        *, ctx: dict | None = None,
    ) -> list[Tension]:
        """Raccourci : detect puis prend les top-n par score."""
        return self.detect(year, ctx=ctx).top(n)


__all__ = ["TensionDetector"]
