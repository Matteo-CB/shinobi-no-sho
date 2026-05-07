"""TensionDetector : orchestre les 21 invariants deterministes.

Usage typique :
    detector = TensionDetector(store)
    tensions = detector.detect(year=12)
    top = tensions.top(5)

L'invocation est purement deterministe (~ms par invariant). Le LLM analyste
est dans un module separe (llm_analyst.py).

Phase H wiring 9.3 : la 21eme regle
`political_alliance_brittle_via_dead_leader` opt-in si le caller injecte
`canon` au constructeur, ce qui permet de pre-builder le ctx commun
(`political_forces`, `char_deaths`) pour ne pas le rebuilder a chaque
detect().
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from shinobi.kg.store import KnowledgeGraphStore
from shinobi.logging_setup import get_logger
from shinobi.tension.invariants import INVARIANTS, TensionInvariant
from shinobi.tension.types import Tension, TensionList

logger = get_logger(__name__)


def build_canon_ctx(canon: Any) -> dict[str, Any]:
    """Phase H wiring 9.3 : extrait le ctx canon-driven depuis CanonBundle.

    Retourne un dict avec :
    - `political_forces` : canon.political_forces (dataset 9.3)
    - `char_deaths` : map char_id -> death_year pour les chars canon avec
      death_year defini. Permet a l'invariant de tester "leader mort
      avant year" sans accepter le CanonBundle entier (couplage faible).

    Si canon est None ou n'a pas les attributs attendus, retourne dict vide.
    """
    ctx: dict[str, Any] = {}
    if canon is None:
        return ctx
    political_forces = getattr(canon, "political_forces", None)
    if political_forces:
        ctx["political_forces"] = political_forces
    chars = getattr(canon, "characters", None)
    if chars:
        deaths: dict[str, int] = {}
        for cid, char in chars.items():
            dy = getattr(char, "death_year", None)
            if isinstance(dy, int):
                deaths[cid] = dy
        if deaths:
            ctx["char_deaths"] = deaths
    return ctx


class TensionDetector:
    """Orchestre les invariants. Stateless (instance reutilisable a chaque tour)."""

    def __init__(
        self,
        store: KnowledgeGraphStore,
        *,
        invariants: Sequence[TensionInvariant] = INVARIANTS,
        canon: Any = None,
    ) -> None:
        self._store = store
        self._invariants = list(invariants)
        # Phase H wiring 9.3 : pre-build le ctx canon une fois pour eviter
        # d'iterer canon.characters a chaque tick. Default {} si canon=None.
        self._canon_ctx: dict[str, Any] = build_canon_ctx(canon)

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
        # Phase H wiring 9.3 : merge le ctx canon (pre-built au constructor)
        # avec le ctx caller-provided. Le caller peut override en passant les
        # memes keys, sinon on inherite les defaults canon.
        merged_ctx: dict[str, Any] = dict(self._canon_ctx)
        if ctx:
            merged_ctx.update(ctx)
        ctx = merged_ctx
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


__all__ = ["TensionDetector", "build_canon_ctx"]
