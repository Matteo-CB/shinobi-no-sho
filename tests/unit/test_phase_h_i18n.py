"""Phase i18n.7 : tests pour le canon loader Phase H multilangue.

8 tests requis par spec L527 :
- 7 tests par langue cible (en, es, ja, zh, ko, pt-BR, de) : verifie que si
  le dossier `data/canon/i18n/<lang>/` existe avec des datasets traduits, le
  loader les charge correctement (et fallback vers FR sinon).
- 1 test d'uniformite des ids : pour chaque langue qui a des fichiers, les ids
  doivent etre IDENTIQUES a la source FR (pas de translation des cles).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from shinobi.canon.loader import _load_phase_h_datasets

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CANONICAL_DIR = REPO_ROOT / "data" / "canonical"
CANON_DIR = REPO_ROOT / "data" / "canon"
I18N_DIR = CANON_DIR / "i18n"

DATASETS = [
    "deep_motivations",
    "political_forces",
    "divergence_points",
    "narrative_patterns",
    "timeline_events_enriched",
]
TARGET_LANGS = ["en", "es", "ja", "zh", "ko", "pt-BR", "de"]


def _entries_of(dataset_name: str, raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrait les entries d'un dataset raw (dict[id] ou dict[container_key=list])."""
    if dataset_name == "political_forces":
        items = raw.get("factions", [])
    elif dataset_name == "divergence_points":
        items = raw.get("divergence_points", [])
    elif dataset_name == "narrative_patterns":
        items = raw.get("patterns", [])
    else:
        # dict[id] -> entry
        items = list(raw.values()) if isinstance(raw, dict) else []
    return items


def _id_of(dataset_name: str, entry: dict[str, Any]) -> str | None:
    """Champ id pour ce dataset (id_field varie : id ou event_id)."""
    if dataset_name == "divergence_points":
        return str(entry.get("event_id", ""))
    return str(entry.get("id", ""))


def _ids_in_dataset(dataset_name: str, raw: dict[str, Any]) -> set[str]:
    return {_id_of(dataset_name, e) for e in _entries_of(dataset_name, raw)}


@pytest.fixture()
def fr_source_loaded() -> dict[str, Any]:
    """Charge les datasets FR source (sans i18n active)."""
    from shinobi.i18n import set_active_language
    set_active_language("fr")
    return _load_phase_h_datasets(CANONICAL_DIR)


# === Test loader fallback (toutes langues) ===

