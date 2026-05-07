"""20 invariants abstraits de physique sociale Naruto.

Chaque invariant est une fonction pure qui prend (KG store, current_year, ctx)
et retourne une liste de Tensions detectees. PAS DE LLM, deterministe, ms.

Ces invariants signalent des configurations critiques. Ils n'inventent JAMAIS
d'evenements (cf docs/02 §14.6). Le LLM analyste, le Director et les agents
multi-agent les exploiteront en aval.

Liste (cf docs/02 §5.3) :
 1. kage_absent_or_dead       - kage manquant dans un grand village
 2. jinchuuriki_unprotected   - bijuu hote sans protection geographique
 3. obsessive_npc_idle        - NPC obsessionnel passif (placeholder strict)
 4. wronged_faction_unrevenged - faction lesee sans vengeance
 5. power_vacuum              - absence de leader charismatique
 6. unresolved_blood_ties     - liens de sang non resolus
 7. clan_extinction_threat    - clan menace d'extinction
 8. tailed_beast_uncontrolled - bijuu sans jinchuriki actif
 9. wartime_alliance_unstable - alliance fragile (partage_ressources<0.4)
10. hidden_truth_about_to_surface - secret canon menace
11. death_anniversary         - anniversaire d'event majeur (modulo 5/10)
12. geographic_imbalance      - desequilibre village (forces/clans)
13. student_surpasses_master  - eleve deja plus puissant
14. prophecy_unfulfilled      - prophetie en suspens
15. cursed_hatred_rising      - haine cumulative dans clan/faction
16. kekkei_genkai_carrier_isolated - dernier porteur kekkei isole
17. forbidden_jutsu_threat    - kinjutsu actif en circulation
18. lone_survivor_obsessed    - dernier survivant focalise vengeance
19. border_dispute            - conflit frontalier non resolu
20. chekhovs_gun_unfired      - element introduit sans payoff

Chaque invariant a :
- name : str (slug)
- detect(store, current_year, ctx) -> list[Tension]
- severity_default : TensionSeverity (souvent overrideable selon contexte)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from shinobi.kg.store import KnowledgeGraphStore
from shinobi.tension.types import Tension, TensionSeverity, TensionType


@dataclass(frozen=True)
class TensionInvariant:
    """Definition d'un invariant : nom + fonction de detection."""

    name: str
    description_fr: str
    detect: Callable[[KnowledgeGraphStore, int, dict], list[Tension]]


# ============================================================================
# Helpers communs
# ============================================================================


def _facts_active_at(
    store: KnowledgeGraphStore, year: int,
    *, subject: str | None = None, relation: str | None = None,
    object_value: str | None = None,
) -> list:
    """Raccourci : facts actifs a year."""
    return store.get_facts(
        subject=subject, relation=relation, object_value=object_value,
        year=year,
    )


def _entities_of_type(store: KnowledgeGraphStore, type_value: str) -> list[str]:
    """Liste les ids des entites d'un type (ex: 'character', 'village', 'clan')."""
    facts = store.get_facts(relation="type", object_value=type_value)
    return sorted({f.subject for f in facts})


def _is_alive(store: KnowledgeGraphStore, npc_id: str, year: int) -> bool:
    """Verifie via le KG si l'NPC est vivant (pas de death_year < year)."""
    death = store.get_facts(subject=npc_id, relation="death_year", limit=1)
    if not death:
        return True  # pas d'info -> on suppose vivant
    try:
        return year < int(death[0].object or "9999")
    except (TypeError, ValueError):
        return True


# ============================================================================
# Les 20 invariants
# ============================================================================


