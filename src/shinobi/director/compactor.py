"""NarrativeCompactor : compaction periodique style NexusSum (2025).

Spec doc 02 §7.4 : tous les N mois in-game, genere un resume des events
recents pour eviter l'explosion du contexte LLM au-dela de ~100 turns.

Strategie :
- Lit world.completed_events sur la fenetre [last_compaction, now]
- Lit world.cancelled_events sur la meme fenetre
- Lit substituts injectes (Phase F) via world.substitute_events
- Construit un prompt LLM compact "resume ces N events en 200-400 mots"
- Fallback offline si LLM indispo : concatenation deterministe des
  event names + cancelled reasons

Le summary va dans le NudgeContext.recent_summary, et peut etre
persiste dans DirectorState.last_summary pour reuse au prochain tick.
"""

from __future__ import annotations

from shinobi.canon.models import CanonBundle
from shinobi.engine.world import WorldState
from shinobi.llm.client import LLMClient, Message
from shinobi.logging_setup import get_logger

logger = get_logger(__name__)

# Compaction par defaut tous les 6 mois in-game (cf spec).
DEFAULT_COMPACTION_INTERVAL_MONTHS: int = 6

# Round G12 : cap dur sur la sortie compactor pour eviter LLM runaway -> save
# bloat. System prompt demande 200-400 mots (~1500-3000 chars). 5000 chars
# laisse une marge x2 confortable. Au-dela, on tronque proprement.
_MAX_SUMMARY_CHARS: int = 5000

_SYSTEM_PROMPT = (
    "Tu es l'archiviste narratif de Shinobi no Sho. Resume en francais "
    "les evenements de la periode donnee en 200 a 400 mots, focus sur "
    "les fils narratifs en cours, les changements politiques, et les "
    "personnages emergents. Pas de tirets cadratins, pas d'emoji, pas "
    "de speculation : reste factuel, reference les events par leur nom."
)


def _eras_covering_period(
    canon: CanonBundle | None,
    *,
    period_start_year: int,
    period_end_year: int,
) -> list[tuple[str, str]]:
    """Retourne les (era_id, era_name_fr) qui chevauchent la periode.

    Round G30 : enrichit le prompt LLM avec le contexte d'ere canon pour
    que la summary puisse referencer les arcs ('Periode war 4', 'Era
    fondation Konoha', etc.) au lieu de juste lister les event_ids.
    """
    if canon is None:
        return []
    eras: list[tuple[str, str]] = []
    for era in canon.eras.values():
        era_start = era.year_start
        era_end = era.year_end if era.year_end is not None else 99999
        # Chevauchement : intervalles non-disjoints
        if era_start <= period_end_year and era_end >= period_start_year:
            eras.append((era.id, era.name_fr))
    return eras


def _format_events_for_prompt(
    *,
    completed: list[str],
    cancelled: list[tuple[str, str]],  # (event_id, reason)
    substitutes: list[str],
    period_start_year: int,
    period_end_year: int,
    eras: list[tuple[str, str]] | None = None,
) -> str:
    """Formate la liste d'events pour le prompt user message.

    Round G20 : pas de souci ambigu ici (separateur ' -> ' deja explicite),
    mais on uniformise le wording 'year ... a year ...' avec la version
    offline fallback pour coherence.
    Round G30 : prepend contexte canon eras pour LLM-richer summary.
    """
    lines = [
        f"Periode : year {period_start_year} a year {period_end_year}",
        "",
    ]
    # Round G30 : contexte ere canon
    if eras:
        lines.append(f"Eres canon couvertes ({len(eras)}) :")
        for era_id, era_name in eras[:5]:  # cap pour eviter prompt blowup
            lines.append(f"  - {era_name} ({era_id})")
        lines.append("")
    if completed:
        lines.append(f"Events declenches ({len(completed)}) :")
        for eid in completed[:30]:
            lines.append(f"  - {eid}")
        if len(completed) > 30:
            lines.append(f"  ... et {len(completed) - 30} autres")
        lines.append("")
    if cancelled:
        lines.append(f"Events annules ({len(cancelled)}) :")
        for eid, reason in cancelled[:20]:
            lines.append(f"  - {eid} (raison: {reason[:80]})")
        lines.append("")
    if substitutes:
        lines.append(f"Substituts injectes (Phase F) ({len(substitutes)}) :")
        for sid in substitutes[:20]:
            lines.append(f"  - {sid}")
        lines.append("")
    return "\n".join(lines)


def _format_offline_fallback(
    *,
    completed: list[str],
    cancelled: list[tuple[str, str]],
    substitutes: list[str],
    period_start_year: int,
    period_end_year: int,
) -> str:
    """Resume deterministe (sans LLM) si client indispo.

    Format : 1 paragraphe enumeratif par bucket, max ~600 chars total.

    Round G20 : separateur ' a ' (preposition francaise) au lieu de '-'.
    Avant : 'year 5-10' OK mais 'year -50-10' ambigu (3 tirets) et
    'year -100--50' illisible (double tiret). Le canon Naruto inclut des
    arcs pre-Konoha avec annees negatives -> ce cas est realiste.
    """
    parts: list[str] = [
        f"Periode year {period_start_year} a {period_end_year} : ",
    ]
    if completed:
        sample = ", ".join(completed[:5])
        parts.append(
            f"{len(completed)} event(s) declenche(s) "
            f"(notamment {sample}). "
        )
    if cancelled:
        sample = ", ".join(eid for eid, _ in cancelled[:5])
        parts.append(
            f"{len(cancelled)} event(s) annule(s) "
            f"(dont {sample}). "
        )
    if substitutes:
        sample = ", ".join(substitutes[:5])
        parts.append(
            f"{len(substitutes)} substitut(s) injecte(s) "
            f"(dont {sample}). "
        )
    if len(parts) == 1:
        # Aucun event : periode quiet
        parts.append("Aucun fait notable, branche stable.")
    return "".join(parts)


