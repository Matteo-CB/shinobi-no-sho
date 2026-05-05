"""Tests sur le chargement et les requetes canoniques."""

from __future__ import annotations

import pytest

from shinobi.canon.loader import load_canon
from shinobi.canon.profiles import CanonicityProfile, filter_canon
from shinobi.canon.queries import find_techniques, list_villages
from shinobi.types import TechniqueRank


@pytest.fixture(scope="module")
def canon():
    return load_canon(
        optional=(
            "organizations",
            "tailed_beasts",
            "kekkei_mora",
            "hiden",
            "timeline_events",
            "voice_profiles",
        )
    )


def test_canon_has_data(canon) -> None:
    assert len(canon.natures) >= 5
    assert len(canon.ranks) >= 5
    assert len(canon.eras) >= 5
    # Verifie que les data scrapees sont la (peut etre absent en test minimal)
    if canon.characters:
        assert len(canon.characters) > 50
    if canon.techniques:
        assert len(canon.techniques) > 50


def test_villages_exist(canon) -> None:
    villages = list_villages(canon)
    if villages:
        ids = {v.id for v in villages}
        # Pas necessairement present si scrape pas fait
        assert len(ids) == len(villages)


def test_find_techniques_filter(canon) -> None:
    if not canon.techniques:
        pytest.skip("aucune technique chargee")
    techs = find_techniques(canon, max_rank=TechniqueRank.b)
    for t in techs:
        assert t.rank in {TechniqueRank.e, TechniqueRank.d, TechniqueRank.c, TechniqueRank.b}


def test_canonicity_profile_filter(canon) -> None:
    profile = CanonicityProfile.manga_only()
    filtered = filter_canon(canon, profile)
    for tech in filtered.techniques.values():
        assert str(tech.canonicity) in profile.sources
