"""Phase i18n.9 : middleware Accept-Language.

Parse le header HTTP `Accept-Language` et, si une langue supportee est
demandee, l'active pour la duree de la requete via la ContextVar
`shinobi.i18n.catalog._REQUEST_LANGUAGE`.

Pas d'effet de bord global : la langue process-wide `_ACTIVE_LANGUAGE`
n'est PAS modifiee. Apres la requete, la ContextVar revient a sa valeur
precedente (typiquement None -> get_active_language() retombe sur le
global lu depuis preferences.json).

Strategie de parsing :
- `Accept-Language: en-US, en;q=0.9, fr;q=0.8`
- Decompose en `[(lang, q)]`, normalise les codes (`en-US` -> `en`,
  `pt-BR` -> `pt-BR`, `zh-CN` -> `zh`) puis trie par q desc.
- Premiere langue supportee = active. Si aucune n'est supportee, on
  retombe sur le global (pas d'override).

Edge cases :
- Header absent / vide : no-op.
- Header malforme : tolere (best-effort), no-op si rien d'extractible.
- `*` : ignore (signifie "n'importe quelle langue", on garde le global).
- Quality q invalide : default 1.0.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from shinobi.i18n.catalog import (
    reset_request_language,
    set_request_language,
)
from shinobi.i18n.loader import SUPPORTED_LANGUAGES, is_supported

# Pattern : un range `lang[;q=0.5]` avec ou sans subtag.
_LANG_RANGE_RE = re.compile(
    r"""
    \s*([A-Za-z\-]+|\*)      # token: code lang ou *
    (?:\s*;\s*q\s*=\s*       # ;q=
        ([0-9]*(?:\.[0-9]+)?))?  # quality, optionnel
    \s*
    """,
    re.VERBOSE,
)


def _norm_subtag(raw: str) -> str | None:
    """Normalise un token Accept-Language vers un code SUPPORTED_LANGUAGES.

    Exemples :
      en       -> en
      en-US    -> en
      pt       -> pt-BR
      pt-BR    -> pt-BR
      pt-PT    -> pt-BR  (best-effort, on n'a pas pt-PT)
      zh       -> zh
      zh-CN    -> zh
      zh-Hans  -> zh
      ja-JP    -> ja
      *        -> None
    """
    if not raw or raw == "*":
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    # Match direct (preserve la casse de pt-BR)
    for lng in SUPPORTED_LANGUAGES:
        if candidate.lower() == lng.lower():
            return lng
    head = candidate.split("-", 1)[0].split("_", 1)[0].lower()
    direct = {
        "en": "en", "fr": "fr", "es": "es", "ja": "ja", "zh": "zh",
        "ko": "ko", "de": "de",
    }
    if head in direct:
        return direct[head]
    if head == "pt":
        return "pt-BR"
    return None


def parse_accept_language(header: str | None) -> list[str]:
    """Parse un header Accept-Language en liste de langues supportees.

    Retourne les langues triees par quality factor decroissant. Les codes
    non supportes sont filtres. Liste vide si header absent / pas de match.
    """
    if not header:
        return []
    pairs: list[tuple[float, int, str]] = []
    for idx, m in enumerate(_LANG_RANGE_RE.finditer(header)):
        token = m.group(1)
        q_raw = m.group(2)
        if token is None:
            continue
        norm = _norm_subtag(token)
        if norm is None:
            continue
        if not is_supported(norm):
            continue
        try:
            q = float(q_raw) if q_raw not in (None, "") else 1.0
        except ValueError:
            q = 1.0
        # Quality 0 = explicitement non desire
        if q <= 0:
            continue
        # `idx` preserve l'ordre d'apparition pour tiebreak stable.
        pairs.append((-q, idx, norm))
    pairs.sort()
    # Deduplique en preservant l'ordre.
    seen: set[str] = set()
    out: list[str] = []
    for _q, _i, lng in pairs:
        if lng in seen:
            continue
        seen.add(lng)
        out.append(lng)
    return out


def select_language(header: str | None) -> str | None:
    """Retourne le premier code supporte demande, ou None.

    Caller : si None, ne touche pas a la ContextVar (=> get_active_language()
    retombe sur le global).
    """
    langs = parse_accept_language(header)
    return langs[0] if langs else None


class AcceptLanguageMiddleware(BaseHTTPMiddleware):
    """Active la langue per-request depuis l'header Accept-Language.

    Si le header est present et resout une langue supportee, on set la
    ContextVar pour la duree de la requete. Sinon, no-op (le global tient).

    Permet a un client (UI, curl) de demander :
      curl -H "Accept-Language: ja" /canon/characters/uchiha_itachi
    et de recevoir la reponse avec les chaines en japonais, sans changer
    la langue par defaut du serveur.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        header = request.headers.get("accept-language")
        chosen = select_language(header)
        token: object | None = None
        if chosen is not None:
            token = set_request_language(chosen)
            # Expose au handler pour debug / introspection si jamais utile.
            request.state.accept_language_chosen = chosen
        else:
            request.state.accept_language_chosen = None
        try:
            response = await call_next(request)
        finally:
            if token is not None:
                reset_request_language(token)
        if chosen is not None:
            # Echo du choix au client pour debug. Le header standard est
            # `Content-Language`.
            response.headers["Content-Language"] = chosen
        return response


__all__ = [
    "AcceptLanguageMiddleware",
    "parse_accept_language",
    "select_language",
]
