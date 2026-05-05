"""Purge les references casssees inter-datasets non auto-reparables.

Pour chaque ref cassee detectee par integrity (ex: techniques.canonical_users
pointant vers un NPC inexistant), on la supprime du dataset.

Resultat : integrity = 0 broken refs.

Usage : python scripts/purge_broken_refs.py [--dry-run]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from shinobi.canon.integrity import (  # noqa: E402
    collect_unfixable_broken_refs,
    validate_canon_integrity,
)
from shinobi.canon.loader import load_canon, reset_canon_cache  # noqa: E402
from shinobi.config import settings  # noqa: E402

cli = typer.Typer(add_completion=False, no_args_is_help=False)


def _purge_list_refs(items: list, broken_set: set[str]) -> tuple[list, int]:
    """Filtre une liste pour enlever les refs cassees. Retourne (clean_list, removed_count)."""
    if not isinstance(items, list):
        return items, 0
    clean = [x for x in items if not (isinstance(x, str) and x in broken_set)]
    return clean, len(items) - len(clean)


def _purge_techniques(canon_dir: Path, broken: set[str]) -> int:
    """Purge canonical_users dans techniques.json."""
    path = canon_dir / "techniques.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    removed = 0
    for entry in data:
        users = entry.get("canonical_users", [])
        clean, n = _purge_list_refs(users, broken)
        if n:
            entry["canonical_users"] = clean
            removed += n
    if removed:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return removed


def _purge_clans(canon_dir: Path, broken: set[str]) -> int:
    """Purge key_kekkei_genkai/key_techniques/exclusive_techniques dans clans.json."""
    path = canon_dir / "clans.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    removed = 0
    for entry in data:
        for field in ("key_kekkei_genkai", "key_techniques", "exclusive_techniques"):
            items = entry.get(field, [])
            clean, n = _purge_list_refs(items, broken)
            if n:
                entry[field] = clean
                removed += n
    if removed:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return removed


def _purge_villages(canon_dir: Path, broken: set[str]) -> int:
    """Purge main_clans + kage_lineage dans villages.json."""
    path = canon_dir / "villages.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    removed = 0
    for entry in data:
        items = entry.get("main_clans", [])
        clean, n = _purge_list_refs(items, broken)
        if n:
            entry["main_clans"] = clean
            removed += n
        kages = entry.get("kage_lineage", [])
        clean_kages = [k for k in kages if k.get("character_id") not in broken]
        n2 = len(kages) - len(clean_kages)
        if n2:
            entry["kage_lineage"] = clean_kages
            removed += n2
    if removed:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return removed


def _purge_events(canon_dir: Path, broken: set[str]) -> int:
    """Purge involved_characters + location dans timeline_events.json."""
    path = canon_dir / "timeline_events.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    removed = 0
    for entry in data:
        items = entry.get("involved_characters", [])
        clean, n = _purge_list_refs(items, broken)
        if n:
            entry["involved_characters"] = clean
            removed += n
        loc = entry.get("location")
        if loc and loc in broken:
            entry["location"] = None
            removed += 1
    if removed:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return removed


def _purge_organizations(canon_dir: Path, broken: set[str]) -> int:
    """Purge founders + leaders + members dans organizations.json."""
    path = canon_dir / "organizations.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    removed = 0
    for entry in data:
        items = entry.get("founders", [])
        clean, n = _purge_list_refs(items, broken)
        if n:
            entry["founders"] = clean
            removed += n
        leaders = entry.get("leaders_by_era", [])
        clean_leaders = [le for le in leaders if le.get("leader") not in broken]
        n2 = len(leaders) - len(clean_leaders)
        if n2:
            entry["leaders_by_era"] = clean_leaders
            removed += n2
        members_eras = entry.get("members_by_era", [])
        for me in members_eras:
            members = me.get("members", [])
            clean_m, n3 = _purge_list_refs(members, broken)
            if n3:
                me["members"] = clean_m
                removed += n3
    if removed:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return removed


def _purge_tailed_beasts(canon_dir: Path, broken: set[str]) -> int:
    """Purge jinchuuriki entries dans tailed_beasts.json (set jinchuuriki=None)."""
    path = canon_dir / "tailed_beasts.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    removed = 0
    for entry in data:
        jin_eras = entry.get("current_jinchuuriki_by_era", [])
        for je in jin_eras:
            j = je.get("jinchuuriki")
            if j and j in broken:
                je["jinchuuriki"] = ""
                removed += 1
    if removed:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return removed


@cli.command()
def purge(
    dry_run: bool = typer.Option(False, "--dry-run", help="Affiche sans modifier"),
) -> None:
    """Purge toutes les refs cassees non auto-reparables des datasets canon."""
    print("Chargement du canon...")
    reset_canon_cache()
    bundle = load_canon()
    print()
    print("Audit referentiel...")
    report = validate_canon_integrity(bundle, auto_fix=True)
    broken = collect_unfixable_broken_refs(report)
    print(f"Refs cassees non reparables : {len(broken)}")
    if dry_run:
        print("DRY RUN : pas de modification.")
        sample = list(broken)[:20]
        print(f"Exemples (20 premiers): {sample}")
        raise typer.Exit(0)
    if not broken:
        print("Rien a purger.")
        raise typer.Exit(0)
    canon_dir = settings.canonical_data_dir
    print()
    print("Purge des refs cassees dans les datasets...")
    total = 0
    total += _purge_techniques(canon_dir, broken)
    total += _purge_clans(canon_dir, broken)
    total += _purge_villages(canon_dir, broken)
    total += _purge_events(canon_dir, broken)
    total += _purge_organizations(canon_dir, broken)
    total += _purge_tailed_beasts(canon_dir, broken)
    print(f"Total : {total} refs supprimees.")
    print()
    print("Verifie avec : python scripts/repair_canon_refs.py report-only")


if __name__ == "__main__":
    cli()
