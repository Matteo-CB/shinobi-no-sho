"""CRUD complet des sauvegardes."""

from __future__ import annotations

import gc
import json
import os
import shutil
import sqlite3
import stat
import tarfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shinobi.config import settings
from shinobi.constants import SCHEMA_VERSION
from shinobi.engine.actions import ActionResult
from shinobi.engine.character import Character
from shinobi.engine.world import WorldState
from shinobi.errors import SaveCorruptError, SaveNotFoundError
from shinobi.goals.breadcrumbs import Breadcrumb
from shinobi.goals.declaration import Goal
from shinobi.logging_setup import get_logger
from shinobi.persistence.database import close, open_connection
from shinobi.persistence.serialize import decode_payload, encode_json, encode_payload
from shinobi.utils.slug import slugify

logger = get_logger(__name__)


@dataclass(frozen=True)
class SaveMeta:
    save_id: str
    schema_version: int
    character_name: str
    character_age: int
    current_year: int
    current_date: str
    village: str
    rank: str
    canonicity_profile: str
    playtime_hours: float
    total_turns: int
    last_played: str
    created_at: str
    thumbnail_summary: str
    warnings: list[str]


def _save_dir(save_id: str) -> Path:
    return settings.saves_dir / save_id


def _meta_path(save_id: str) -> Path:
    return _save_dir(save_id) / "meta.json"


def _state_path(save_id: str) -> Path:
    return _save_dir(save_id) / "state.sqlite"


def _narrative_log_path(save_id: str) -> Path:
    return _save_dir(save_id) / "narrative_log.jsonl"


def _divergence_log_path(save_id: str) -> Path:
    return _save_dir(save_id) / "divergence_log.jsonl"


def dialogue_log_path(save_id: str) -> Path:
    """Chemin du log des dialogues style VN pour cette save (rolling window)."""
    return _save_dir(save_id) / "dialogues.jsonl"


def dialogue_archive_path(save_id: str) -> Path:
    """Chemin de l'archive JSONL des dialogues offloads (au-dela du window)."""
    return _save_dir(save_id) / "dialogues_archive.jsonl"


def kg_db_path(save_id: str) -> Path:
    """Chemin de la base SQLite du Knowledge Graph dynamique (Phase A/B/C)."""
    return _save_dir(save_id) / "kg.sqlite"


def personality_db_path(save_id: str) -> Path:
    """Chemin de la base SQLite des personnalites vectorielles (Phase D)."""
    return _save_dir(save_id) / "personality.sqlite"


def agents_db_path(save_id: str) -> Path:
    """Chemin de la base SQLite multi-agent (Phase E)."""
    return _save_dir(save_id) / "agents.sqlite"


def llm_cache_db_path(save_id: str) -> Path:
    """Chemin du cache disque pour inferences LLM (Phase E §11.2)."""
    return _save_dir(save_id) / "llm_cache.sqlite"


def agents_embeddings_db_path(save_id: str) -> Path:
    """Chemin de l'index BGE-M3 multi-agent (Phase E §6.1)."""
    return _save_dir(save_id) / "agents_embeddings.sqlite"


def list_saves() -> list[SaveMeta]:
    """Liste les saves presentes sur disque."""
    out: list[SaveMeta] = []
    if not settings.saves_dir.exists():
        return out
    for entry in sorted(settings.saves_dir.iterdir()):
        meta_p = entry / "meta.json"
        if not meta_p.exists():
            continue
        try:
            data = json.loads(meta_p.read_text(encoding="utf-8"))
            out.append(SaveMeta(**data))
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("save_meta_corrupt", save=entry.name, error=str(exc))
    return out


def create_save(
    character: Character,
    world: WorldState,
    *,
    canonicity_profile: str = "default",
    thumbnail_summary: str = "",
) -> str:
    """Cree une nouvelle save et persiste l'etat initial."""
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    save_id = f"{slugify(character.name)}_{timestamp}"
    sd = _save_dir(save_id)
    sd.mkdir(parents=True, exist_ok=True)

    meta = SaveMeta(
        save_id=save_id,
        schema_version=SCHEMA_VERSION,
        character_name=character.name,
        character_age=character.age_years,
        current_year=world.current_year,
        current_date=world.current_date,
        village=character.current_village,
        rank=character.rank,
        canonicity_profile=canonicity_profile,
        playtime_hours=0.0,
        total_turns=0,
        last_played=_iso_now(),
        created_at=_iso_now(),
        thumbnail_summary=thumbnail_summary
        or f"Personnage {character.name} a {character.age_years} ans",
        warnings=[],
    )
    _write_meta(save_id, meta)

    conn = open_connection(_state_path(save_id))
    try:
        _insert_character_snapshot(conn, character, year=world.current_year, turn=0)
        _insert_world_snapshot(conn, world, year=world.current_year, turn=0)
        conn.commit()
    finally:
        close(conn)

    _narrative_log_path(save_id).touch()
    _divergence_log_path(save_id).touch()

    logger.info("save_create", save_id=save_id)
    return save_id


