"""Systeme de dialogues style visual novel.

Module dedie a la representation, l'enregistrement et l'export des dialogues
prononces dans le jeu. Distinct du `narration.NarrationResponse.npc_dialogue`
(qui reste la sortie LLM brute) : ici on a une representation riche, indexable
et persistente, pretes pour une future application VN.

Composants :
- types.py : DialogueLine, DialogueEmotion, DialogueExpression, DialogueTone
- log.py   : DialogueLog rolling window avec persistance JSON
- formatter.py : extracteur narrative -> DialogueLines (parse les dits, attribue
  un speaker, gere narrateur omniscient et discours rapporte)
- vn_export.py : sortie JSON canonique pour application VN externe
"""

from __future__ import annotations

from shinobi.dialogue.formatter import (
    NARRATOR_SPEAKER_ID,
    DialogueFormatter,
)
from shinobi.dialogue.log import DialogueLog, DialogueLogConfig
from shinobi.dialogue.types import (
    DialogueEmotion,
    DialogueExpression,
    DialogueLine,
    DialogueTone,
)
from shinobi.dialogue.vn_export import (
    VNExportConfig,
    export_to_vn_json,
    export_to_vn_payload,
)

__all__ = [
    "NARRATOR_SPEAKER_ID",
    "DialogueEmotion",
    "DialogueExpression",
    "DialogueFormatter",
    "DialogueLine",
    "DialogueLog",
    "DialogueLogConfig",
    "DialogueTone",
    "VNExportConfig",
    "export_to_vn_json",
    "export_to_vn_payload",
]
