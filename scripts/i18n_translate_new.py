"""Phase i18n.12 : traduit incrementalement les nouvelles cles i18n.

Workflow typique du dev :
1. Le dev ajoute du code avec `t("new.key")` + ajoute `"new.key": "Texte FR"`
   a `data/i18n/fr.json` (la source manuelle).
2. Le dev run `python scripts/i18n_translate_new.py`.
3. Le script :
   - Detecte les cles presentes dans fr.json mais absentes en en.json
     (= nouvelles cles ajoutees apres la phase i18n batch).
   - Pour chaque cle, demande a Anthropic Sonnet de traduire vers les
     8 langues (EN canonical + 7 autres).
   - Ajoute les traductions dans tous les catalogues.
4. `scripts/i18n_lint.py` retourne 0 ensuite.

Cost typique : ~$0.10 par run (qq cles a la fois).

Le script reuse le client Anthropic et le glossary (chakra, Hokage, etc.)
preserve via la liste centralisee `data/i18n/glossary.json`.

Mode --dry-run : montre ce qui sera traduit sans appeler l'API.
Mode --backend qwen : utilise Qwen3-4B local au lieu de Sonnet (gratuit).

Usage :

    python scripts/i18n_translate_new.py --dry-run    # preview
    python scripts/i18n_translate_new.py              # Sonnet (paid)
    python scripts/i18n_translate_new.py --backend qwen  # local free
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
I18N_DIR = REPO_ROOT / "data" / "i18n"
GLOSSARY_PATH = I18N_DIR / "glossary.json"
SOURCE_LANG = "fr"  # langue source manuelle (le dev tape en FR)
CANONICAL_LANG = "en"
TARGET_LANGS = ("en", "es", "ja", "zh", "ko", "pt-BR", "de")  # cibles
LANG_NAMES = {
    "en": "English",
    "fr": "French",
    "es": "Spanish",
    "ja": "Japanese",
    "zh": "Mandarin Chinese (Simplified)",
    "ko": "Korean",
    "pt-BR": "Brazilian Portuguese",
    "de": "German",
}


def _load_catalog(lang: str) -> dict[str, str]:
    path = I18N_DIR / f"{lang}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if isinstance(v, str)}


def _save_catalog(lang: str, payload: dict[str, str]) -> None:
    path = I18N_DIR / f"{lang}.json"
    # Reload to keep meta-keys + ordering
    raw = json.loads(path.read_text(encoding="utf-8"))
    for k, v in payload.items():
        raw[k] = v
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_glossary_terms() -> list[str]:
    if not GLOSSARY_PATH.exists():
        return []
    raw = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
    terms: list[str] = []
    for category, items in raw.items():
        if category.startswith("_"):
            continue
        if isinstance(items, list):
            terms.extend(str(t) for t in items if isinstance(t, str))
    return sorted(set(terms), key=lambda s: (-len(s), s.lower()))


def detect_new_keys() -> dict[str, str]:
    """Retourne {key: french_value} pour les cles presentes dans
    `fr.json` mais absentes dans `en.json` (= nouvelles cles a traduire).
    """
    fr = _load_catalog(SOURCE_LANG)
    en = _load_catalog(CANONICAL_LANG)
    return {k: v for k, v in fr.items() if k not in en}


def _build_prompt(
    new_keys: dict[str, str], target_lang: str, glossary: list[str],
) -> tuple[str, str]:
    target_name = LANG_NAMES.get(target_lang, target_lang)
    glossary_block = ", ".join(glossary[:80])
    system = (
        f"You are a professional translator. Translate the following i18n "
        f"keys from French to {target_name}. "
        f"PRESERVE these Naruto-universe terms verbatim (do NOT translate): "
        f"{glossary_block}. "
        f"Keep all `{{placeholder}}` tokens intact. "
        f"Output ONLY valid JSON mapping key -> translated string. No "
        f"preamble, no markdown."
    )
    user = (
        f"Translate to {target_name}:\n\n"
        + json.dumps(new_keys, ensure_ascii=False, indent=2)
    )
    return system, user


def _call_anthropic(system: str, user: str, model: str) -> str:
    """Appel Anthropic Sonnet. Necessite ANTHROPIC_API_KEY."""
    import anthropic  # type: ignore[import-not-found]

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(
        block.text for block in response.content if hasattr(block, "text")
    )


def _call_qwen(system: str, user: str) -> str:
    """Appel Qwen3-4B local via llama.cpp HTTP (gratuit)."""
    import httpx  # type: ignore[import-not-found]

    payload = {
        "model": "Qwen3-4B-UD-Q4_K_XL.gguf",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 4096,
        "temperature": 0.2,
    }
    with httpx.Client(timeout=120.0) as cli:
        resp = cli.post(
            "http://localhost:8080/v1/chat/completions", json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"]


def _parse_json_response(raw: str) -> dict[str, str]:
    s = raw.strip()
    if s.startswith("```"):
        # Strip fences markdown
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"No JSON object in response: {raw[:200]!r}")
    parsed = json.loads(s[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("response is not a JSON object")
    return {str(k): str(v) for k, v in parsed.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Translate new i18n keys (in fr.json, absent in en.json) "
                    "to all supported languages.",
    )
    parser.add_argument(
        "--backend", choices=("anthropic", "qwen"), default="anthropic",
        help="LLM backend (anthropic Sonnet $$ or local Qwen3-4B free).",
    )
    parser.add_argument(
        "--model", default="claude-sonnet-4-6",
        help="Anthropic model (ignored if backend=qwen).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show detected new keys, do not call LLM nor write files.",
    )
    args = parser.parse_args(argv)

    new_keys = detect_new_keys()
    if not new_keys:
        print("[i18n_translate_new] No new keys detected (fr.json aligned).")
        return 0

    print(f"[i18n_translate_new] Found {len(new_keys)} new key(s) in fr.json:")
    for k in sorted(new_keys):
        print(f"  + {k}: {new_keys[k][:60]!r}")
    if args.dry_run:
        print("\n[i18n_translate_new] dry-run, no API call, no write.")
        return 0

    if args.backend == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print(
                "[i18n_translate_new] ANTHROPIC_API_KEY not set. Use "
                "--backend qwen for free local backend.",
                file=sys.stderr,
            )
            return 2

    glossary = _load_glossary_terms()
    failures = 0
    for lang in TARGET_LANGS:
        if lang == SOURCE_LANG:
            continue
        print(f"\n[i18n_translate_new] -> {lang}")
        system, user = _build_prompt(new_keys, lang, glossary)
        try:
            if args.backend == "anthropic":
                raw = _call_anthropic(system, user, args.model)
            else:
                raw = _call_qwen(system, user)
            parsed = _parse_json_response(raw)
        except Exception as exc:
            print(
                f"  ERROR : {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            failures += 1
            continue
        # On accepte seulement les cles qu'on a demandees
        applied = {k: parsed[k] for k in new_keys if k in parsed}
        missing = [k for k in new_keys if k not in parsed]
        _save_catalog(lang, applied)
        print(f"  applied {len(applied)}/{len(new_keys)} keys")
        if missing:
            print(f"  WARNING : missing translations for {missing[:5]}")
    if failures:
        print(
            f"\n[i18n_translate_new] {failures} target lang(s) failed.",
            file=sys.stderr,
        )
        return 1
    print("\n[i18n_translate_new] DONE. Run `i18n_lint.py` to verify.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
