"""Formattage du contexte pour injection dans le prompt LLM."""

from __future__ import annotations

from shinobi.rag.retriever import RetrievedChunk, RetrievedContext


def format_context(
    ctx: RetrievedContext,
    *,
    max_tokens: int = 2500,
    chars_per_token: int = 4,
) -> str:
    """Compose une section [CONTEXTE CANONIQUE] pour le system prompt."""
    chunks = ctx.deduplicated(max_count=20)
    sections: dict[str, list[RetrievedChunk]] = {
        "characters": [],
        "techniques": [],
        "clans": [],
        "villages": [],
        "events": [],
        "dialogue": [],
        "lore": [],
    }
    for c in chunks:
        sections.setdefault(c.type, []).append(c)

    out: list[str] = ["[CONTEXTE CANONIQUE]"]
    if sections.get("characters"):
        out.append("\n## Personnages")
        for c in sections["characters"]:
            out.append(_indent(c.text))
    if sections.get("techniques"):
        out.append("\n## Techniques")
        for c in sections["techniques"]:
            out.append(_indent(c.text))
    if sections.get("clans"):
        out.append("\n## Clans")
        for c in sections["clans"]:
            out.append(_indent(c.text))
    if sections.get("villages"):
        out.append("\n## Lieux")
        for c in sections["villages"]:
            out.append(_indent(c.text))
    if sections.get("events"):
        out.append("\n## Evenements")
        for c in sections["events"]:
            out.append(_indent(c.text))
    if sections.get("dialogue"):
        out.append("\n## Voix des PNJ presents")
        for c in sections["dialogue"]:
            out.append(_indent(c.text))
    out.append("\n[FIN CONTEXTE]")

    text = "\n".join(out)
    budget = max_tokens * chars_per_token
    if len(text) > budget:
        text = text[:budget].rsplit("\n", 1)[0] + "\n[CONTEXTE TRONQUE]"
    return text


def _indent(text: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())
