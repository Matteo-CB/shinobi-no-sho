"""AbstractActComposer : transforme TensionList en list[AbstractAct].

Spec doc 02 §7.2 : le Director ne dicte pas qui fait quoi. Il compose
des actes abstraits (StoryVerse 2024) qui orientent la tension globale.

Round G19 : import logger pour tracer les Pydantic ValidationError lors
de la construction d'AbstractAct (avant : silent skip).

Exemple :
- Input  : Tension(type=alliance_breakdown, score=0.8, entities=[konoha, suna])
- Output : AbstractAct(description='Les frictions Konoha-Suna doivent
           s'amplifier vers un test diplomatique dans les 6 prochains mois')

Implementation deterministe, pas de LLM. Mappings tension_type -> template
narratif. Le composer ne genere PAS de texte libre ; il fixe une direction
structuree que le narrator interpretera.

Algorithme :
1. Filtre TensionList : top-N par score (eviter spam d'acts)
2. Map tension_type -> template description_fr
3. Calcule fenetre temporelle (urgency-aware : critical = 3 mois,
   high = 6 mois, medium/low = 12 mois)
4. Dedup par signature (type + entities) pour eviter actes redondants
   sur tensions repetees du meme detecteur tick a tick
"""

from __future__ import annotations

from shinobi.director.types import (
    MONTH_MAX,
    MONTH_MIN,
    YEAR_MAX,
    AbstractAct,
)
from shinobi.logging_setup import get_logger
from shinobi.tension.types import Tension, TensionList, TensionSeverity, TensionType

logger = get_logger(__name__)


# Templates description_fr par TensionType. Le {entities} et {year}
# sont substitues dynamiquement.
_ACT_TEMPLATES: dict[TensionType, str] = {
    TensionType.power_vacuum: (
        "Le vide de pouvoir autour de {entities} doit s'incarner en lutte "
        "de succession ou en montee d'un nouveau leader. Le narrator donne "
        "voix aux pretendants ; les agents s'aligneront ou s'opposeront."
    ),
    TensionType.border_conflict: (
        "Les frictions geographiques entre {entities} doivent escalader "
        "vers incident diplomatique ou armed clash dans la fenetre cible. "
        "Le narrator amplifie les rumeurs frontalières."
    ),
    TensionType.succession_dispute: (
        "La querelle de succession touchant {entities} doit basculer : "
        "soit revelation publique du complot, soit consolidation tacite. "
        "Eviter le statu quo passe la deadline."
    ),
    TensionType.alliance_breakdown: (
        "L'alliance fragile entre {entities} doit fracturer ou se "
        "consolider visiblement. Le narrator force un moment de verite."
    ),
    TensionType.clan_extinction_threat: (
        "Le risque d'extinction qui pese sur {entities} doit produire soit "
        "un acte desespere des derniers porteurs, soit une intervention "
        "providentielle (canon ou divergente)."
    ),
    TensionType.bloodline_unresolved: (
        "Le lien de sang non resolu autour de {entities} doit affleurer "
        "publiquement ou rester secret a un cout narratif visible."
    ),
    TensionType.factional_revenge: (
        "La faction lesee ({entities}) doit poser un acte de vengeance "
        "concret ou se diviser sur la strategie a adopter."
    ),
    TensionType.obsessive_npc_idle: (
        "Le PNJ {entities} obsede mais inactif doit franchir le seuil : "
        "passer a l'acte ou abandonner publiquement son obsession."
    ),
    TensionType.lone_survivor_obsessed: (
        "Le dernier survivant ({entities}) doit etre confronte a un choix "
        "narratif : cycle de haine ou rupture par lien humain."
    ),
    TensionType.student_surpasses_master: (
        "Le moment de surpassement entre {entities} doit etre acte "
        "(combat, revelation, transmission). Le maitre doit reconnaitre "
        "ou refuser, jamais ignorer."
    ),
    TensionType.cursed_hatred: (
        "La haine cumulative de {entities} doit trouver soit un exutoire "
        "destructeur, soit une rupture par dialogue avec un personnage "
        "qui partage la blessure."
    ),
    TensionType.jinchuuriki_unprotected: (
        "Le jinchuuriki {entities} doit etre cible ou sauve : aucun statu "
        "quo possible. Le narrator amplifie le risque pour forcer la "
        "decision des agents (Akatsuki, Konoha, etc.)."
    ),
    TensionType.tailed_beast_uncontrolled: (
        "Le tailed beast non controle ({entities}) doit produire un "
        "incident visible : dechainement partiel, sceau a renforcer, ou "
        "bond avec son hote."
    ),
    TensionType.forbidden_jutsu_threat: (
        "La technique interdite liee a {entities} doit etre soit utilisee "
        "ouvertement (revelation), soit prevenue par enquete / sceau."
    ),
    TensionType.kekkei_carrier_isolated: (
        "Le porteur isole ({entities}) doit recevoir une opportunite de "
        "reconnexion ou perir narrativement. Le narrator force le choix."
    ),
    TensionType.hidden_truth_pending: (
        "La verite cachee ({entities}) doit s'eroder : indice public, "
        "fuite, ou aveu force. Aucun secret ne tient indefiniment."
    ),
    TensionType.chekhovs_gun_unfired: (
        "L'element introduit ({entities}) doit etre 'fire' : reapparition "
        "narrative qui paie sa premiere mention canon. Pattern Kishimoto."
    ),
    TensionType.prophecy_unfulfilled: (
        "La prophetie liee a {entities} doit avancer : prediction "
        "confirmee partiellement ou refutee de maniere spectaculaire."
    ),
    TensionType.death_anniversary: (
        "L'anniversaire de mort ({entities}) doit etre marque : "
        "souvenir public, drift de personnalite, ou flashback narratif."
    ),
    TensionType.canon_event_pending: (
        "L'event canon attendu ({entities}) approche sa date. Le narrator "
        "prepare l'atmosphere ; les agents convergent vers leur role."
    ),
    TensionType.other: (
        "Le fil narratif identifie autour de {entities} doit progresser. "
        "Le narrator amplifie ou resoud selon le contexte joueur."
    ),
}


