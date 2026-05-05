"""Parse les wikitext scrapes vers des JSON intermediaires structures.

Pour chaque .wikitext :
- detection du type d'entite via les categories
- parsing du wikitext (templates, sections, links)
- ecriture d'un JSON intermediaire dans data/raw/narutopedia/parsed/<type>/<pageid>.json

Le format intermediaire est volontairement libre : il garde tous les params bruts
des templates et toute la prose. Le mapping vers le schema canonique se fait dans
build_canonical_jsons.py.

Usage :
  python scripts/parse_narutopedia.py
  python scripts/parse_narutopedia.py --types character,technique
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import typer

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from shinobi.canon.classifier import EntityType, classify_categories  # noqa: E402
from shinobi.canon.wikitext import (  # noqa: E402
    ParsedPage,
    parse_wikitext,
    strip_wiki_markup,
)
from shinobi.config import settings  # noqa: E402
from shinobi.logging_setup import configure_logging, get_logger  # noqa: E402

configure_logging()
logger = get_logger("parse_narutopedia")

cli = typer.Typer(add_completion=False, no_args_is_help=False)


@cli.command()
def parse(
    types: str = typer.Option(
        "all",
        help="CSV des types d'entites a parser. 'all' pour tous.",
    ),
    raw_dir: str = typer.Option(
        "data/raw/narutopedia",
        help="Repertoire raw du scrape.",
    ),
) -> None:
    """Parse tous les wikitext scrapes."""
    base = (
        (settings.canonical_data_dir.parent / "raw" / "narutopedia")
        if raw_dir == "data/raw/narutopedia"
        else Path(raw_dir)
    )
    pages_dir = base / "pages"
    meta_dir = base / "meta"
    parsed_dir = base / "parsed"

    if not pages_dir.exists():
        raise typer.BadParameter(f"Repertoire des pages introuvable: {pages_dir}")

    accepted: set[EntityType] | None = None
    if types != "all":
        accepted = {EntityType(t.strip()) for t in types.split(",") if t.strip()}

    counts: Counter[EntityType] = Counter()
    skipped = 0
    redirects = 0
    errors = 0

    for meta_path in meta_dir.glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            errors += 1
            continue

        pageid = meta["pageid"]
        title = meta["title"]
        categories = meta.get("categories", [])

        wikitext_files = list(pages_dir.glob(f"{pageid}_*.wikitext"))
        if not wikitext_files:
            skipped += 1
            continue
        wikitext = wikitext_files[0].read_text(encoding="utf-8")

        parsed = parse_wikitext(wikitext)
        if parsed.redirect_target:
            redirects += 1
            counts[EntityType.redirect] += 1
            continue

        entity_type = classify_categories(categories)
        if accepted is not None and entity_type not in accepted:
            counts[entity_type] += 1
            continue

        counts[entity_type] += 1

        out_dir = parsed_dir / entity_type.value
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{pageid}.json"
        payload = {
            "pageid": pageid,
            "title": title,
            "categories": categories,
            "entity_type": entity_type.value,
            "wiki_links": parsed.wiki_links,
            "templates": [{"name": t.name, "params": t.params} for t in parsed.templates],
            "sections": [
                {"level": s.level, "title": s.title, "text": strip_wiki_markup(s.body)}
                for s in parsed.sections
            ],
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(
        "parse_done",
        counts=dict(counts),
        skipped=skipped,
        redirects=redirects,
        errors=errors,
    )
    print("Counts par type:")
    for t, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {t.value}: {n}")
    print(f"Redirects: {redirects}")
    print(f"Skipped (no wikitext): {skipped}")
    print(f"Errors: {errors}")


def parse_one(meta: dict, wikitext: str) -> tuple[EntityType, ParsedPage]:
    """Helper expose pour tests : parse une page et la classifie."""
    parsed = parse_wikitext(wikitext)
    if parsed.redirect_target:
        return EntityType.redirect, parsed
    return classify_categories(meta.get("categories", [])), parsed


if __name__ == "__main__":
    cli()
