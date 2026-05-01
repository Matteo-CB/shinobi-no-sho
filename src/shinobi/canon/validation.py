"""Regles de coherence inter-datasets sur le canon charge."""

from __future__ import annotations

from dataclasses import dataclass

from shinobi.canon.models import CanonBundle
from shinobi.errors import CanonValidationError


@dataclass
class ValidationReport:
    """Rapport detaille de la verification de coherence."""

    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_errors(self) -> None:
        if self.errors:
            raise CanonValidationError("\n".join(self.errors))


def validate_bundle(bundle: CanonBundle) -> ValidationReport:
    """Applique les regles de coherence inter-datasets."""
    errors: list[str] = []
    warnings: list[str] = []
    char_ids = set(bundle.characters)
    village_ids = set(bundle.villages)
    technique_ids = set(bundle.techniques)
    clan_ids = set(bundle.clans)

    for char in bundle.characters.values():
        if char.village_of_origin and char.village_of_origin not in village_ids:
            errors.append(f"character {char.id}: village inconnu {char.village_of_origin}")
        if char.clan and char.clan not in clan_ids:
            warnings.append(f"character {char.id}: clan inconnu {char.clan}")
        for entry in char.techniques_known_by_era:
            for tid in entry.techniques:
                if tid not in technique_ids:
                    warnings.append(
                        f"character {char.id}: technique inconnue {tid} a l'an {entry.year}"
                    )
        for tid in char.teachable_techniques:
            if tid not in technique_ids:
                warnings.append(f"character {char.id}: enseignable inconnu {tid}")
        for rel in char.key_relationships:
            if rel.with_character not in char_ids:
                warnings.append(
                    f"character {char.id}: relation vers id inconnu {rel.with_character}"
                )
        if char.death_year is not None and char.birth_year is not None:
            if char.death_year <= char.birth_year:
                errors.append(
                    f"character {char.id}: death_year {char.death_year} <= birth_year {char.birth_year}"
                )

    for tech in bundle.techniques.values():
        for user_id in tech.canonical_users:
            if user_id not in char_ids:
                warnings.append(f"technique {tech.id}: utilisateur inconnu {user_id}")
        if tech.creator_id and tech.creator_id not in char_ids:
            warnings.append(f"technique {tech.id}: creator_id inconnu {tech.creator_id}")

    for ev in bundle.timeline_events.values():
        for char_ref in ev.involved_characters:
            if char_ref not in char_ids:
                warnings.append(f"event {ev.id}: personnage inconnu {char_ref}")

    for village in bundle.villages.values():
        for kage in village.kage_lineage:
            if kage.character_id not in char_ids:
                warnings.append(
                    f"village {village.id}: kage inconnu {kage.character_id} ordre {kage.order}"
                )

    for beast in bundle.tailed_beasts.values():
        for j in beast.current_jinchuuriki_by_era:
            if j.jinchuuriki not in char_ids:
                warnings.append(f"tailed_beast {beast.id}: jinchuuriki inconnu {j.jinchuuriki}")

    return ValidationReport(errors=errors, warnings=warnings)
