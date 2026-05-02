"""Repare les references inter-datasets cassees (sakura_haruno -> haruno_sakura, etc.).

1. Charge le canon.
2. Lance validate_canon_integrity() qui detecte les refs cassees + suggere des fixes.
3. Applique les fixes auto-reparables sur les fichiers JSON canon.
4. Pour les non-reparables, log un rapport.

Usage : python scripts/repair_canon_refs.py [--dry-run]
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from shinobi.canon.integrity import format_report, validate_canon_integrity  # noqa: E402
from shinobi.canon.loader import load_canon, reset_canon_cache  # noqa: E402
from shinobi.config import settings  # noqa: E402

cli = typer.Typer(add_completion=False, no_args_is_help=False)


def _apply_substitutions_to_json(
    json_path: Path, substitutions: dict[str, str], *, dry_run: bool
) -> int:
    """Applique les substitutions de strings dans un JSON canon. Retourne nb remplacements."""
    text = json_path.read_text(encoding="utf-8")
    count = 0
    for old, new in substitutions.items():
        # Match strict : entre guillemets pour cibler les valeurs JSON
        old_quoted = f'"{old}"'
        new_quoted = f'"{new}"'
        n = text.count(old_quoted)
        if n > 0:
            text = text.replace(old_quoted, new_quoted)
            count += n
    if count > 0 and not dry_run:
        json_path.write_text(text, encoding="utf-8")
    return count


@cli.command()
def repair(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Affiche les changements sans les appliquer"
    ),
) -> None:
    """Repare les refs cassees auto-reparables et affiche le rapport."""
    print("Chargement du canon...")
    reset_canon_cache()
    bundle = load_canon()
    print()
    print("Audit referentiel...")
    report = validate_canon_integrity(bundle, auto_fix=True)
    print(format_report(report))
    print()
    if not report.auto_fixable:
        print("Aucune reparation auto possible.")
        raise typer.Exit(0)

    if dry_run:
        print(f"DRY RUN : {len(report.auto_fixable)} substitutions identifiees, "
              "ne sont PAS appliquees.")
        raise typer.Exit(0)

    canon_dir = settings.canonical_data_dir
    total_replacements = 0
    files_touched = 0
    for json_file in canon_dir.glob("*.json"):
        n = _apply_substitutions_to_json(json_file, report.auto_fixable, dry_run=dry_run)
        if n > 0:
            print(f"  {json_file.name}: {n} substitutions")
            files_touched += 1
            total_replacements += n
    print()
    print(f"Repare : {total_replacements} refs cassees dans {files_touched} fichiers.")
    print("Relance le canon pour verifier :")
    print("  python -c 'from shinobi.canon.loader import load_canon; load_canon()'")


@cli.command()
def report_only() -> None:
    """Affiche juste le rapport, sans modification."""
    reset_canon_cache()
    bundle = load_canon()
    report = validate_canon_integrity(bundle, auto_fix=True)
    print(format_report(report))


if __name__ == "__main__":
    cli()
