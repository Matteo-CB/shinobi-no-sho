"""Logique d'apprentissage de techniques."""

from __future__ import annotations

from dataclasses import dataclass

from shinobi.canon.models import Technique, WorldRules
from shinobi.engine.character import Character, KnownTechnique, LearningTechnique


@dataclass(frozen=True)
class LearningEligibility:
    eligible: bool
    reasons: list[str]


def can_attempt_learning(character: Character, technique: Technique) -> LearningEligibility:
    """Verifie les prerequis. Retourne une eligibilite, jamais un blocage absolu."""
    reasons: list[str] = []
    pre = technique.prerequisites
    if character.chakra.max < pre.min_chakra_pool:
        reasons.append(f"chakra_pool {character.chakra.max} < {pre.min_chakra_pool}")
    if character.extended_stats.chakra_control < pre.min_chakra_control:
        reasons.append(
            f"chakra_control {character.extended_stats.chakra_control} < {pre.min_chakra_control}"
        )
    for nature in pre.required_natures:
        if nature not in character.chakra.natures_unlocked:
            reasons.append(f"nature manquante: {nature}")
    known_tech_ids = {t.technique_id for t in character.techniques_known}
    for tech_id in pre.required_techniques:
        if tech_id not in known_tech_ids:
            reasons.append(f"technique requise manquante: {tech_id}")
    if (
        pre.kekkei_genkai_restriction
        and pre.kekkei_genkai_restriction not in character.kekkei_genkai
    ):
        reasons.append(f"kekkei genkai requis: {pre.kekkei_genkai_restriction}")
    if pre.clan_restriction and character.clan != pre.clan_restriction:
        reasons.append(f"clan requis: {pre.clan_restriction}")
    if pre.min_age and character.age_years < pre.min_age:
        reasons.append(f"age trop jeune: {character.age_years} < {pre.min_age}")
    return LearningEligibility(eligible=not reasons, reasons=reasons)


def compute_learning_hours_required(
    character: Character,
    technique: Technique,
    *,
    rules: WorldRules,
    mentor_quality: float = 1.0,
) -> int:
    """Calcule le total d'heures necessaires pour apprendre une technique."""
    base = rules.learning.difficulty_to_hours_baseline.get(str(technique.learning_difficulty), 100)
    int_modifier = 1.0 + (character.stats.intelligence - 3.0) * rules.learning.stat_modifiers.get(
        "intelligence_per_point", -0.05
    )
    cc_modifier = 1.0 + (
        character.extended_stats.chakra_control - 3.0
    ) * rules.learning.stat_modifiers.get("chakra_control_per_point", -0.04)
    genius_modifier = 1.0 + (
        character.extended_stats.learning_genius - 3.0
    ) * rules.learning.stat_modifiers.get("talent_genius_per_point", -0.06)
    total_modifier = max(0.1, int_modifier * cc_modifier * genius_modifier * mentor_quality)
    return max(1, int(base * total_modifier))


def progress_learning(
    character: Character,
    technique_id: str,
    *,
    hours: int,
    learn_year: int,
) -> tuple[Character, bool]:
    """Avance la progression d'une technique en cours. Retourne (perso, completed)."""
    new_in_progress: list[LearningTechnique] = []
    completed_tech: LearningTechnique | None = None
    for tech in character.techniques_in_progress:
        if tech.technique_id == technique_id:
            new_progress = tech.progress_hours + hours
            if new_progress >= tech.progress_required:
                completed_tech = tech.model_copy(update={"progress_hours": tech.progress_required})
            else:
                new_in_progress.append(tech.model_copy(update={"progress_hours": new_progress}))
        else:
            new_in_progress.append(tech)
    if completed_tech is None:
        return character.model_copy(update={"techniques_in_progress": new_in_progress}), False

    learned = KnownTechnique(
        technique_id=completed_tech.technique_id,
        mastery_level=1.0,
        learned_year=learn_year,
        learned_from=completed_tech.teacher_id,
    )
    return (
        character.model_copy(
            update={
                "techniques_in_progress": new_in_progress,
                "techniques_known": [*character.techniques_known, learned],
            }
        ),
        True,
    )


def start_learning(
    character: Character,
    *,
    technique_id: str,
    progress_required: int,
    teacher_id: str | None,
    started_year: int,
    quality_modifier: float = 1.0,
) -> Character:
    """Insere une technique en cours d'apprentissage."""
    if any(t.technique_id == technique_id for t in character.techniques_in_progress):
        return character
    entry = LearningTechnique(
        technique_id=technique_id,
        progress_hours=0,
        progress_required=progress_required,
        started_year=started_year,
        teacher_id=teacher_id,
        quality_modifier=quality_modifier,
    )
    return character.model_copy(
        update={"techniques_in_progress": [*character.techniques_in_progress, entry]}
    )
