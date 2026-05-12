"""Phase i18n.6.B : on-the-fly wiki translator (Qwen3-4B fallback).

Strategie :
- Pre-traduits via Sonnet : 100 chars top dans `data/i18n/wiki/<lang>/<id>.json`
  (Phase 6.A). Lecture < 50ms.
- Hors top-100 : appel a Qwen3-4B local. 5-15s pour le 1er acces, puis cache
  persistant dans `data/i18n/wiki/<lang>/<id>.json`.
- Hors-ligne : si Qwen ne repond pas, retourne le source EN avec marqueur
  `_translation_pending: True`. Le jeu reste fonctionnel.

Cle d'invalidation : aucune (les wiki sont du contenu canon stable). Une
re-traduction force est possible via `force=True`.

API publique :

```python
from shinobi.i18n.wiki_translator import get_wiki_sections

# Sync (lit le cache si dispo, sinon delegue)
sections = get_wiki_sections(
    char_id="uchiha_sasuke",
    lang="ja",
    canon_characters=canon.characters,  # dict[str, CanonCharacter]
)
# {"Background": "...", "Personality": "...", "Abilities": "..."}
```
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Native lang names pour les prompts (eviter "ja" -> garder "Japanese").
_LANG_NATIVE_NAMES = {
    "fr": "French",
    "es": "Spanish (Spain)",
    "ja": "Japanese",
    "zh": "Mandarin Chinese (Simplified)",
    "ko": "Korean",
    "pt-BR": "Brazilian Portuguese",
    "de": "German",
}

WIKI_SECTIONS = ("Background", "Personality", "Abilities")
PENDING_MARKER_KEY = "_translation_pending"

# Module-level paths (resolved relative to repo root)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
WIKI_DIR = _REPO_ROOT / "data" / "i18n" / "wiki"
GLOSSARY_PATH = _REPO_ROOT / "data" / "i18n" / "glossary.json"


def load_default_glossary() -> tuple[str, ...]:
    """Charge la liste des termes glossary preserves depuis data/i18n/glossary.json.

    Retourne tuple vide si le fichier n'existe pas (mode degrade).
    """
    if not GLOSSARY_PATH.exists():
        logger.warning("glossary file not found at %s", GLOSSARY_PATH)
        return ()
    try:
        data: dict[str, Any] = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("glossary parse error: %s", exc)
        return ()
    terms: list[str] = []
    for category, items in data.items():
        if category.startswith("_"):
            continue
        if isinstance(items, list):
            terms.extend(str(t) for t in items if isinstance(t, str))
    # Sort by length desc to prefer longer matches
    return tuple(sorted(set(terms), key=lambda s: (-len(s), s.lower())))


class WikiCacheError(Exception):
    """Erreur de cache wiki (lecture/ecriture)."""


# Identifiants canon : alphanumerique + underscore + tiret. Pas de slash, pas
# de dots (sauf le suffixe .json applique en fin). Filtre defensif contre
# path traversal sur cache_path() meme si les ids viennent du canon.
_SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]*$")


def _ensure_safe_segment(name: str, kind: str) -> str:
    """Valide qu'un segment de path (char_id/lang) ne contient pas de chars
    dangereux pour path traversal. Leve ValueError si invalide."""
    if not name or not _SAFE_ID_PATTERN.match(name):
        raise ValueError(f"unsafe {kind} segment: {name!r}")
    return name


def cache_path(char_id: str, lang: str, base_dir: Path | None = None) -> Path:
    """Chemin du fichier cache pour (char, lang).

    Filtre defensif : refuse les segments contenant `/`, `..`, ou caracteres
    suspects pouvant escaper `base_dir`. Leve ValueError sinon.
    """
    base = base_dir or WIKI_DIR
    safe_id = _ensure_safe_segment(char_id, "char_id")
    safe_lang = _ensure_safe_segment(lang, "lang")
    return base / safe_lang / f"{safe_id}.json"


def load_cached(char_id: str, lang: str, base_dir: Path | None = None) -> dict[str, Any] | None:
    """Lit le cache wiki si present + valide JSON. None si absent."""
    p = cache_path(char_id, lang, base_dir)
    if not p.exists():
        return None
    try:
        data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
        return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("wiki_cache_corrupt char=%s lang=%s err=%s", char_id, lang, exc)
        return None


def write_cache(
    char_id: str,
    lang: str,
    sections: dict[str, str],
    *,
    pending: bool = False,
    base_dir: Path | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Ecrit le cache wiki localise. Cree le repertoire si besoin."""
    p = cache_path(char_id, lang, base_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "_schema": "i18n_wiki_v1",
        "_language": lang,
        "_char_id": char_id,
    }
    if pending:
        payload[PENDING_MARKER_KEY] = True
    if extra:
        payload.update(extra)
    for section in WIKI_SECTIONS:
        payload[section] = sections.get(section, "")
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def _extract_source_sections(canon_char: Any) -> dict[str, str]:
    """Extrait les 3 sections wiki source EN d'un canon Character.

    Supporte 3 formes de canon_char :
    - Plain dict (raw JSON) : `canon_char["wiki_sections"]`
    - Pydantic v2 model : `canon_char.wiki_sections` ou `model_dump()`
    - Object avec attribut direct
    """
    ws: dict[str, Any] = {}
    # 1. Plain dict
    if isinstance(canon_char, dict):
        ws_raw = canon_char.get("wiki_sections", {})
        if isinstance(ws_raw, dict):
            ws = ws_raw
    else:
        # 2. Object avec attribut direct
        ws_attr = getattr(canon_char, "wiki_sections", None)
        if isinstance(ws_attr, dict):
            ws = ws_attr
        elif ws_attr is None:
            # 3. Pydantic v2 model
            try:
                dumped = canon_char.model_dump()  # type: ignore[union-attr]
                ws_dump = dumped.get("wiki_sections", {})
                if isinstance(ws_dump, dict):
                    ws = ws_dump
            except AttributeError:
                pass
    return {section: str(ws.get(section, "") or "") for section in WIKI_SECTIONS}