class NarrativeCompactor:
    """Compaction NexusSum-style : LLM-summary ou fallback offline."""

    def __init__(
        self,
        client: LLMClient | None = None,
        *,
        canon: CanonBundle | None = None,
    ) -> None:
        """client peut etre None (fallback offline deterministe)."""
        self.client = client
        self.canon = canon

    async def compact(
        self,
        world: WorldState,
        *,
        period_start_year: int,
        period_end_year: int,
    ) -> str:
        """Genere un resume narratif pour la periode.

        Returns:
            string. Si LLM disponible : 200-400 mots francais. Si offline :
            ~150-300 chars deterministe.

        Round G27 : si period_start > period_end (swap par bug caller / save
        corrompu), swap silencieusement avec log warning. Sans ca,
        _collect_* filtrait `start <= year <= end` qui ne match jamais ->
        summary vide trompeuse alors que des events reels existaient.
        """
        if period_start_year > period_end_year:
            logger.warning(
                "phase_g_compactor_period_swapped",
                original_start=period_start_year,
                original_end=period_end_year,
            )
            period_start_year, period_end_year = (
                period_end_year, period_start_year,
            )
        completed = self._collect_completed(
            world, period_start_year, period_end_year,
        )
        cancelled = self._collect_cancelled(
            world, period_start_year, period_end_year,
        )
        # Round G15 : filtre par target year dans la periode (cf docstring).
        substitutes = self._collect_substitutes(
            world, period_start_year, period_end_year,
        )

        # Fallback offline
        if self.client is None:
            return _format_offline_fallback(
                completed=completed, cancelled=cancelled,
                substitutes=substitutes,
                period_start_year=period_start_year,
                period_end_year=period_end_year,
            )

        # Round G30 : enrichit le prompt avec les eres canon couvrant la
        # periode (avant : le canon param etait stocke mais jamais utilise).
        eras = _eras_covering_period(
            self.canon,
            period_start_year=period_start_year,
            period_end_year=period_end_year,
        )
        user_msg = _format_events_for_prompt(
            completed=completed, cancelled=cancelled,
            substitutes=substitutes,
            period_start_year=period_start_year,
            period_end_year=period_end_year,
            eras=eras,
        )
        from shinobi.i18n.prompts_loader import load_prompt
        try:
            response = await self.client.generate(
                messages=[
                    Message(role="system", content=load_prompt("director_compactor")),
                    Message(role="user", content=user_msg),
                ],
            )
        except Exception as exc:
            logger.warning(
                "phase_g_compactor_llm_failed",
                error=type(exc).__name__,
                msg=str(exc)[:200],
            )
            # Fallback offline si LLM crash
            return _format_offline_fallback(
                completed=completed, cancelled=cancelled,
                substitutes=substitutes,
                period_start_year=period_start_year,
                period_end_year=period_end_year,
            )

        text = (response.text or "").strip()
        if not text:
            # LLM a renvoye vide : fallback
            return _format_offline_fallback(
                completed=completed, cancelled=cancelled,
                substitutes=substitutes,
                period_start_year=period_start_year,
                period_end_year=period_end_year,
            )
        # Round G12 : cap dur sur la sortie LLM. Sans cap, un LLM runaway
        # (drift, run-on) pourrait produire 50K chars, persistes dans
        # DirectorState.last_summary -> save bloat sur sessions longues.
        if len(text) > _MAX_SUMMARY_CHARS:
            text = text[:_MAX_SUMMARY_CHARS - 4] + "..."
            logger.warning(
                "phase_g_compactor_truncated",
                original_len=len(response.text or ""),
                truncated_to=_MAX_SUMMARY_CHARS,
            )
        return text

    @staticmethod
    def _collect_completed(
        world: WorldState, start: int, end: int,
    ) -> list[str]:
        """event_ids des completed_events sur [start, end]."""
        return [
            ev.event_id for ev in world.completed_events
            if start <= ev.triggered_at_year <= end
        ]

    @staticmethod
    def _collect_cancelled(
        world: WorldState, start: int, end: int,
    ) -> list[tuple[str, str]]:
        """(event_id, reason) des cancelled_events sur [start, end]."""
        return [
            (ev.event_id, ev.reason or "")
            for ev in world.cancelled_events
            if start <= ev.cancelled_at_year <= end
        ]

    @staticmethod
    def _collect_substitutes(
        world: WorldState, start: int, end: int,
    ) -> list[str]:
        """substitute_ids actifs dans world.substitute_events filtres par
        target year dans [start, end].

        Round G15 : avant, retournait TOUS les pending substitutes sans
        filtre temporel. Un substitute scheduled pour year 30 apparaissait
        dans le summary "Periode year 5-10" comme s'il appartenait a cette
        periode. Le narrator LLM recevait une directive trompeuse.

        Defensive : si l'inner dict est mal forme (R52-style corruption),
        skip silencieusement plutot que crash.
        """
        out: list[str] = []
        for sid, payload in world.substitute_events.items():
            if not isinstance(payload, dict):
                continue
            year = payload.get("year")
            # Round G23 : exclude bool (subclass de int en Python). Un
            # payload corrompu avec year=True passait isinstance(int) et
            # etait traite comme year=1 silencieusement.
            if not isinstance(year, int) or isinstance(year, bool):
                continue
            if start <= year <= end:
                out.append(sid)
        return out


__all__ = ["DEFAULT_COMPACTION_INTERVAL_MONTHS", "NarrativeCompactor"]
