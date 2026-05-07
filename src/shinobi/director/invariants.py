"""Invariants narratifs Naruto pour le Director / Drama Manager.

Spec doc 02 §7.3 : motifs recurrents que Kishimoto utilise consistantement
dans le canon. Le Director les passe en contexte au LLM createur comme
style guide. Ils ne prescrivent PAS d'events ; ils orientent le ton.

Source : analyse du canon (manga + databook), patterns Kishimoto §9.5.
Couvre les 5 motifs centraux + 4 thematiques secondaires.
"""

from __future__ import annotations

from shinobi.director.types import NarrativeInvariant


# Invariants centraux (weight=1.0) : motifs que Kishimoto incarne dans
# presque chaque arc majeur.
NARUTO_INVARIANTS_CENTRAL: tuple[NarrativeInvariant, ...] = (
    NarrativeInvariant(
        id="invariant_power_has_cost",
        principle_fr=(
            "Le pouvoir s'accompagne toujours d'un cout. Plus la technique "
            "est destructive, plus le tribut sur le porteur est lourd."
        ),
        examples_canon=[
            "Edo Tensei requiert un sacrifice humain par invocation",
            "Mangekyo Sharingan rend aveugle progressivement",
            "Hachimon Tonkou tue son utilisateur a la 8e porte",
            "Chimera Senjutsu corrompt l'ame du fusionne",
        ],
        applies_to_contexts=[
            "training", "forbidden_jutsu", "kekkei_genkai", "evolution",
            "transformation",
        ],
        weight=1.0,
    ),
    NarrativeInvariant(
        id="invariant_bonds_transform",
        principle_fr=(
            "Les liens humains transforment plus profondement que la force "
            "brute. Un ennemi peut devenir allie par le dialogue avant le combat."
        ),
        examples_canon=[
            "Naruto rallie Gaara puis Nagato par l'empathie",
            "Sasuke retourne grace a Naruto apres la 4e Guerre",
            "Obito est sauve par les mots d'enfance de Kakashi/Rin",
            "Kurama devient ami de Naruto apres avoir partage sa douleur",
        ],
        applies_to_contexts=[
            "rivalry", "redemption", "team", "enemy", "dialogue", "war",
            # Round G9 : ajout 'jinchuuriki' - couvre le theme central
            # Naruto/Kurama et les autres jinchuuriki (Gaara/Shukaku, etc.)
            # qui resolvent leur tension via lien plutot que combat.
            "jinchuuriki",
        ],
        weight=1.0,
    ),
    NarrativeInvariant(
        id="invariant_hatred_breakable",
        principle_fr=(
            "La haine engendre la haine, mais elle peut etre brisee. La "
            "rupture passe toujours par la reconnaissance de la souffrance "
            "de l'adversaire, jamais par sa destruction pure."
        ),
        examples_canon=[
            "Cycle de haine Uchiha-Senju brise par Naruto-Sasuke",
            "Pain converti par le discours de Naruto sur la paix",
            "Sasuke abandonne sa vengeance apres Itachi+Kabuto reveal",
        ],
        applies_to_contexts=[
            "war", "vengeance", "redemption", "clan_conflict", "trauma",
        ],
        weight=1.0,
    ),
    NarrativeInvariant(
        id="invariant_political_roots",
        principle_fr=(
            "Les conflits politiques ont des racines historiques profondes. "
            "Aucun antagonisme present n'est sans precedent ; remonter a la "
            "cause initiale revele toujours une trahison ou un malentendu."
        ),
        examples_canon=[
            "Massacre Uchiha trace au coup d'etat avorte de Madara",
            "4e Guerre trace a la doctrine Hagoromo-Hagoromo Otsutsuki",
            "Hidden des villages caches issu des guerres clan-vs-clan",
            "Akatsuki original = trio paix-pacifiste d'Ame avant Yahiko",
        ],
        applies_to_contexts=[
            "clan_conflict", "village_war", "succession", "alliance",
            "history",
        ],
        weight=1.0,
    ),
    NarrativeInvariant(
        id="invariant_inheritance_choice",
        principle_fr=(
            "L'heritage n'est jamais purement subi : chaque heritier doit "
            "choisir ce qu'il garde du legs ancestral et ce qu'il refuse. "
            "Les heros choisissent, les antagonistes subissent."
        ),
        examples_canon=[
            "Naruto choisit la Volonte du Feu plutot que la haine Uzumaki",
            "Sasuke heritier Uchiha choisit (finalement) la voie de l'amour",
            "Boruto refuse l'heritage Hokage initial, choisit shinobi-gardien",
            "Kawaki rejette Karma puis l'embrasse comme arme contre Jigen",
        ],
        applies_to_contexts=[
            "lineage", "family", "rivalry", "training", "succession",
        ],
        weight=1.0,
    ),
)


