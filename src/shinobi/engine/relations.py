"""Relations entre personnages, reputation, climat politique."""

from __future__ import annotations

from shinobi.engine.character import Character, Relationship

# Decay : -1 affinity tous les 90 jours sans interaction (lent).
DECAY_DAYS_THRESHOLD = 90
DECAY_AMOUNT_PER_PERIOD = 1
# Au-dela de ce score, affinity tres positive resiste mieux (passion fixe les choses).
DECAY_PROTECTED_THRESHOLD = 60


def update_affinity(
    character: Character,
    *,
    with_id: str,
    delta: int,
) -> Character:
    """Modifie l'affinity d'une relation existante ou cree une nouvelle relation."""
    new_relations: list[Relationship] = []
    found = False
    for rel in character.relationships:
        if rel.with_character_id == with_id:
            new_relations.append(
                rel.model_copy(update={"affinity": _clamp_affinity(rel.affinity + delta)})
            )
            found = True
        else:
            new_relations.append(rel)
    if not found:
        new_relations.append(
            Relationship(
                with_character_id=with_id,
                type="acquaintance",
                affinity=_clamp_affinity(delta),
            )
        )
    return character.model_copy(update={"relationships": new_relations})


def _clamp_affinity(value: int) -> int:
    return max(-100, min(100, value))


def add_reputation(
    character: Character,
    village_id: str,
    delta: int,
) -> Character:
    """Modifie la reputation dans un village donne."""
    entries = list(character.reputation.by_village)
    found = False
    new_entries: list = []
    for e in entries:
        if e.village_id == village_id:
            new_entries.append(e.model_copy(update={"score": max(-200, min(200, e.score + delta))}))
            found = True
        else:
            new_entries.append(e)
    if not found:
        from shinobi.engine.character import ReputationEntry

        new_entries.append(ReputationEntry(village_id=village_id, score=max(-200, min(200, delta))))
    new_rep = character.reputation.model_copy(update={"by_village": new_entries})
    return character.model_copy(update={"reputation": new_rep})


def touch_relationship(character: Character, *, with_id: str, year: int) -> Character:
    """Marque la derniere interaction d'une relation a l'annee donnee.

    Stocke l'annee dans le dernier event d'historique pour servir de base au decay.
    """
    new_relations: list[Relationship] = []
    for rel in character.relationships:
        if rel.with_character_id == with_id:
            from shinobi.engine.character import RelationshipEvent

            new_history = [
                *rel.history,
                RelationshipEvent(year=year, description="interaction", affinity_delta=0),
            ]
            new_relations.append(rel.model_copy(update={"history": new_history[-20:]}))
        else:
            new_relations.append(rel)
    return character.model_copy(update={"relationships": new_relations})


def decay_affinities(character: Character, *, current_year: int) -> Character:
    """Reduit l'affinity des relations qu'on n'a pas vues depuis longtemps.

    Une periode = DECAY_DAYS_THRESHOLD jours. Pour chaque periode de silence,
    -DECAY_AMOUNT_PER_PERIOD. Les affinities >= DECAY_PROTECTED_THRESHOLD se
    degradent deux fois plus lentement (lien fort).
    """
    if not character.relationships:
        return character
    new_relations: list[Relationship] = []
    for rel in character.relationships:
        last_year = rel.history[-1].year if rel.history else None
        if last_year is None or current_year <= last_year:
            new_relations.append(rel)
            continue
        days_since = (current_year - last_year) * 365
        periods = days_since // DECAY_DAYS_THRESHOLD
        if periods <= 0:
            new_relations.append(rel)
            continue
        decay = periods * DECAY_AMOUNT_PER_PERIOD
        if abs(rel.affinity) >= DECAY_PROTECTED_THRESHOLD:
            decay = max(1, decay // 2)
        # Le decay tend vers 0 (positif baisse, negatif remonte).
        if rel.affinity > 0:
            new_aff = max(0, rel.affinity - decay)
        elif rel.affinity < 0:
            new_aff = min(0, rel.affinity + decay)
        else:
            new_aff = 0
        if new_aff != rel.affinity:
            from shinobi.engine.character import RelationshipEvent

            new_history = [
                *rel.history,
                RelationshipEvent(
                    year=current_year, description="silence prolonge", affinity_delta=new_aff - rel.affinity
                ),
            ]
            new_relations.append(rel.model_copy(update={"affinity": new_aff, "history": new_history[-20:]}))
        else:
            new_relations.append(rel)
    return character.model_copy(update={"relationships": new_relations})