def load_save(save_id: str) -> tuple[Character, WorldState, SaveMeta]:
    """Charge un save (snapshots les plus recents)."""
    if not _meta_path(save_id).exists():
        raise SaveNotFoundError(f"save inconnu: {save_id}")
    meta = SaveMeta(**json.loads(_meta_path(save_id).read_text(encoding="utf-8")))
    conn = open_connection(_state_path(save_id))
    try:
        char_payload = _fetch_current_payload(conn, "character")
        world_payload = _fetch_current_payload(conn, "world")
    finally:
        close(conn)
    if not char_payload or not world_payload:
        raise SaveCorruptError(f"save sans snapshot courant: {save_id}")
    character = decode_payload(char_payload, Character)
    world = decode_payload(world_payload, WorldState)
    return character, world, meta


def save_passive_state(
    save_id: str,
    *,
    turn_number: int,
    new_character: Character,
    new_world: WorldState,
    seed_state: int,
) -> None:
    """Persiste un snapshot character + world SANS log de turn (fast-forward).

    Spec Phase E §6.5 : 'le monde tourne sans le joueur'. Apres un
    fast-forward, le world state DOIT etre persiste pour que la session
    suivante reprenne dans l'etat avance. Mais il n'y a pas d'action joueur
    a logger ce 'tour' -> on saute la table turns.
    """
    conn = open_connection(_state_path(save_id))
    try:
        _insert_character_snapshot(
            conn, new_character, year=new_world.current_year, turn=turn_number,
        )
        _insert_world_snapshot(
            conn, new_world, year=new_world.current_year, turn=turn_number,
        )
        _update_current_character(
            conn, new_character, year=new_world.current_year, turn=turn_number,
        )
        _update_current_world(
            conn, new_world, year=new_world.current_year, turn=turn_number,
        )
        conn.commit()
    finally:
        close(conn)
    _bump_meta(
        save_id, turn_number=turn_number, world=new_world, character=new_character,
    )


def save_turn(
    save_id: str,
    *,
    turn_number: int,
    action_result: ActionResult,
    new_character: Character,
    new_world: WorldState,
    seed_state: int,
) -> None:
    """Persiste un tour : log de l'action + snapshot incremental."""
    conn = open_connection(_state_path(save_id))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO turns (
                turn_number, year, date, hour, action_type,
                action_payload, action_result, duration_minutes,
                seed_state, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turn_number,
                new_world.current_year,
                new_world.current_date,
                new_world.current_hour,
                action_result.action.action_type.value,
                encode_json(action_result.action.model_dump(mode="json")),
                encode_json(action_result.model_dump(mode="json")),
                action_result.duration_minutes,
                str(seed_state),
                _iso_now(),
            ),
        )
        if turn_number % settings.saves_snapshot_interval == 0 or turn_number == 1:
            _insert_character_snapshot(
                conn, new_character, year=new_world.current_year, turn=turn_number
            )
            _insert_world_snapshot(conn, new_world, year=new_world.current_year, turn=turn_number)
        else:
            _update_current_character(
                conn, new_character, year=new_world.current_year, turn=turn_number
            )
            _update_current_world(conn, new_world, year=new_world.current_year, turn=turn_number)
        conn.commit()
    finally:
        close(conn)
    _bump_meta(save_id, turn_number=turn_number, world=new_world, character=new_character)


def append_narrative_log(save_id: str, payload: dict[str, Any]) -> None:
    """Append-only journal narratif."""
    p = _narrative_log_path(save_id)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


# Goals + Breadcrumbs CRUD ---------------------------------------------------


def save_goal(save_id: str, goal: Goal) -> None:
    """Insere ou remplace un objectif."""
    conn = open_connection(_state_path(save_id))
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO goals (id, payload, status, declared_at_year, completed_at_year, abandoned_at_year) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                goal.id,
                encode_json(goal.model_dump(mode="json")),
                goal.status.value,
                goal.declared_at_year,
                goal.completed_at_year,
                goal.abandoned_at_year,
            ),
        )
        conn.commit()
    finally:
        close(conn)


def load_goals(save_id: str) -> list[Goal]:
    """Charge tous les objectifs d'un save."""
    conn = open_connection(_state_path(save_id))
    try:
        cur = conn.cursor()
        cur.execute("SELECT payload FROM goals")
        out: list[Goal] = []
        for row in cur.fetchall():
            out.append(Goal.model_validate_json(row[0]))
        return out
    finally:
        close(conn)


