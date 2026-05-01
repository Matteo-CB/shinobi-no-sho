-- Schema SQLite pour une sauvegarde de partie Shinobi no Sho.
-- Une base par save (data/saves/<save_id>/state.sqlite).

CREATE TABLE IF NOT EXISTS character (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payload TEXT NOT NULL,
    snapshot_at_year INTEGER NOT NULL,
    snapshot_at_turn INTEGER NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_character_current ON character(is_current);

CREATE TABLE IF NOT EXISTS world (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payload TEXT NOT NULL,
    snapshot_at_year INTEGER NOT NULL,
    snapshot_at_turn INTEGER NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_world_current ON world(is_current);

CREATE TABLE IF NOT EXISTS turns (
    turn_number INTEGER PRIMARY KEY,
    year INTEGER NOT NULL,
    date TEXT NOT NULL,
    hour INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    action_payload TEXT NOT NULL,
    action_result TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL,
    seed_state TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_year ON turns(year);

CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    declared_at_year INTEGER NOT NULL,
    completed_at_year INTEGER,
    abandoned_at_year INTEGER
);
CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);

CREATE TABLE IF NOT EXISTS breadcrumbs (
    id TEXT PRIMARY KEY,
    parent_goal_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    revealed INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    sequence_index INTEGER NOT NULL,
    FOREIGN KEY (parent_goal_id) REFERENCES goals(id)
);
CREATE INDEX IF NOT EXISTS idx_breadcrumbs_active ON breadcrumbs(revealed, completed);

CREATE TABLE IF NOT EXISTS relationships (
    character_id TEXT NOT NULL,
    with_character_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    last_updated_year INTEGER NOT NULL,
    PRIMARY KEY (character_id, with_character_id)
);

CREATE TABLE IF NOT EXISTS npc_states (
    character_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    attention_level TEXT NOT NULL,
    last_updated_year INTEGER NOT NULL,
    last_updated_turn INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_npc_attention ON npc_states(attention_level);

CREATE TABLE IF NOT EXISTS scheduled_events (
    event_id TEXT PRIMARY KEY,
    year INTEGER NOT NULL,
    date TEXT,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    triggered_at_turn INTEGER,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_status_year ON scheduled_events(status, year);

CREATE TABLE IF NOT EXISTS rumors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_event_id TEXT,
    content TEXT NOT NULL,
    fidelity REAL NOT NULL,
    diffusion_radius TEXT NOT NULL,
    born_at_year INTEGER NOT NULL,
    expires_at_year INTEGER,
    received_by_player INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_rumors_active ON rumors(expires_at_year);

CREATE TABLE IF NOT EXISTS knowledge (
    subject_id TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    knowledge_level TEXT NOT NULL,
    acquired_at_year INTEGER NOT NULL,
    notes TEXT,
    PRIMARY KEY (subject_id, subject_type)
);

CREATE TABLE IF NOT EXISTS techniques_known (
    technique_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    learned_at_year INTEGER NOT NULL,
    mastery_level REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS techniques_in_progress (
    technique_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    started_at_year INTEGER NOT NULL,
    progress_hours INTEGER NOT NULL,
    progress_required INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS save_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