# Invariants thematiques secondaires (weight=0.5-0.75) : presents mais
# moins centraux. Activent par contexte specifique.
NARUTO_INVARIANTS_SECONDARY: tuple[NarrativeInvariant, ...] = (
    NarrativeInvariant(
        id="invariant_master_student_arc",
        principle_fr=(
            "Le rapport maitre-eleve est sacre mais voue a etre depasse. "
            "L'eleve doit (eventuellement) surpasser le maitre, qui doit "
            "(eventuellement) reconnaitre cette superiorite avec fierte."
        ),
        examples_canon=[
            "Naruto > Jiraiya > Hiruzen apres 4e Guerre",
            "Kakashi reconnait Naruto comme superieur post-Pain",
            "Itachi reconnait Sasuke avant de mourir au combat truque",
        ],
        applies_to_contexts=["training", "team", "succession", "death"],
        weight=0.75,
    ),
    NarrativeInvariant(
        id="invariant_secret_corrodes",
        principle_fr=(
            "Tout secret cache trop longtemps corrode son porteur. Plus le "
            "secret est lourd, plus la revelation est devastatrice."
        ),
        examples_canon=[
            "Itachi porte la verite du massacre seul, mort par tuberculose",
            "Sandaime garde le secret Kyuubi-Naruto, mort par Orochimaru",
            "Tobi/Obito porte 16 ans le secret Madara, devient antagoniste",
        ],
        applies_to_contexts=[
            "hidden_truth", "lineage", "clan_secret", "death", "trauma",
        ],
        weight=0.75,
    ),
    NarrativeInvariant(
        id="invariant_redemption_through_sacrifice",
        principle_fr=(
            "La redemption d'un personnage tombe ne passe jamais par les "
            "mots seuls : elle exige un sacrifice tangible (la vie, un "
            "membre, une relation cherie)."
        ),
        examples_canon=[
            "Itachi se sacrifie pour Sasuke + Konoha",
            "Obito se sacrifie pour Naruto et donne son Sharingan",
            "Nagato se sacrifie pour ressusciter Konoha apres Pain Invasion",
            "Neji se sacrifie pour Naruto + Hinata pendant 4e Guerre",
        ],
        applies_to_contexts=[
            "redemption", "death", "war", "team", "vengeance",
        ],
        weight=0.75,
    ),
    NarrativeInvariant(
        id="invariant_underdog_perseverance",
        principle_fr=(
            "Le talent inne est toujours surpasse par la perseverance "
            "acharnee. L'underdog gagne par accumulation, jamais par genie pur."
        ),
        examples_canon=[
            "Rock Lee sans chakra battant des prodiges par taijutsu",
            "Naruto deadlast battant Neji genie Hyuuga",
            "Might Guy contre Madara Juubi via accumulation Hachimon",
        ],
        applies_to_contexts=[
            "training", "rivalry", "exam", "team", "battle",
        ],
        weight=0.5,
    ),
)


# Liste agregee : 5 centraux + 4 secondaires = 9 invariants.
NARUTO_INVARIANTS: tuple[NarrativeInvariant, ...] = (
    *NARUTO_INVARIANTS_CENTRAL,
    *NARUTO_INVARIANTS_SECONDARY,
)


