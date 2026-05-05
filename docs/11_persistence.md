# 11. Persistance et sauvegardes

Comment l'etat de jeu est sauvegarde, charge, et conserve dans le temps. Une partie peut durer des centaines de tours et evoluer sur plusieurs decennies in-game, le systeme doit etre robuste et compact.

## 1. Stockage par partie

Chaque partie a son propre dossier sous `data/saves/[save_id]/` :

```
data/saves/[save_id]/
  meta.json                     metadonnees pour l'index (visible sans charger la save)
  state.sqlite                  etat complet de la partie
  narrative_log.jsonl           historique narratif compresse, append-only
  thumbnail.txt                 resume visible dans la liste des saves
  divergence_log.jsonl          journal des divergences canoniques
```

Le `save_id` est un slug genere a la creation : `[character_name_slug]_[timestamp]`, par exemple `uchiha_kano_20260501_193412`.

## 2. meta.json

Contient les informations affichees dans le menu de chargement sans avoir a ouvrir la base SQLite.

```json
{
  "save_id": "uchiha_kano_20260501_193412",
  "schema_version": 1,
  "character_name": "Kano Uchiha",
  "character_age": 14,
  "current_year": 15,
  "current_date": "06-22",
  "village": "konohagakure",
  "rank": "genin",
  "canonicity_profile": "default",
  "playtime_hours": 12.5,
  "total_turns": 287,
  "last_played": "2026-05-01T19:34:12Z",
  "created_at": "2026-04-22T10:00:00Z",
  "thumbnail_summary": "Genin Uchiha apres son examen. A demande l'objectif de proteger Itachi.",
  "warnings": []
}
```

`schema_version` permet de gerer les migrations entre versions du moteur.

## 3. state.sqlite

Une base SQLite par partie, isolee. Tables principales :