# Fenetre temporelle par severity (en mois in-game).
_URGENCY_WINDOW_MONTHS: dict[TensionSeverity, int] = {
    TensionSeverity.critical: 3,    # tres urgent : 3 mois
    TensionSeverity.high: 6,        # urgent : 6 mois
    TensionSeverity.medium: 12,     # moyen terme : 12 mois
    TensionSeverity.low: 24,        # long terme : 2 ans
}


def _act_id_from_tension(tension: Tension, current_year: int) -> str:
    """Genere un id deterministe stable inter-ticks.

    Format : `act_{type}_{first_entity}`. Sanitize ASCII snake_case
    (R7 generator Phase F). Si pas d'involved_entities, fallback
    `act_{type}_unscoped` pour rester unique au sein du type.

    Round G1 : avant, l'id incluait `current_year` -> meme tension sur 2
    ticks (year 10 puis year 11) produisait 2 acts (..._10, ..._11), tous
    deux inseres car ids differents. Maintenant id stable : merge_with_existing
    detecte la collision et garde l'existing (preserve status, evite dup).
    `current_year` reste un argument pour compat futur (id-version, salting).
    """
    import unicodedata
    del current_year  # currently unused, garde le parametre pour compat

    parts: list[str] = [tension.type.value]
    if tension.involved_entities:
        first = tension.involved_entities[0].lower()
        normalized = unicodedata.normalize("NFKD", first)
        ascii_only = normalized.encode("ascii", errors="ignore").decode("ascii")
        safe = "".join(c for c in ascii_only if c.isalnum() or c == "_")
        if safe:
            parts.append(safe[:30])
        else:
            parts.append("unscoped")
    else:
        parts.append("unscoped")
    return f"act_{'_'.join(parts)}"