def select_relevant_invariants(
    contexts: list[str],
    *,
    max_invariants: int = 5,
) -> list[NarrativeInvariant]:
    """Selectionne les invariants applicables aux contextes donnes.

    Score = somme(weight) sur les invariants dont applies_to_contexts
    intersect contexts. Retourne le top-N par score (centraux prioritaires).

    Round G6 : si aucun invariant ne matche les contextes (cas des
    tension types comme chekhovs_gun_unfired qui produisent des keywords
    inconnus de toutes les applies_to_contexts), fallback sur les
    centraux. Le narrator LLM ne doit jamais perdre son style guide.
    """
    if not contexts:
        # Pas de contexte specifique : retourne les centraux.
        return list(NARUTO_INVARIANTS_CENTRAL[:max_invariants])

    contexts_set = set(contexts)
    scored: list[tuple[float, NarrativeInvariant]] = []
    for inv in NARUTO_INVARIANTS:
        overlap = contexts_set & set(inv.applies_to_contexts)
        if not overlap:
            continue
        # Score = weight * nb contextes matches (densite de pertinence).
        score = inv.weight * len(overlap)
        scored.append((score, inv))

    if not scored:
        # Round G6 : safety net - aucun match -> centraux par defaut.
        return list(NARUTO_INVARIANTS_CENTRAL[:max_invariants])

    scored.sort(key=lambda x: -x[0])
    return [inv for _, inv in scored[:max_invariants]]


def select_relevant_patterns(
    patterns: list[dict],
    *,
    contexts: list[str],
    max_patterns: int = 3,
) -> list[dict]:
    """Phase H 9.5 : selectionne les patterns Kishimoto pertinents au tick.

    Avant : Director.tick prenait les 3 premiers patterns dans l'ordre du
    dataset (canon-arbitrary). Sur 14 patterns disponibles, seuls 3 fixes
    parvenaient au LLM, jamais filtres par les tensions du moment.

    Strategie : pour chaque pattern, compte combien de mots-cles `contexts`
    apparaissent dans `description_fr` + `when_to_apply_fr`. Score = nombre
    d'occurrences. Tie-break sur l'ordre canon (stable).

    Defensive :
    - Si contexts vide ou patterns vide, retourne `patterns[:max_patterns]`
      (back-compat avec le comportement initial).
    - Si aucun pattern ne match, fallback sur les premiers (les patterns
      canoniques 'Revelation en couches' et 'Trahison preparee' sont des
      defaults raisonnables).
    """
    if not patterns:
        return []
    if not contexts:
        return list(patterns[:max_patterns])

    # Normalise contexts : lowercase + filter empty
    ctx_kw = {c.lower().strip() for c in contexts if isinstance(c, str) and c}
    if not ctx_kw:
        return list(patterns[:max_patterns])

    scored: list[tuple[int, int, dict]] = []
    for idx, p in enumerate(patterns):
        if not isinstance(p, dict):
            continue
        haystack_parts: list[str] = []
        for key in ("description_fr", "when_to_apply_fr"):
            v = p.get(key)
            if isinstance(v, str) and v:
                haystack_parts.append(v.lower())
        if not haystack_parts:
            continue
        haystack = " ".join(haystack_parts)
        # Score : nombre de keywords contexts qui apparaissent dans haystack.
        score = sum(1 for kw in ctx_kw if kw in haystack)
        scored.append((score, idx, p))  # idx = tie-break ordre canon

    if not scored:
        return list(patterns[:max_patterns])

    # Tri : score desc, puis idx asc (stable canon order)
    scored.sort(key=lambda t: (-t[0], t[1]))
    top = [p for _, _, p in scored[:max_patterns]]
    # Si aucun n'a de score > 0, fallback sur les premiers patterns canon.
    if all(s == 0 for s, _, _ in scored[:max_patterns]):
        return list(patterns[:max_patterns])
    return top


__all__ = [
    "NARUTO_INVARIANTS",
    "NARUTO_INVARIANTS_CENTRAL",
    "NARUTO_INVARIANTS_SECONDARY",
    "select_relevant_invariants",
    "select_relevant_patterns",
]
