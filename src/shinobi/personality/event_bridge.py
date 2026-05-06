"""Bridge canon TimelineEvent / Mission -> `ExperiencedEvent` per-NPC.

Phase D consomme ce que les PNJ ont VECU. Le canon fournit deux sources :
- `TimelineEvent` (narratif global)
- `Mission` (operation specifique)

Ce module convertit chaque event canon firing en une liste d'`ExperiencedEvent`
par PNJ implique, en deduisant la categorie via heuristique de mots-cles
deterministe sur `name_fr` + `narrative_summary_fr`.

Heuristique deterministe (pas de LLM ici, on garde la Phase D pure) :
- 'massacre' + clan tue -> witnessed_atrocity, mass_killing_committed
- 'mort de X' / 'tue X' -> mentor/parent/sibling/lover_lost (sans
  resolution de rôle - le caller peut affiner si l'engine connaît la
  relation)
- 'guerre' / 'bataille' -> violent_combat_won (heuristique : default)
- 'examen reussi' / 'promotion' -> rank_promotion
- 'trahison' / 'deserte' -> betrayal_witnessed (pour les allies)
- 'reunion' / 'pacte' -> reconciliation
- defaut : witnessed_atrocity si 'attaque/massacre/tuer/mort' detecte,
  sinon aucun event genere

Pour les missions :
- outcome=success ET rank in (B/A/S/forbidden) -> achieved_goal pour les
  participants
- outcome=failure -> failed_goal
- type=assassination/sabotage et participant role=executor -> mass_killing_committed
- type=rescue ET outcome=success -> rescued_by pour le sauve

Le bridge est volontairement simple. Phase E enrichira via le contexte
multi-agent (agents qui vivent un combat decident eux-memes si gagne/perdu).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from shinobi.personality.types import EventCategory, ExperiencedEvent


def _norm(text: str | None) -> str:
    """Lower + strip accents."""
    if not text:
        return ""
    s = unicodedata.normalize("NFD", text)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower()


# Keywords -> EventCategory. L'ordre compte : le premier match l'emporte.
_TIMELINE_KEYWORD_MAP: tuple[tuple[tuple[str, ...], EventCategory], ...] = (
    (("massacre", "extermine", "extermination", "genocide"),
     EventCategory.witnessed_atrocity),
    (("trahit", "trahison", "deserte", "deserteur", "betray"),
     EventCategory.betrayal_witnessed),
    (("promotion", "hokage promu", "promu hokage", "promu chunin",
      "promu jounin", "examen reussi", "examen passe"),
     EventCategory.rank_promotion),
    (("destitue", "demis", "retrograde"),
     EventCategory.rank_demotion),
    (("guerre declaree", "bataille", "combat", "affrontement"),
     EventCategory.violent_combat_won),
    (("reconciliation", "pacte signe", "alliance", "traite"),
     EventCategory.reconciliation),
    (("prophetie", "prediction", "oracle"),
     EventCategory.prophecy_received),
    (("isolement", "exil", "ostracise"),
     EventCategory.long_isolation),
    (("mort", "tue", "meurt", "decede", "abattu"),
     EventCategory.witnessed_atrocity),  # fallback : dramatique
)


@dataclass(frozen=True)
class CanonEventLike:
    """Vue minimale d'un canon event consommee par le bridge.

    On ne couple pas le bridge a CanonBundle pour faciliter les tests : on
    accepte n'importe quel objet qui a name_fr + narrative_summary_fr +
    involved_characters + year.
    """

    id: str
    year: int
    name_fr: str
    narrative_summary_fr: str
    involved_characters: tuple[str, ...]


def detect_category_from_text(text: str) -> EventCategory | None:
    """Detecte la categorie depuis du texte (name_fr + summary_fr concatenes).

    Retourne None si aucun keyword ne matche.
    """
    norm = _norm(text)
    if not norm:
        return None
    for keywords, category in _TIMELINE_KEYWORD_MAP:
        for kw in keywords:
            if kw in norm:
                return category
    return None


def experienced_events_from_timeline_event(
    event: CanonEventLike,
    *,
    intensity: float = 1.0,
) -> list[ExperiencedEvent]:
    """Convertit un canon TimelineEvent en liste d'ExperiencedEvent.

    Pour chaque PNJ implique, on cree UNE ExperiencedEvent dans la categorie
    deduite. Si aucune categorie ne se degage, retourne [].
    """
    text = f"{event.name_fr} {event.narrative_summary_fr}"
    category = detect_category_from_text(text)
    if category is None:
        return []

    out: list[ExperiencedEvent] = []
    for npc_id in event.involved_characters:
        out.append(ExperiencedEvent(
            npc_id=npc_id,
            category=category,
            year=event.year,
            intensity=intensity,
            related_event_id=event.id,
            notes=f"Canon: {event.name_fr}",
        ))
    return out


# Mission bridge ----------------------------------------------------------------


@dataclass(frozen=True)
class MissionLike:
    """Vue minimale d'une Mission canon."""

    id: str
    year: int
    rank: str  # 'D'/'C'/'B'/'A'/'S'/'forbidden'/'unranked'
    type: str  # MissionType.value
    outcome: str  # MissionOutcome.value
    participants: tuple[tuple[str, str], ...]  # (npc_id, role)


