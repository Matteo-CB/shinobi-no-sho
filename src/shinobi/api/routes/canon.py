"""Routes /canon Phase 9.

Consultation des datasets canoniques : characters, techniques, villages,
plus une route de resolution fuzzy d'un canon_id depuis un texte libre.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from shinobi.api.dependencies import get_canon
from shinobi.api.i18n_helpers import localize_description, localize_name
from shinobi.api.schemas import (
    CanonCharactersResponse,
    CanonCharacterSummary,
    CanonCharacterWikiResponse,
    CanonClansResponse,
    CanonClanSummary,
    CanonErasResponse,
    CanonEraSummary,
    CanonHidenResponse,
    CanonHidenSummary,
    CanonKekkeiGenkaiResponse,
    CanonKekkeiGenkaiSummary,
    CanonKekkeiMoraResponse,
    CanonLocationsResponse,
    CanonLocationSummary,
    CanonNaturesResponse,
    CanonNatureSummary,
    CanonOrganizationsResponse,
    CanonOrganizationSummary,
    CanonPhaseHDatasetResponse,
    CanonRanksResponse,
    CanonRankSummary,
    CanonTailedBeastsResponse,
    CanonTailedBeastSummary,
    CanonTechniquesResponse,
    CanonTechniqueSummary,
    CanonTimelineEventsResponse,
    CanonTimelineEventSummary,
    CanonVillagesResponse,
    CanonVillageSummary,
    CanonVoiceProfilesResponse,
    CanonVoiceProfileSummary,
    CanonWeaponsToolsResponse,
    CanonWeaponToolSummary,
    CanonWorldRulesResponse,
    ResolveCanonRequest,
    ResolveCanonResponse,
)
from shinobi.cli.canon_incarnation import (
    list_playable_canon_characters,
    resolve_canon_id,
)
from shinobi.i18n import t
from shinobi.i18n.catalog import get_active_language
from shinobi.i18n.wiki_translator import (
    PENDING_MARKER_KEY,
    QwenHttpBackend,
    get_wiki_sections,
    load_cached,
)

router = APIRouter(prefix="/canon", tags=["canon"])


def _rank_of(c: Any) -> str | None:
    rp = getattr(c, "rank_progression", None) or []
    if rp:
        last = rp[-1]
        return getattr(last, "rank", None) or None
    return None


@router.get(
    "/characters",
    response_model=CanonCharactersResponse,
    summary="List canon characters",
)
def list_characters(
    village: str | None = Query(None, description="Filter by village_of_origin."),
    alive_at_year: int | None = Query(
        None, description="Only return characters alive at this year.",
    ),
    playable_only: bool = Query(
        False,
        description="Only return characters with a birth_year (playable).",
    ),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    canon: Any = Depends(get_canon),
) -> CanonCharactersResponse:
    """List canon characters with simple filters."""
    if playable_only:
        chars = list_playable_canon_characters(
            canon, village_filter=village, alive_at_year=alive_at_year,
        )
    else:
        chars = list(canon.characters.values())
        if village:
            chars = [c for c in chars if c.village_of_origin == village]
        if alive_at_year is not None:
            chars = [
                c for c in chars
                if (c.birth_year is None or c.birth_year <= alive_at_year)
                and (c.death_year is None or c.death_year > alive_at_year)
            ]
        chars.sort(key=lambda c: c.id)

    total = len(chars)
    sliced = chars[offset : offset + limit]
    return CanonCharactersResponse(
        characters=[
            CanonCharacterSummary(
                id=c.id,
                name=localize_name(c),
                name_romaji=getattr(c, "name_romaji", None),
                name_fr=getattr(c, "name_fr", None),
                description=localize_description(c) or getattr(c, "personality_fr", None),
                village_of_origin=getattr(c, "village_of_origin", None),
                clan=getattr(c, "clan", None),
                birth_year=getattr(c, "birth_year", None),
                death_year=getattr(c, "death_year", None),
                rank=_rank_of(c),
                natures=list(getattr(c, "natures", []) or []),
                kekkei_genkai=list(getattr(c, "kekkei_genkai", []) or []),
            )
            for c in sliced
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/characters/{canon_id}",
    response_model=CanonCharacterSummary,
    summary="Canon character detail",
)
def get_character(
    canon_id: str,
    canon: Any = Depends(get_canon),
) -> CanonCharacterSummary:
    """Return the summary of a canon character by exact id."""
    if canon_id not in canon.characters:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("api.canon.character_not_found", canon_id=canon_id),
        )
    c = canon.characters[canon_id]
    return CanonCharacterSummary(
        id=c.id,
        name=localize_name(c),
        name_romaji=getattr(c, "name_romaji", None),
        name_fr=getattr(c, "name_fr", None),
        description=localize_description(c) or getattr(c, "personality_fr", None),
        village_of_origin=getattr(c, "village_of_origin", None),
        clan=getattr(c, "clan", None),
        birth_year=getattr(c, "birth_year", None),
        death_year=getattr(c, "death_year", None),
        rank=_rank_of(c),
        natures=list(getattr(c, "natures", []) or []),
        kekkei_genkai=list(getattr(c, "kekkei_genkai", []) or []),
    )


@router.get(
    "/characters/{canon_id}/wiki",
    response_model=CanonCharacterWikiResponse,
    summary="Character wiki sections in active language",
)
def get_character_wiki(
    canon_id: str,
    canon: Any = Depends(get_canon),
) -> CanonCharacterWikiResponse:
    """Phase i18n.9: return the 3 wiki sections in the active language.

    Strategy:
    - If the active language is EN: raw source sections from canon (no
      translation needed).
    - Otherwise: read cache `data/i18n/wiki/<lang>/<id>.json` (cf
      Phase 6 wiki_translator). If missing, try local Qwen3-4B; if Qwen
      is down, return the EN source with `pending=True`.

    The character's `name_romaji` is NOT modified: it is the latin
    transcription, never translated.
    """
    if canon_id not in canon.characters:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("api.canon.character_not_found", canon_id=canon_id),
        )
    active_lang = get_active_language()
    # Backend : on tente le cache d'abord (rapide). Si miss, on instancie un
    # backend Qwen qui tentera de remplir + ecrire le cache. Si Qwen down,
    # fallback_to_source ecrit un cache pending et on retourne pending=True.
    cached = (
        load_cached(canon_id, active_lang)
        if active_lang != "en"
        else None
    )
    if cached is not None and not cached.get(PENDING_MARKER_KEY):
        sections = {
            k: str(cached.get(k, "")) for k in ("Background", "Personality", "Abilities")
        }
        return CanonCharacterWikiResponse(
            canon_id=canon_id,
            language=active_lang,
            Background=sections["Background"],
            Personality=sections["Personality"],
            Abilities=sections["Abilities"],
            pending=False,
        )

    backend = QwenHttpBackend() if active_lang != "en" else None
    sections = get_wiki_sections(
        canon_id,
        active_lang,
        canon_characters=canon.characters,
        backend=backend,
    )
    # Determine pending : si on lit le cache fraichement ecrit, il aura le marker.
    pending = False
    if active_lang != "en":
        cached_after = load_cached(canon_id, active_lang)
        if cached_after is not None and cached_after.get(PENDING_MARKER_KEY):
            pending = True
    return CanonCharacterWikiResponse(
        canon_id=canon_id,
        language=active_lang,
        Background=sections.get("Background", ""),
        Personality=sections.get("Personality", ""),
        Abilities=sections.get("Abilities", ""),
        pending=pending,
    )


@router.post(
    "/characters/resolve",
    response_model=ResolveCanonResponse,
    summary="Fuzzy canon_id resolution",
)
def resolve_character(
    payload: ResolveCanonRequest,
    canon: Any = Depends(get_canon),
) -> ResolveCanonResponse:
    """Fuzzy lookup: exact id, name_romaji, name_fr, alias, substring."""
    canon_id, candidates = resolve_canon_id(canon, payload.query)
    return ResolveCanonResponse(canon_id=canon_id, candidates=candidates)


@router.get(
    "/techniques",
    response_model=CanonTechniquesResponse,
    summary="List canon techniques",
)
def list_techniques(
    nature: str | None = Query(None, description="Filter by nature (katon, ...)."),
    rank: str | None = Query(None, description="Filter by rank (E..S)."),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    canon: Any = Depends(get_canon),
) -> CanonTechniquesResponse:
    """List canon techniques with simple filters."""
    techs = list(canon.techniques.values())
    if nature:
        techs = [t for t in techs if nature in (t.natures or [])]
    if rank:
        techs = [t for t in techs if str(t.rank) == rank or getattr(t.rank, "value", None) == rank]
    techs.sort(key=lambda t: t.id)
    total = len(techs)
    sliced = techs[offset : offset + limit]
    return CanonTechniquesResponse(
        techniques=[
            CanonTechniqueSummary(
                id=t.id,
                name=localize_name(t),
                name_romaji=getattr(t, "name_romaji", None),
                name_fr=getattr(t, "name_fr", None),
                rank=str(getattr(t.rank, "value", t.rank)) if getattr(t, "rank", None) else None,
                natures=list(getattr(t, "natures", []) or []),
                classification=list(getattr(t, "classification", []) or []),
            )
            for t in sliced
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/techniques/{technique_id}",
    response_model=CanonTechniqueSummary,
    summary="Canon technique detail",
)
def get_technique(
    technique_id: str,
    canon: Any = Depends(get_canon),
) -> CanonTechniqueSummary:
    """Return a canon technique by id."""
    if technique_id not in canon.techniques:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("api.canon.technique_not_found", technique_id=technique_id),
        )
    tech = canon.techniques[technique_id]
    return CanonTechniqueSummary(
        id=tech.id,
        name=localize_name(tech),
        name_romaji=getattr(tech, "name_romaji", None),
        name_fr=getattr(tech, "name_fr", None),
        rank=str(getattr(tech.rank, "value", tech.rank)) if getattr(tech, "rank", None) else None,
        natures=list(getattr(tech, "natures", []) or []),
        classification=list(getattr(tech, "classification", []) or []),
    )


@router.get(
    "/clans",
    response_model=CanonClansResponse,
    summary="List canon clans",
)
def list_clans(canon: Any = Depends(get_canon)) -> CanonClansResponse:
    """List all canon clans."""
    clans = sorted(canon.clans.values(), key=lambda c: c.id)
    return CanonClansResponse(
        clans=[
            CanonClanSummary(
                id=c.id,
                name=localize_name(c),
                name_romaji=getattr(c, "name_romaji", None),
                name_fr=getattr(c, "name_fr", None),
                home_village=getattr(c, "village_of_origin", None),
                kekkei_genkai=list(getattr(c, "key_kekkei_genkai", []) or []),
            )
            for c in clans
        ],
        count=len(clans),
    )


@router.get(
    "/clans/{clan_id}",
    response_model=CanonClanSummary,
    summary="Canon clan detail",
)
def get_clan(clan_id: str, canon: Any = Depends(get_canon)) -> CanonClanSummary:
    if clan_id not in canon.clans:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("api.canon.clan_not_found", clan_id=clan_id),
        )
    c = canon.clans[clan_id]
    return CanonClanSummary(
        id=c.id,
        name=localize_name(c),
        name_romaji=getattr(c, "name_romaji", None),
        name_fr=getattr(c, "name_fr", None),
        home_village=getattr(c, "village_of_origin", None),
        kekkei_genkai=list(getattr(c, "key_kekkei_genkai", []) or []),
    )


@router.get(
    "/organizations",
    response_model=CanonOrganizationsResponse,
    summary="List canon organizations",
)
def list_organizations(canon: Any = Depends(get_canon)) -> CanonOrganizationsResponse:
    orgs = sorted(canon.organizations.values(), key=lambda o: o.id)
    return CanonOrganizationsResponse(
        organizations=[
            CanonOrganizationSummary(
                id=o.id,
                name=localize_name(o),
                name_romaji=getattr(o, "name_romaji", None),
                name_fr=getattr(o, "name_fr", None),
            )
            for o in orgs
        ],
        count=len(orgs),
    )


@router.get(
    "/eras",
    response_model=CanonErasResponse,
    summary="List canon eras",
)
def list_eras(canon: Any = Depends(get_canon)) -> CanonErasResponse:
    eras = sorted(
        canon.eras.values(),
        key=lambda e: (getattr(e, "year_start", 0) or 0),
    )
    return CanonErasResponse(
        eras=[
            CanonEraSummary(
                id=e.id,
                name=localize_name(e),
                name_fr=getattr(e, "name_fr", None),
                year_start=getattr(e, "year_start", None),
                year_end=getattr(e, "year_end", None),
                key_figures=list(getattr(e, "key_figures", []) or []),
            )
            for e in eras
        ],
        count=len(eras),
    )


@router.get(
    "/kekkei_genkai",
    response_model=CanonKekkeiGenkaiResponse,
    summary="List canon kekkei genkai",
)
def list_kekkei_genkai(canon: Any = Depends(get_canon)) -> CanonKekkeiGenkaiResponse:
    kgs = sorted(canon.kekkei_genkai.values(), key=lambda k: k.id)
    return CanonKekkeiGenkaiResponse(
        kekkei_genkai=[
            CanonKekkeiGenkaiSummary(
                id=k.id,
                name=localize_name(k),
                name_romaji=getattr(k, "name_romaji", None),
                name_fr=getattr(k, "name_fr", None),
                associated_clans=list(getattr(k, "carrier_clans", []) or []),
            )
            for k in kgs
        ],
        count=len(kgs),
    )


@router.get(
    "/locations",
    response_model=CanonLocationsResponse,
    summary="List canon locations",
)
def list_locations(canon: Any = Depends(get_canon)) -> CanonLocationsResponse:
    """List of locations (inns, mountains, sanctuaries, ...)."""
    locs = sorted(canon.locations.values(), key=lambda l: l.id)
    return CanonLocationsResponse(
        locations=[
            CanonLocationSummary(
                id=l.id,
                name=localize_name(l),
                name_romaji=getattr(l, "name_romaji", None),
                name_fr=getattr(l, "name_fr", None),
                location_type=getattr(l, "location_type", None)
                or getattr(l, "type", None),
                parent_location=getattr(l, "parent_location", None)
                or getattr(l, "parent_location_id", None),
            )
            for l in locs
        ],
        count=len(locs),
    )


@router.get(
    "/tailed_beasts",
    response_model=CanonTailedBeastsResponse,
    summary="List canon bijuu (tailed beasts)",
)
def list_tailed_beasts(canon: Any = Depends(get_canon)) -> CanonTailedBeastsResponse:
    """Bijuu (1-tail Shukaku ... 10-tails Juubi)."""
    tbs = sorted(canon.tailed_beasts.values(), key=lambda t: t.id)
    return CanonTailedBeastsResponse(
        tailed_beasts=[
            CanonTailedBeastSummary(
                id=t.id,
                name=localize_name(t),
                name_romaji=getattr(t, "name_romaji", None),
                name_fr=getattr(t, "name_fr", None),
                tails=getattr(t, "tails_count", None)
                or getattr(t, "tails", None),
            )
            for t in tbs
        ],
        count=len(tbs),
    )


@router.get(
    "/hiden",
    response_model=CanonHidenResponse,
    summary="List canon hiden techniques",
)
def list_hiden(canon: Any = Depends(get_canon)) -> CanonHidenResponse:
    """Hiden (secret clan techniques)."""
    items = sorted(canon.hiden.values(), key=lambda h: h.id)
    return CanonHidenResponse(
        hiden=[
            CanonHidenSummary(
                id=h.id,
                name=localize_name(h),
                name_romaji=getattr(h, "name_romaji", None),
                name_fr=getattr(h, "name_fr", None),
                associated_clan=getattr(h, "associated_clan", None)
                or getattr(h, "clan", None),
            )
            for h in items
        ],
        count=len(items),
    )


@router.get(
    "/weapons_tools",
    response_model=CanonWeaponsToolsResponse,
    summary="List canon weapons and tools",
)
def list_weapons_tools(
    canon: Any = Depends(get_canon),
) -> CanonWeaponsToolsResponse:
    """Catalog of canonical weapons and tools."""
    items = sorted(canon.weapons_tools.values(), key=lambda w: w.id)
    return CanonWeaponsToolsResponse(
        weapons_tools=[
            CanonWeaponToolSummary(
                id=w.id,
                name=localize_name(w),
                name_romaji=getattr(w, "name_romaji", None),
                name_fr=getattr(w, "name_fr", None),
                category=getattr(w, "category", None),
            )
            for w in items
        ],
        count=len(items),
    )


@router.get(
    "/natures",
    response_model=CanonNaturesResponse,
    summary="List canon chakra natures",
)
def list_natures(canon: Any = Depends(get_canon)) -> CanonNaturesResponse:
    """Elemental + advanced + special chakra natures."""
    items = sorted(canon.natures.values(), key=lambda n: n.id)
    return CanonNaturesResponse(
        natures=[
            CanonNatureSummary(
                id=n.id,
                name=localize_name(n),
                name_romaji=getattr(n, "name_romaji", None),
                name_fr=getattr(n, "name_fr", None),
                type=getattr(n, "type", None),
            )
            for n in items
        ],
        count=len(items),
    )


@router.get(
    "/timeline_events",
    response_model=CanonTimelineEventsResponse,
    summary="Paginated list of canon timeline events",
)
def list_timeline_events(
    year_min: int | None = Query(None),
    year_max: int | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    canon: Any = Depends(get_canon),
) -> CanonTimelineEventsResponse:
    """Canon events with optional time filter."""
    events = list(canon.timeline_events.values())
    if year_min is not None:
        events = [
            e for e in events
            if (getattr(e, "year", None) or 0) >= year_min
        ]
    if year_max is not None:
        events = [
            e for e in events
            if (getattr(e, "year", None) or 0) <= year_max
        ]
    events.sort(key=lambda e: (getattr(e, "year", None) or 0, e.id))
    total = len(events)
    sliced = events[offset : offset + limit]
    return CanonTimelineEventsResponse(
        events=[
            CanonTimelineEventSummary(
                id=e.id,
                name=localize_name(e),
                name_fr=getattr(e, "name_fr", None),
                year=getattr(e, "year", None),
                arc=getattr(e, "arc", None),
                involves=list(getattr(e, "involves", []) or []),
            )
            for e in sliced
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/voice_profiles",
    response_model=CanonVoiceProfilesResponse,
    summary="List canon voice profiles",
)
def list_voice_profiles(
    canon: Any = Depends(get_canon),
) -> CanonVoiceProfilesResponse:
    """Voice profiles for canon NPCs."""
    items = sorted(canon.voice_profiles.values(), key=lambda v: v.id)
    return CanonVoiceProfilesResponse(
        voice_profiles=[
            CanonVoiceProfileSummary(
                id=v.id,
                speaker_id=getattr(v, "speaker_id", None)
                or getattr(v, "character_id", None),
                style_fr=getattr(v, "style_fr", None)
                or getattr(v, "description_fr", None),
            )
            for v in items
        ],
        count=len(items),
    )


@router.get(
    "/world_rules",
    response_model=CanonWorldRulesResponse,
    summary="World rules (chakra/learning/combat/...)",
)
def world_rules(canon: Any = Depends(get_canon)) -> CanonWorldRulesResponse:
    """Return canon WorldRules serialized as dicts."""
    wr = canon.world_rules
    return CanonWorldRulesResponse(
        chakra=wr.chakra.model_dump(mode="json"),
        learning=wr.learning.model_dump(mode="json"),
        combat=wr.combat.model_dump(mode="json"),
        social=wr.social.model_dump(mode="json"),
        economy=wr.economy.model_dump(mode="json"),
        time=wr.time.model_dump(mode="json"),
    )


@router.get(
    "/ranks",
    response_model=CanonRanksResponse,
    summary="List canon ninja ranks",
)
def list_ranks(canon: Any = Depends(get_canon)) -> CanonRanksResponse:
    """List of Ranks (academy_student, genin, chunin, jonin, ...)."""
    ranks = sorted(
        canon.ranks.values(),
        key=lambda r: (getattr(r, "level", 0) or 0, r.id),
    )
    return CanonRanksResponse(
        ranks=[
            CanonRankSummary(
                id=r.id,
                name=localize_name(r),
                name_romaji=getattr(r, "name_romaji", None),
                name_fr=getattr(r, "name_fr", None),
                level=getattr(r, "level", None),
                min_age=getattr(r, "min_age", None),
                typical_max_age=getattr(r, "typical_max_age", None),
            )
            for r in ranks
        ],
        count=len(ranks),
    )


@router.get(
    "/kekkei_mora",
    response_model=CanonKekkeiMoraResponse,
    summary="List kekkei mora (Karma, Tenseigan, ...)",
)
def list_kekkei_mora(canon: Any = Depends(get_canon)) -> CanonKekkeiMoraResponse:
    """Distinct from kekkei genkai: kekkei mora (Otsutsuki abilities)."""
    kgs = sorted(canon.kekkei_mora.values(), key=lambda k: k.id)
    return CanonKekkeiMoraResponse(
        kekkei_mora=[
            CanonKekkeiGenkaiSummary(
                id=k.id,
                name=localize_name(k),
                name_romaji=getattr(k, "name_romaji", None),
                name_fr=getattr(k, "name_fr", None),
                associated_clans=list(getattr(k, "carrier_clans", []) or []),
            )
            for k in kgs
        ],
        count=len(kgs),
    )


# Mapping dataset_id -> attribut sur CanonBundle pour routes Phase H.
_PHASE_H_DATASETS = {
    "deep_motivations": "deep_motivations",
    "political_forces": "political_forces",
    "divergence_points": "divergence_points",
    "narrative_patterns": "narrative_patterns",
    "timeline_events_enriched": "timeline_events_enriched",
}


@router.get(
    "/phase_h/{dataset_id}",
    response_model=CanonPhaseHDatasetResponse,
    summary="Phase H enriched datasets (LLM-extracted)",
)
def get_phase_h_dataset(
    dataset_id: str,
    canon: Any = Depends(get_canon),
) -> CanonPhaseHDatasetResponse:
    """Return one of the 5 Phase H datasets: deep_motivations,
    political_forces, divergence_points, narrative_patterns,
    timeline_events_enriched.

    If the dataset is absent (legacy canon without Phase H), returns
    available=False without 404 to allow graceful UI handling.
    """
    if dataset_id not in _PHASE_H_DATASETS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t(
                "api.canon.phase_h_dataset_unknown",
                dataset_id=dataset_id,
                expected=", ".join(_PHASE_H_DATASETS.keys()),
            ),
        )
    attr = _PHASE_H_DATASETS[dataset_id]
    payload = getattr(canon, attr, None)
    if payload is None or (
        isinstance(payload, (dict, list)) and len(payload) == 0
    ):
        return CanonPhaseHDatasetResponse(
            dataset_id=dataset_id, available=False, payload=None, count=0,
        )
    if isinstance(payload, dict) or isinstance(payload, list):
        count = len(payload)
    else:
        count = None
    return CanonPhaseHDatasetResponse(
        dataset_id=dataset_id,
        available=True,
        payload=payload,
        count=count,
    )


@router.get(
    "/villages",
    response_model=CanonVillagesResponse,
    summary="List canon villages",
)
def list_villages_endpoint(
    canon: Any = Depends(get_canon),
) -> CanonVillagesResponse:
    """Return all canon villages."""
    villages = list(canon.villages.values())
    villages.sort(key=lambda v: v.id)
    return CanonVillagesResponse(
        villages=[
            CanonVillageSummary(
                id=v.id,
                name=localize_name(v),
                name_romaji=getattr(v, "name_romaji", None),
                name_fr=getattr(v, "name_fr", None),
                country=getattr(v, "country_name_fr", None) or getattr(v, "country", None),
            )
            for v in villages
        ],
        count=len(villages),
    )