def _all_sections_empty(sections: dict[str, str]) -> bool:
    return not any(sections.values())


def fallback_to_source(
    char_id: str,
    lang: str,
    source_sections: dict[str, str],
    *,
    base_dir: Path | None = None,
    write_marker: bool = True,
) -> dict[str, str]:
    """Retourne les sections source EN avec marqueur pending. Cache si demande."""
    if write_marker:
        try:
            write_cache(char_id, lang, source_sections, pending=True, base_dir=base_dir)
        except OSError as exc:
            logger.warning("wiki_fallback_write_failed char=%s lang=%s err=%s", char_id, lang, exc)
    return dict(source_sections)


# Translator backend protocol : tout objet ayant `translate(sections, lang) -> dict[str,str]`.

class TranslatorBackend:
    """Interface : un backend traduit 3 sections vers une langue cible."""

    def translate(self, sections: dict[str, str], lang: str) -> dict[str, str]:
        raise NotImplementedError


def _build_translation_prompt(sections: dict[str, str], lang_native: str, glossary: Iterable[str]) -> tuple[str, str]:
    """Construit (system_prompt, user_prompt) pour la traduction wiki."""
    system = (
        f"You are a professional translator of Naruto wiki content. "
        f"Translate from English to {lang_native}.\n\n"
        f"RULES:\n"
        f"1. Output VALID JSON with exactly these 3 keys: 'Background', 'Personality', 'Abilities'.\n"
        f"2. PRESERVE these glossary terms in original romaji form (case-insensitive): "
        f"{', '.join(glossary)}.\n"
        f"3. Keep wikitext markup like [[link|alias]], <ref>, {{{{template}}}}, ''italic'' INTACT.\n"
        f"4. Character/clan/village/jutsu names stay in romaji.\n"
        f"5. Output ONLY the JSON object, no preamble, no markdown fences.\n"
        f"6. Newlines inside string values must be escaped as \\n; quotes as \\\"."
    )
    user = (
        f"Translate the following wiki sections to {lang_native}.\n\n"
        f"Source JSON:\n{json.dumps(sections, ensure_ascii=False, indent=2)}\n\n"
        f"Output only the JSON object."
    )
    return system, user


def _parse_translation(raw: str) -> dict[str, str]:
    """Extract JSON object from LLM text output."""
    s = raw.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("aucun JSON dans la sortie LLM")
    parsed: dict[str, str] = json.loads(s[start:end + 1])
    return parsed


