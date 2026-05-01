"""Relations entre personnages, reputation, climat politique."""

from __future__ import annotations

from shinobi.engine.character import Character, Relationship


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