def save_breadcrumb(save_id: str, breadcrumb: Breadcrumb) -> None:
    """Insere ou remplace un breadcrumb."""
    conn = open_connection(_state_path(save_id))
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO breadcrumbs (id, parent_goal_id, payload, revealed, completed, sequence_index) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                breadcrumb.id,
                breadcrumb.parent_goal_id,
                encode_json(breadcrumb.model_dump(mode="json")),
                int(breadcrumb.revealed),
                int(breadcrumb.completed),
                breadcrumb.sequence_index,
            ),
        )
        conn.commit()
    finally:
        close(conn)


def save_active_mission(save_id: str, mission, *, year: int) -> None:
    """Persiste une mission acceptee (status: en cours)."""
    conn = open_connection(_state_path(save_id))
    try:
        cur = conn.cursor()
        # Mission est un dataclass frozen, on serialise en dict via dataclasses.asdict
        from dataclasses import asdict

        cur.execute(
            "INSERT OR REPLACE INTO active_missions (id, rank, title, payload, accepted_at_year, completed_at_year, success) "
            "VALUES (?, ?, ?, ?, ?, NULL, NULL)",
            (
                mission.id,
                mission.rank,
                mission.title,
                encode_json(asdict(mission)),
                year,
            ),
        )
        conn.commit()
    finally:
        close(conn)


def mark_mission_completed(save_id: str, mission_id: str, *, year: int, success: bool) -> None:
    """Marque une mission comme accomplie ou echouee."""
    conn = open_connection(_state_path(save_id))
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE active_missions SET completed_at_year = ?, success = ? WHERE id = ?",
            (year, int(success), mission_id),
        )
        conn.commit()
    finally:
        close(conn)


def load_active_missions(save_id: str) -> list[dict]:
    """Liste les missions acceptees (en cours ou completees)."""
    conn = open_connection(_state_path(save_id))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, rank, title, payload, accepted_at_year, completed_at_year, success "
            "FROM active_missions ORDER BY accepted_at_year DESC"
        )
        out = []
        for row in cur.fetchall():
            out.append(
                {
                    "id": row[0],
                    "rank": row[1],
                    "title": row[2],
                    "payload": json.loads(row[3]),
                    "accepted_at_year": row[4],
                    "completed_at_year": row[5],
                    "success": bool(row[6]) if row[6] is not None else None,
                }
            )
        return out
    finally:
        close(conn)


def load_breadcrumbs(save_id: str, *, parent_goal_id: str | None = None) -> list[Breadcrumb]:
    """Charge les breadcrumbs (filtres optionnellement par goal parent)."""
    conn = open_connection(_state_path(save_id))
    try:
        cur = conn.cursor()
        if parent_goal_id:
            cur.execute(
                "SELECT payload FROM breadcrumbs WHERE parent_goal_id = ? ORDER BY sequence_index",
                (parent_goal_id,),
            )
        else:
            cur.execute("SELECT payload FROM breadcrumbs ORDER BY sequence_index")
        out: list[Breadcrumb] = []
        for row in cur.fetchall():
            out.append(Breadcrumb.model_validate_json(row[0]))
        return out
    finally:
        close(conn)


def append_divergence(save_id: str, payload: dict[str, Any]) -> None:
    """Append-only journal des divergences."""
    p = _divergence_log_path(save_id)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def delete_save(save_id: str) -> None:
    """Supprime entierement une save (robuste vs Windows file locks)."""
    sd = _save_dir(save_id)
    if not sd.exists():
        raise SaveNotFoundError(save_id)
    _robust_rmtree(sd)


def _robust_rmtree(path: Path, *, retries: int = 6, base_delay: float = 0.3) -> None:
    """Supprime un dossier avec retry et permissions reset (resiste aux locks Windows)."""
    gc.collect()  # libere les handles SQLite eventuellement non fermes

    def _on_error(func, target, exc_info):
        try:
            os.chmod(target, stat.S_IWRITE)
        except OSError:
            pass
        try:
            func(target)
        except OSError:
            pass

    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            shutil.rmtree(path, onerror=_on_error)
            if not path.exists():
                return
        except PermissionError as exc:
            last_exc = exc
        except FileNotFoundError:
            return
        time.sleep(base_delay * (2**attempt))
    if path.exists() and last_exc is not None:
        raise last_exc


