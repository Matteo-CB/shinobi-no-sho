"""Phase i18n.8 : Player input strategy (detection + translation a la volee).

Objectif : quand le joueur saisit du texte libre (objectifs, intentions,
dialogues custom), on detecte la langue source et on traduit vers la langue
config courante. Le texte original est conserve verbatim. La traduction
est mise en cache dans le payload du goal/intent pour eviter de retraduire
a chaque affichage.

Strategie :

1. Detection via Qwen3-4B (local llama.cpp HTTP API, ~50 tokens out, ~200ms)
   Fallback : heuristique basee sur le set de caracteres (CJK, accents
   latins) si le backend est indisponible.
2. Si lang_detectee == lang_config : on stocke verbatim, pas de traduction.
3. Sinon : on traduit lang_detectee -> lang_config et on retourne le tuple
   (lang_detectee, {lang_config: traduction}).
4. Si Qwen est down a la traduction : on retourne le texte source dans la
   cle lang_config avec un marqueur `_translation_pending`.

API publique :

    from shinobi.i18n.player_translator import process_player_input

    original_lang, translated = process_player_input(
        "Je veux apprendre le Rasengan",
        target_lang="en",
    )
    # original_lang = "fr"
    # translated = {"en": "I want to learn the Rasengan"}

Le module est utilise par :
- CLI /declare flow (`shinobi.cli.play`)
- API POST /play/{id}/goals (`shinobi.api.routes.goals`)
- Migration script (`scripts/migrate_goals_i18n.py`)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from shinobi.i18n.loader import SUPPORTED_LANGUAGES, is_supported

logger = logging.getLogger(__name__)


# Native lang names (envoyes au LLM pour eviter ambiguite codes ISO).
_LANG_NATIVE_NAMES: dict[str, str] = {
    "en": "English",
    "fr": "French",
    "es": "Spanish",
    "ja": "Japanese",
    "zh": "Mandarin Chinese (Simplified)",
    "ko": "Korean",
    "pt-BR": "Brazilian Portuguese",
    "de": "German",
}

# Heuristique de detection sans LLM (fallback) : ranges Unicode caracteristiques.
# Si un texte contient au moins 1 char dans un range, on penche pour cette langue.
# Tres grossier mais suffisant pour distinguer EN / FR / DE / CJK les uns des autres
# quand le LLM est down.
_HIRAGANA_KATAKANA = re.compile(r"[぀-ヿ]")
_HAN_IDEOGRAPHS = re.compile(r"[一-鿿]")
_HANGUL = re.compile(r"[가-힯ᄀ-ᇿ]")
_FRENCH_DIACRITICS = re.compile(r"[à-ÿ]")  # a-z avec accents
_GERMAN_UMLAUT = re.compile(r"[ÄÖÜäöüß]")
_PORTUGUESE_TILDE = re.compile(r"[ãõÃÕ]")  # ã/õ caracteristiques

# Mots indicateurs (sans accents) pour distinguer FR/ES/PT/EN quand pas de
# diacritique. Set conservateur, on prefere "unknown" plutot que faux positif.
_FRENCH_MARKERS = {
    "je", "le", "la", "les", "des", "un", "une", "et", "ou", "qui", "que",
    "pour", "avec", "sans", "veux", "vais", "suis", "etre", "avoir", "dans",
    "mon", "ma", "mes", "ton", "ta", "tes", "son", "sa", "ses",
}
_SPANISH_MARKERS = {
    "yo", "el", "la", "los", "las", "un", "una", "y", "o", "que", "quien",
    "para", "con", "sin", "quiero", "voy", "soy", "estar", "tener", "en",
    "mi", "mis", "tu", "tus", "su", "sus", "muy", "como",
}
_PORTUGUESE_MARKERS = {
    "eu", "o", "a", "os", "as", "um", "uma", "e", "ou", "que", "quem",
    "para", "com", "sem", "quero", "vou", "sou", "estar", "ter", "em",
    "meu", "minha", "seu", "sua", "muito", "como", "nao",
}
_GERMAN_MARKERS = {
    "ich", "der", "die", "das", "ein", "eine", "und", "oder", "fuer",
    "mit", "ohne", "will", "bin", "sein", "haben", "in", "mein", "dein",
    "sehr", "wie", "nicht", "auf", "zu", "von",
}
_ENGLISH_MARKERS = {
    "i", "the", "a", "an", "and", "or", "who", "that", "for", "with",
    "without", "want", "am", "be", "have", "in", "my", "your", "his", "her",
    "very", "how", "not", "to", "of",
}


def _norm_lang(raw: str) -> str | None:
    """Normalise un code lang LLM/heuristique en code SUPPORTED_LANGUAGES.

    Tolere : "fr-FR" -> "fr", "PT" -> "pt-BR", "zh-CN"/"zh-Hans" -> "zh",
    "pt-PT"/"pt-BR" -> "pt-BR", "EN" -> "en", "ja-JP" -> "ja", etc.
    Retourne None si non reconnu.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip().lower()
    # match direct
    if s in {lng.lower() for lng in SUPPORTED_LANGUAGES}:
        for lng in SUPPORTED_LANGUAGES:
            if lng.lower() == s:
                return lng
    # extrait prefixe ISO 2 lettres
    head = s.split("-", 1)[0].split("_", 1)[0]
    direct = {
        "en": "en", "fr": "fr", "es": "es", "ja": "ja", "zh": "zh",
        "ko": "ko", "de": "de",
    }
    if head in direct:
        return direct[head]
    if head == "pt":
        return "pt-BR"
    return None


