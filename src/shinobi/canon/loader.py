"""Chargement et validation des datasets canoniques."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from shinobi.canon.models import (
    CanonBundle,
    Character,
    Clan,
    Era,
    HidenTechnique,
    KekkeiGenkai,
    Location,
    Nature,
    Organization,
    Rank,
    TailedBeast,
    Technique,
    TimelineEvent,
    Village,
    VoiceProfile,
    WeaponTool,
    WorldRules,
)
from shinobi.config import settings
from shinobi.errors import CanonLoadError, CanonValidationError
from shinobi.logging_setup import get_logger
from shinobi.utils.json_utils import load_json

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


def _load_indexed(path: Path, cls: type[T]) -> dict[str, T]:
    """Charge un fichier liste de dicts et retourne un dict par id."""
    data = load_json(path)
    if not isinstance(data, list):
        raise CanonLoadError(f"{path} doit contenir une liste a la racine")
    out: dict[str, T] = {}
    for raw in data:
        try:
            obj = cls.model_validate(raw)
        except ValidationError as exc:
            raise CanonValidationError(f"{path}: {exc}") from exc
        oid = getattr(obj, "id", None)
        if oid is None:
            raise CanonValidationError(f"{path}: objet sans id: {raw}")
        if oid in out:
            raise CanonValidationError(f"{path}: id en double: {oid}")
        out[oid] = obj
    return out


def _load_world_rules(path: Path) -> WorldRules:
    """Charge world_rules.json (objet unique, pas une liste)."""
    data = load_json(path)
    try:
        return WorldRules.model_validate(data)
    except ValidationError as exc:
        raise CanonValidationError(f"{path}: {exc}") from exc


def _maybe_path(canon_dir: Path, filename: str) -> Path:
    """Retourne le chemin d'un dataset, qu'il existe ou non."""
    return canon_dir / filename


def load_canon(
    canon_dir: Path | str | None = None,
    *,
    optional: tuple[str, ...] = (),
    profile: object | None = None,
) -> CanonBundle:
    """Charge tous les datasets canoniques en memoire et retourne un bundle.

    Args:
        canon_dir: repertoire racine des datasets. Defaut depuis settings.
        optional: noms de datasets autorises a etre absents.
        profile: si fourni (CanonicityProfile), filtre les entites au chargement.
    """
    base = Path(canon_dir) if canon_dir else settings.canonical_data_dir
    if not base.exists():
        raise CanonLoadError(f"Repertoire canonique introuvable: {base}")

    logger.info("canon_load_start", path=str(base))

    world_rules_path = _maybe_path(base, "world_rules.json")
    if not world_rules_path.exists() and "world_rules" not in optional:
        raise CanonLoadError(f"Fichier requis manquant: {world_rules_path}")

    characters = _safe_indexed(base, "characters.json", Character, optional)
    # Patch des birth_year/death_year manquants pour les NPCs canon majeurs.
    # Permet aux fact sheets et au filter temporel de fonctionner pour Naruto, Sasuke,
    # Sakura, Konohamaru, Itachi, Kakashi, Tsunade, etc. meme si le scraping initial
    # ne les a pas extraits.
    characters = _apply_birth_years_patch(base, characters)

    bundle = CanonBundle(
        world_rules=_load_world_rules(world_rules_path),
        natures=_safe_indexed(base, "natures.json", Nature, optional),
        ranks=_safe_indexed(base, "ranks.json", Rank, optional),
        eras=_safe_indexed(base, "eras.json", Era, optional),
        villages=_safe_indexed(base, "villages.json", Village, optional),
        clans=_safe_indexed(base, "clans.json", Clan, optional),
        organizations=_safe_indexed(base, "organizations.json", Organization, optional),
        characters=characters,
        tailed_beasts=_safe_indexed(base, "tailed_beasts.json", TailedBeast, optional),
        kekkei_genkai=_safe_indexed(base, "kekkei_genkai.json", KekkeiGenkai, optional),
        kekkei_mora=_safe_indexed(base, "kekkei_mora.json", KekkeiGenkai, optional),
        hiden=_safe_indexed(base, "hiden.json", HidenTechnique, optional),
        techniques=_safe_indexed(base, "techniques.json", Technique, optional),
        weapons_tools=_safe_indexed(base, "weapons_tools.json", WeaponTool, optional),
        locations=_safe_indexed(base, "locations.json", Location, optional),
        timeline_events=_safe_indexed(base, "timeline_events.json", TimelineEvent, optional),
        voice_profiles=_safe_indexed(base, "voice_profiles.json", VoiceProfile, optional),
    )
    if profile is not None:
        from shinobi.canon.profiles import filter_canon

        bundle = filter_canon(bundle, profile)
        logger.info(
            "canon_load_filtered",
            profile=getattr(profile, "label", "?"),
            characters=len(bundle.characters),
            techniques=len(bundle.techniques),
            events=len(bundle.timeline_events),
        )
    logger.info(
        "canon_load_ok",
        characters=len(bundle.characters),
        techniques=len(bundle.techniques),
        clans=len(bundle.clans),
        events=len(bundle.timeline_events),
    )
    return bundle


def _apply_birth_years_patch(
    base: Path, characters: dict[str, Character]
) -> dict[str, Character]:
    """Applique character_birth_years_patch.json sur le dict de personnages.

    Le patch corrige uniquement les champs birth_year/death_year manquants ;
    il ne remplace jamais des valeurs deja presentes dans characters.json.
    """
    patch_path = base / "character_birth_years_patch.json"
    if not patch_path.exists():
        return characters
    try:
        patch_data = load_json(patch_path)
    except Exception as exc:
        logger.warning("birth_years_patch_load_failed", error=str(exc))
        return characters
    patches = patch_data.get("patches", {}) if isinstance(patch_data, dict) else {}
    if not patches:
        return characters
    patched_count = 0
    out = dict(characters)
    for cid, data in patches.items():
        char = out.get(cid)
        if char is None:
            continue
        update: dict[str, Any] = {}
        if char.birth_year is None and "birth_year" in data:
            update["birth_year"] = data["birth_year"]
        if char.death_year is None and "death_year" in data:
            update["death_year"] = data["death_year"]
        if update:
            try:
                out[cid] = char.model_copy(update=update)
                patched_count += 1
            except Exception:
                pass
    if patched_count:
        logger.info("birth_years_patched", count=patched_count)
    return out


def _safe_indexed(
    base: Path,
    filename: str,
    cls: type[T],
    optional: tuple[str, ...],
) -> dict[str, T]:
    path = base / filename
    name = filename.removesuffix(".json")
    if not path.exists():
        if name in optional:
            return {}
        raise CanonLoadError(f"Fichier requis manquant: {path}")
    return _load_indexed(path, cls)


def reload_canon(*, optional: tuple[str, ...] = ()) -> CanonBundle:
    """Helper pour recharger le canon (utile en developpement)."""
    return load_canon(optional=optional)


_CACHED: CanonBundle | None = None


def get_canon(*, optional: tuple[str, ...] = ()) -> CanonBundle:
    """Charge le canon une seule fois et le mémorise pour la durée du process."""
    global _CACHED
    if _CACHED is None:
        _CACHED = load_canon(optional=optional)
    return _CACHED


def reset_canon_cache() -> None:
    """Vide le cache (pour tests)."""
    global _CACHED
    _CACHED = None


def serialize_for_chunking(obj: BaseModel) -> dict[str, Any]:
    """Serialise un modele canon pour l'utilisation dans le RAG."""
    return obj.model_dump(mode="json", by_alias=True)