_HIGH_RANK = {"B", "A", "S", "forbidden"}


def experienced_events_from_mission(
    mission: MissionLike,
    *,
    intensity: float = 1.0,
) -> list[ExperiencedEvent]:
    """Mappe une Mission en ExperiencedEvent par participant.

    Regles strictes :
    - outcome=success ET rank in {B,A,S,forbidden} -> achieved_goal pour tous
    - outcome=failure -> failed_goal pour tous
    - type='assassination'/'sabotage' ET role='executor' -> mass_killing_committed
    - type='rescue' ET outcome=success ET role='operative' -> achieved_goal
    """
    out: list[ExperiencedEvent] = []
    for npc_id, role in mission.participants:
        category: EventCategory | None = None
        if mission.outcome == "failure":
            category = EventCategory.failed_goal
        elif mission.outcome == "success":
            if mission.type in ("assassination", "sabotage") and role == "executor":
                category = EventCategory.mass_killing_committed
            elif mission.rank in _HIGH_RANK or mission.type == "rescue":
                category = EventCategory.achieved_goal
        if category is None:
            continue
        out.append(ExperiencedEvent(
            npc_id=npc_id,
            category=category,
            year=mission.year,
            intensity=intensity,
            related_mission_id=mission.id,
            notes=f"Mission: {mission.id} ({role})",
        ))
    return out


def _coerce_canon_event(obj: object) -> CanonEventLike | None:
    """Adapter : transforme un canon TimelineEvent en CanonEventLike."""
    if isinstance(obj, CanonEventLike):
        return obj
    try:
        return CanonEventLike(
            id=obj.id,
            year=obj.year,
            name_fr=getattr(obj, "name_fr", "") or "",
            narrative_summary_fr=getattr(obj, "narrative_summary_fr", "") or "",
            involved_characters=tuple(getattr(obj, "involved_characters", ()) or ()),
        )
    except (AttributeError, TypeError):
        return None


def _coerce_mission(obj: object) -> MissionLike | None:
    """Adapter : transforme une Mission canon en MissionLike."""
    if isinstance(obj, MissionLike):
        return obj
    try:
        rank = obj.rank
        rank_val = rank.value if hasattr(rank, "value") else str(rank)
        mtype = obj.type
        type_val = mtype.value if hasattr(mtype, "value") else str(mtype)
        outcome = obj.outcome
        outcome_val = outcome.value if hasattr(outcome, "value") else str(outcome)
        participants_raw = getattr(obj, "participants", ()) or ()
        participants_t: list[tuple[str, str]] = []
        for p in participants_raw:
            cid = getattr(p, "character_id", None)
            role = getattr(p, "role", "operative") or "operative"
            if cid:
                participants_t.append((str(cid), str(role)))
        return MissionLike(
            id=str(obj.id),
            year=int(obj.year),
            rank=rank_val,
            type=type_val,
            outcome=outcome_val,
            participants=tuple(participants_t),
        )
    except (AttributeError, TypeError):
        return None


def collect_experienced_events(
    *,
    timeline_events: Iterable[object] = (),
    missions: Iterable[object] = (),
    intensity: float = 1.0,
) -> list[ExperiencedEvent]:
    """Helper : agrege ExperiencedEvent depuis des iterables d'events canon."""
    out: list[ExperiencedEvent] = []
    for ev in timeline_events:
        coerced = _coerce_canon_event(ev)
        if coerced is None:
            continue
        out.extend(experienced_events_from_timeline_event(
            coerced, intensity=intensity,
        ))
    for m in missions:
        coerced_m = _coerce_mission(m)
        if coerced_m is None:
            continue
        out.extend(experienced_events_from_mission(
            coerced_m, intensity=intensity,
        ))
    return out


__all__ = [
    "CanonEventLike",
    "MissionLike",
    "collect_experienced_events",
    "detect_category_from_text",
    "experienced_events_from_mission",
    "experienced_events_from_timeline_event",
]