def duplicate_save(save_id: str, new_label: str) -> str:
    """Duplique une save avec un nouveau timestamp et un suffixe label."""
    if not _save_dir(save_id).exists():
        raise SaveNotFoundError(save_id)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    new_id = f"{slugify(new_label)}_{timestamp}"
    shutil.copytree(_save_dir(save_id), _save_dir(new_id))
    meta = json.loads(_meta_path(new_id).read_text(encoding="utf-8"))
    meta["save_id"] = new_id
    meta["created_at"] = _iso_now()
    _meta_path(new_id).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return new_id


def export_save(save_id: str, output_path: Path) -> Path:
    """Exporte une save sous forme d'archive .shinosave (tar.gz)."""
    if not _save_dir(save_id).exists():
        raise SaveNotFoundError(save_id)
    output_path = output_path.with_suffix(".shinosave")
    with tarfile.open(output_path, "w:gz") as tar:
        tar.add(_save_dir(save_id), arcname=save_id)
    return output_path


def import_save(archive_path: Path) -> str:
    """Importe une archive .shinosave et retourne le save_id."""
    settings.saves_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as tar:
        members = tar.getnames()
        roots = {m.split("/")[0] for m in members}
        if len(roots) != 1:
            raise SaveCorruptError("archive contenant plusieurs roots")
        save_id = roots.pop()
        if _save_dir(save_id).exists():
            raise SaveCorruptError(f"save_id deja present: {save_id}")
        tar.extractall(settings.saves_dir, filter="data")
    return save_id


# helpers internes -------------------------------------------------------------


def _write_meta(save_id: str, meta: SaveMeta) -> None:
    payload = {
        "save_id": meta.save_id,
        "schema_version": meta.schema_version,
        "character_name": meta.character_name,
        "character_age": meta.character_age,
        "current_year": meta.current_year,
        "current_date": meta.current_date,
        "village": meta.village,
        "rank": meta.rank,
        "canonicity_profile": meta.canonicity_profile,
        "playtime_hours": meta.playtime_hours,
        "total_turns": meta.total_turns,
        "last_played": meta.last_played,
        "created_at": meta.created_at,
        "thumbnail_summary": meta.thumbnail_summary,
        "warnings": list(meta.warnings),
    }
    _meta_path(save_id).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _bump_meta(
    save_id: str,
    *,
    turn_number: int,
    world: WorldState,
    character: Character,
) -> None:
    """Met a jour meta.json apres un tour."""
    p = _meta_path(save_id)
    if not p.exists():
        return
    data = json.loads(p.read_text(encoding="utf-8"))
    data["total_turns"] = turn_number
    data["last_played"] = _iso_now()
    data["current_year"] = world.current_year
    data["current_date"] = world.current_date
    data["character_age"] = character.age_years
    data["village"] = character.current_village
    data["rank"] = character.rank
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _insert_character_snapshot(
    conn: sqlite3.Connection, character: Character, *, year: int, turn: int
) -> None:
    cur = conn.cursor()
    cur.execute("UPDATE character SET is_current = 0")
    cur.execute(
        "INSERT INTO character (payload, snapshot_at_year, snapshot_at_turn, is_current) VALUES (?, ?, ?, 1)",
        (encode_payload(character), year, turn),
    )


def _insert_world_snapshot(
    conn: sqlite3.Connection, world: WorldState, *, year: int, turn: int
) -> None:
    cur = conn.cursor()
    cur.execute("UPDATE world SET is_current = 0")
    cur.execute(
        "INSERT INTO world (payload, snapshot_at_year, snapshot_at_turn, is_current) VALUES (?, ?, ?, 1)",
        (encode_payload(world), year, turn),
    )


def _update_current_character(
    conn: sqlite3.Connection, character: Character, *, year: int, turn: int
) -> None:
    cur = conn.cursor()
    payload = encode_payload(character)
    cur.execute(
        "UPDATE character SET payload = ?, snapshot_at_year = ?, snapshot_at_turn = ? WHERE is_current = 1",
        (payload, year, turn),
    )
    if cur.rowcount == 0:
        _insert_character_snapshot(conn, character, year=year, turn=turn)


def _update_current_world(
    conn: sqlite3.Connection, world: WorldState, *, year: int, turn: int
) -> None:
    cur = conn.cursor()
    payload = encode_payload(world)
    cur.execute(
        "UPDATE world SET payload = ?, snapshot_at_year = ?, snapshot_at_turn = ? WHERE is_current = 1",
        (payload, year, turn),
    )
    if cur.rowcount == 0:
        _insert_world_snapshot(conn, world, year=year, turn=turn)


def _fetch_current_payload(conn: sqlite3.Connection, table: str) -> bytes | None:
    cur = conn.cursor()
    cur.execute(f"SELECT payload FROM {table} WHERE is_current = 1 ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    if row is None:
        return None
    return row[0] if isinstance(row[0], bytes) else bytes(row[0], "utf-8")


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