class QwenHttpBackend(TranslatorBackend):
    """Backend Qwen3-4B via llama.cpp HTTP API (OpenAI-compatible).

    Defaut : http://localhost:8080. Le serveur llama.cpp doit avoir Qwen3-4B
    charge. Latence typique : 5-15s pour 3 sections wiki (~2.5K tokens out).

    Si le serveur est down ou repond mal, leve une exception qui sera capturee
    par `get_wiki_sections()` et fera tomber sur `fallback_to_source` (marker
    `_translation_pending: True`).
    """

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:8080",
        model: str = "Qwen3-4B-UD-Q4_K_XL.gguf",
        glossary: Iterable[str] | None = None,
        timeout_s: float = 60.0,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        # None : auto-load depuis data/i18n/glossary.json (preserve termes
        # canon Naruto). Liste explicite (meme vide) : utilisee telle quelle.
        self._glossary = load_default_glossary() if glossary is None else tuple(glossary)
        self._timeout_s = timeout_s
        self._max_tokens = max_tokens
        self._temperature = temperature

    def translate(self, sections: dict[str, str], lang: str) -> dict[str, str]:
        lang_native = _LANG_NATIVE_NAMES.get(lang, lang)
        system, user = _build_translation_prompt(sections, lang_native, self._glossary)
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
        }
        try:
            with httpx.Client(timeout=self._timeout_s) as client:
                resp = client.post(f"{self._base_url}/v1/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Qwen HTTP backend failed: {exc}") from exc

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("Qwen response missing 'choices'")
        text = (choices[0].get("message") or {}).get("content", "")
        if not text:
            raise RuntimeError("Qwen response empty content")
        try:
            parsed = _parse_translation(text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Qwen output JSON parse fail: {exc}") from exc
        return {section: str(parsed.get(section, sections.get(section, ""))) for section in WIKI_SECTIONS}


def get_wiki_sections(
    char_id: str,
    lang: str,
    *,
    canon_characters: dict[str, Any] | None = None,
    backend: TranslatorBackend | None = None,
    base_dir: Path | None = None,
    force: bool = False,
) -> dict[str, str]:
    """Retourne les 3 sections wiki d'un char dans la langue demandee.

    Strategie :
    1. Si `lang == "en"` : retourne les sections source EN directement (pas
       de traduction necessaire).
    2. Cache hit : lit `data/i18n/wiki/<lang>/<char_id>.json`.
    3. Cache miss + backend dispo : traduit via backend, cache, retourne.
    4. Cache miss + backend down/erreur : retourne source EN avec marqueur
       `_translation_pending` (cache pour eviter retry boucle).
    """
    canon_char = (canon_characters or {}).get(char_id)
    if canon_char is None:
        # Char canon inconnu : retourne dict vide (caller decide)
        return dict.fromkeys(WIKI_SECTIONS, "")

    source = _extract_source_sections(canon_char)

    # Lang == EN : source-of-truth, pas de traduction
    if lang == "en":
        return source

    # Cache hit
    if not force:
        cached = load_cached(char_id, lang, base_dir)
        if cached is not None and not cached.get(PENDING_MARKER_KEY):
            return {section: str(cached.get(section, "")) for section in WIKI_SECTIONS}

    # Source vide : pas la peine de traduire
    if _all_sections_empty(source):
        return source

    # Backend disponible : traduit
    if backend is not None:
        try:
            translated = backend.translate(source, lang)
            # Validate : 3 keys present
            normalized = {section: str(translated.get(section, source.get(section, ""))) for section in WIKI_SECTIONS}
            write_cache(char_id, lang, normalized, base_dir=base_dir)
            return normalized
        except Exception as exc:
            logger.warning("wiki_translator_backend_failed char=%s lang=%s err=%s", char_id, lang, exc)
            # Tombe sur fallback ci-dessous

    # Pas de backend ou backend a echoue : fallback EN avec marqueur
    return fallback_to_source(char_id, lang, source, base_dir=base_dir)


__all__ = [
    "GLOSSARY_PATH",
    "PENDING_MARKER_KEY",
    "WIKI_DIR",
    "WIKI_SECTIONS",
    "QwenHttpBackend",
    "TranslatorBackend",
    "WikiCacheError",
    "cache_path",
    "fallback_to_source",
    "get_wiki_sections",
    "load_cached",
    "load_default_glossary",
    "write_cache",
]
