"""NudgeBuilder : assemble le contexte 'directives narratives' pour le LLM.

Spec doc 02 §7.2 : le Director influence les agents via des nudges passes
en contexte LLM, PAS d'ordres directs. Le narrator + les agents lisent
ce nudge et decident comment l'incarner.

Structure du nudge texte (passe au prompt LLM) :

    [DIRECTIVES NARRATIVES / DIRECTOR]
    Acts actifs (cette periode) :
      - {act.description_fr} (urgence: {urgency}, deadline: year {end})
      - ...

    Invariants Naruto a respecter :
      - {invariant.principle_fr}
      - ...

    Contexte recent (compaction) :
    {recent_summary}

    [FIN DIRECTIVES]

Le texte est en francais (langue de l'interface joueur). Capacite max
~1200 chars pour ne pas exploser le prompt. Acts top-3 + invariants top-3
+ summary tronque a 600 chars.
"""

from __future__ import annotations

from shinobi.director.types import AbstractAct, NarrativeInvariant, NudgeContext


_MAX_ACTS_IN_NUDGE = 3
_MAX_ACT_DESCRIPTION_CHARS = 200  # cap par act_description (canon ~300+ chars)
_MAX_INVARIANTS_IN_NUDGE = 3
_MAX_INVARIANT_PRINCIPLE_CHARS = 150  # cap par invariant
_MAX_SUMMARY_CHARS = 400  # reduit de 600 a 400 pour laisser place aux patterns
_MAX_PATTERNS_IN_NUDGE = 2  # Phase H wiring : 2 patterns max pour eviter bloat
_MAX_PATTERN_EXAMPLE_CHARS = 120  # cap par exemple canon dans le nudge
_MAX_NUDGE_TOTAL_CHARS = 1200


def build_nudge_text(
    nudge: NudgeContext,
    *,
    narrative_patterns: list[dict] | None = None,
) -> str:
    """Convertit un NudgeContext en string de prompt LLM.

    Defensive : si nudge vide, retourne string vide (pas de marker
    [DIRECTIVES] inutile dans le prompt).

    Phase H wiring : `narrative_patterns` peut etre passe explicitement
    OR lu depuis `nudge.narrative_patterns` (set par Director.tick).
    L'argument explicit override le field si fourni.
    """
    # Resolve patterns : argument > nudge field > empty
    effective_patterns = narrative_patterns
    if effective_patterns is None:
        effective_patterns = list(nudge.narrative_patterns or [])
    has_acts = bool(nudge.active_acts)
    has_invariants = bool(nudge.active_invariants)
    has_summary = bool(nudge.recent_summary and nudge.recent_summary.strip())
    has_patterns = bool(effective_patterns)

    if not (has_acts or has_invariants or has_summary or has_patterns):
        return ""

    lines: list[str] = ["[DIRECTIVES NARRATIVES / DIRECTOR]"]

    # Phase H wiring 9.5 : section "Style Kishimoto" placee EN PREMIER.
    # Ordre revise apres avoir constate que sur du canon riche (3 acts a
    # 200 chars + 3 invariants a 150 chars + summary 400 chars + patterns
    # 200 chars) on hit le cap 1200 et les patterns - tout en bas - se font
    # systematiquement tronquer. Patterns FIRST = directive de ton inviolable.
    # Cap a 2 patterns pour eviter prompt bloat. On prend les 2 premiers
    # (deja tries par pertinence par le caller, ou ordre canon par defaut).
    if has_patterns:
        lines.append("Style Kishimoto a respecter :")
        for pattern in (effective_patterns or [])[:_MAX_PATTERNS_IN_NUDGE]:
            if not isinstance(pattern, dict):
                continue
            title = pattern.get("title_fr")
            desc = pattern.get("description_fr")
            if not title or not desc:
                continue
            # Cap description a 150 chars pour rester compact en haut.
            short_desc = desc[:150] + ("..." if len(desc) > 150 else "")
            lines.append(f"  - {title} : {short_desc}")
            # Phase H 9.5 : when_to_apply_fr precise QUAND utiliser le pattern.
            # Sans cette ligne, le LLM avait le pattern mais pas le contexte
            # d'application. Cap a 130 chars + indent pour visibilite.
            when = pattern.get("when_to_apply_fr")
            if isinstance(when, str) and when:
                short_when = when[:130] + ("..." if len(when) > 130 else "")
                lines.append(f"    Quand : {short_when}")
            # Phase H 9.5 (suite) : canon_examples ancre le pattern dans des
            # cas concrets de l'oeuvre. Sans exemple, le pattern reste abstrait
            # ; avec 1 exemple canonique, le LLM peut l'imiter directement.
            # 1 exemple par pattern (cap _MAX_PATTERN_EXAMPLE_CHARS) pour ne
            # pas faire exploser le block.
            examples = pattern.get("canon_examples")
            if isinstance(examples, list) and examples:
                first = next(
                    (e for e in examples if isinstance(e, str) and e), None,
                )
                if first:
                    short_ex = first[:_MAX_PATTERN_EXAMPLE_CHARS] + (
                        "..." if len(first) > _MAX_PATTERN_EXAMPLE_CHARS else ""
                    )
                    lines.append(f"    Ex. canon : {short_ex}")
        lines.append("")  # blank separator

    if has_acts:
        lines.append("Acts actifs (cette periode) :")
        # Top par urgency desc, tronque a 3
        sorted_acts = sorted(nudge.active_acts, key=lambda a: -a.urgency)
        for act in sorted_acts[:_MAX_ACTS_IN_NUDGE]:
            # Cap description per-act pour laisser place aux patterns 9.5.
            desc = act.description_fr
            if len(desc) > _MAX_ACT_DESCRIPTION_CHARS:
                desc = desc[:_MAX_ACT_DESCRIPTION_CHARS - 3] + "..."
            lines.append(
                f"  - {desc} "
                f"(urgence={act.urgency:.2f}, deadline year {act.target_year_end})"
            )

    if has_invariants:
        lines.append("")  # blank line separator
        lines.append("Invariants Naruto a respecter :")
        # Top par weight desc, tronque a 3
        sorted_invs = sorted(
            nudge.active_invariants, key=lambda i: -i.weight,
        )
        for inv in sorted_invs[:_MAX_INVARIANTS_IN_NUDGE]:
            principle = inv.principle_fr
            if len(principle) > _MAX_INVARIANT_PRINCIPLE_CHARS:
                principle = principle[:_MAX_INVARIANT_PRINCIPLE_CHARS - 3] + "..."
            lines.append(f"  - {principle}")

    if has_summary:
        lines.append("")
        lines.append("Contexte recent (compaction) :")
        summary_text = (nudge.recent_summary or "").strip()
        if len(summary_text) > _MAX_SUMMARY_CHARS:
            summary_text = summary_text[:_MAX_SUMMARY_CHARS] + "..."
        lines.append(summary_text)

    lines.append("[FIN DIRECTIVES]")
    out = "\n".join(lines)

    # Hard cap final pour eviter prompt blowup.
    # Round G13 : la version avant comptait `out[:cap-4] + "..."` puis
    # appendait `"\n[FIN DIRECTIVES tronquee]"` (26 chars) -> le total
    # depassait le cap de 25 chars. Maintenant : reserve les 26 chars du
    # suffixe tronquee + 3 chars du "..." dans le calcul du slice.
    if len(out) > _MAX_NUDGE_TOTAL_CHARS:
        truncation_suffix = "...\n[FIN DIRECTIVES tronquee]"
        keep_chars = _MAX_NUDGE_TOTAL_CHARS - len(truncation_suffix)
        out = out[:keep_chars] + truncation_suffix
    return out


