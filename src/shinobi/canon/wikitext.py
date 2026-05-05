"""Parsing du wikitext MediaWiki (infobox, sections, links).

Le wikitext utilise des templates {{...}} avec parametres pipe-separated et des
sections preced ees de === Title ===. On extrait :
- les templates de premier niveau et leurs parametres nommes
- les sections de prose
- les wiki-links [[Target|Display]]
- les categories [[Category:Name]]
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SECTION_HEADER = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.MULTILINE)
WIKI_LINK = re.compile(r"\[\[([^\[\]|]+?)(?:\|([^\[\]]+))?\]\]")
CATEGORY_LINK = re.compile(r"\[\[Category:([^\[\]|]+?)(?:\|[^\[\]]+)?\]\]", re.IGNORECASE)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
REF_TAG = re.compile(r"<ref[^/]*?/>|<ref[^>]*>.*?</ref>", re.DOTALL | re.IGNORECASE)
NOWIKI_TAG = re.compile(r"<nowiki>(.*?)</nowiki>", re.DOTALL | re.IGNORECASE)
TEMPLATE_NEWLINE = re.compile(r"\n+")
REDIRECT = re.compile(r"^\s*#REDIRECT\s*\[\[([^\[\]|]+?)(?:\|[^\[\]]+)?\]\]", re.IGNORECASE)


@dataclass
class WikiTemplate:
    """Template MediaWiki {{Name|param=value|...}}."""

    name: str
    params: dict[str, str] = field(default_factory=dict)


@dataclass
class WikiSection:
    """Section de prose."""

    level: int
    title: str
    body: str


@dataclass
class ParsedPage:
    """Resultat du parsing d'une page wikitext."""

    raw: str
    redirect_target: str | None
    templates: list[WikiTemplate]
    sections: list[WikiSection]
    categories: list[str]
    wiki_links: list[str]


def parse_wikitext(text: str) -> ParsedPage:
    """Parse une page wikitext et retourne sa structure."""
    if text is None:
        return ParsedPage(
            raw="", redirect_target=None, templates=[], sections=[], categories=[], wiki_links=[]
        )

    cleaned = HTML_COMMENT.sub("", text)
    cleaned = REF_TAG.sub("", cleaned)
    cleaned = NOWIKI_TAG.sub(r"\1", cleaned)

    redirect_target: str | None = None
    m = REDIRECT.search(cleaned)
    if m:
        redirect_target = m.group(1).strip()

    templates = _extract_templates(cleaned)
    sections = _extract_sections(cleaned)
    categories = [c.strip() for c in CATEGORY_LINK.findall(cleaned)]
    wiki_links = list({m.group(1).strip() for m in WIKI_LINK.finditer(cleaned)})

    return ParsedPage(
        raw=text,
        redirect_target=redirect_target,
        templates=templates,
        sections=sections,
        categories=categories,
        wiki_links=wiki_links,
    )


def _extract_templates(text: str) -> list[WikiTemplate]:
    """Extrait tous les templates de premier niveau."""
    out: list[WikiTemplate] = []
    pos = 0
    while pos < len(text):
        start = text.find("{{", pos)
        if start == -1:
            break
        end = _find_template_close(text, start + 2)
        if end == -1:
            break
        body = text[start + 2 : end]
        out.append(_parse_template_body(body))
        pos = end + 2
    return out


def _find_template_close(text: str, start: int) -> int:
    """Trouve le }} correspondant au {{ d'ouverture en respectant les nesting."""
    depth = 1
    i = start
    while i < len(text) - 1:
        if text[i] == "{" and text[i + 1] == "{":
            depth += 1
            i += 2
            continue
        if text[i] == "}" and text[i + 1] == "}":
            depth -= 1
            if depth == 0:
                return i
            i += 2
            continue
        i += 1
    return -1


def _parse_template_body(body: str) -> WikiTemplate:
    """Parse un corps de template (entre {{ et }})."""
    parts = _split_template_params(body)
    if not parts:
        return WikiTemplate(name="")
    name = parts[0].strip()
    params: dict[str, str] = {}
    positional_index = 1
    for part in parts[1:]:
        if "=" in part:
            head, _, value = part.partition("=")
            key = head.strip()
            params[key] = value.strip()
        else:
            params[str(positional_index)] = part.strip()
            positional_index += 1
    return WikiTemplate(name=name, params=params)


def _split_template_params(body: str) -> list[str]:
    """Split un body de template par | en respectant les niveaux de nesting."""
    parts: list[str] = []
    buf: list[str] = []
    depth_brace = 0
    depth_link = 0
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "{" and i + 1 < len(body) and body[i + 1] == "{":
            depth_brace += 1
            buf.append("{{")
            i += 2
            continue
        if ch == "}" and i + 1 < len(body) and body[i + 1] == "}":
            depth_brace -= 1
            buf.append("}}")
            i += 2
            continue
        if ch == "[" and i + 1 < len(body) and body[i + 1] == "[":
            depth_link += 1
            buf.append("[[")
            i += 2
            continue
        if ch == "]" and i + 1 < len(body) and body[i + 1] == "]":
            depth_link -= 1
            buf.append("]]")
            i += 2
            continue
        if ch == "|" and depth_brace == 0 and depth_link == 0:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _extract_sections(text: str) -> list[WikiSection]:
    """Decoupe le wikitext en sections en suivant les titres ==/===."""
    sections: list[WikiSection] = []
    matches = list(SECTION_HEADER.finditer(text))
    if not matches:
        sections.append(WikiSection(level=0, title="", body=text.strip()))
        return sections

    intro_end = matches[0].start()
    intro = text[:intro_end].strip()
    if intro:
        sections.append(WikiSection(level=0, title="(intro)", body=intro))

    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        sections.append(WikiSection(level=level, title=title, body=body))
    return sections


def find_template(parsed: ParsedPage, *names: str) -> WikiTemplate | None:
    """Retourne le premier template dont le nom matche (case-insensitive)."""
    lower_names = {n.lower() for n in names}
    for tpl in parsed.templates:
        if tpl.name.lower() in lower_names:
            return tpl
    return None


def strip_wiki_markup(text: str) -> str:
    """Nettoie une chaine wikitext pour extraire du texte brut."""
    s = HTML_COMMENT.sub("", text)
    s = REF_TAG.sub("", s)
    s = NOWIKI_TAG.sub(r"\1", s)

    def _replace_link(m: re.Match[str]) -> str:
        target = m.group(1).strip()
        display = m.group(2)
        return display.strip() if display else target

    s = WIKI_LINK.sub(_replace_link, s)
    s = re.sub(r"'''([^']+?)'''", r"\1", s)
    s = re.sub(r"''([^']+?)''", r"\1", s)
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    return TEMPLATE_NEWLINE.sub("\n", s).strip()


def split_list(value: str) -> list[str]:
    """Split un champ d'infobox en liste (separateurs <br>, *, virgule)."""
    if not value:
        return []
    items: list[str] = []
    cleaned = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    cleaned = re.sub(r"\*\s*", "\n", cleaned)
    for line in cleaned.split("\n"):
        line = strip_wiki_markup(line).strip(",;: \t")
        if line:
            items.append(line)
    return items
