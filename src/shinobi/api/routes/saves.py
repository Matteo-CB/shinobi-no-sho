"""Routes /saves Phase 9.

CRUD complet sur les saves : list, get, create (random ou canon), delete,
duplicate, export, import.
"""

from __future__ import annotations

import random
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse

from shinobi.api.dependencies import get_canon
from shinobi.api.schemas import (
    CreateSaveRequest,
    CreateSaveResponse,
    SaveSummary,
    SavesListResponse,
)
from shinobi.canon.profiles import CanonicityProfile
from shinobi.cli.canon_incarnation import (
    incarnate_canon_character,
    resolve_canon_id,
)
from shinobi.engine.character import (
    ChakraState,
    Character,
    FamilyMember,
    FamilyState,
)
from shinobi.engine.events import initialize_scheduler
from shinobi.engine.stats import CoreStats, ExtendedStats
from shinobi.engine.world import create_default_world
from shinobi.errors import SaveCorruptError, SaveNotFoundError
from shinobi.i18n import t
from shinobi.persistence import saves as save_module
from shinobi.types import Gender
from shinobi.utils.slug import slugify


router = APIRouter(prefix="/saves", tags=["saves"])


def _to_summary(meta: save_module.SaveMeta) -> SaveSummary:
    return SaveSummary(
        save_id=meta.save_id,
        schema_version=meta.schema_version,
        character_name=meta.character_name,
        character_age=meta.character_age,
        current_year=meta.current_year,
        current_date=meta.current_date,
        village=meta.village,
        rank=meta.rank,
        canonicity_profile=meta.canonicity_profile,
        playtime_hours=meta.playtime_hours,
        total_turns=meta.total_turns,
        last_played=meta.last_played,
        created_at=meta.created_at,
        thumbnail_summary=meta.thumbnail_summary,
        warnings=list(meta.warnings),
    )


@router.get(
    "",
    response_model=SavesListResponse,
    summary="List saves",
)
def list_saves() -> SavesListResponse:
    """Return all saves present on disk."""
    items = save_module.list_saves()
    return SavesListResponse(
        saves=[_to_summary(m) for m in items],
        count=len(items),
    )


@router.get(
    "/{save_id}",
    response_model=SaveSummary,
    summary="Read save metadata",
)
def get_save(save_id: str) -> SaveSummary:
    """Return the SaveMeta of the requested save."""
    for m in save_module.list_saves():
        if m.save_id == save_id:
            return _to_summary(m)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=t("api.saves.not_found", save_id=save_id),
    )


@router.post(
    "",
    response_model=CreateSaveResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new save",
)
def create_save(
    payload: CreateSaveRequest = Body(...),
    canon: Any = Depends(get_canon),
) -> CreateSaveResponse:
    """Create a new save in 'random' or 'canon' mode.

    - mode='random': use defaults when fields are missing.
    - mode='canon': requires `canon_id` (or `canon_query` for fuzzy)
      and `age_at_start`.
    """
    if payload.mode not in ("random", "canon"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=t("api.saves.invalid_mode"),
        )

    profile = CanonicityProfile.default()

    if payload.mode == "canon":
        canon_id = payload.canon_id
        if canon_id is None and payload.canon_query:
            resolved, candidates = resolve_canon_id(canon, payload.canon_query)
            if resolved is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=t(
                        "api.saves.canon_id_ambiguous",
                        canon_query=payload.canon_query,
                        candidates=str(candidates[:8]),
                    ),
                )
            canon_id = resolved
        if canon_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=t("api.saves.canon_id_required"),
            )
        if payload.age_at_start is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=t("api.saves.age_required"),
            )
        try:
            character, current_year = incarnate_canon_character(
                canon, canon_id, payload.age_at_start,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        world = create_default_world(profile=profile, starting_year=current_year)
        scheduled = initialize_scheduler(canon, starting_year=current_year)
        world = world.model_copy(update={"scheduled_events": scheduled})
        save_id = save_module.create_save(
            character,
            world,
            canonicity_profile=payload.canonicity_profile,
            thumbnail_summary=(
                f"{character.name} (canon) a {character.age_years} ans, "
                f"an {current_year}"
            ),
        )
        return CreateSaveResponse(
            save_id=save_id,
            character_name=character.name,
            current_year=current_year,
        )

    # Mode random : minimal viable. On fait simple pour l'API.
    name = payload.name or "Shinobi"
    starting_year = payload.starting_year or 12
    age_years = payload.starting_age or 12
    village_id = payload.village or "konohagakure"
    clan_id = payload.clan
    g = (payload.gender or "male").lower()
    if g == "female":
        gender = Gender.female
    elif g == "non_binary":
        gender = Gender.non_binary
    else:
        gender = Gender.male

    char_id = slugify(name) or "shinobi"
    rank = payload.rank or (
        "academy_student" if age_years < 12 else "genin"
    )
    rng = random.Random(f"api|{name}|{starting_year}")
    natures_list = list(payload.natures or [])
    kekkei_list = list(payload.kekkei_genkai or [])

    # Stats : roll avec biais clan/kekkei/natures (parite CLI) si roll_stats=True
    if payload.roll_stats:
        from shinobi.cli.character_creation import _roll_stats

        stats, extended_stats, chakra_state = _roll_stats(
            name, starting_year, clan_id, kekkei_list, natures_list,
        )
    else:
        stats = CoreStats()
        extended_stats = ExtendedStats()
        chakra_state = ChakraState()

    # Family : 'typical' (defaut) / 'orphan' / 'lineage' (parite CLI _pick_family)
    fam_status = (payload.family_status or "typical").lower()
    if fam_status == "orphan":
        family = FamilyState(members=[])
    elif fam_status == "lineage" and clan_id:
        family = FamilyState(
            members=[
                FamilyMember(
                    relationship_label="pere",
                    character_id=f"{clan_id}_father",
                    is_alive=True,
                ),
                FamilyMember(
                    relationship_label="mere",
                    character_id=f"{clan_id}_mother",
                    is_alive=True,
                ),
                FamilyMember(
                    relationship_label="ancetre",
                    character_id=f"{clan_id}_elder",
                    is_alive=True,
                ),
            ]
        )
    else:
        family = FamilyState(
            members=[
                FamilyMember(
                    relationship_label="pere",
                    character_id=f"{clan_id or 'civilian'}_father",
                    is_alive=True,
                ),
                FamilyMember(
                    relationship_label="mere",
                    character_id=f"{clan_id or 'civilian'}_mother",
                    is_alive=True,
                ),
            ]
        )

    character = Character(
        id=char_id,
        name=name,
        gender=gender,
        birth_year=starting_year - age_years,
        birth_date=f"{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
        age_years=age_years,
        village_of_origin=village_id,
        current_village=village_id,
        current_location=village_id,
        clan=clan_id,
        family=family,
        rank=rank,
        natures=natures_list,
        kekkei_genkai=kekkei_list,
        kekkei_mora=list(payload.kekkei_mora or []),
        tailed_beast=payload.tailed_beast,
        stats=stats,
        extended_stats=extended_stats,
        chakra=chakra_state,
    )

    world = create_default_world(profile=profile, starting_year=starting_year)
    scheduled = initialize_scheduler(canon, starting_year=starting_year)
    world = world.model_copy(update={"scheduled_events": scheduled})

    save_id = save_module.create_save(
        character,
        world,
        canonicity_profile=payload.canonicity_profile,
        thumbnail_summary=f"{name}, {age_years} ans, {rank} a {village_id}",
    )
    return CreateSaveResponse(
        save_id=save_id,
        character_name=name,
        current_year=starting_year,
    )


@router.delete(
    "/{save_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a save",
)
def delete_save(save_id: str) -> JSONResponse:
    """Permanently delete a save."""
    try:
        save_module.delete_save(save_id)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)


