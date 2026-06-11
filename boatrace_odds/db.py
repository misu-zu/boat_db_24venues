"""SQLite access layer. SQLite is the single source of truth."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from . import config
from .timeutil import fmt_jst, now_jst

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_file: Path | None = None) -> sqlite3.Connection:
    path = db_file or config.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


# ---------------------------------------------------------------------------
# venue_day_status
# ---------------------------------------------------------------------------

def upsert_venue_day_status(
    conn: sqlite3.Connection,
    race_date_jst: str,
    venue_code: str,
    venue_name: str,
    status: str,
    race_count: int,
    source_url: str,
    error_detail: str | None = None,
    discovered_at_jst: str | None = None,
) -> None:
    """Record the daily discovery outcome for a venue.

    ``status`` is one of: held, no_meeting, discovery_failed.
    """
    now = fmt_jst(now_jst())
    discovered = discovered_at_jst or now
    conn.execute(
        "INSERT INTO venue_day_status"
        " (race_date_jst, venue_code, venue_name, status, race_count,"
        " discovered_at_jst, source_url, error_detail, created_at_jst,"
        " updated_at_jst)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(race_date_jst, venue_code) DO UPDATE SET"
        " venue_name=excluded.venue_name,"
        " status=excluded.status,"
        " race_count=excluded.race_count,"
        " discovered_at_jst=excluded.discovered_at_jst,"
        " source_url=excluded.source_url,"
        " error_detail=excluded.error_detail,"
        " updated_at_jst=excluded.updated_at_jst",
        (
            race_date_jst, venue_code, venue_name, status, race_count,
            discovered, source_url, error_detail, now, now,
        ),
    )


def venue_day_statuses(
    conn: sqlite3.Connection,
    race_date_jst: str,
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM venue_day_status WHERE race_date_jst=?"
        " ORDER BY venue_code",
        (race_date_jst,),
    ).fetchall()


# ---------------------------------------------------------------------------
# races
# ---------------------------------------------------------------------------

def upsert_race(
    conn: sqlite3.Connection,
    race_date_jst: str,
    venue_code: str,
    venue_name: str,
    race_no: int,
    deadline_at_jst: str | None,
) -> int:
    """Insert or update a race; returns race_id."""
    now = fmt_jst(now_jst())
    row = conn.execute(
        "SELECT race_id, deadline_at_jst FROM races "
        "WHERE race_date_jst=? AND venue_code=? AND race_no=?",
        (race_date_jst, venue_code, race_no),
    ).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO races (race_date_jst, venue_code, venue_name, race_no,"
            " deadline_at_jst, created_at_jst, updated_at_jst)"
            " VALUES (?,?,?,?,?,?,?)",
            (race_date_jst, venue_code, venue_name, race_no,
             deadline_at_jst, now, now),
        )
        return int(cur.lastrowid)
    race_id = int(row["race_id"])
    if deadline_at_jst and deadline_at_jst != row["deadline_at_jst"]:
        conn.execute(
            "UPDATE races SET deadline_at_jst=?, updated_at_jst=? WHERE race_id=?",
            (deadline_at_jst, now, race_id),
        )
    return race_id


def record_schedule_observation(
    conn: sqlite3.Connection,
    race_id: int,
    deadline_at_jst: str,
    source_url: str,
    observed_at: datetime | None = None,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO race_schedule_observations"
        " (race_id, observed_at_jst, deadline_at_jst, source_url)"
        " VALUES (?,?,?,?)",
        (race_id, fmt_jst(observed_at or now_jst()), deadline_at_jst, source_url),
    )


# ---------------------------------------------------------------------------
# capture_jobs
# ---------------------------------------------------------------------------

def upsert_job(
    conn: sqlite3.Connection,
    race_id: int,
    capture_slot: str,
    scheduled_at_jst: str,
) -> int:
    """Create a job if absent; reschedule pending jobs if the deadline moved."""
    now = fmt_jst(now_jst())
    row = conn.execute(
        "SELECT job_id, status, scheduled_at_jst FROM capture_jobs"
        " WHERE race_id=? AND capture_slot=?",
        (race_id, capture_slot),
    ).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO capture_jobs (race_id, capture_slot, scheduled_at_jst,"
            " status, created_at_jst, updated_at_jst)"
            " VALUES (?,?,?,?,?,?)",
            (race_id, capture_slot, scheduled_at_jst, "pending", now, now),
        )
        return int(cur.lastrowid)
    job_id = int(row["job_id"])
    if row["status"] == "pending" and row["scheduled_at_jst"] != scheduled_at_jst:
        conn.execute(
            "UPDATE capture_jobs SET scheduled_at_jst=?, updated_at_jst=?"
            " WHERE job_id=?",
            (scheduled_at_jst, now, job_id),
        )
    return job_id


def set_job_status(
    conn: sqlite3.Connection,
    job_id: int,
    status: str,
    last_error: str | None = None,
) -> None:
    conn.execute(
        "UPDATE capture_jobs SET status=?, last_error=?, updated_at_jst=?"
        " WHERE job_id=?",
        (status, last_error, fmt_jst(now_jst()), job_id),
    )


def bump_attempt_count(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute(
        "UPDATE capture_jobs SET attempt_count = attempt_count + 1,"
        " updated_at_jst=? WHERE job_id=?",
        (fmt_jst(now_jst()), job_id),
    )


def due_jobs(conn: sqlite3.Connection, now_iso: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT j.*, r.race_date_jst, r.venue_code, r.venue_name, r.race_no,"
        " r.deadline_at_jst"
        " FROM capture_jobs j JOIN races r ON r.race_id = j.race_id"
        " WHERE j.status IN ('pending','failed')"
        "   AND j.attempt_count < ?"
        "   AND j.scheduled_at_jst <= ?"
        " ORDER BY j.scheduled_at_jst",
        (config.MAX_RETRIES, now_iso),
    ).fetchall()


# ---------------------------------------------------------------------------
# fetch_attempts
# ---------------------------------------------------------------------------

def insert_fetch_attempt(
    conn: sqlite3.Connection,
    job_id: int,
    url: str,
    requested_at_jst: str,
    fetched_at_jst: str | None,
    http_status: int | None,
    elapsed_ms: int | None,
    outcome: str,
    error_detail: str | None,
    raw_html_path: str | None,
    response_sha256: str | None,
    collector_version: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO fetch_attempts (job_id, requested_at_jst, fetched_at_jst,"
        " url, http_status, elapsed_ms, outcome, error_detail, raw_html_path,"
        " response_sha256, collector_version)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (job_id, requested_at_jst, fetched_at_jst, url, http_status,
         elapsed_ms, outcome, error_detail, raw_html_path, response_sha256,
         collector_version),
    )
    return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# odds_snapshots / trifecta_odds
# ---------------------------------------------------------------------------

def snapshot_exists_for_job(conn: sqlite3.Connection, job_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM odds_snapshots WHERE job_id=?", (job_id,)
    ).fetchone()
    return row is not None


def insert_snapshot(
    conn: sqlite3.Connection,
    job_id: int,
    race_id: int,
    capture_slot: str,
    attempt_id: int,
    fetched_at_jst: str,
    source_updated_at_jst: str | None,
    is_final: bool,
    raw_html_path: str,
    response_sha256: str,
    odds_rows: list[dict],
) -> int:
    """Insert a snapshot + its 120 odds rows atomically.

    Idempotent: if a snapshot already exists for the job, nothing is
    inserted and the existing snapshot_id is returned.
    """
    existing = conn.execute(
        "SELECT snapshot_id FROM odds_snapshots WHERE job_id=?", (job_id,)
    ).fetchone()
    if existing is not None:
        return int(existing["snapshot_id"])

    cur = conn.execute(
        "INSERT INTO odds_snapshots (job_id, race_id, capture_slot, attempt_id,"
        " fetched_at_jst, source_updated_at_jst, is_final, raw_html_path,"
        " response_sha256, created_at_jst)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (job_id, race_id, capture_slot, attempt_id, fetched_at_jst,
         source_updated_at_jst, 1 if is_final else 0, raw_html_path,
         response_sha256, fmt_jst(now_jst())),
    )
    snapshot_id = int(cur.lastrowid)
    conn.executemany(
        "INSERT INTO trifecta_odds (snapshot_id, combination_code, first_boat,"
        " second_boat, third_boat, odds_text, odds_tenths)"
        " VALUES (?,?,?,?,?,?,?)",
        [
            (snapshot_id, r["combination_code"], r["first_boat"],
             r["second_boat"], r["third_boat"], r["odds_text"],
             r["odds_tenths"])
            for r in odds_rows
        ],
    )
    return snapshot_id