def build_nudge(
    *,
    active_acts: list[AbstractAct],
    active_invariants: list[NarrativeInvariant],
    recent_summary: str | None,
    current_year: int,
    narrative_patterns: list[dict] | None = None,
) -> NudgeContext:
    """Construit un NudgeContext immuable.

    Phase H wiring : narrative_patterns (depuis CanonBundle 9.5) inclu
    dans le NudgeContext pour que les callers (CLI, agents) puissent les
    afficher via build_nudge_text sans avoir a les chercher ailleurs.
    """
    return NudgeContext(
        active_acts=active_acts[:10],
        active_invariants=active_invariants[:10],
        recent_summary=recent_summary,
        composed_at_year=current_year,
        narrative_patterns=(narrative_patterns or [])[:3],
    )


def build_director_nudge_text(
    *,
    canon,  # type: shinobi.canon.models.CanonBundle | None
    director_state,  # type: shinobi.director.scheduler.DirectorState | None
    current_year: int,
) -> str:
    """Helper unique pour composer le nudge Director hors-tick.

    Phase G+H wiring : avant ce helper, 3 call sites CLI duplique le path
    `active_acts -> contexts -> select_relevant_invariants/patterns ->
    build_nudge -> build_nudge_text` (main loop pre-narration, FF init,
    FF refresh). Chaque call site etait une opportunite de drift entre eux.

    Returns "" si pas de director_state, pas d'acts actifs, ou crash interne
    (defensive : la narration / l'agent doit pouvoir tourner sans Director).
    """
    if director_state is None:
        return ""
    try:
        active_acts = list(director_state.active_acts.values())
    except Exception:  # noqa: BLE001
        # Defensive : tolere n'importe quel state corrompu / mock-broken.
        return ""
    if not active_acts:
        return ""
    try:
        # Imports lazy pour eviter cyclic dependency avec director.core /
        # director.invariants au top-level de nudge_builder.
        from shinobi.director.core import (
            _contexts_from_acts,
            _enrich_contexts_with_fr,
        )
        from shinobi.director.invariants import (
            select_relevant_invariants,
            select_relevant_patterns,
        )
        raw_contexts = _contexts_from_acts(active_acts)
        fr_contexts = _enrich_contexts_with_fr(raw_contexts)
        invariants_contexts = (
            raw_contexts or [a.id for a in active_acts][:3]
        )
        invariants = select_relevant_invariants(
            invariants_contexts, max_invariants=3,
        )
        all_patterns = []
        if canon is not None:
            np_dict = getattr(canon, "narrative_patterns", None) or {}
            all_patterns = np_dict.get("patterns", []) or []
        relevant_patterns = select_relevant_patterns(
            all_patterns, contexts=fr_contexts, max_patterns=3,
        )
        nudge_ctx = build_nudge(
            active_acts=active_acts,
            active_invariants=invariants,
            recent_summary=getattr(director_state, "last_summary", None),
            current_year=current_year,
            narrative_patterns=relevant_patterns,
        )
        return build_nudge_text(nudge_ctx) or ""
    except Exception as exc:  # noqa: BLE001
        # Audit anti-silent : log au lieu de pass nu. Le bug de signature
        # `select_relevant_invariants(tensions=[], limit=3)` etait swallowed
        # ici avant cette ligne, le caller voyait juste "" sans diagnostic.
        from shinobi.logging_setup import get_logger as _glog
        _glog(__name__).warning(
            "build_director_nudge_text_failed",
            error=type(exc).__name__,
            msg=str(exc)[:200],
        )
        return ""


__all__ = [
    "build_director_nudge_text",
    "build_nudge",
    "build_nudge_text",
]