def compose_acts(
    tensions: TensionList,
    *,
    current_year: int,
    top_n: int = 5,
    min_score: float = 0.5,
    divergence_event_ids: set[str] | None = None,
) -> list[AbstractAct]:
    """Compose abstract acts depuis une TensionList.

    Args:
        tensions: output du tension scheduler (deterministic + LLM merged)
        current_year: annee in-game courante (pour fenetre temporelle)
        top_n: max acts a generer (eviter spam dans le contexte LLM)
        min_score: seuil bas (low severity = score 0.25 -> ignore par defaut)
        divergence_event_ids: optionnel, set d'event_id consideres comme
            charnieres canon (Phase H 9.4). Si une tension est rattachee
            a un event charniere via involved_entities matching ou via
            son description mentionnant l'event_id, urgency boost x1.3.
            Cap urgency a 1.0 pour respecter les bornes Pydantic.

    Returns:
        list[AbstractAct] tries par urgency decroissante. Dedupliques par
        (type, first_entity) signature pour eviter les redondances tick-a-tick.
    """
    divergence_event_ids = divergence_event_ids or set()
    # 1. Top-N + filtre seuil
    candidates = [
        t for t in tensions.top(top_n * 2)  # over-fetch pour permettre dedup
        if t.score >= min_score
    ]
    if not candidates:
        return []

    # 2. Map vers AbstractAct + dedup
    seen_signatures: set[tuple[str, str]] = set()
    acts: list[AbstractAct] = []
    for t in candidates:
        first_entity = t.involved_entities[0] if t.involved_entities else "unknown"
        signature = (t.type.value, first_entity)
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)

        template = _ACT_TEMPLATES.get(t.type, _ACT_TEMPLATES[TensionType.other])
        entities_str = ", ".join(t.involved_entities[:3]) or "(non specifies)"
        description = template.format(entities=entities_str)

        window_months = _URGENCY_WINDOW_MONTHS[t.severity]
        # Round G16 : conversion mois -> years offset, sans `max(1, ...)`.
        # Round G31 : granularite mois ajoutee. window_months est interprete
        # exactement : critical=3 mois -> deadline (year, month+3) avec
        # carry sur l'annee si overflow. Plus de collapse severity -> meme
        # year offset.
        # Calcul tuple (year, month) target_end :
        # current_month implicite = 1 (debut d'annee, conservatif). Caller
        # passe current_year mais pas current_month a compose_acts ; on
        # assume 1.
        target_year_end_calc = current_year + (window_months // 12)
        target_month_end_calc = MONTH_MIN + (window_months % 12)
        if target_month_end_calc > MONTH_MAX:
            target_year_end_calc += 1
            target_month_end_calc -= 12
        # Clamp dans les bornes Pydantic
        target_year_end_calc = min(target_year_end_calc, YEAR_MAX)
        target_month_end_calc = max(MONTH_MIN, min(target_month_end_calc, MONTH_MAX))
        target_end = target_year_end_calc

        # Le year dans l'id est current_year pour deterministic ID generation.
        act_id = _act_id_from_tension(t, current_year)

        # Phase H wiring : urgency boost si tension touche un divergence_point.
        # Heuristique : si l'une des involved_entities OR la description
        # mentionne un event_id de la liste charniere, x1.3 (cap a 1.0).
        urgency = t.score
        if divergence_event_ids:
            mentions_charniere = any(
                eid in (t.description or "")
                or eid in (t.involved_entities or [])
                for eid in divergence_event_ids
            )
            if mentions_charniere:
                urgency = min(1.0, urgency * 1.3)

        try:
            act = AbstractAct(
                id=act_id,
                description_fr=description,
                related_tension_types=[t.type.value],
                involved_entities=t.involved_entities[:20],
                target_year_start=current_year,
                target_year_end=target_end,
                target_month_start=MONTH_MIN,  # Round G31
                target_month_end=target_month_end_calc,  # Round G31
                urgency=urgency,
                source_tension_descriptions=[t.description[:200]],
                status="proposed",
                created_at_year=current_year,
            )
        except Exception as exc:  # noqa: BLE001
            # Defensive : si Pydantic refuse (ex: id sanitize a vide,
            # description trop courte/longue apres template format),
            # on skip plutot que de crash le tick Director.
            # Round G19 : log warning structure pour traceability au lieu
            # du silent skip. Avant, un act perdu etait invisible aux ops.
            # Le tuple `(ValueError, Exception)` etait redundant (Exception
            # est super-classe de ValueError) -> simplifie en Exception.
            logger.warning(
                "phase_g_compose_act_pydantic_failed",
                tension_type=t.type.value,
                act_id=act_id,
                error=type(exc).__name__,
                msg=str(exc)[:200],
            )
            continue
        acts.append(act)
        if len(acts) >= top_n:
            break

    # 3. Sort by urgency descending
    acts.sort(key=lambda a: -a.urgency)
    return acts


def merge_with_existing(
    new_acts: list[AbstractAct],
    existing: dict[str, AbstractAct],
    *,
    current_year: int,
) -> tuple[list[AbstractAct], list[AbstractAct], dict[str, AbstractAct]]:
    """Reconcilie nouveaux acts avec ceux deja actifs en DirectorState.

    Logique :
    - Acts existants avec target_year_end < current_year -> retired (expired)
    - Acts existants avec id deja en proposition -> garde la version existante
    - Acts nouveaux non en collision -> added

    Returns:
        (added, retired, updated_state) ou updated_state est le dict
        complet apres merge (active uniquement, pas les retired).
    """
    added: list[AbstractAct] = []
    retired: list[AbstractAct] = []
    updated: dict[str, AbstractAct] = {}

    # 1. Process existing : retire les expires
    for act_id, act in existing.items():
        if act.target_year_end < current_year:
            # Expire
            retired_act = act.model_copy(update={"status": "expired"})
            retired.append(retired_act)
        else:
            # Encore actif, on le promeut a 'active' s'il etait 'proposed'.
            if act.status == "proposed":
                updated[act_id] = act.model_copy(update={"status": "active"})
            else:
                updated[act_id] = act

    # 2. Process new : ajoute s'il n'est pas deja la
    for act in new_acts:
        if act.id in updated:
            # Round G5 : collision -> escalade urgency si la nouvelle est
            # plus haute. Avant : on gardait simplement l'existing -> si
            # une tension passe high (0.75) -> critical (1.0) entre 2 ticks,
            # l'urgency restait figee a 0.75 et la deadline aussi.
            # Maintenant : update urgency = max(old, new), target_year_end
            # aussi pris en max (escalade prolonge la window si critical
            # demande plus de temps... actually critical demande 3 mois,
            # high 6 mois, donc critical = window plus courte. Mais le
            # MAX preserve la window la plus genereuse, evitant qu'une
            # escalade reduise mecaniquement la deadline).
            existing_act = updated[act.id]
            if act.urgency > existing_act.urgency:
                updated[act.id] = existing_act.model_copy(update={
                    "urgency": act.urgency,
                    "target_year_end": max(
                        existing_act.target_year_end, act.target_year_end,
                    ),
                    # Preserve status (active) ; update source tension
                    # descriptions pour traceability de l'escalade.
                    "source_tension_descriptions": (
                        list(existing_act.source_tension_descriptions)
                        + [d for d in act.source_tension_descriptions
                           if d not in existing_act.source_tension_descriptions]
                    )[:10],
                })
            continue
        # Round G3 : skip si l'act est deja expire (target_year_end < current).
        # Cas defensif : compose_acts ne produit jamais ca (target_end =
        # current_year + 1 minimum), mais un caller direct ou un save
        # corrompu pourrait. Sans skip, l'act est ajoute puis retire au
        # tick suivant -> 1 cycle de visibilite dans le nudge avec deadline
        # passee -> narrator confus.
        if act.target_year_end < current_year:
            retired_act = act.model_copy(update={"status": "expired"})
            retired.append(retired_act)
            continue
        # Promote to 'active' immediately when added
        promoted = act.model_copy(update={"status": "active"})
        updated[act.id] = promoted
        added.append(promoted)

    return added, retired, updated


__all__ = ["compose_acts", "merge_with_existing"]