```sql
CREATE TABLE character (
    id INTEGER PRIMARY KEY,
    payload JSON NOT NULL,        -- serialisation complete du Character
    snapshot_at_year INTEGER NOT NULL,
    snapshot_at_turn INTEGER NOT NULL,
    is_current BOOLEAN NOT NULL DEFAULT 1
);
CREATE INDEX idx_character_current ON character(is_current);

CREATE TABLE world (
    id INTEGER PRIMARY KEY,
    payload JSON NOT NULL,        -- serialisation complete du WorldState
    snapshot_at_year INTEGER NOT NULL,
    snapshot_at_turn INTEGER NOT NULL,
    is_current BOOLEAN NOT NULL DEFAULT 1
);
CREATE INDEX idx_world_current ON world(is_current);

CREATE TABLE turns (
    turn_number INTEGER PRIMARY KEY,
    year INTEGER NOT NULL,
    date TEXT NOT NULL,
    hour INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    action_payload JSON NOT NULL,
    action_result JSON NOT NULL,
    duration_minutes INTEGER NOT NULL,
    seed_state INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_turns_year ON turns(year);

CREATE TABLE goals (
    id TEXT PRIMARY KEY,
    payload JSON NOT NULL,        -- serialisation du Goal
    status TEXT NOT NULL,
    declared_at_year INTEGER NOT NULL,
    completed_at_year INTEGER,
    abandoned_at_year INTEGER
);
CREATE INDEX idx_goals_status ON goals(status);

CREATE TABLE breadcrumbs (
    id TEXT PRIMARY KEY,
    parent_goal_id TEXT NOT NULL,
    payload JSON NOT NULL,
    revealed BOOLEAN NOT NULL DEFAULT 0,
    completed BOOLEAN NOT NULL DEFAULT 0,
    sequence_index INTEGER NOT NULL,
    FOREIGN KEY (parent_goal_id) REFERENCES goals(id)
);
CREATE INDEX idx_breadcrumbs_active ON breadcrumbs(revealed, completed);

CREATE TABLE relationships (
    character_id TEXT NOT NULL,
    with_character_id TEXT NOT NULL,
    payload JSON NOT NULL,
    last_updated_year INTEGER NOT NULL,
    PRIMARY KEY (character_id, with_character_id)
);

CREATE TABLE npc_states (
    character_id TEXT PRIMARY KEY,
    payload JSON NOT NULL,
    attention_level TEXT NOT NULL,    -- HIGH, MEDIUM, LOW, DORMANT
    last_updated_year INTEGER NOT NULL,
    last_updated_turn INTEGER NOT NULL
);
CREATE INDEX idx_npc_attention ON npc_states(attention_level);

CREATE TABLE scheduled_events (
    event_id TEXT PRIMARY KEY,
    year INTEGER NOT NULL,
    date TEXT NOT NULL,
    payload JSON NOT NULL,
    status TEXT NOT NULL,             -- scheduled, triggered, cancelled, modified, delayed
    triggered_at_turn INTEGER,
    notes TEXT
);
CREATE INDEX idx_events_status_year ON scheduled_events(status, year);

CREATE TABLE rumors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_event_id TEXT,
    content TEXT NOT NULL,
    fidelity REAL NOT NULL,
    diffusion_radius TEXT NOT NULL,
    born_at_year INTEGER NOT NULL,
    expires_at_year INTEGER,
    received_by_player BOOLEAN NOT NULL DEFAULT 0
);
CREATE INDEX idx_rumors_active ON rumors(expires_at_year);

CREATE TABLE knowledge (
    subject_id TEXT NOT NULL,
    subject_type TEXT NOT NULL,       -- event, technique, character, location, secret
    knowledge_level TEXT NOT NULL,    -- rumor, confirmed, witnessed
    acquired_at_year INTEGER NOT NULL,
    notes TEXT,
    PRIMARY KEY (subject_id, subject_type)
);

CREATE TABLE techniques_known (
    technique_id TEXT PRIMARY KEY,
    payload JSON NOT NULL,
    learned_at_year INTEGER NOT NULL,
    mastery_level REAL NOT NULL
);

CREATE TABLE techniques_in_progress (
    technique_id TEXT PRIMARY KEY,
    payload JSON NOT NULL,
    started_at_year INTEGER NOT NULL,
    progress_hours INTEGER NOT NULL,
    progress_required INTEGER NOT NULL
);

CREATE TABLE save_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

Les tables `character` et `world` peuvent contenir plusieurs lignes, une par snapshot. Le snapshot le plus recent a `is_current = 1`. Les snapshots anciens servent au rejeu.

## 4. Strategie de snapshot

### 4.1 Snapshot complet vs delta

Sauvegarder le `Character` et le `WorldState` complets a chaque tour serait simple mais couteux. Pour les longues parties, on utilise une strategie hybride :

```
- Snapshot complet toutes les N actions (par defaut N=50).
- Entre deux snapshots, seuls les ActionResult sont stockes dans turns.
- Pour reconstruire l'etat a un tour donne : charger le snapshot precedent, puis appliquer
  les ActionResult successifs.
