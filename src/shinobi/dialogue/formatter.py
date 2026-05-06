"""Extraction de DialogueLines depuis la sortie LLM narration.

Le narrator produit deux flux :
1. `narrative` : prose narrative (le narrateur omniscient parle)
2. `npc_dialogue` : liste explicite de (character_id, line, tone)

Le formatter convertit ces deux flux en une sequence ordonnee de DialogueLines :
- Chaque entree de npc_dialogue -> 1 DialogueLine pour le speaker NPC
- Le texte 'narrative' devient des DialogueLines pour speaker_id='narrator',
  decoupes par phrase. On extrait aussi les discours rapportes simples
  ('X dit : "Y"') pour les attribuer au bon NPC quand possible.

Le formatter ENRICHIT chaque DialogueLine avec le contexte fourni :
in_game_year, in_game_date, location_id, turn_number, related_event_id, etc.

Heuristiques :
- Match emotion via mots-cles (joyful: 'rit', 'sourit'; angry: 'cri', 'fureur'; ...)
- Match tone si 'crie', 'murmure', 'soupire'
- 'is_thought' si entoure de '*...*' ou '(pense ...)'
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from shinobi.dialogue.types import (
    DialogueEmotion,
    DialogueExpression,
    DialogueLine,
    DialogueTone,
)

NARRATOR_SPEAKER_ID = "narrator"


# ============================================================================
# Heuristiques d'extraction
# ============================================================================


_EMOTION_KEYWORDS: list[tuple[re.Pattern[str], DialogueEmotion]] = [
    (re.compile(r"\b(?:rit|rire|sourit|hilare|joyeux|euphorique)\b", re.I), DialogueEmotion.joyful),
    (re.compile(r"\b(?:colere|fureur|enrage|hurle|crie\s+de\s+rage)\b", re.I), DialogueEmotion.angry),
    (re.compile(r"\b(?:triste|pleure|larmes|sanglot|deuil)\b", re.I), DialogueEmotion.sad),
    (re.compile(r"\b(?:peur|terrifie|tremble|panique)\b", re.I), DialogueEmotion.fearful),
    (re.compile(r"\b(?:surprise|etonne|surpris|stupefait)\b", re.I), DialogueEmotion.surprised),
    (re.compile(r"\b(?:degout|repugnance|ecoeur)\b", re.I), DialogueEmotion.disgusted),
    (re.compile(r"\b(?:confiant|assure|determine|resolu)\b", re.I), DialogueEmotion.determined),
    (re.compile(r"\b(?:gene|honteux|embarrasse|rougit)\b", re.I), DialogueEmotion.embarrassed),
    (re.compile(r"\b(?:nostalgi|souvenir|reminisce)\b", re.I), DialogueEmotion.nostalgic),
    (re.compile(r"\b(?:serieux|grave|solennel)\b", re.I), DialogueEmotion.serious),
    (re.compile(r"\b(?:joue|taquin|moqueur|narquois)\b", re.I), DialogueEmotion.playful),
    (re.compile(r"\b(?:contempl|reflechi|pensif|songeur)\b", re.I), DialogueEmotion.contemplative),
    (re.compile(r"\b(?:soup[cç]onneux|mefiant|suspicieux)\b", re.I), DialogueEmotion.suspicious),
    (re.compile(r"\b(?:fatigue|epuise|las|harasse)\b", re.I), DialogueEmotion.weary),
    (re.compile(r"\b(?:espoir|confiant|optimi)\b", re.I), DialogueEmotion.hopeful),
    (re.compile(r"\b(?:desespere|abattu|perdu)\b", re.I), DialogueEmotion.desperate),
    (re.compile(r"\b(?:smug|suffisance|arrogant|hautain)\b", re.I), DialogueEmotion.smug),
]


_TONE_KEYWORDS: list[tuple[re.Pattern[str], DialogueTone]] = [
    (re.compile(r"\b(?:murmure|chuchote|chuchot)\b", re.I), DialogueTone.whisper),
    (re.compile(r"\b(?:hurle|crie|s\W+ecrie|gueule)\b", re.I), DialogueTone.shout),
    (re.compile(r"\b(?:grogne|gronde|grommell)\b", re.I), DialogueTone.growl),
    (re.compile(r"\b(?:rit\b|riant|hilare)\b", re.I), DialogueTone.laugh),
    (re.compile(r"\b(?:sangloter|sanglote|pleure)\b", re.I), DialogueTone.sob),
    (re.compile(r"\b(?:soupire|soupir)\b", re.I), DialogueTone.sigh),
    (re.compile(r"\b(?:monocord|monotone|atone)\b", re.I), DialogueTone.monotone),
    (re.compile(r"\b(?:hushed|voix\s+basse|tres\s+bas)\b", re.I), DialogueTone.hushed),
]


# Pattern : 'X dit/declare/repond/cria : "Y"' (extraction discours rapporte)
_REPORTED_SPEECH = re.compile(
    r"\b(?P<speaker>[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\s+"
    r"(?:dit|d[ée]clara|d[ée]clare|r[ée]pond|r[ée]pondit|cria|chuchota|"
    r"murmura|annon[cç]a|protesta|s[' ]exclama|lan[cç]a|repris|reprend)"
    r"\s*[:,]?\s*"
    r"[«\"](?P<line>[^»\"]+)[»\"]",
)


# Pattern pour pensees : *...* ou (pense que ...) ou (interieurement, ...)
_THOUGHT = re.compile(
    r"\*([^*]{3,})\*|\((?:pense\b[^)]*|interieurement[^)]*)\)",
    re.IGNORECASE,
)


def _detect_emotion(text: str) -> DialogueEmotion:
    for pattern, emo in _EMOTION_KEYWORDS:
        if pattern.search(text):
            return emo
    return DialogueEmotion.neutral


def _detect_tone(text: str) -> DialogueTone:
    for pattern, tone in _TONE_KEYWORDS:
        if pattern.search(text):
            return tone
    return DialogueTone.normal


def _detect_expression(emotion: DialogueEmotion) -> DialogueExpression:
    """Mapping basique emotion -> expression faciale par defaut."""
    return {
        DialogueEmotion.joyful: DialogueExpression.smile,
        DialogueEmotion.playful: DialogueExpression.smirk,
        DialogueEmotion.angry: DialogueExpression.glare,
        DialogueEmotion.sad: DialogueExpression.crying,
        DialogueEmotion.fearful: DialogueExpression.shocked,
        DialogueEmotion.surprised: DialogueExpression.eyes_wide,
        DialogueEmotion.smug: DialogueExpression.smirk,
        DialogueEmotion.embarrassed: DialogueExpression.blush,
        DialogueEmotion.contemplative: DialogueExpression.closed_eyes,
        DialogueEmotion.weary: DialogueExpression.closed_eyes,
        DialogueEmotion.desperate: DialogueExpression.crying,
    }.get(emotion, DialogueExpression.default)


# ============================================================================
# Formatter context
# ============================================================================


@dataclass
class _FormatterCtx:
    """Contexte injecte par le caller pour enrichir chaque DialogueLine."""

    in_game_year: int | None = None
    in_game_date: str | None = None
    location_id: str | None = None
    turn_number: int | None = None
    related_event_id: str | None = None
    related_mission_id: str | None = None
    scene_mood: str | None = None


# ============================================================================
# Formatter principal
# ============================================================================


class DialogueFormatter:
    """Convertit une sortie narration LLM en sequence de DialogueLines.

    Stateless : chaque appel a `format()` produit une nouvelle liste,
    le caller decide d'appender au DialogueLog.
    """

    def __init__(
        self,
        *,
        narrator_speaker_id: str = NARRATOR_SPEAKER_ID,
        split_narrative_by_sentence: bool = True,
        extract_reported_speech: bool = True,
    ) -> None:
        self._narrator_id = narrator_speaker_id
        self._split = split_narrative_by_sentence
        self._extract_reported = extract_reported_speech

    def format(
        self,
        *,
        narrative: str = "",
        npc_dialogue: list[dict] | None = None,
        in_game_year: int | None = None,
        in_game_date: str | None = None,
        location_id: str | None = None,
        turn_number: int | None = None,
        related_event_id: str | None = None,
        related_mission_id: str | None = None,
        scene_mood: str | None = None,
    ) -> list[DialogueLine]:
        """Genere la sequence de DialogueLines pour ce tour."""
        ctx = _FormatterCtx(
            in_game_year=in_game_year,
            in_game_date=in_game_date,
            location_id=location_id,
            turn_number=turn_number,
            related_event_id=related_event_id,
            related_mission_id=related_mission_id,
            scene_mood=scene_mood,
        )
        out: list[DialogueLine] = []

        # 1. Narrative -> narrator lines (split par phrase si demande)
        if narrative:
            out.extend(self._format_narrative(narrative, ctx))

        # 2. npc_dialogue : un DialogueLine par entree
        for entry in (npc_dialogue or []):
            speaker = (entry.get("character_id") or "").strip()
            text = (entry.get("line") or "").strip()
            if not speaker or not text:
                continue
            tone_hint = (entry.get("tone") or "").strip().lower()
            emotion = _detect_emotion(text)
            tone = self._tone_from_hint(tone_hint) or _detect_tone(text)
            out.append(self._make_line(
                speaker_id=speaker, text=text,
                emotion=emotion, tone=tone, ctx=ctx,
            ))

        return out

    def _format_narrative(
        self, narrative: str, ctx: _FormatterCtx,
    ) -> list[DialogueLine]:
        """Decoupe la narrative en lignes narrateur, extrait discours rapporte."""
        out: list[DialogueLine] = []

        # Extraction des discours rapportes : 'X dit : "Y"' -> attribue a X
        if self._extract_reported:
            consumed_spans: list[tuple[int, int]] = []
            for m in _REPORTED_SPEECH.finditer(narrative):
                speaker_token = m.group("speaker")
                line_text = m.group("line").strip()
                speaker_id = self._resolve_speaker(speaker_token)
                if speaker_id and line_text:
                    out.append(self._make_line(
                        speaker_id=speaker_id,
                        text=line_text,
                        emotion=_detect_emotion(line_text),
                        tone=_detect_tone(line_text),
                        ctx=ctx,
                    ))
                    consumed_spans.append((m.start(), m.end()))
            # Texte narrateur restant : on retire les spans consommes
            remaining = self._strip_spans(narrative, consumed_spans).strip()
        else:
            remaining = narrative.strip()

        if not remaining:
            return out

        # Pensees inline : *...* ou (pense que ...) -> narrator + is_thought
        thought_segments: list[tuple[int, int, str]] = []
        for m in _THOUGHT.finditer(remaining):
            content = (m.group(1) or m.group(0)).strip("()*\"' ").strip()
            if content:
                thought_segments.append((m.start(), m.end(), content))
        if thought_segments:
            for _start, _end, content in thought_segments:
                out.append(self._make_line(
                    speaker_id=self._narrator_id,
                    text=content,
                    emotion=_detect_emotion(content),
                    tone=DialogueTone.normal,
                    ctx=ctx,
                    is_thought=True,
                ))
            remaining = self._strip_spans(
                remaining, [(s, e) for s, e, _ in thought_segments]
            ).strip()

        if not remaining:
            return out

        # Decoupage final en phrases narrateur
        if self._split:
            sentences = self._split_sentences(remaining)
        else:
            sentences = [remaining]

        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 2:
                continue
            out.append(self._make_line(
                speaker_id=self._narrator_id,
                text=sentence,
                emotion=_detect_emotion(sentence),
                tone=_detect_tone(sentence),
                ctx=ctx,
            ))
        return out

    def _make_line(
        self,
        *,
        speaker_id: str,
        text: str,
        emotion: DialogueEmotion,
        tone: DialogueTone,
        ctx: _FormatterCtx,
        is_thought: bool = False,
    ) -> DialogueLine:
        return DialogueLine(
            speaker_id=speaker_id,
            text=text,
            emotion=emotion,
            tone=tone,
            expression=_detect_expression(emotion),
            in_game_year=ctx.in_game_year,
            in_game_date=ctx.in_game_date,
            location_id=ctx.location_id,
            turn_number=ctx.turn_number,
            related_event_id=ctx.related_event_id,
            related_mission_id=ctx.related_mission_id,
            scene_mood=ctx.scene_mood,
            is_thought=is_thought,
        )

    @staticmethod
    def _strip_spans(text: str, spans: list[tuple[int, int]]) -> str:
        if not spans:
            return text
        spans_sorted = sorted(spans, key=lambda s: -s[0])
        out = text
        for start, end in spans_sorted:
            out = out[:start] + out[end:]
        return out

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split simple par ponctuation forte. Pour MVP, suffisant."""
        parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÉÀÈÇÂÊÎÔÛŒ])", text)
        return [p.strip() for p in parts if p.strip()]

    @staticmethod
    def _tone_from_hint(hint: str) -> DialogueTone | None:
        if not hint:
            return None
        norm = hint.lower().strip()
        for member in DialogueTone:
            if member.value == norm:
                return member
        return None

    def _resolve_speaker(self, name_token: str) -> str | None:
        """Convertit un nom (ex: 'Naruto') en id canon. Lazy import pour
        eviter le couplage dur avec le module canon."""
        try:
            from shinobi.canon.fact_sheet import PRIMARY_NPC_NAMES
        except ImportError:
            return None
        n = (name_token or "").lower().strip()
        if not n:
            return None
        if n in PRIMARY_NPC_NAMES:
            return PRIMARY_NPC_NAMES[n]
        if " " in n:
            first = n.split()[0]
            return PRIMARY_NPC_NAMES.get(first)
        return None


__all__ = ["NARRATOR_SPEAKER_ID", "DialogueFormatter"]