def kage_absent_or_dead(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """1. Aucun kage en place dans un grand village."""
    out: list[Tension] = []
    great_villages = ctx.get(
        "great_villages",
        ["konohagakure", "sunagakure", "kirigakure", "kumogakure", "iwagakure"],
    )
    for village in great_villages:
        kage_facts = _facts_active_at(store, year, subject=village, relation="kage")
        if not kage_facts:
            out.append(Tension.from_severity(
                type=TensionType.power_vacuum,
                description=f"Le village {village} n'a aucun kage en fonction en l'an {year}.",
                severity=TensionSeverity.critical,
                involved_entities=[village],
                source_rule="kage_absent_or_dead",
                detected_at_year=year,
            ))
            continue
        # Si le kage est mort
        for f in kage_facts:
            kage_id = f.object
            if kage_id and not _is_alive(store, kage_id, year):
                out.append(Tension.from_severity(
                    type=TensionType.power_vacuum,
                    description=(
                        f"Le kage {kage_id} de {village} est mort, "
                        f"position vacante en l'an {year}."
                    ),
                    severity=TensionSeverity.high,
                    involved_entities=[village, kage_id],
                    source_rule="kage_absent_or_dead",
                    detected_at_year=year,
                ))
    return out


def jinchuuriki_unprotected(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """2. Un bijuu a un jinchuriki actif mais le village hote est faible /
    en guerre / a perdu son kage. Configuration ou les forces exterieures
    pourraient l'attaquer."""
    out: list[Tension] = []
    bijuus = _entities_of_type(store, "tailed_beast")
    for bijuu in bijuus:
        jin_facts = _facts_active_at(store, year, subject=bijuu, relation="current_jinchuriki")
        if not jin_facts:
            continue
        jin_id = jin_facts[0].object
        if not jin_id:
            continue
        # Trouver le village de l'hote
        village_facts = _facts_active_at(store, year, subject=jin_id, relation="village_of_origin")
        if not village_facts:
            continue
        village = village_facts[0].object
        if not village:
            continue
        # Si le village a un power_vacuum -> tension
        kage_in_place = _facts_active_at(store, year, subject=village, relation="kage")
        if not kage_in_place:
            out.append(Tension.from_severity(
                type=TensionType.jinchuuriki_unprotected,
                description=(
                    f"Le jinchuriki {jin_id} (bijuu {bijuu}) reside a "
                    f"{village} qui est sans kage. Cible vulnerable pour "
                    f"factions exterieures."
                ),
                severity=TensionSeverity.high,
                involved_entities=[bijuu, jin_id, village],
                source_rule="jinchuuriki_unprotected",
                detected_at_year=year,
            ))
    return out


def obsessive_npc_idle(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """3. NPC marque comme obsessionnel (deep_motivation = 'revenge' /
    'avenge' / 'kill_target') sans action recente.

    Strict : on detecte les facts (subject=npc, relation='deep_motivation',
    object contient 'revenge' ou 'avenge'). Si le NPC n'a aucun fact
    'last_action_year' >= year - 2, on flag.
    """
    out: list[Tension] = []
    candidates = ctx.get("obsessive_npcs", [])
    # Si pas de candidats explicites, scanne les facts deep_motivation
    if not candidates:
        motiv_facts = store.get_facts(relation="deep_motivation")
        candidates = sorted({
            f.subject for f in motiv_facts
            if f.object and any(kw in f.object.lower()
                                for kw in ("revenge", "avenge", "kill_target"))
        })
    for npc in candidates:
        if not _is_alive(store, npc, year):
            continue
        # Verifier last action (champ optionnel ; si absent, on flag par defaut)
        last_action = store.get_facts(subject=npc, relation="last_action_year", limit=1)
        recent = False
        if last_action and last_action[0].object:
            try:
                last_year = int(last_action[0].object)
                recent = year - last_year < 2
            except (TypeError, ValueError):
                pass
        if not recent:
            out.append(Tension.from_severity(
                type=TensionType.obsessive_npc_idle,
                description=(
                    f"{npc} est marque comme obsessionnel mais inactif "
                    f"en l'an {year}. Tension dramatique en suspens."
                ),
                severity=TensionSeverity.medium,
                involved_entities=[npc],
                source_rule="obsessive_npc_idle",
                detected_at_year=year,
            ))
    return out


def wronged_faction_unrevenged(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """4. Une faction (clan / village / organisation) marquee comme 'lesee'
    (relation 'wronged_by') sans 'revenge_completed'."""
    out: list[Tension] = []
    wronged = store.get_facts(relation="wronged_by")
    for f in wronged:
        faction = f.subject
        if not faction:
            continue
        revenged = store.get_facts(
            subject=faction, relation="revenge_completed",
            year=year,
        )
        if not revenged:
            out.append(Tension.from_severity(
                type=TensionType.factional_revenge,
                description=(
                    f"La faction {faction} a ete lesee par {f.object} "
                    f"sans vengeance accomplie au {year}."
                ),
                severity=TensionSeverity.high,
                involved_entities=[faction, f.object or ""],
                source_rule="wronged_faction_unrevenged",
                detected_at_year=year,
            ))
    return out


def power_vacuum_global(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """5. Absence d'une figure de leader 'world_authority' (Sage des 6 Chemins,
    Hokage charismatique, equivalent). Heuristique : aucun fact
    'world_authority' actif a year."""
    authorities = _facts_active_at(store, year, relation="world_authority")
    if not authorities:
        return [Tension.from_severity(
            type=TensionType.power_vacuum,
            description=(
                f"Aucune autorite mondiale charismatique reconnue en l'an {year}. "
                f"Le monde shinobi entre dans une periode d'instabilite."
            ),
            severity=TensionSeverity.medium,
            source_rule="power_vacuum_global",
            detected_at_year=year,
        )]
    return []


def unresolved_blood_ties(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """6. NPC vivant avec un fact 'blood_oath_with' OR 'unfinished_business_with'
    pointant vers un autre NPC vivant et non resolu."""
    out: list[Tension] = []
    for relation in ("blood_oath_with", "unfinished_business_with"):
        for f in store.get_facts(relation=relation):
            target = f.object
            if not target:
                continue
            if not _is_alive(store, f.subject, year):
                continue
            if not _is_alive(store, target, year):
                continue
            resolved = store.get_facts(
                subject=f.subject, relation=f"{relation}_resolved",
                object_value=target, year=year,
            )
            if not resolved:
                out.append(Tension.from_severity(
                    type=TensionType.bloodline_unresolved,
                    description=(
                        f"Lien de sang non resolu : {f.subject} -> {target} "
                        f"({relation}) en l'an {year}."
                    ),
                    severity=TensionSeverity.high,
                    involved_entities=[f.subject, target],
                    source_rule="unresolved_blood_ties",
                    detected_at_year=year,
                ))
    return out


def clan_extinction_threat(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """7. Un clan dont moins de 3 membres sont vivants au current_year."""
    out: list[Tension] = []
    clans = _entities_of_type(store, "clan")
    for clan in clans:
        # Membres = facts (npc, clan, <clan_id>) ou (clan, key_member, <npc>)
        members_facts = store.get_facts(relation="clan", object_value=clan)
        member_ids = {f.subject for f in members_facts}
        # Filtrage vivants
        alive = sum(1 for npc in member_ids if _is_alive(store, npc, year))
        if 0 < alive <= 2:
            out.append(Tension.from_severity(
                type=TensionType.clan_extinction_threat,
                description=(
                    f"Le clan {clan} a {alive} membre(s) vivant(s) en l'an "
                    f"{year}. Risque d'extinction."
                ),
                severity=TensionSeverity.high if alive == 1 else TensionSeverity.medium,
                involved_entities=[clan],
                source_rule="clan_extinction_threat",
                detected_at_year=year,
            ))
    return out


def tailed_beast_uncontrolled(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """8. Un bijuu sans jinchuriki actif a year."""
    out: list[Tension] = []
    bijuus = _entities_of_type(store, "tailed_beast")
    for bijuu in bijuus:
        jin_facts = _facts_active_at(store, year, subject=bijuu, relation="current_jinchuriki")
        if not jin_facts or not (jin_facts[0].object or "").strip():
            out.append(Tension.from_severity(
                type=TensionType.tailed_beast_uncontrolled,
                description=(
                    f"Le bijuu {bijuu} n'a pas de jinchuriki actif en "
                    f"l'an {year}. Force libre, convoitee."
                ),
                severity=TensionSeverity.high,
                involved_entities=[bijuu],
                source_rule="tailed_beast_uncontrolled",
                detected_at_year=year,
            ))
    return out


def wartime_alliance_unstable(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """9. Une alliance (relation 'allied_with') avec un partage_ressources < 0.4.

    Convention : la force de partage est encodee dans un fact compagnon
    `(<a>, alliance_resource_share_with_<b>, "<float>")` ou `<b>` est le
    partner. Permet d'avoir plusieurs alliances avec niveaux de partage
    differents pour un meme NPC.
    """
    out: list[Tension] = []
    alliances = _facts_active_at(store, year, relation="allied_with")
    for f in alliances:
        partner = f.object
        if not partner:
            continue
        # Cherche le fact compagnon par relation suffixee
        share_facts = store.get_facts(
            subject=f.subject,
            relation=f"alliance_resource_share_with_{partner}",
            year=year, limit=1,
        )
        if not share_facts:
            continue
        try:
            share = float(share_facts[0].object or "0")
        except (TypeError, ValueError):
            continue
        if share < 0.4:
            out.append(Tension.from_severity(
                type=TensionType.alliance_breakdown,
                description=(
                    f"Alliance fragile entre {f.subject} et {partner} "
                    f"(partage de ressources : {share:.2f})."
                ),
                severity=TensionSeverity.medium,
                involved_entities=[f.subject, partner],
                source_rule="wartime_alliance_unstable",
                detected_at_year=year,
            ))
    return out


def hidden_truth_about_to_surface(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """10. Un fact (relation = 'hidden_secret') dont known_by_npc_ids commence
    a contenir des NPCs hors du cercle initial."""
    out: list[Tension] = []
    secrets = store.get_facts(relation="hidden_secret")
    threshold = ctx.get("secret_leak_threshold", 3)
    for f in secrets:
        if len(f.known_by_npc_ids) >= threshold:
            out.append(Tension.from_severity(
                type=TensionType.hidden_truth_pending,
                description=(
                    f"Le secret '{f.object}' (sujet {f.subject}) est connu "
                    f"par {len(f.known_by_npc_ids)} NPCs. Risque de revelation."
                ),
                severity=TensionSeverity.high,
                involved_entities=[f.subject, *f.known_by_npc_ids[:5]],
                source_rule="hidden_truth_about_to_surface",
                detected_at_year=year,
            ))
    return out


def death_anniversary(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """11. Un perso est mort il y a exactement 5/10/20 ans : moment narrativement
    charge (anniversaire commemore par les vivants liees)."""
    out: list[Tension] = []
    deaths = store.get_facts(relation="death_year")
    cycles = ctx.get("anniversary_cycles", [5, 10, 20, 50])
    for f in deaths:
        try:
            dy = int(f.object or "0")
        except (TypeError, ValueError):
            continue
        delta = year - dy
        if delta in cycles:
            out.append(Tension.from_severity(
                type=TensionType.death_anniversary,
                description=(
                    f"Anniversaire {delta}e de la mort de {f.subject} "
                    f"(an {dy}). Moment narratif lourd pour les proches."
                ),
                severity=TensionSeverity.medium,
                involved_entities=[f.subject],
                source_rule="death_anniversary",
                detected_at_year=year,
            ))
    return out


def geographic_imbalance(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """12. Un village a >= 5 fois plus de membres connus qu'un village voisin
    de meme rang. Indicateur de disproportion politique."""
    out: list[Tension] = []
    villages = ctx.get(
        "great_villages",
        ["konohagakure", "sunagakure", "kirigakure", "kumogakure", "iwagakure"],
    )
    counts: dict[str, int] = {}
    for v in villages:
        members = store.get_facts(relation="village_of_origin", object_value=v)
        counts[v] = sum(1 for f in members if _is_alive(store, f.subject, year))
    if not counts or max(counts.values()) == 0:
        return out
    max_v = max(counts, key=lambda k: counts[k])
    for v, c in counts.items():
        if v == max_v or c == 0:
            continue
        if counts[max_v] >= 5 * c:
            out.append(Tension.from_severity(
                type=TensionType.border_conflict,
                description=(
                    f"Desequilibre geographique : {max_v} a {counts[max_v]} "
                    f"shinobi vivants vs {v} a {c}. Risque d'agression."
                ),
                severity=TensionSeverity.medium,
                involved_entities=[max_v, v],
                source_rule="geographic_imbalance",
                detected_at_year=year,
            ))
    return out


def student_surpasses_master(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """13. Pour chaque paire (master, student), si le student a un fact 'power_level'
    > celui du master (les deux vivants), tension."""
    out: list[Tension] = []
    pairs = store.get_facts(relation="student_of")
    for f in pairs:
        student = f.subject
        master = f.object
        if not (master and _is_alive(store, master, year)
                and _is_alive(store, student, year)):
            continue
        s_lvl_f = store.get_facts(subject=student, relation="power_level", limit=1)
        m_lvl_f = store.get_facts(subject=master, relation="power_level", limit=1)
        if not s_lvl_f or not m_lvl_f:
            continue
        try:
            s = float(s_lvl_f[0].object or "0")
            m = float(m_lvl_f[0].object or "0")
        except (TypeError, ValueError):
            continue
        if s > m and m > 0:
            out.append(Tension.from_severity(
                type=TensionType.student_surpasses_master,
                description=(
                    f"L'eleve {student} (lvl {s:.1f}) a depasse son maitre "
                    f"{master} (lvl {m:.1f}). Tension narrative."
                ),
                severity=TensionSeverity.medium,
                involved_entities=[student, master],
                source_rule="student_surpasses_master",
                detected_at_year=year,
            ))
    return out


def prophecy_unfulfilled(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """14. Facts (subject=prophecy, relation='deadline_year') deja passes
    sans 'fulfilled' a year."""
    out: list[Tension] = []
    deadlines = store.get_facts(relation="deadline_year")
    for f in deadlines:
        # Le sujet doit etre une prophetie
        type_facts = store.get_facts(subject=f.subject, relation="type", limit=1)
        if not type_facts or type_facts[0].object != "prophecy":
            continue
        try:
            deadline = int(f.object or "9999")
        except (TypeError, ValueError):
            continue
        if year < deadline:
            continue
        fulfilled = store.get_facts(
            subject=f.subject, relation="fulfilled", object_value="true",
        )
        if not fulfilled:
            out.append(Tension.from_severity(
                type=TensionType.prophecy_unfulfilled,
                description=(
                    f"Prophetie {f.subject} en suspens : deadline {deadline} "
                    f"depassee, accomplissement attendu."
                ),
                severity=TensionSeverity.high,
                involved_entities=[f.subject],
                source_rule="prophecy_unfulfilled",
                detected_at_year=year,
            ))
    return out


def cursed_hatred_rising(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """15. Un clan ou un perso avec un cumul d'evenements 'trauma' >= 3 sans
    'reconciliation'. Curse of Hatred Naruto (Uchiha typiquement)."""
    out: list[Tension] = []
    trauma_facts = store.get_facts(relation="trauma_event")
    counts: dict[str, int] = {}
    for f in trauma_facts:
        if f.valid_from_year is not None and f.valid_from_year > year:
            continue
        counts[f.subject] = counts.get(f.subject, 0) + 1
    for npc, n in counts.items():
        if n < 3:
            continue
        recon = store.get_facts(
            subject=npc, relation="reconciliation", year=year,
        )
        if recon:
            continue
        out.append(Tension.from_severity(
            type=TensionType.cursed_hatred,
            description=(
                f"{npc} a cumule {n} traumas sans reconciliation. "
                f"Risque de basculement vers la haine en l'an {year}."
            ),
            severity=TensionSeverity.high,
            involved_entities=[npc],
            source_rule="cursed_hatred_rising",
            detected_at_year=year,
        ))
    return out


def kekkei_genkai_carrier_isolated(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """16. Si un kekkei_genkai a 1 seul porteur vivant, tension (cible
    convoitee, perte potentielle de la lignee)."""
    out: list[Tension] = []
    kekkei = _entities_of_type(store, "kekkei_genkai") + _entities_of_type(store, "kekkei_mora")
    for kg in kekkei:
        carriers_facts = store.get_facts(relation="has_kekkei_genkai", object_value=kg)
        carrier_ids = {f.subject for f in carriers_facts}
        alive = [npc for npc in carrier_ids if _is_alive(store, npc, year)]
        if len(alive) == 1:
            out.append(Tension.from_severity(
                type=TensionType.kekkei_carrier_isolated,
                description=(
                    f"Le kekkei {kg} n'a plus qu'un seul porteur vivant : "
                    f"{alive[0]} en l'an {year}. Cible convoitee."
                ),
                severity=TensionSeverity.high,
                involved_entities=[kg, alive[0]],
                source_rule="kekkei_genkai_carrier_isolated",
                detected_at_year=year,
            ))
    return out


def forbidden_jutsu_threat(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """17. Un kinjutsu (technique forbidden) en circulation : au moins un
    NPC vivant qui le maitrise en l'an year."""
    out: list[Tension] = []
    forbidden = store.get_facts(relation="rank", object_value="forbidden")
    for f in forbidden:
        users = store.get_facts(subject=f.subject, relation="has_canonical_user")
        for u in users:
            if u.object and _is_alive(store, u.object, year):
                out.append(Tension.from_severity(
                    type=TensionType.forbidden_jutsu_threat,
                    description=(
                        f"Le kinjutsu {f.subject} est maitrise par {u.object} "
                        f"vivant en l'an {year}. Menace systemique."
                    ),
                    severity=TensionSeverity.medium,
                    involved_entities=[f.subject, u.object],
                    source_rule="forbidden_jutsu_threat",
                    detected_at_year=year,
                ))
                break  # une seule tension par kinjutsu
    return out


def lone_survivor_obsessed(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """18. Un perso etiquete 'lone_survivor_of' avec une 'deep_motivation' =
    revenge/avenge. Configuration explosive (ex: Sasuke post-massacre)."""
    out: list[Tension] = []
    survivors = store.get_facts(relation="lone_survivor_of")
    for f in survivors:
        npc = f.subject
        if not _is_alive(store, npc, year):
            continue
        motiv = store.get_facts(subject=npc, relation="deep_motivation", limit=1)
        if motiv and motiv[0].object and any(
            kw in motiv[0].object.lower() for kw in ("revenge", "avenge")
        ):
            out.append(Tension.from_severity(
                type=TensionType.lone_survivor_obsessed,
                description=(
                    f"{npc} est le seul survivant de {f.object} et porte "
                    f"une motivation de vengeance."
                ),
                severity=TensionSeverity.critical,
                involved_entities=[npc, f.object or ""],
                source_rule="lone_survivor_obsessed",
                detected_at_year=year,
            ))
    return out


def border_dispute(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """19. Deux villages voisins avec un fact 'border_dispute_with'."""
    out: list[Tension] = []
    seen: set[tuple[str, str]] = set()
    for f in _facts_active_at(store, year, relation="border_dispute_with"):
        a, b = f.subject, (f.object or "")
        pair = (a, b) if a < b else (b, a)
        if pair in seen:
            continue
        seen.add(pair)
        out.append(Tension.from_severity(
            type=TensionType.border_conflict,
            description=(
                f"Conflit frontalier non resolu entre {a} et {b} "
                f"en l'an {year}."
            ),
            severity=TensionSeverity.medium,
            involved_entities=[a, b],
            source_rule="border_dispute",
            detected_at_year=year,
        ))
    return out


def political_alliance_brittle_via_dead_leader(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """21. Phase H wiring 9.3 : alliance_breakdown depuis political_forces.

    Lit `ctx['political_forces']` (canon.political_forces, dataset 9.3) et
    detecte les paires de factions (A allies B) ou A.leader_id est mort
    avant `year` (dans canon.characters). Sans leader vivant, l'alliance
    est tres fragile -> tension `alliance_breakdown`.

    Defensive :
    - Skip si political_forces non fourni dans ctx (back-compat).
    - Skip si char_deaths non fourni (canon.characters indisponible).
    - Skip une faction si son `active_year_end` < year (dissoute).
    - Skip une faction si son `active_year_start` > year (pas encore active).
    - Cap output a 5 paires par tick pour ne pas inonder le top-N.

    Cette regle complete `wartime_alliance_unstable` (qui lit le KG via
    facts allied_with) en ajoutant un signal canon-level qui ne necessite
    pas que le KG ait des facts allied_with explicites.
    """
    political_forces = ctx.get("political_forces")
    if not isinstance(political_forces, dict):
        return []
    factions = political_forces.get("factions")
    if not isinstance(factions, list):
        return []
    char_deaths = ctx.get("char_deaths")
    if not isinstance(char_deaths, dict):
        return []

    by_id: dict[str, dict] = {}
    for fac in factions:
        if isinstance(fac, dict) and isinstance(fac.get("id"), str):
            by_id[fac["id"]] = fac

    def _is_active(fac: dict) -> bool:
        start = fac.get("active_year_start")
        end = fac.get("active_year_end")
        if isinstance(start, int) and start > year:
            return False
        if isinstance(end, int) and end < year:
            return False
        return True

    out: list[Tension] = []
    for fac in factions:
        if not isinstance(fac, dict) or not _is_active(fac):
            continue
        leader_id = fac.get("leader_id")
        if not isinstance(leader_id, str) or not leader_id:
            continue
        leader_death = char_deaths.get(leader_id)
        if not isinstance(leader_death, int) or leader_death >= year:
            continue
        # Leader mort avant year -> faction sans tete
        allies = fac.get("allies")
        if not isinstance(allies, list) or not allies:
            continue
        for ally_id in allies:
            if not isinstance(ally_id, str):
                continue
            ally_fac = by_id.get(ally_id)
            if ally_fac is None or not _is_active(ally_fac):
                continue
            out.append(Tension.from_severity(
                type=TensionType.alliance_breakdown,
                description=(
                    f"L'alliance entre {fac['id']} et {ally_id} est fragile : "
                    f"{fac['id']} a perdu son leader {leader_id} en {leader_death}, "
                    f"sans successeur designe."
                ),
                severity=TensionSeverity.medium,
                involved_entities=[fac["id"], ally_id, leader_id],
                source_rule="political_alliance_brittle_via_dead_leader",
                detected_at_year=year,
            ))
            if len(out) >= 5:
                return out
    return out


def political_faction_isolated_with_active_enemies(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """22. Phase H wiring 9.3 (suite) : faction avec leader mort ET au moins
    2 ennemis actifs simultanement -> tension `factional_revenge`.

    `political_alliance_brittle_via_dead_leader` regarde le cote allies. Cette
    regle complementaire regarde le cote enemies : si une faction a perdu sa
    tete et qu'elle compte plusieurs ennemis encore actifs, elle est exposee
    a une attaque coordonnee.

    Skip systematique si political_forces ou char_deaths absents (back-compat).
    Cap output a 5 factions par tick.
    """
    political_forces = ctx.get("political_forces")
    if not isinstance(political_forces, dict):
        return []
    factions = political_forces.get("factions")
    if not isinstance(factions, list):
        return []
    char_deaths = ctx.get("char_deaths")
    if not isinstance(char_deaths, dict):
        return []

    by_id: dict[str, dict] = {}
    for fac in factions:
        if isinstance(fac, dict) and isinstance(fac.get("id"), str):
            by_id[fac["id"]] = fac

    def _is_active(fac: dict) -> bool:
        start = fac.get("active_year_start")
        end = fac.get("active_year_end")
        if isinstance(start, int) and start > year:
            return False
        if isinstance(end, int) and end < year:
            return False
        return True

    out: list[Tension] = []
    for fac in factions:
        if not isinstance(fac, dict) or not _is_active(fac):
            continue
        leader_id = fac.get("leader_id")
        if not isinstance(leader_id, str) or not leader_id:
            continue
        leader_death = char_deaths.get(leader_id)
        if not isinstance(leader_death, int) or leader_death >= year:
            continue
        enemies = fac.get("enemies")
        if not isinstance(enemies, list):
            continue
        active_enemies: list[str] = []
        for eid in enemies:
            if not isinstance(eid, str):
                continue
            ef = by_id.get(eid)
            if ef is not None and _is_active(ef):
                active_enemies.append(eid)
        if len(active_enemies) < 2:
            continue
        out.append(Tension.from_severity(
            type=TensionType.factional_revenge,
            description=(
                f"La faction {fac['id']} a perdu son leader {leader_id} en "
                f"{leader_death} et fait face a {len(active_enemies)} ennemis "
                f"encore actifs ({', '.join(active_enemies[:3])}). "
                f"Vulnerabilite a une attaque coordonnee."
            ),
            severity=TensionSeverity.high,
            involved_entities=[fac["id"], leader_id, *active_enemies[:3]],
            source_rule="political_faction_isolated_with_active_enemies",
            detected_at_year=year,
        ))
        if len(out) >= 5:
            return out
    return out


def chekhovs_gun_unfired(
    store: KnowledgeGraphStore, year: int, ctx: dict,
) -> list[Tension]:
    """20. Un fact (relation = 'chekhovs_gun') introduit dans le passe sans
    'fired' ulterieur. Element pose qui doit etre paye dramatiquement."""
    out: list[Tension] = []
    guns = store.get_facts(relation="chekhovs_gun")
    for f in guns:
        if f.valid_from_year is not None and f.valid_from_year > year:
            continue
        introduced_year = f.valid_from_year or 0
        if year - introduced_year < 2:
            continue  # juste introduit, pas encore en suspens
        fired = store.get_facts(
            subject=f.subject, relation="chekhovs_gun_fired",
            object_value="true",
        )
        if not fired:
            out.append(Tension.from_severity(
                type=TensionType.chekhovs_gun_unfired,
                description=(
                    f"Element narratif '{f.object}' (sujet {f.subject}) "
                    f"introduit en l'an {introduced_year} sans payoff "
                    f"({year - introduced_year} ans). Tension structurelle."
                ),
                severity=TensionSeverity.medium,
                involved_entities=[f.subject],
                source_rule="chekhovs_gun_unfired",
                detected_at_year=year,
            ))
    return out


# ============================================================================
# Registry des invariants (~20)
# ============================================================================

INVARIANTS: tuple[TensionInvariant, ...] = (
    TensionInvariant("kage_absent_or_dead", "Un kage doit etre en place dans chaque grand village.", kage_absent_or_dead),
    TensionInvariant("jinchuuriki_unprotected", "Un jinchuriki vulnerable est convoite par les forces exterieures.", jinchuuriki_unprotected),
    TensionInvariant("obsessive_npc_idle", "Un perso obsessionnel ne reste pas passif longtemps.", obsessive_npc_idle),
    TensionInvariant("wronged_faction_unrevenged", "Une faction lesee cherche a se venger.", wronged_faction_unrevenged),
    TensionInvariant("power_vacuum_global", "L'absence de leader charismatique cree du vide politique.", power_vacuum_global),
    TensionInvariant("unresolved_blood_ties", "Les liens de sang non resolus reviennent toujours hanter.", unresolved_blood_ties),
    TensionInvariant("clan_extinction_threat", "Un clan menace d'extinction force la main du destin.", clan_extinction_threat),
    TensionInvariant("tailed_beast_uncontrolled", "Un bijuu sans hote est une force convoitee.", tailed_beast_uncontrolled),
    TensionInvariant("wartime_alliance_unstable", "Une alliance fragile finit par rompre.", wartime_alliance_unstable),
    TensionInvariant("hidden_truth_about_to_surface", "Un secret connu de plusieurs finit par eclater.", hidden_truth_about_to_surface),
    TensionInvariant("death_anniversary", "Les anniversaires d'evenements lourds raniment les passions.", death_anniversary),
    TensionInvariant("geographic_imbalance", "Un desequilibre geographique invite a l'agression.", geographic_imbalance),
    TensionInvariant("student_surpasses_master", "L'eleve qui depasse le maitre cree un conflit.", student_surpasses_master),
    TensionInvariant("prophecy_unfulfilled", "Une prophetie en attente force le cours des evenements.", prophecy_unfulfilled),
    TensionInvariant("cursed_hatred_rising", "La haine cumulee bascule en violence.", cursed_hatred_rising),
    TensionInvariant("kekkei_genkai_carrier_isolated", "Le dernier porteur d'un kekkei est convoite.", kekkei_genkai_carrier_isolated),
    TensionInvariant("forbidden_jutsu_threat", "Un kinjutsu vivant menace l'equilibre.", forbidden_jutsu_threat),
    TensionInvariant("lone_survivor_obsessed", "Un survivant solitaire focalise sa vengeance.", lone_survivor_obsessed),
    TensionInvariant("border_dispute", "Un conflit frontalier non resolu mene a la guerre.", border_dispute),
    TensionInvariant("chekhovs_gun_unfired", "Un element introduit doit etre paye narrativement.", chekhovs_gun_unfired),
    # Phase H wiring 9.3 : 21eme invariant, opt-in (ne fire que si
    # ctx['political_forces'] et ctx['char_deaths'] sont injectes).
    TensionInvariant(
        "political_alliance_brittle_via_dead_leader",
        "Une alliance dont l'un des leaders est mort sans successeur tient mal.",
        political_alliance_brittle_via_dead_leader,
    ),
    # Phase H wiring 9.3 (suite) : 22eme invariant, lit canon.political_forces
    # cote enemies (vs alliance_brittle qui regarde allies).
    TensionInvariant(
        "political_faction_isolated_with_active_enemies",
        "Une faction sans leader avec >=2 ennemis actifs est exposee.",
        political_faction_isolated_with_active_enemies,
    ),
)


__all__ = [
    "INVARIANTS",
    "TensionInvariant",
    "border_dispute",
    "chekhovs_gun_unfired",
    "clan_extinction_threat",
    "cursed_hatred_rising",
    "death_anniversary",
    "forbidden_jutsu_threat",
    "geographic_imbalance",
    "hidden_truth_about_to_surface",
    "jinchuuriki_unprotected",
    "kage_absent_or_dead",
    "kekkei_genkai_carrier_isolated",
    "lone_survivor_obsessed",
    "obsessive_npc_idle",
    "political_alliance_brittle_via_dead_leader",
    "political_faction_isolated_with_active_enemies",
    "power_vacuum_global",
    "prophecy_unfulfilled",
    "student_surpasses_master",
    "tailed_beast_uncontrolled",
    "unresolved_blood_ties",
    "wartime_alliance_unstable",
    "wronged_faction_unrevenged",
]
