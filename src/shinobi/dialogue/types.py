"""Types Pydantic pour les dialogues style visual novel.

Une DialogueLine est immutable, identifiee par un id unique, attribuee a un
speaker (id canon ou 'narrator'). Elle porte les meta dont une app VN a besoin :
emotion, expression faciale, ton, mood scene, position spatiale.

Pour la phase actuelle (CLI), seuls speaker_id, text, emotion sont
strictement requis. Les autres champs sont optionnels et seront enrichis
par le formatter ou par hooks ulterieurs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

# ============================================================================
# Enums controles (limite l'inventivite du LLM, simplifie l'app VN)
# ============================================================================


class DialogueEmotion(StrEnum):
    """Emotion principale du speaker au moment de la ligne."""

    neutral = "neutral"
    joyful = "joyful"
    angry = "angry"
    sad = "sad"
    fearful = "fearful"
    surprised = "surprised"
    disgusted = "disgusted"
    confident = "confident"
    determined = "determined"
    smug = "smug"
    embarrassed = "embarrassed"
    grieving = "grieving"
    nostalgic = "nostalgic"
    serious = "serious"
    playful = "playful"
    contemplative = "contemplative"
    suspicious = "suspicious"
    weary = "weary"
    hopeful = "hopeful"
    desperate = "desperate"


class DialogueExpression(StrEnum):
    """Expression faciale (utile pour les sprite VN)."""

    default = "default"
    smile = "smile"
    smirk = "smirk"
    grin = "grin"
    frown = "frown"
    glare = "glare"
    crying = "crying"
    laughing = "laughing"
    blush = "blush"
    shocked = "shocked"
    closed_eyes = "closed_eyes"
    eyes_wide = "eyes_wide"
    sneer = "sneer"
    soft_smile = "soft_smile"


class DialogueTone(StrEnum):
    """Ton de voix vocale (utile pour TTS futur ou direction d'acteur)."""

    normal = "normal"
    whisper = "whisper"
    shout = "shout"
    growl = "growl"
    laugh = "laugh"
    sob = "sob"
    sigh = "sigh"
    monotone = "monotone"
    melodic = "melodic"
    hushed = "hushed"


# ============================================================================
# DialogueLine (le coeur du systeme)
# ============================================================================


class DialogueLine(BaseModel):
    """Une ligne de dialogue prononcee par un speaker. Immutable.

    Attributs requis : id, speaker_id, text.
    Le speaker_id peut etre :
    - id canon (ex: 'uzumaki_naruto')
    - 'narrator' (voix narrative)
    - 'player' (le perso joueur)
    - 'system' (messages systeme, options menu)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=lambda: f"dline_{uuid4().hex[:12]}")
    speaker_id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)

    # Meta optionnelle (pour app VN future)
    emotion: DialogueEmotion = DialogueEmotion.neutral
    expression: DialogueExpression = DialogueExpression.default
    tone: DialogueTone = DialogueTone.normal

    # Contexte temporel et scene
    in_game_year: int | None = None
    in_game_date: str | None = None  # MM-DD
    location_id: str | None = None
    scene_mood: str | None = None  # libre, ex: "tension", "calme", "festif"

    # Liens narratifs (utile VN pour brancher events)
    related_event_id: str | None = None
    related_mission_id: str | None = None
    addressed_to_id: str | None = None  # destinataire (autre speaker ou 'all')

    # Metadata systeme
    real_time_ts: float = Field(default_factory=lambda: datetime.now(UTC).timestamp())
    turn_number: int | None = None  # tour de jeu (engine.world.WorldState.turn)
    is_thought: bool = False  # pensee interieure vs prononciation
    voice_profile_id: str | None = None  # lien vers VoiceProfile canon

    # Annotation libre (LLM peut decrire pose, geste, etc.)
    stage_directions: str | None = None

    def short_label(self) -> str:
        """Format court pour affichage CLI : 'Speaker: text'."""
        return f"{self.speaker_id}: {self.text}"

    def is_narrator(self) -> bool:
        return self.speaker_id == "narrator"

    def is_player(self) -> bool:
        return self.speaker_id == "player"

    def is_system(self) -> bool:
        return self.speaker_id == "system"

    def is_canon_npc(self) -> bool:
        """True si le speaker n'est ni narrateur, ni joueur, ni systeme."""
        return self.speaker_id not in {"narrator", "player", "system"}


__all__ = [
    "DialogueEmotion",
    "DialogueExpression",
    "DialogueLine",
    "DialogueTone",
]