```

Cela limite la taille de la base et permet aussi le "replay" depuis un snapshot.

### 4.2 Compression

Les JSON dans les colonnes `payload` peuvent etre stockes au choix :

- en JSON brut (lisible, debuggable)
- en JSON compresse zlib (plus compact, moins lisible)

Le choix se fait via un setting `SAVES_COMPRESS_PAYLOADS=true|false` dans `.env`. Default true en production, false en dev.

### 4.3 Pruning

Les snapshots tres anciens (au-dela de 100 actions) peuvent etre purges si l'option `SAVES_PRUNE_OLD_SNAPSHOTS=true`. Un snapshot conserve toujours :

- snapshot a la creation du perso (an 0 du jeu, jamais purge)
- snapshot tous les 5 ans in-game
- snapshot avant un evenement majeur

## 5. narrative_log.jsonl

Format ligne par ligne JSON, append-only. Chaque ligne est un evenement narratif important :

```json
{"turn": 287, "year": 15, "date": "06-22", "type": "narration", "content": "Tu sors du bureau du Hokage avec les details de ta premiere mission C rang."}
{"turn": 287, "year": 15, "date": "06-22", "type": "dialogue", "speaker": "sarutobi_hiruzen", "content": "Sois prudent, jeune Uchiha."}
{"turn": 288, "year": 15, "date": "06-23", "type": "action_result", "action": "travel", "outcome": "Voyage de 2 jours vers Pays des Vagues."}
{"turn": 289, "year": 15, "date": "06-25", "type": "event_triggered", "event_id": "wave_country_arrival"}
```

Ce log sert a la fonctionnalite "relire mon histoire" et a l'export biographique.

### 5.1 Compression du narrative_log

Au-dela de 1000 lignes, le module `llm/summarization.py` peut produire un resume hierarchique :

```
narrative_log.jsonl                        log brut, append-only
narrative_summary_year_001.md              resume du year 1
narrative_summary_year_002.md              resume du year 2
narrative_summary_decade_01.md             resume des 10 premieres annees
```

Les resumes sont generes en background apres une session de jeu, jamais pendant.

## 6. divergence_log.jsonl

Trace des divergences canoniques. Une ligne par divergence :

```json
{"turn": 134, "year": 8, "type": "event_cancelled", "event_id": "uchiha_clan_massacre", "reason": "Itachi tue par le joueur en l'an 7", "cascading_changes": ["sasuke_remains_in_konoha", "obito_no_longer_uses_sasuke", "akatsuki_member_replaced"]}
```

## 7. CRUD des saves

Module `src/shinobi/persistence/saves.py` :

```python
def list_saves() -> list[SaveMeta]: ...
def create_save(character: Character, world: WorldState, profile: str) -> SaveId: ...
def load_save(save_id: SaveId) -> GameSession: ...
def save_turn(save_id: SaveId, action: Action, result: ActionResult, new_state: GameState) -> None: ...
def delete_save(save_id: SaveId) -> None: ...
def export_save(save_id: SaveId, output_path: Path) -> Path: ...
def import_save(archive_path: Path) -> SaveId: ...
def duplicate_save(save_id: SaveId, new_label: str) -> SaveId: ...
```

### 7.1 Duplication

La duplication permet au joueur de creer un point de bifurcation : il duplique sa save actuelle puis continue dans une direction differente. La save originale reste intacte. C'est un usage frequent attendu pour explorer differents choix.

### 7.2 Export et import

L'export produit une archive `.shinosave` qui est en realite un tar.gz contenant le dossier de save complet. L'import deballe et valide. Cela permet le partage entre instances ou le backup.

## 8. Migrations

Quand le schema evolue (changement de modele Pydantic, ajout de table), une migration Alembic est generee :

```
alembic revision --autogenerate -m "add medical_knowledge to extended_stats"
alembic upgrade head
```

Au chargement d'une save, `schema_version` du `meta.json` est compare a la version courante du code. Si different, les migrations applicables sont executees automatiquement avec un avertissement au joueur ("ta save est en version X, mise a jour vers Y...").

Pour les migrations destructrices, le moteur fait un backup automatique de la save avant de migrer.

## 9. Robustesse

### 9.1 Sauvegarde transactionnelle

Chaque tour est sauvegarde dans une transaction SQLite atomique. Si une erreur survient pendant la sauvegarde, l'etat reste coherent.

### 9.2 Sauvegarde automatique

Toutes les actions sont sauvegardees automatiquement. Pas de notion de "sauvegarder manuellement". Pour le joueur, cela ressemble a un autosave en continu.

### 9.3 Detection de corruption

Au chargement, des verifications de coherence :

```
- Le snapshot courant existe et est valide pydantic
- Le narrative_log est readable
- Les ids de goals et breadcrumbs ne sont pas orphelins
- Le meta.json est synchrone avec l'etat
```

En cas d'incoherence, proposer au joueur de restaurer depuis le snapshot precedent.

## 10. Performance

Cibles :

```
sauvegarder un tour standard         < 50 ms
charger une partie de 500 tours       < 3 secondes
lister 50 saves dans le menu          < 200 ms
exporter une save de 1000 tours       < 5 secondes
```

## 11. Tests

Tests unitaires :

- creation de save, sauvegarde, chargement, suppression
- duplication (verifier l'isolation)
- application d'une migration sur une save fictive
- detection de corruption sur une save endommagee

Tests d'integration :

- jouer 50 tours, sauvegarder, recharger, verifier l'identite des etats
- export et import sur une autre instance, verifier l'integrite
