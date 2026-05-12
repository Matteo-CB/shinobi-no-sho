"""Phase i18n.12 : lint des catalogues i18n.

Verifie que tous les fichiers `data/i18n/<lang>.json` ont les memes cles
que la source canonique (`en.json`). Signale les cles manquantes par
langue + les cles extra (presentes dans une lang mais pas EN).

Strict mode (defaut) : exit 1 si une cle est manquante ou extra dans
quelconque catalogue. Garantit la parite catalog inter-langues.

Usage :

    python scripts/i18n_lint.py            # lint global
    python scripts/i18n_lint.py --json     # rapport JSON
    python scripts/i18n_lint.py --quiet    # silencieux, exit code uniquement

Le script est utilise par :
- Le pre-commit hook (`./scripts/pre-commit-i18n-lint.sh`).
- Le CI pour bloquer les regressions.
- Manuellement par le dev avant de commit une nouvelle cle.

Format de sortie console :

    [i18n_lint] EN catalog (canonical): 731 keys
    [i18n_lint] FR: 2 missing, 0 extra
    [i18n_lint]   missing: test.fallback_only_in_en, ...
    [i18n_lint] DE: 0 missing, 0 extra ✓
    ...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
I18N_DIR = REPO_ROOT / "data" / "i18n"
CANONICAL_LANG = "en"
SUPPORTED_LANGS = ("en", "fr", "es", "ja", "zh", "ko", "pt-BR", "de")


def _load_catalog(lang: str) -> dict[str, str]:
    """Charge un catalogue. Filtre :
    - meta-cles `_schema`, `_version` (commencent par `_`).
    - cles `test.*` reservees aux tests de fallback i18n (volontairement
      asymetriques entre catalogues).
    """
    path = I18N_DIR / f"{lang}.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    return {
        k: v for k, v in raw.items()
        if not k.startswith("_") and not k.startswith("test.")
    }


def lint_all() -> dict[str, dict]:
    """Audit complet. Retourne un dict {lang: {missing, extra, total}}.

    `missing` = cles presentes dans canonical mais absentes dans la lang.
    `extra` = cles presentes dans la lang mais absentes dans canonical.
    """
    canonical = _load_catalog(CANONICAL_LANG)
    canonical_keys = set(canonical.keys())
    report: dict[str, dict] = {}
    for lang in SUPPORTED_LANGS:
        if lang == CANONICAL_LANG:
            report[lang] = {
                "missing": [],
                "extra": [],
                "total": len(canonical_keys),
                "canonical": True,
            }
            continue
        cat = _load_catalog(lang)
        cat_keys = set(cat.keys())
        report[lang] = {
            "missing": sorted(canonical_keys - cat_keys),
            "extra": sorted(cat_keys - canonical_keys),
            "total": len(cat_keys),
            "canonical": False,
        }
    return report


def render_console(report: dict[str, dict], quiet: bool = False) -> None:
    if quiet:
        return
    canonical_total = report[CANONICAL_LANG]["total"]
    print(f"[i18n_lint] EN catalog (canonical): {canonical_total} keys")
    for lang, data in report.items():
        if data["canonical"]:
            continue
        missing = data["missing"]
        extra = data["extra"]
        if not missing and not extra:
            print(f"[i18n_lint] {lang.upper()}: {data['total']} keys OK")
        else:
            print(
                f"[i18n_lint] {lang.upper()}: {data['total']} keys, "
                f"{len(missing)} missing, {len(extra)} extra"
            )
            if missing:
                preview = ", ".join(missing[:5])
                suffix = f" (+ {len(missing) - 5} more)" if len(missing) > 5 else ""
                print(f"    missing: {preview}{suffix}")
            if extra:
                preview = ", ".join(extra[:5])
                suffix = f" (+ {len(extra) - 5} more)" if len(extra) > 5 else ""
                print(f"    extra: {preview}{suffix}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lint i18n catalog parity vs en.json.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output report as JSON (machine-readable).",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Silent, only exit code.",
    )
    parser.add_argument(
        "--allow-missing", action="store_true",
        help=(
            "Treat missing keys as warnings instead of errors. Extra keys "
            "still fail. Useful in early i18n bootstrapping."
        ),
    )
    args = parser.parse_args(argv)

    report = lint_all()

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        render_console(report, quiet=args.quiet)

    # Exit code : 0 si tout est aligne, 1 sinon.
    failures = 0
    for _lang, data in report.items():
        if data["canonical"]:
            continue
        if data["extra"]:
            failures += 1
        if data["missing"] and not args.allow_missing:
            failures += 1
    if failures and not args.quiet:
        print(
            f"\n[i18n_lint] FAILED : {failures} catalogue(s) divergent(s).",
            file=sys.stderr,
        )
        return 1
    if not args.quiet:
        print("\n[i18n_lint] OK : tous les catalogues sont alignes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
