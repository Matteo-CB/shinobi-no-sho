"""Tests pour le bootstrap RAG : fingerprint + index detection (sans network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from shinobi.rag import bootstrap


@pytest.fixture()
def isolated_canon_and_index(tmp_path: Path, monkeypatch):
    """Isole canonical_data_dir + chroma_persist_dir dans un tmp_path."""
    from shinobi.config import settings

    canon_dir = tmp_path / "canon"
    embed_dir = tmp_path / "embeddings"
    canon_dir.mkdir()
    embed_dir.mkdir()
    monkeypatch.setattr(
        type(settings), "canonical_data_dir", property(lambda self: canon_dir)
    )
    monkeypatch.setattr(
        type(settings), "chroma_persist_dir", property(lambda self: embed_dir)
    )
    return canon_dir, embed_dir


def test_fingerprint_no_canon_returns_constant(isolated_canon_and_index) -> None:
    canon_dir, _ = isolated_canon_and_index
    # Vide le canon
    for p in canon_dir.iterdir():
        p.unlink()
    fp = bootstrap.compute_canon_fingerprint()
    assert isinstance(fp, str)


def test_fingerprint_changes_when_canon_changes(isolated_canon_and_index) -> None:
    canon_dir, _ = isolated_canon_and_index
    (canon_dir / "test.json").write_text('{"a": 1}', encoding="utf-8")
    fp1 = bootstrap.compute_canon_fingerprint()
    (canon_dir / "test.json").write_text('{"a": 2}', encoding="utf-8")
    fp2 = bootstrap.compute_canon_fingerprint()
    assert fp1 != fp2


def test_fingerprint_stable_across_calls(isolated_canon_and_index) -> None:
    canon_dir, _ = isolated_canon_and_index
    (canon_dir / "test.json").write_text('{"x": [1, 2, 3]}', encoding="utf-8")
    assert bootstrap.compute_canon_fingerprint() == bootstrap.compute_canon_fingerprint()


def test_fingerprint_independent_of_file_order(isolated_canon_and_index) -> None:
    """Fingerprint doit dependre du contenu trie, pas de l'ordre de creation."""
    canon_dir, _ = isolated_canon_and_index
    (canon_dir / "z.json").write_text('{"k": "z"}', encoding="utf-8")
    (canon_dir / "a.json").write_text('{"k": "a"}', encoding="utf-8")
    fp1 = bootstrap.compute_canon_fingerprint()
    # Recree dans l'ordre inverse
    (canon_dir / "a.json").unlink()
    (canon_dir / "z.json").unlink()
    (canon_dir / "a.json").write_text('{"k": "a"}', encoding="utf-8")
    (canon_dir / "z.json").write_text('{"k": "z"}', encoding="utf-8")
    fp2 = bootstrap.compute_canon_fingerprint()
    assert fp1 == fp2


def test_index_is_present_false_when_db_missing(isolated_canon_and_index) -> None:
    assert bootstrap.index_is_present() is False


def test_index_is_present_true_when_db_exists(isolated_canon_and_index) -> None:
    _, embed_dir = isolated_canon_and_index
    (embed_dir / "chroma.sqlite3").write_bytes(b"fake-but-not-empty")
    assert bootstrap.index_is_present() is True


def test_read_stored_fingerprint_returns_none_if_absent(isolated_canon_and_index) -> None:
    assert bootstrap.read_stored_fingerprint() is None


def test_write_then_read_fingerprint_roundtrip(isolated_canon_and_index) -> None:
    bootstrap.write_stored_fingerprint("abc123")
    assert bootstrap.read_stored_fingerprint() == "abc123"


def test_index_is_up_to_date_false_when_no_db(isolated_canon_and_index) -> None:
    bootstrap.write_stored_fingerprint("anything")
    assert bootstrap.index_is_up_to_date() is False


def test_index_is_up_to_date_when_fingerprint_matches(isolated_canon_and_index) -> None:
    canon_dir, embed_dir = isolated_canon_and_index
    (canon_dir / "x.json").write_text('{"a": 1}', encoding="utf-8")
    (embed_dir / "chroma.sqlite3").write_bytes(b"db")
    bootstrap.write_stored_fingerprint(bootstrap.compute_canon_fingerprint())
    assert bootstrap.index_is_up_to_date() is True


def test_index_is_up_to_date_false_when_canon_changes(isolated_canon_and_index) -> None:
    canon_dir, embed_dir = isolated_canon_and_index
    (canon_dir / "x.json").write_text('{"a": 1}', encoding="utf-8")
    (embed_dir / "chroma.sqlite3").write_bytes(b"db")
    bootstrap.write_stored_fingerprint(bootstrap.compute_canon_fingerprint())
    # Le canon change apres l'enregistrement du fingerprint
    (canon_dir / "x.json").write_text('{"a": 2}', encoding="utf-8")
    assert bootstrap.index_is_up_to_date() is False


def test_index_present_without_fingerprint_is_trusted(isolated_canon_and_index) -> None:
    """Compat installs existants sans fingerprint enregistre : pas de re-build force."""
    _, embed_dir = isolated_canon_and_index
    (embed_dir / "chroma.sqlite3").write_bytes(b"db")
    # Pas de fingerprint
    assert bootstrap.index_is_up_to_date() is True
