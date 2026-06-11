-- BOAT RACE trifecta odds collector - SQLite DDL
-- SQLite is the source of truth. CSV/Parquet are derived artifacts.
-- All *_jst columns store ISO-8601 strings with +09:00 offset
-- (e.g. 2026-06-10T19:30:00+0900).

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- One row per date x venue discovery result. This records both held and
-- non-held venues so daily operation can prove every venue was considered.
CREATE TABLE IF NOT EXISTS venue_day_status (
    race_date_jst      TEXT NOT NULL,              -- 'YYYY-MM-DD'
    venue_code         TEXT NOT NULL,              -- '01'..'24', leading zero kept
    venue_name         TEXT NOT NULL,
    status             TEXT NOT NULL CHECK (status IN
                          ('held','no_meeting','discovery_failed')),
    race_count         INTEGER NOT NULL DEFAULT 0,
    discovered_at_jst  TEXT NOT NULL,
    source_url         TEXT NOT NULL,
    error_detail       TEXT,
    created_at_jst     TEXT NOT NULL,
    updated_at_jst     TEXT NOT NULL,
    PRIMARY KEY (race_date_jst, venue_code)
);

-- 1 row = 1 race (date x venue x race number)
CREATE TABLE IF NOT EXISTS races (
    race_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    race_date_jst      TEXT NOT NULL,              -- 'YYYY-MM-DD'
    venue_code         TEXT NOT NULL,              -- '01'..'24', leading zero kept
    venue_name         TEXT NOT NULL,
    race_no            INTEGER NOT NULL CHECK (race_no BETWEEN 1 AND 12),
    deadline_at_jst    TEXT,                       -- latest known scheduled deadline
    created_at_jst     TEXT NOT NULL,
    updated_at_jst     TEXT NOT NULL,
    UNIQUE (race_date_jst, venue_code, race_no)
);

-- Observation history of scheduled deadline times (they can shift).
CREATE TABLE IF NOT EXISTS race_schedule_observations (
    observation_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id            INTEGER NOT NULL REFERENCES races(race_id),
    observed_at_jst    TEXT NOT NULL,
    deadline_at_jst    TEXT NOT NULL,
    source_url         TEXT NOT NULL,
    UNIQUE (race_id, observed_at_jst, deadline_at_jst)
);

-- One planned capture per race x slot.
CREATE TABLE IF NOT EXISTS capture_jobs (
    job_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id            INTEGER NOT NULL REFERENCES races(race_id),
    capture_slot       TEXT NOT NULL CHECK (capture_slot IN
                          ('m20','m12','m08','m05','m02','final')),
    scheduled_at_jst   TEXT NOT NULL,              -- when this slot should fire
    status             TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
                          ('pending','running','done','no_data','failed',
                           'skipped','expired','aborted')),
    attempt_count      INTEGER NOT NULL DEFAULT 0,
    last_error         TEXT,
    created_at_jst     TEXT NOT NULL,
    updated_at_jst     TEXT NOT NULL,
    UNIQUE (race_id, capture_slot)
);

-- Every HTTP attempt, success or not. Never deleted.
CREATE TABLE IF NOT EXISTS fetch_attempts (
    attempt_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id             INTEGER NOT NULL REFERENCES capture_jobs(job_id),
    requested_at_jst   TEXT NOT NULL,
    fetched_at_jst     TEXT,
    url                TEXT NOT NULL,
    http_status        INTEGER,
    elapsed_ms         INTEGER,
    outcome            TEXT NOT NULL CHECK (outcome IN
                          ('success','no_data','http_error','network_error',
                           'parse_error','incomplete','mismatch')),
    error_detail       TEXT,
    raw_html_path      TEXT,
    response_sha256    TEXT,
    collector_version  TEXT NOT NULL
);

-- A validated, complete set of 120 trifecta odds.
CREATE TABLE IF NOT EXISTS odds_snapshots (
    snapshot_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id                 INTEGER NOT NULL UNIQUE REFERENCES capture_jobs(job_id),
    race_id                INTEGER NOT NULL REFERENCES races(race_id),
    capture_slot           TEXT NOT NULL,
    attempt_id             INTEGER NOT NULL REFERENCES fetch_attempts(attempt_id),
    fetched_at_jst         TEXT NOT NULL,
    source_updated_at_jst  TEXT,                   -- オッズ更新時間 from the page
    is_final               INTEGER NOT NULL DEFAULT 0,  -- 1 = 締切時オッズ
    raw_html_path          TEXT NOT NULL,
    response_sha256        TEXT NOT NULL,
    created_at_jst         TEXT NOT NULL
);

-- 120 rows per snapshot, long format. Wide format is forbidden.
CREATE TABLE IF NOT EXISTS trifecta_odds (
    snapshot_id        INTEGER NOT NULL REFERENCES odds_snapshots(snapshot_id),
    combination_code   INTEGER NOT NULL,           -- e.g. 123 for 1-2-3
    first_boat         INTEGER NOT NULL CHECK (first_boat BETWEEN 1 AND 6),
    second_boat        INTEGER NOT NULL CHECK (second_boat BETWEEN 1 AND 6),
    third_boat         INTEGER NOT NULL CHECK (third_boat BETWEEN 1 AND 6),
    odds_text          TEXT NOT NULL,              -- displayed string, e.g. '6.8'
    odds_tenths        INTEGER,                    -- display value * 10; NULL = explicit missing
    PRIMARY KEY (snapshot_id, combination_code),
    CHECK (first_boat <> second_boat
       AND second_boat <> third_boat
       AND first_boat <> third_boat)
);

CREATE INDEX IF NOT EXISTS idx_races_date
    ON races (race_date_jst, venue_code);
CREATE INDEX IF NOT EXISTS idx_venue_day_status_date
    ON venue_day_status (race_date_jst, status);
CREATE INDEX IF NOT EXISTS idx_jobs_status_sched
    ON capture_jobs (status, scheduled_at_jst);
CREATE INDEX IF NOT EXISTS idx_attempts_job
    ON fetch_attempts (job_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_race
    ON odds_snapshots (race_id, capture_slot);
CREATE INDEX IF NOT EXISTS idx_trifecta_snapshot
    ON trifecta_odds (snapshot_id);