@router.post(
    "/{save_id}/duplicate",
    response_model=CreateSaveResponse,
    summary="Duplicate a save",
)
def duplicate_save(
    save_id: str,
    label: str = Body(..., embed=True, description="Label for the new save."),
) -> CreateSaveResponse:
    """Duplicate the save under a new timestamp + label."""
    try:
        new_id = save_module.duplicate_save(save_id, label)
    except SaveNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    # Recharge meta pour les champs renvoyes
    for m in save_module.list_saves():
        if m.save_id == new_id:
            return CreateSaveResponse(
                save_id=new_id,
                character_name=m.character_name,
                current_year=m.current_year,
            )
    return CreateSaveResponse(
        save_id=new_id,
        character_name="",
        current_year=0,
    )


@router.get(
    "/{save_id}/export",
    summary="Export a save (.shinosave)",
    response_class=FileResponse,
)
def export_save(save_id: str) -> FileResponse:
    """Download a tar.gz archive of the save."""
    if save_id not in {m.save_id for m in save_module.list_saves()}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("api.saves.not_found", save_id=save_id),
        )
    tmp_dir = Path(tempfile.mkdtemp(prefix="shinobi_export_"))
    out_path = tmp_dir / f"{save_id}"
    final = save_module.export_save(save_id, out_path)
    return FileResponse(
        path=str(final),
        filename=final.name,
        media_type="application/gzip",
    )


@router.post(
    "/import",
    response_model=CreateSaveResponse,
    summary="Import a .shinosave archive",
)
async def import_save(request: Request) -> CreateSaveResponse:
    """Receive an archive (raw body application/gzip or octet-stream)
    and extract it into the saves directory.
    """
    payload = await request.body()
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=t("api.saves.body_empty"),
        )
    tmp_dir = Path(tempfile.mkdtemp(prefix="shinobi_import_"))
    archive_path = tmp_dir / "archive.shinosave"
    archive_path.write_bytes(payload)
    try:
        # Validation defensive : refuse les archives multi-root ou path traversal
        with tarfile.open(archive_path, "r:gz") as tar:
            members = tar.getnames()
            roots = {m.split("/")[0] for m in members}
            if len(roots) != 1:
                raise SaveCorruptError("archive multi-root rejetee")
        new_id = save_module.import_save(archive_path)
    except SaveCorruptError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    for m in save_module.list_saves():
        if m.save_id == new_id:
            return CreateSaveResponse(
                save_id=new_id,
                character_name=m.character_name,
                current_year=m.current_year,
            )
    return CreateSaveResponse(
        save_id=new_id, character_name="", current_year=0,
    )
