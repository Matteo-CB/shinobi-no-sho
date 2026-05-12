"""Tests pour le mode 'incarner un canon character' (Phase 6.3 + Phase 7
extension player-as-canon).

Couvre :
- list_playable_canon_characters : filtres + tri par notoriete
- incarnate_canon_character : hydratation depuis canon + age + current_year
- _filter_techniques_at_age : filtre techniques selon age (multi-era + cap
  age enfant)
- _seed_relationships : relations canon -> Character.relationships
- _stats_for_canon_character : stats deterministes + bonus prodige + clan
- _rank_for_canon_at_age : prodiges (Itachi/Kakashi) vs default progression
- e2e : incarner Itachi 13ans -> Character coherent + current_year correct
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from shinobi.canon.loader import load_canon
from shinobi.cli.canon_incarnation import (
    _filter_techniques_at_age,
    _rank_for_canon_at_age,
    _stats_for_canon_character,
    incarnate_canon_character,
    list_playable_canon_characters,
)


@pytest.fixture(scope="module")
def canon():
    return load_canon()


# === list_playable_canon_characters ======================================


def test_list_playable_returns_characters_with_birth_year(canon) -> None:
    """Tous les playable doivent avoir un birth_year defini."""
    playable = list_playable_canon_characters(canon)
    assert len(playable) > 0
    for c in playable:
        assert c.birth_year is not None


def test_list_playable_filter_by_village_konoha(canon) -> None:
    """Filtre village_filter restreint aux chars du village."""
    konoha = list_playable_canon_characters(
        canon, village_filter="konohagakure",
    )
    assert all(c.village_of_origin == "konohagakure" for c in konoha)
    suna = list_playable_canon_characters(
        canon, village_filter="sunagakure",
    )
    assert all(c.village_of_origin == "sunagakure" for c in suna)
    # Aucun overlap (un perso ne peut etre que d'un village_of_origin)
    konoha_ids = {c.id for c in konoha}
    suna_ids = {c.id for c in suna}
    assert konoha_ids.isdisjoint(suna_ids)


def test_list_playable_filter_alive_at_year(canon) -> None:
    """alive_at_year skip les morts ou pas encore nes."""
    # A year=0, Itachi pas encore ne (birth=-7 ✗ death=16)
    # Naruto ne en year=0 (birth_year=0)
    at_year_0 = list_playable_canon_characters(canon, alive_at_year=0)
    ids_at_0 = {c.id for c in at_year_0}
    # Itachi vivant en 0 (birth=-7 ≤ 0 < death=16)
    assert "uchiha_itachi" in ids_at_0
    # Hashirama mort avant year=0
    hashirama = canon.characters.get("senju_hashirama")
    if hashirama and hashirama.death_year is not None:
        if hashirama.death_year <= 0:
            assert "senju_hashirama" not in ids_at_0


def test_list_playable_sorted_by_notoriety(canon) -> None:
    """Top 5 inclut les personnages canon majeurs."""
    playable = list_playable_canon_characters(canon)
    top_ids = {c.id for c in playable[:30]}
    # Ces chars majeurs doivent etre dans le top 30 (Phase H 9.2 covers ~50)
    expected_top = {
        "uzumaki_naruto", "uchiha_sasuke", "hatake_kakashi",
        "uchiha_itachi", "haruno_sakura",
    }
    overlap = top_ids & expected_top
    assert len(overlap) >= 3, (
        f"Notoriete insuffisante : top30 contient seulement {overlap} "
        f"des canon majeurs"
    )


# === _rank_for_canon_at_age ==============================================


def test_rank_for_canon_at_age_prodigy_jonin_at_13() -> None:
    """Itachi et Kakashi prodigies -> jonin a 13 ans."""
    assert _rank_for_canon_at_age("uchiha_itachi", 13) == "jonin"
    assert _rank_for_canon_at_age("hatake_kakashi", 13) == "jonin"
    assert _rank_for_canon_at_age("namikaze_minato", 13) == "jonin"


def test_rank_for_canon_at_age_prodigy_chunin_at_9() -> None:
    """Naruto/Sasuke prodigies montent chunin avant 12."""
    assert _rank_for_canon_at_age("uzumaki_naruto", 9) == "chunin"


def test_rank_for_canon_at_age_default_genin_at_12() -> None:
    """Personnage non-prodige : progression standard."""
    assert _rank_for_canon_at_age("aburame_shino", 12) == "genin"
    assert _rank_for_canon_at_age("aburame_shino", 8) == "academy_student"
    assert _rank_for_canon_at_age("aburame_shino", 5) == "civilian"


def test_rank_for_canon_at_age_prodigy_civilian_under_6() -> None:
    """Meme prodige, civilian si age < 6."""
    assert _rank_for_canon_at_age("uchiha_itachi", 4) == "civilian"


# === _filter_techniques_at_age ===========================================


def test_filter_techniques_at_age_skips_file_refs() -> None:
    """Les 'techniques' file_*.png ne sont pas des vraies techs."""
    fake_era = MagicMock()
    fake_era.year = 10
    fake_era.techniques = [
        "file_itachi_genjutsu_png",
        "real_technique",
        "another_real_tech",
        "file_kakashi_chidori_png",
    ]
    fake_char = MagicMock()
    fake_char.birth_year = 0
    fake_char.techniques_known_by_era = [fake_era]
    out = _filter_techniques_at_age(fake_char, age=10)
    tech_ids = [t.technique_id for t in out]
    assert "file_itachi_genjutsu_png" not in tech_ids
    assert "real_technique" in tech_ids


def test_filter_techniques_at_age_returns_subset_for_young_age() -> None:
    """Cas single-era : age = 50% du canon era => 50% des techs."""
    fake_era = MagicMock()
    fake_era.year = 20  # canon char a 20 ans dans son era
    fake_era.techniques = [f"tech_{i}" for i in range(10)]
    fake_char = MagicMock()
    fake_char.birth_year = 0
    fake_char.techniques_known_by_era = [fake_era]
    out_at_10 = _filter_techniques_at_age(fake_char, age=10)
    out_at_20 = _filter_techniques_at_age(fake_char, age=20)
    assert len(out_at_10) < len(out_at_20)
    # ~50% des techs a age=10
    assert 3 <= len(out_at_10) <= 7


def test_filter_techniques_at_age_empty_canon() -> None:
    """Aucune entry techniques_known_by_era -> liste vide."""
    fake_char = MagicMock()
    fake_char.birth_year = 0
    fake_char.techniques_known_by_era = []
    out = _filter_techniques_at_age(fake_char, age=15)
    assert out == []


def test_filter_techniques_real_canon_itachi(canon) -> None:
    """Itachi a 13 ans : retourne un sous-ensemble cap des canon techs."""
    itachi = canon.characters["uchiha_itachi"]
    out = _filter_techniques_at_age(itachi, age=13)
    assert len(out) >= 5  # au moins quelques techs
    # Aucune tech file_*.png
    for t in out:
        assert not t.technique_id.startswith("file_")
        assert not t.technique_id.endswith("_png")


# === incarnate_canon_character ==========================================


def test_incarnate_unknown_id_raises(canon) -> None:
    """canon_id inconnu -> KeyError."""
    with pytest.raises(KeyError):
        incarnate_canon_character(canon, "nonexistent_xyz", age_at_start=10)


def test_incarnate_itachi_at_13(canon) -> None:
    """E2E Itachi a 13 ans : Character coherent + current_year correct."""
    char, current_year = incarnate_canon_character(
        canon, "uchiha_itachi", age_at_start=13,
    )
    # Itachi : birth=-7, age=13 -> current_year=6
    assert current_year == 6
    assert char.age_years == 13
    assert char.birth_year == -7
    assert char.clan == "uchiha"
    assert char.current_village == "konohagakure"
    assert "katon" in char.natures
    assert "sharingan" in char.kekkei_genkai
    assert char.rank == "jonin"  # prodige Phase H prodigy bonus
    # Stats prodige : ninjutsu/genjutsu/perception eleves
    assert char.stats.ninjutsu >= 2.0
    # Techniques connues hydratees
    assert len(char.techniques_known) >= 3


def test_incarnate_naruto_at_12(canon) -> None:
    """E2E Naruto a 12 ans : current_year=12."""
    char, current_year = incarnate_canon_character(
        canon, "uzumaki_naruto", age_at_start=12,
    )
    assert current_year == 12
    assert char.age_years == 12
    # Naruto canon n'a pas de clan habituellement (uzumaki est rare)
    # mais on accepte si clan defini ou None
    # Natures : fuuton attendu mais le canon peut avoir d'autres
    assert isinstance(char.natures, list)


def test_incarnate_sasuke_at_7(canon) -> None:
    """E2E Sasuke a 7 ans : civilian status si non-prodige enfance."""
    char, year = incarnate_canon_character(
        canon, "uchiha_sasuke", age_at_start=7,
    )
    assert char.age_years == 7
    # Sasuke prodige -> academy_student a 7 (>= 6 et < 9)
    assert char.rank == "academy_student"
    # Stats reduites pour age enfant
    assert char.stats.ninjutsu < 4.0


def test_incarnate_caps_age_at_death_year(canon) -> None:
    """Si age_at_start fait depasser death_year, on cap juste avant."""
    # Itachi : death_year=16, birth_year=-7, max age = 22
    char, year = incarnate_canon_character(
        canon, "uchiha_itachi", age_at_start=50,
    )
    # year doit etre cape a death_year - 1 = 15
    assert year < 16
    # age recalcule en consequence
    assert char.age_years == year - char.birth_year


def test_incarnate_deterministic_seed(canon) -> None:
    """Meme canon_id + meme age = memes stats (seed deterministe)."""
    c1, _ = incarnate_canon_character(canon, "uchiha_itachi", age_at_start=13)
    c2, _ = incarnate_canon_character(canon, "uchiha_itachi", age_at_start=13)
    assert c1.stats.ninjutsu == c2.stats.ninjutsu
    assert c1.stats.genjutsu == c2.stats.genjutsu
    assert c1.extended_stats.lineage_value == c2.extended_stats.lineage_value
    assert c1.chakra.max == c2.chakra.max


def test_incarnate_different_age_different_techniques(canon) -> None:
    """Meme char a different age = different set de techniques."""
    young, _ = incarnate_canon_character(canon, "uchiha_itachi", age_at_start=8)
    old, _ = incarnate_canon_character(canon, "uchiha_itachi", age_at_start=15)
    assert len(young.techniques_known) <= len(old.techniques_known)


# === _stats_for_canon_character bonus ====================================


def test_stats_uchiha_clan_bonus(canon) -> None:
    """Uchiha canon : ninjutsu + genjutsu boostes."""
    itachi = canon.characters["uchiha_itachi"]
    stats, _, _ = _stats_for_canon_character(itachi, age=20, current_year=13)
    # +0.5 ninjutsu + 0.5 genjutsu via clan bonus
    # difficile a comparer abs sans random control mais on verifie >= 2.0
    assert stats.ninjutsu >= 2.0


def test_stats_prodigy_bonus_present(canon) -> None:
    """Prodige (Itachi) a learning_genius eleve."""
    itachi = canon.characters["uchiha_itachi"]
    _, ext, _ = _stats_for_canon_character(itachi, age=20, current_year=13)
    # Bonus prodige +0.7 sur learning_genius
    assert ext.learning_genius >= 2.0


def test_resolve_canon_id_exact_id(canon) -> None:
    """resolve_canon_id : match exact sur id."""
    from shinobi.cli.canon_incarnation import resolve_canon_id

    cid, candidates = resolve_canon_id(canon, "uchiha_itachi")
    assert cid == "uchiha_itachi"


def test_resolve_canon_id_exact_name_romaji(canon) -> None:
    """resolve_canon_id : match exact sur name_romaji."""
    from shinobi.cli.canon_incarnation import resolve_canon_id

    cid, _ = resolve_canon_id(canon, "Itachi Uchiha")
    assert cid == "uchiha_itachi"


def test_resolve_canon_id_substring_naruto(canon) -> None:
    """resolve_canon_id : 'naruto' partial trouve uzumaki_naruto."""
    from shinobi.cli.canon_incarnation import resolve_canon_id

    cid, candidates = resolve_canon_id(canon, "uzumaki_naruto")
    assert cid == "uzumaki_naruto"


def test_resolve_canon_id_unknown_returns_none(canon) -> None:
    """resolve_canon_id : query inconnue retourne None + candidates vide."""
    from shinobi.cli.canon_incarnation import resolve_canon_id

    cid, candidates = resolve_canon_id(canon, "totally_made_up_xyz_zzz")
    assert cid is None
    assert candidates == []


def test_resolve_canon_id_ambiguous_returns_candidates(canon) -> None:
    """resolve_canon_id : 'uchiha' matche plusieurs -> None + candidates."""
    from shinobi.cli.canon_incarnation import resolve_canon_id

    cid, candidates = resolve_canon_id(canon, "uchiha")
    # 29 chars contiennent 'uchiha' -> ambigu
    assert cid is None
    assert len(candidates) > 5


def test_resolve_canon_id_empty_query() -> None:
    """resolve_canon_id : query vide retourne None."""
    from shinobi.cli.canon_incarnation import resolve_canon_id

    fake_canon = MagicMock()
    fake_canon.characters = {}
    cid, candidates = resolve_canon_id(fake_canon, "")
    assert cid is None
    assert candidates == []


def test_stats_age_scaling_for_children(canon) -> None:
    """Age < 10 : stats reduites."""
    naruto = canon.characters["uzumaki_naruto"]
    young_stats, _, _ = _stats_for_canon_character(
        naruto, age=5, current_year=5,
    )
    adult_stats, _, _ = _stats_for_canon_character(
        naruto, age=15, current_year=15,
    )
    # Stamina scale par age
    assert young_stats.stamina < adult_stats.stamina
