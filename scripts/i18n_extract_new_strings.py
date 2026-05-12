"""Phase i18n.12 : detection des nouvelles cles FR ajoutees apres i18n.

Strategie :
1. Scan recursif de `src/shinobi/**/*.py` pour les call-sites `t("key", ...)`.
2. Compare l'ensemble des cles trouvees au catalogue canonique `en.json`.
3. Signale les cles utilisees dans le code mais absentes du catalogue =
   probable nouvelle chaine FR ajoutee sans i18n.

Sortie :
    NEW KEYS (in code, missing from en.json):
        - some.new.key.from.code
        - another.key

Exit code : 0 si pas de nouvelle cle, 1 sinon (utile pre-commit).

Note : c'est un detecteur, pas un correcteur. Le script
`i18n_translate_new.py` est l'etape suivante (traduit + ajoute aux
catalogues).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src" / "shinobi"
I18N_DIR = REPO_ROOT / "data" / "i18n"
CANONICAL_LANG = "en"

# Capture les call-sites `t("key", ...)` ou `t('key', ...)`.
# Tolerant : single ou double quote, multi-args, ne capture pas les
# f-strings (qui sont des cles dynamiques, hors scope detection).
_T_CALL = re.compile(
    r"""\bt\(\s*                          # t(
        ["']([a-zA-Z_][a-zA-Z0-9_.\-]*)["']  # "key" or 'key'
    """,
    re.VERBOSE,
)


def _load_canonical_keys() -> set[str]:
    path = I18N_DIR / f"{CANONICAL_LANG}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k for k in raw if not k.startswith("_")}


def scan_code() -> dict[str, list[Path]]:
    """Retourne dict {key: [files where used]}.

    Note :
    - ne scan pas les `.pyc`, ni les `__pycache__/`, ni les tests
      (les tests peuvent referencer des cles ad-hoc pour valider le fallback).
    - Saute `src/shinobi/i18n/__init__.py` qui contient des exemples
      `t("...")` dans son docstring (faux positifs).
    """
    out: dict[str, list[Path]] = {}
    i18n_init = SRC_DIR / "i18n" / "__init__.py"
    for path in SRC_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path == i18n_init:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # Strip docstrings / triple-quoted blocks pour eviter faux
        # positifs sur les exemples d'usage. Approche simple : retire
        # tout entre paires de `"""` (greedy avec DOTALL).
        text_no_docs = re.sub(
            r'"""[\s\S]*?"""', "", text,
        )
        for match in _T_CALL.finditer(text_no_docs):
            key = match.group(1)
            out.setdefault(key, []).append(path.relative_to(REPO_ROOT))
    return out


def detect_new_keys() -> dict[str, list[Path]]:
    """Retourne les cles trouvees dans le code mais absentes du canonical."""
    canonical = _load_canonical_keys()
    used = scan_code()
    return {k: files for k, files in used.items() if k not in canonical}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detect new i18n keys used in code but missing from en.json.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="JSON output (machine-readable).",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Silent, only exit code.",
    )
    args = parser.parse_args(argv)

    new_keys = detect_new_keys()
    if args.json:
        payload = {k: [str(p) for p in v] for k, v in new_keys.items()}
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif not args.quiet:
        if not new_keys:
            print("[i18n_extract] No new keys detected. OK")
            return 0
        print(f"[i18n_extract] {len(new_keys)} new key(s) found in code")
        print("[i18n_extract] (used in src/shinobi/**, missing from "
              "en.json):")
        for key, files in sorted(new_keys.items()):
            print(f"  {key}")
            for f in files[:3]:
                print(f"    - {f}")
            if len(files) > 3:
                print(f"    ... and {len(files) - 3} more")
        print(
            "\n[i18n_extract] Next step : run "
            "`python scripts/i18n_translate_new.py` to add these keys "
            "with auto-translation.",
            file=sys.stderr,
        )
    return 1 if new_keys else 0


if __name__ == "__main__":
    raise SystemExit(main())