def detect_language_heuristic(text: str) -> str | None:
    """Detecte la langue d'un texte par heuristique (sans LLM).

    Strategie ordonnee :
    1. CJK : si script HIRAGANA/KATAKANA -> ja, HANGUL -> ko, sinon HAN -> zh.
    2. Accents latins : umlauts allemands -> de, tildes portugais -> pt-BR,
       diacritiques generaux -> tente FR.
    3. Mots indicateurs (set lowercase). On compte les hits par lang, max gagne.
    4. Default : `None` si rien de discriminant (caller fallback sur target_lang).
    """
    if not text or not text.strip():
        return None

    # Script-based detection
    has_hiragana_katakana = bool(_HIRAGANA_KATAKANA.search(text))
    has_hangul = bool(_HANGUL.search(text))
    has_han = bool(_HAN_IDEOGRAPHS.search(text))
    if has_hiragana_katakana:
        return "ja"
    if has_hangul:
        return "ko"
    if has_han and not has_hiragana_katakana:
        # Han pur sans hiragana -> chinois (Naruto JP utilise toujours hiragana)
        return "zh"

    # Latin scripts : umlauts allemands tres distinctifs
    has_german_umlaut = bool(_GERMAN_UMLAUT.search(text))
    has_portuguese_tilde = bool(_PORTUGUESE_TILDE.search(text))
    has_french_diacritics = bool(_FRENCH_DIACRITICS.search(text))

    if has_german_umlaut:
        return "de"
    if has_portuguese_tilde:
        return "pt-BR"

    # Tokenize (lowercase, alphanumeric + accents)
    words = re.findall(r"[\w]+", text.lower(), flags=re.UNICODE)
    if not words:
        return None
    bag = set(words)

    hits = {
        "fr": len(bag & _FRENCH_MARKERS),
        "es": len(bag & _SPANISH_MARKERS),
        "pt-BR": len(bag & _PORTUGUESE_MARKERS),
        "de": len(bag & _GERMAN_MARKERS),
        "en": len(bag & _ENGLISH_MARKERS),
    }
    # Si on a vu un diacritique latin, priviligie FR
    if has_french_diacritics:
        hits["fr"] += 1

    max_hits = max(hits.values())
    if max_hits == 0:
        return None
    # En cas d'egalite, on retourne None pour eviter un faux positif arbitraire.
    winners = [lng for lng, n in hits.items() if n == max_hits]
    if len(winners) > 1:
        return None
    return winners[0]


