"""Schema minimal du state runtime (pilier 4.1).

Distinct de `shinobi.engine.world.WorldState` qui gere la simulation canonique
complete. `RuntimeState` est une snapshot focalisee sur le tour courant pour
le pipeline anti-hallucination : narrative_time, player_character, world_state
divergent, scene_context, dialogue_history.

Implemente le Protocol `StateView` de
`shinobi.preprocessing.reference_resolver` via duck typing : expose
`last_mentioned_character`, `present_characters`, `current_location` comme
properties pour pouvoir etre passe directement au resolver.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class NarrativeTime(BaseModel):
    """Position narrative du tour courant."""

    arc: str = "(non défini)"
    approximate_year: int = 0
    post_timeskip: bool = False


class PlayerCharacterState(BaseModel):
    """Etat persistant du perso joueur (OC)."""

    name: str
    birth_year: int = 0
    village: str = "(non défini)"
    rank: str = "academy_student"
    known_jutsu: list[str] = Field(default_factory=list)
    location: str = "(non défini)"
    # Relations etablies en jeu : ids canon avec qui le joueur a une relation
    # explicitement positive (affinity > seuil amical, lien etabli par
    # interactions repetees). Vide par defaut (le joueur est un OC inconnu
    # du canon). Sera alimente par le KG dynamique en Phase A et au-dela.
    established_npc_relationships: list[str] = Field(default_factory=list)


class CharacterDeath(BaseModel):
    """Mort d'un perso canon, divergente ou conforme au canon."""

    name: str
    death_arc: str
    death_year: int | None = None


class WorldStateData(BaseModel):
    """Etat divergent du monde par rapport au canon (KG_world_state du §9.3).

    `characters_alive` est un dict id -> {birth_year, ...} qui permet d'enregistrer
    les divergences (perso reste vivant alors que canon le fait mourir, etc.).
    `characters_dead` enregistre les morts confirmees, utilisable par les
    sherlock rules (couche A du validator §3).
    """

    characters_alive: dict[str, dict[str, int]] = Field(default_factory=dict)
    characters_dead: list[CharacterDeath] = Field(default_factory=list)
    destroyed_locations: list[str] = Field(default_factory=list)
    key_events_resolved: list[str] = Field(default_factory=list)


class SceneContextSnapshot(BaseModel):
    """Snapshot du tour courant pour query rewriting et resolution referentielle."""

    location: str | None = None
    present_characters: list[str] = Field(default_factory=list)
    last_mentioned_character: str | None = None
    time_of_day: str | None = None
    mood: str | None = None


class DialogueTurn(BaseModel):
    """Tour de dialogue archive."""

    turn: int
    speaker: str
    text: str
    referents: dict[str, str] = Field(default_factory=dict)


class RuntimeState(BaseModel):
    """State runtime du jeu pour le pipeline anti-hallucination.

    Distinct de `shinobi.engine.world.WorldState`, qui gere la simulation
    canonique complete (NPCState, VillageState, OrganizationState).
    `RuntimeState` capture une snapshot focalisee sur le tour courant.

    Implemente le Protocol `StateView` du resolver via duck typing :
    `last_mentioned_character`, `present_characters`, `current_location` sont
    exposes comme properties au top level pour pouvoir etre passe directement
    a `resolve_references` ou `rewrite_query`.
    """

    model_config = ConfigDict(extra="forbid")

    narrative_time: NarrativeTime = Field(default_factory=NarrativeTime)
    player_character: PlayerCharacterState
    world_state: WorldStateData = Field(default_factory=WorldStateData)
    scene_context: SceneContextSnapshot = Field(default_factory=SceneContextSnapshot)
    dialogue_history: list[DialogueTurn] = Field(default_factory=list)

    @property
    def last_mentioned_character(self) -> str | None:
        return self.scene_context.last_mentioned_character

    @property
    def present_characters(self) -> Sequence[str]:
        return tuple(self.scene_context.present_characters)

    @property
    def current_location(self) -> str | None:
        return self.scene_context.location

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, raw: str) -> RuntimeState:
        return cls.model_validate_json(raw)

    def save(self, path: Path) -> None:
        """Persiste le state sur disque (UTF-8, indented JSON)."""
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> RuntimeState:
        """Charge un state depuis un fichier JSON."""
        return cls.from_json(path.read_text(encoding="utf-8"))
