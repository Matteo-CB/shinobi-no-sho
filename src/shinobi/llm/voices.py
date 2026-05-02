"""Application des voice profiles aux PNJ presents dans la narration."""

from __future__ import annotations

from shinobi.canon.models import CanonBundle, VoiceProfile


def voice_block_for(canon: CanonBundle, character_id: str) -> str | None:
    """Compose un bloc texte de voice profile pour injection prompt.

    Si le PNJ a un voice_profile_id assigne, utilise le profil canonique.
    Sinon, genere un fallback heuristique base sur clan/rang/age pour eviter
    que la narration recouvre tous les PNJ d'une voix neutre.
    """
    char = canon.characters.get(character_id)
    if char is None:
        return None
    if char.voice_profile_id:
        voice = canon.voice_profiles.get(char.voice_profile_id)
        if voice is not None:
            return _format_voice(voice, character_id)
    return _fallback_voice_block(canon, character_id)


def _fallback_voice_block(canon: CanonBundle, character_id: str) -> str:
    """Heuristique : registre derive du clan et du dernier rang connu."""
    char = canon.characters[character_id]
    clan = (char.clan or "").lower()
    last_rank = char.rank_progression[-1].rank.lower() if char.rank_progression else ""
    lines = [f"- {character_id}"]
    if "kage" in last_rank or "leader" in last_rank:
        lines.append("  Registre : autorite, formulations posees, peu de mots")
    elif "jonin" in last_rank or "anbu" in last_rank or "sannin" in last_rank:
        lines.append("  Registre : adulte experimente, ton assure")
    elif "chunin" in last_rank:
        lines.append("  Registre : adulte jeune, professionnel")
    elif "genin" in last_rank or "academy" in last_rank or "student" in last_rank:
        lines.append("  Registre : jeune, mots expressifs ou hesitants")
    else:
        lines.append("  Registre : neutre, adapte au contexte")
    if clan == "uchiha":
        lines.append("  Themes lexicaux : honneur, fierte, regard")
    elif clan == "hyuga":
        lines.append("  Themes lexicaux : destin, branche, byakugan")
    elif clan == "nara":
        lines.append("  Themes lexicaux : ennui calcule, strategie, ombre")
    elif clan == "inuzuka":
        lines.append("  Themes lexicaux : meute, instinct, odeur")
    elif clan == "akimichi":
        lines.append("  Themes lexicaux : nourriture, force, amitie")
    elif clan == "yamanaka":
        lines.append("  Themes lexicaux : esprit, fleurs, communication")
    elif clan == "aburame":
        lines.append("  Themes lexicaux : insectes, logique, observation distante")
    lines.append("  Sample 1 : (voix non documentee canon, garde la coherence du contexte)")
    return "\n".join(lines)


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
