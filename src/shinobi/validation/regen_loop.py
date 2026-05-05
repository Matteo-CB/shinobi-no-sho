"""Formatter de feedback pour la regen loop (pilier 3.4).

Compatible avec le pattern de retry existant de
`shinobi.llm.narration.Narrator.narrate()` qui prepend un bloc
[CORRECTION REQUISE] au user message du tour suivant.

Le branchement effectif du Validator dans la pipeline narrator existante
est volontairement reporte : `Narrator.narrate()` a deja sa propre boucle
retry avec `claim_validator + judge`. Y greffer le nouveau Validator
maintenant melangerait deux orchestrateurs concurrents alors que les
couches B/D/E ne sont pas encore en place. Le branchement reel viendra
avec le pilier 6 (enums canon + structured generation), une fois le
nouveau pipeline complet.
"""

from __future__ import annotations

from shinobi.validation.validator import ValidationResult


def format_violations_for_regen(results: list[ValidationResult]) -> str:
    """Formate les violations pour injection dans le prompt de regen.

    Les couches valides ne sont pas mentionnees (uniquement les rejets).
    Retourne une chaine vide si tout est valide.
    """
    failed = [r for r in results if not r.is_valid]
    if not failed:
        return ""
    lines = ["Ta sortie précédente a été rejetée par le validator :"]
    for r in failed:
        lines.append(f"  [Couche {r.layer}] {r.reason or '(raison non spécifiée)'}")
        for d in r.details:
            lines.append(f"    - {d}")
    lines.append(
        "\nRégénère en respectant strictement les contraintes ci-dessus. "
        "Les PNJ morts ne parlent ni n'agissent, les lieux détruits sont inaccessibles, "
        "le langage doit correspondre à l'âge du personnage."
    )
    return "\n".join(lines)
