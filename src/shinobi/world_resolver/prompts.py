"""Prompts LLM pour Phase F - generation SubstituteEvent.

Spec doc 02 §8.2 + §14.6 (pas de hard-code de templates) :
le prompt guide la creativite mais ne pre-ecrit aucun outcome.
"""

from __future__ import annotations

from textwrap import dedent

SUBSTITUTE_EVENT_SYSTEM_PROMPT = dedent("""
    Tu es le resolveur creatif de la timeline Naruto. Un evenement canon
    n'a pas pu se produire (precondition violee par les actions du joueur
    ou par derive du monde). Ton role : generer l'evenement qui prend sa
    place naturellement dans cette branche divergente.

    REGLES STRICTES :
    - Tu reponds UNIQUEMENT en JSON conforme au schema fourni.
    - Tu n'inventes PAS de personnages ou techniques hors canon.
    - Pas de retour magique d'un personnage mort.
    - Le substitut doit etre une CONSEQUENCE LOGIQUE des conditions actuelles
      du monde (qui est vivant, qui est leader, quels evenements ont eu lieu).
    - Outcomes : au moins 1 outcome plausible. Types valides : character_death,
      character_acquired_power, hokage_succession, relationship_formed,
      war_started, character_becomes_missing_nin, war_ended, character_traumatized,
      village_damaged, lineage_founded, character_apparent_death,
      organization_founded, character_born, character_marked, title_granted,
      sharingan_transplanted, jinchuuriki_transfer, team_formed,
      character_trained, character_revelation, character_redeemed, era_started,
      kyuubi_bound, clan_displaced, tension_increased, alliance_formed.
    - Preconditions : types valides UNIQUEMENT : character_alive (parameters:
      {character_id}), no_event_triggered (parameters: {event_id}), clan_active
      (parameters: {clan_id}), jinchuuriki_held_by (parameters: {beast,
      jinchuuriki_id}). Tout autre type est silencieusement ignore par l'engine.
    - cancellation_strategy_type :
        * 'substitute' : un autre event prend la place
        * 'cascade_cancel' : effet domino, autres events futurs annules
        * 'silent_cancel' : pas de remplacement (rare ; preferer un substitut)
        * 'delay' : event repousse a une date ulterieure
    - id_suffix : court, lowercase, snake_case (ex: 'fugaku_negociation_year9').
      Le prefixe 'substitute_' est ajoute automatiquement.
    - rumor_template : phrase courte qui se propagera dans le monde
      (optionnel mais souvent utile pour la diffusion).
    - Pas d'em dash, pas d'emoji, francais sans abreviations.

    Exemples d'evenements de divergence canon credibles :
    - Si le massacre Uchiha est annule (Itachi expose le complot) :
      le clan se restructure mais Danzo perd son influence.
    - Si Naruto meurt jeune : Konohamaru reprend le destin du jinchuriki Kyuubi.
    - Si Sasuke ne deserte pas : il devient anbu sous Kakashi.

    Tu reponds en JSON conforme au schema.
""").strip()


def build_substitute_user_message(
    *,
    cancelled_event_id: str,
    cancelled_event_name: str,
    cancelled_event_year: int,
    cancellation_reason: str,
    current_year: int,
    world_state_summary: str,
    kg_recent_facts: str,
    enriched_narrative_invariants: list[str] | None = None,
    enriched_alternative_seeds: list[str] | None = None,
    divergence_severity: str | None = None,
    divergence_why_pivotal: str | None = None,
    divergence_consequences: list[str] | None = None,
) -> str:
    """Compose le message user complet pour le LLM substitute generator.

    Spec §8.2 : LLM doit recevoir cancelled + state actuel + KG recent
    pour decider d'un substitut plausible.

    Phase H wiring 9.1 : `enriched_narrative_invariants` (themes que le
    substitut DOIT respecter) et `enriched_alternative_seeds` (variantes
    deja identifiees par l'extraction canon) sont injectes dans le prompt
    pour que le LLM ait des points d'ancrage canoniques au lieu d'inventer.
    Les 2 args sont optionnels - si None ou vides, le block est skip.

    Phase H wiring 9.4 : si l'event annule est un divergence_point canon
    (~21 events critiques), `divergence_severity` ('fundamental', 'very_high',
    'high'), `divergence_why_pivotal` (1 phrase) et `divergence_consequences`
    (liste de cascades) sont injectes en bloc d'avertissement. Sans ce signal,
    le LLM traite tous les events egalement et peut produire un substitut
    insipide pour un pivot critique.
    """
    base = dedent(f"""
        [EVENEMENT CANON ANNULE]
        id : {cancelled_event_id}
        nom : {cancelled_event_name}
        annee canon prevue : {cancelled_event_year}
        annee in-game courante : {current_year}
        raison de l'annulation : {cancellation_reason}

        [ETAT DU MONDE A CETTE DATE]
        {world_state_summary}

        [FAITS RECENTS DU KG (derive de la branche joueur)]
        {kg_recent_facts}
    """).strip()

    if divergence_severity or divergence_why_pivotal:
        # Phase H 9.4 : avertit le LLM que l'event annule est un pivot
        # canon. Place avant les invariants pour priorite tonale.
        sev_label = (divergence_severity or "high").upper()
        cascade_lines = ""
        if divergence_consequences:
            cascade_lines = "\n  - " + "\n  - ".join(
                divergence_consequences[:5]
            )
        why_text = (divergence_why_pivotal or "").strip()
        base += (
            f"\n\n[ATTENTION - POINT DE DIVERGENCE CANON {sev_label}]\n"
            "L'evenement annule est un PIVOT NARRATIF crucial du canon "
            "Naruto. Ton substitut doit etre proportionnel a son importance "
            "(pas un evenement insipide).\n"
            f"Pourquoi ce pivot : {why_text}"
        )
        if cascade_lines:
            base += (
                "\nConsequences canon attendues si l'evenement est altere :"
                f"{cascade_lines}"
            )

    if enriched_narrative_invariants:
        # Cap a 5 invariants pour eviter prompt bloat
        inv_lines = "\n".join(
            f"  - {inv}" for inv in enriched_narrative_invariants[:5]
        )
        base += (
            "\n\n[INVARIANTS NARRATIFS A RESPECTER]\n"
            "Ces themes sont au cur de l'evenement canon. Ton substitut DOIT "
            "les preserver (sinon l'arc Naruto est rompu) :\n"
            f"{inv_lines}"
        )

    if enriched_alternative_seeds:
        # Cap a 3 seeds pour rester compact
        seed_lines = "\n".join(
            f"  {i+1}. {seed}"
            for i, seed in enumerate(enriched_alternative_seeds[:3])
        )
        base += (
            "\n\n[VARIANTES CANONIQUES DEJA IDENTIFIEES]\n"
            "Le canon a deja imagine ces alternatives (= bons points de "
            "depart, libre a toi de les adapter au contexte joueur) :\n"
            f"{seed_lines}"
        )

    base += dedent("""

        [INSTRUCTION]
        Genere le SubstituteEvent qui prend la place de cet evenement
        annule, conforme au schema JSON fourni. Outcomes au minimum 1.
        Justifie via narrative_summary_fr.
    """).rstrip()
    return base


__all__ = [
    "SUBSTITUTE_EVENT_SYSTEM_PROMPT",
    "build_substitute_user_message",
]