class PlayerTranslator:
    """Detecte la langue d'un texte joueur et traduit vers la langue config.

    Architecture :
    - Backend par defaut : Qwen3-4B via llama.cpp HTTP (`http://localhost:8080`).
    - Si le backend est down ou repond mal, on retombe sur l'heuristique
      `detect_language_heuristic` + on ecrit le marqueur `_translation_pending`
      a la place de la traduction.
    - Aucune dependance reseau exterieure (pas d'API payante).

    Methodes :
    - `detect(text)` -> code lang ou None
    - `translate(text, source, target)` -> texte traduit ou source si fail
    - `process(text, target_lang)` -> tuple (original_lang, translated_dict)
    """

    PENDING_MARKER_KEY = "_translation_pending"

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:8080",
        model: str = "Qwen3-4B-UD-Q4_K_XL.gguf",
        timeout_s: float = 30.0,
        max_tokens_detect: int = 20,
        max_tokens_translate: int = 512,
        temperature: float = 0.1,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_s = timeout_s
        self._max_tokens_detect = max_tokens_detect
        self._max_tokens_translate = max_tokens_translate
        self._temperature = temperature
        # Permet d'injecter un client httpx (utile pour tests / mocks).
        self._http_client = http_client

    # === Detection ====================================================

    def detect(self, text: str) -> str | None:
        """Detecte la langue d'un texte. Strategie : LLM -> heuristique fallback.

        Retourne un code parmi SUPPORTED_LANGUAGES, ou None si indeterminable.
        """
        if not text or not text.strip():
            return None
        # 1. LLM
        try:
            lang = self._detect_llm(text)
            if lang is not None:
                return lang
        except Exception as exc:
            logger.debug("player_translator_llm_detect_failed err=%s", exc)
        # 2. Heuristique
        return detect_language_heuristic(text)

    def _detect_llm(self, text: str) -> str | None:
        system = (
            "You are a language identification tool. Given a short user message, "
            "output ONLY the ISO 639-1 language code from this list: "
            "en, fr, es, ja, zh, ko, pt-BR, de. "
            "No explanation, no quotes, no punctuation, lowercase. "
            "If unsure, output 'unknown'."
        )
        user = f"Message:\n{text}\n\nLanguage code:"
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": self._max_tokens_detect,
            "temperature": self._temperature,
        }
        raw = self._post_chat(payload)
        if raw is None:
            return None
        # Cleanup : keep first whitespace-delimited token, strip punctuation.
        token = raw.strip().split()[0] if raw.strip() else ""
        token = token.strip(".,;:'\"`").lower()
        if not token or token == "unknown":
            return None
        return _norm_lang(token)

    # === Translation ==================================================

    def translate(self, text: str, *, source: str, target: str) -> str | None:
        """Traduit `text` de `source` vers `target`.

        Retourne None si :
        - source == target (rien a faire, le caller doit court-circuiter)
        - le backend est indisponible / sortie invalide
        """
        if source == target:
            return None
        if not is_supported(source) or not is_supported(target):
            return None
        try:
            return self._translate_llm(text, source=source, target=target)
        except Exception as exc:
            logger.warning(
                "player_translator_translate_failed src=%s tgt=%s err=%s",
                source, target, exc,
            )
            return None

    def _translate_llm(self, text: str, *, source: str, target: str) -> str | None:
        src_native = _LANG_NATIVE_NAMES.get(source, source)
        tgt_native = _LANG_NATIVE_NAMES.get(target, target)
        system = (
            f"You are a professional translator. Translate the player input "
            f"from {src_native} to {tgt_native}. "
            f"Preserve Naruto-universe terms in romaji (chakra, Rasengan, "
            f"Sharingan, Hokage, kage, jutsu, ninja, shinobi, kunai, kekkei "
            f"genkai, byakugan, hiraishin, etc.). "
            f"Preserve character/clan/village names in romaji. "
            f"Output ONLY the translation as a plain string, no quotes, no "
            f"explanation, no preamble."
        )
        user = text
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": self._max_tokens_translate,
            "temperature": self._temperature,
        }
        raw = self._post_chat(payload)
        if raw is None:
            return None
        cleaned = raw.strip()
        # Strip surrounding markdown / quotes that the LLM might add.
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        if len(cleaned) >= 2 and cleaned[0] in '"“' and cleaned[-1] in '"”':
            cleaned = cleaned[1:-1].strip()
        return cleaned or None

    # === Process (detect + translate combinees) =======================

    def process(
        self,
        text: str,
        *,
        target_lang: str,
        fallback_source: str | None = None,
    ) -> tuple[str | None, dict[str, str], bool]:
        """Detecte la langue source + traduit vers `target_lang`.

        Args:
            text: texte joueur verbatim.
            target_lang: langue config courante (langue cible).
            fallback_source: si la detection echoue, on suppose cette langue
                (typiquement la langue config — assume que le joueur ecrit
                dans la langue de l'interface).

        Returns:
            tuple (source_lang, translated_dict, pending).
            - source_lang : code detecte ou fallback_source ou None
            - translated_dict : {target_lang: ...} si traduction necessaire,
                vide si source == target ou si target deja couvert
            - pending : True si on a echoue a traduire et qu'on doit marquer
                le payload `_translation_pending` (cas backend down)
        """
        if not is_supported(target_lang):
            raise ValueError(f"target_lang unsupported: {target_lang!r}")
        if not text or not text.strip():
            return None, {}, False

        source = self.detect(text) or fallback_source
        if source is None:
            # Aucune detection + pas de fallback : on ne fait rien.
            return None, {}, False

        # Source == cible : verbatim, pas de traduction
        if source == target_lang:
            return source, {}, False

        translated = self.translate(text, source=source, target=target_lang)
        if translated is None:
            # Traduction echouee : on retourne le source brut sous la cle
            # target_lang avec marqueur pending (pour que le caller decide).
            return source, {target_lang: text}, True

        return source, {target_lang: translated}, False

    # === HTTP helper ==================================================

    def _post_chat(self, payload: dict[str, Any]) -> str | None:
        """POST llama.cpp /v1/chat/completions. Retourne le content ou None."""
        url = f"{self._base_url}/v1/chat/completions"
        try:
            if self._http_client is not None:
                resp = self._http_client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
            else:
                with httpx.Client(timeout=self._timeout_s) as client:
                    resp = client.post(url, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            logger.debug("player_translator_http_failed err=%s", exc)
            return None
        choices = data.get("choices") or []
        if not choices:
            return None
        return ((choices[0].get("message") or {}).get("content") or "").strip() or None


# === Module-level helpers (singleton-lite) ============================

_DEFAULT_TRANSLATOR: PlayerTranslator | None = None


def get_default_translator() -> PlayerTranslator:
    """Retourne l'instance singleton (lazy)."""
    global _DEFAULT_TRANSLATOR
    if _DEFAULT_TRANSLATOR is None:
        _DEFAULT_TRANSLATOR = PlayerTranslator()
    return _DEFAULT_TRANSLATOR


def reset_default_translator_for_tests() -> None:
    """Reset le singleton (tests uniquement)."""
    global _DEFAULT_TRANSLATOR
    _DEFAULT_TRANSLATOR = None


def process_player_input(
    text: str,
    *,
    target_lang: str,
    fallback_source: str | None = None,
    translator: PlayerTranslator | None = None,
) -> tuple[str | None, dict[str, str], bool]:
    """Helper one-shot : detecte + traduit. Utilise le singleton par defaut.

    Args:
        text: texte joueur.
        target_lang: langue config courante.
        fallback_source: langue presumee si detection echoue (typiquement
            target_lang).
        translator: instance custom (tests / DI). Sinon utilise le singleton.

    Returns:
        Voir `PlayerTranslator.process(...)`.
    """
    pt = translator or get_default_translator()
    return pt.process(text, target_lang=target_lang, fallback_source=fallback_source)


__all__ = [
    "PlayerTranslator",
    "detect_language_heuristic",
    "get_default_translator",
    "process_player_input",
    "reset_default_translator_for_tests",
]
