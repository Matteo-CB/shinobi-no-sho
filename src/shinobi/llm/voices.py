"""Application des voice profiles aux PNJ presents dans la narration."""

from __future__ import annotations

from shinobi.canon.models import CanonBundle, VoiceProfile


def voice_block_for(canon: CanonBundle, character_id: str) -> str | None:
    """Compose un bloc texte de voice profile pour injection prompt."""
    char = canon.characters.get(character_id)
    if char is None or char.voice_profile_id is None:
        return None
    voice = canon.voice_profiles.get(char.voice_profile_id)
    if voice is None:
        return None
    return _format_voice(voice, character_id)


def _format_voice(voice: VoiceProfile, character_id: str) -> str:
    lines = [
        f"- {character_id}",
        f"  Registre : {voice.register_fr}",
    ]
    if voice.verbal_tics:
        lines.append("  Tics verbaux : " + ", ".join(voice.verbal_tics))
    if voice.vocabulary_themes:
        lines.append("  Themes lexicaux : " + ", ".join(voice.vocabulary_themes))
    if voice.syntactic_patterns:
        lines.append("  Patterns syntaxiques : " + ", ".join(voice.syntactic_patterns))
    if voice.sample_lines:
        for i, sample in enumerate(voice.sample_lines[:3], start=1):
            lines.append(f"  Sample {i} : {sample}")
    if voice.do_not_use:
        lines.append("  A eviter : " + ", ".join(voice.do_not_use))
    return "\n".join(lines)


def compose_voice_section(canon: CanonBundle, npc_ids: list[str]) -> str:
    """Compose la section 'PNJ presents et leur voix' pour le prompt."""
    blocks = []
    for npc_id in npc_ids:
        block = voice_block_for(canon, npc_id)
        if block:
            blocks.append(block)
    if not blocks:
        return ""
    return "PNJ presents et leur voix :\n" + "\n".join(blocks)
