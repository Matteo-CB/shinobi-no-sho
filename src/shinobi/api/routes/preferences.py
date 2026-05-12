"""Routes /preferences Phase i18n.2.

Lecture / ecriture des preferences utilisateur (langue) via HTTP. Le
serveur respecte la preference globale stockee dans preferences.json.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from shinobi.api.schemas import (
    PreferencesResponse,
    SetLanguageRequest,
    SetLanguageResponse,
)
from shinobi.i18n import (
    NATIVE_NAMES,
    SUPPORTED_LANGUAGES,
    is_supported,
    load_preferences,
    set_active_language,
    set_language,
    t,
)

router = APIRouter(prefix="/preferences", tags=["preferences"])


@router.get(
    "",
    response_model=PreferencesResponse,
    summary="Read user preferences (language + meta)",
)
def get_preferences() -> PreferencesResponse:
    """Return the active language + meta + list of supported languages."""
    prefs = load_preferences()
    return PreferencesResponse(
        language=prefs.language,
        first_launch_completed=prefs.first_launch_completed,
        language_chosen_at=prefs.language_chosen_at,
        available_languages=list(SUPPORTED_LANGUAGES),
        native_names=dict(NATIVE_NAMES),
    )


@router.put(
    "/language",
    response_model=SetLanguageResponse,
    summary="Change the active language (persisted + runtime)",
)
def set_language_endpoint(payload: SetLanguageRequest) -> SetLanguageResponse:
    """Persist the language to preferences.json and update the runtime
    for the following requests in the same process."""
    if not is_supported(payload.language):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=t(
                "api.preferences.unsupported_language",
                language=repr(payload.language),
                available=str(list(SUPPORTED_LANGUAGES)),
            ),
        )
    new_prefs = set_language(payload.language)
    set_active_language(payload.language)
    return SetLanguageResponse(
        language=new_prefs.language,
        first_launch_completed=new_prefs.first_launch_completed,
        language_chosen_at=new_prefs.language_chosen_at,
    )