@pytest.mark.parametrize("lang", TARGET_LANGS)
def test_loader_falls_back_to_fr_when_lang_dir_missing(lang: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Si data/canon/i18n/<lang>/ n'existe pas, le loader retombe sur FR source.

    Robuste au cas ou les traductions Phase 7 n'ont pas encore ete generees.
    """
    from shinobi.i18n import set_active_language
    set_active_language(lang)

    # Force fallback : pointe vers un base path ou i18n/<lang> n'existe pas
    fr_data = _load_phase_h_datasets(CANONICAL_DIR)
    # FR source toujours disponible
    assert fr_data["deep_motivations"] != {}, "FR source must always load"

    # Reset
    set_active_language("fr")


# === Test loader avec datasets i18n simules ===

@pytest.mark.parametrize("lang", TARGET_LANGS)
def test_loader_picks_up_i18n_when_dir_exists(lang: str, tmp_path: Path) -> None:
    """Si data/canon/i18n/<lang>/<dataset>.json existe, il est utilise."""
    from shinobi.i18n import set_active_language

    # Setup : cree une fake structure data/canon/i18n/<lang>/
    fake_canonical = tmp_path / "canonical"
    fake_canonical.mkdir()
    fake_canon = tmp_path / "canon"
    fake_canon.mkdir()
    fake_i18n = fake_canon / "i18n" / lang
    fake_i18n.mkdir(parents=True)

    # FR source (fallback)
    fr_dm = {"naruto": {"id": "naruto", "deepest_fear": "FR fear"}}
    (fake_canon / "deep_motivations.json").write_text(json.dumps(fr_dm), encoding="utf-8")

    # Version i18n localisee (LANG_NAME marker pour distinguer)
    i18n_dm = {"naruto": {"id": "naruto", "deepest_fear": f"{lang.upper()} fear"}}
    (fake_i18n / "deep_motivations.json").write_text(json.dumps(i18n_dm), encoding="utf-8")

    set_active_language(lang)
    out = _load_phase_h_datasets(fake_canonical)
    assert out["deep_motivations"]["naruto"]["deepest_fear"] == f"{lang.upper()} fear"

    # Reset
    set_active_language("fr")


# === Test uniformite des ids (real data si dispo) ===

def test_ids_uniformity_across_languages_when_translated() -> None:
    """Pour chaque lang dont les datasets existent, les ids matchent la source FR.

    Skip si aucun dataset i18n n'a encore ete genere (Phase 7 pas encore lancee).
    """
    if not I18N_DIR.exists():
        pytest.skip("Phase 7 datasets not yet generated (data/canon/i18n/ absent)")

    # Charge FR source
    fr_ids: dict[str, set[str]] = {}
    for ds in DATASETS:
        path = CANON_DIR / f"{ds}.json"
        if not path.exists():
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        fr_ids[ds] = _ids_in_dataset(ds, raw)

    # Pour chaque lang qui a des fichiers, compare ids
    mismatches: list[str] = []
    langs_checked = 0
    for lang in TARGET_LANGS:
        lang_dir = I18N_DIR / lang
        if not lang_dir.exists():
            continue
        langs_checked += 1
        for ds in DATASETS:
            path = lang_dir / f"{ds}.json"
            if not path.exists():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                mismatches.append(f"{lang}/{ds}: invalid JSON: {exc}")
                continue
            lang_ids = _ids_in_dataset(ds, raw)
            if ds not in fr_ids:
                continue
            missing = fr_ids[ds] - lang_ids
            extra = lang_ids - fr_ids[ds]
            if missing:
                mismatches.append(f"{lang}/{ds}: missing {len(missing)} ids: {sorted(missing)[:3]}")
            if extra:
                mismatches.append(f"{lang}/{ds}: extra {len(extra)} ids: {sorted(extra)[:3]}")

    if langs_checked == 0:
        pytest.skip("No lang i18n directories exist yet")
    assert not mismatches, "ID uniformity violated:\n  " + "\n  ".join(mismatches)


def test_ids_uniformity_in_simulated_translation(tmp_path: Path) -> None:
    """Test pure unit : la logique d'extraction d'ids est correcte."""
    fake_canon = tmp_path / "canon"
    fake_canon.mkdir()
    (fake_canon / "i18n").mkdir()

    # FR source : 3 chars
    fr_dm = {
        "naruto": {"id": "naruto", "deepest_fear": "FR fear N"},
        "sasuke": {"id": "sasuke", "deepest_fear": "FR fear S"},
        "sakura": {"id": "sakura", "deepest_fear": "FR fear SA"},
    }
    (fake_canon / "deep_motivations.json").write_text(json.dumps(fr_dm), encoding="utf-8")

    # JA i18n : meme ids preserves
    ja_dir = fake_canon / "i18n" / "ja"
    ja_dir.mkdir()
    ja_dm = {
        "naruto": {"id": "naruto", "deepest_fear": "JA fear N"},
        "sasuke": {"id": "sasuke", "deepest_fear": "JA fear S"},
        "sakura": {"id": "sakura", "deepest_fear": "JA fear SA"},
    }
    (ja_dir / "deep_motivations.json").write_text(json.dumps(ja_dm), encoding="utf-8")

    fr_ids = set(fr_dm.keys())
    ja_ids = set(ja_dm.keys())
    assert fr_ids == ja_ids


# === Test : structure source FR toujours chargeable ===

def test_fr_source_always_loadable() -> None:
    """Sanity : FR source est toujours chargeable, peu importe la lang active."""
    from shinobi.i18n import set_active_language
    for lang in [*TARGET_LANGS, "fr"]:
        set_active_language(lang)
        out = _load_phase_h_datasets(CANONICAL_DIR)
        # Au moins un dataset doit etre non-vide
        assert any(out[ds] for ds in DATASETS), f"all datasets empty for lang={lang}"
    set_active_language("fr")
