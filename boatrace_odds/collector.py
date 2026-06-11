"""Capture-job execution: fetch -> archive raw HTML -> parse -> validate -> persist.

Order of operations per attempt (mandatory):
1. HTTP GET (polite client)
2. save gzip raw HTML  <-- BEFORE any parsing
3. record fetch_attempt
4. parse + validate
5. insert odds_snapshot (idempotent, max 1 per job)
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import timedelta

from . import COLLECTOR_VERSION, config, db, storage
from .http_client import AccessBlockedError, FetchResult, PoliteClient
from .parser import parse_odds3t_page
from .scheduler import refresh_schedule_from_page
from .timeutil import (
    date_to_yyyymmdd,
    fmt_jst,
    hhmm_on,
    now_jst,
    parse_iso_date,
    parse_jst,
)
from .validator import ValidationError, validate_odds_page

log = logging.getLogger(__name__)


class DayAborted(Exception):
    """Raised after 403/429 — caller must stop auto collection for the day."""


def _job_expired(job: sqlite3.Row) -> bool:
    slot = job["capture_slot"]
    limit_min = config.SLOT_EXPIRY_MIN.get(slot, 5)
    scheduled = parse_jst(job["scheduled_at_jst"])
    return now_jst() > scheduled + timedelta(minutes=limit_min)


def execute_job(
    conn: sqlite3.Connection,
    job: sqlite3.Row,
    client: PoliteClient,
    retry_sleep: bool = False,
) -> str:
    """Run one capture job once (a single attempt). Returns the job status.

    Retries are driven by the scheduler loop re-selecting failed jobs
    (attempt_count < MAX_RETRIES); when ``retry_sleep`` is set the
    configured backoff is applied inline instead (used by collect-due --once).
    """
    job_id = int(job["job_id"])
    race_id = int(job["race_id"])
    slot = job["capture_slot"]
    race_date = parse_iso_date(job["race_date_jst"])
    yyyymmdd = date_to_yyyymmdd(race_date)
    venue_code = job["venue_code"]
    race_no = int(job["race_no"])
    url = config.odds3t_url(yyyymmdd, venue_code, race_no)

    if db.snapshot_exists_for_job(conn, job_id):
        db.set_job_status(conn, job_id, "done")
        conn.commit()
        log.info("job %s already has a snapshot; marked done", job_id)
        return "done"

    if _job_expired(job):
        db.set_job_status(conn, job_id, "expired", "slot window passed")
        conn.commit()
        log.warning("job %s (%s %s %sR %s) expired", job_id, yyyymmdd,
                    venue_code, race_no, slot)
        return "expired"

    db.set_job_status(conn, job_id, "running")
    db.bump_attempt_count(conn, job_id)
    conn.commit()

    attempt_no = int(
        conn.execute(
            "SELECT attempt_count FROM capture_jobs WHERE job_id=?", (job_id,)
        ).fetchone()[0]
    )
    log.info("job %s start: %s %s %sR slot=%s attempt=%d",
             job_id, yyyymmdd, venue_code, race_no, slot, attempt_no)

    # ---- 1. HTTP ------------------------------------------------------------
    try:
        fetch: FetchResult = client.fetch(url)
    except AccessBlockedError as exc:
        db.insert_fetch_attempt(
            conn, job_id, url, fmt_jst(now_jst()), None, exc.status_code,
            None, "http_error", f"blocked: HTTP {exc.status_code}", None, None,
            COLLECTOR_VERSION,
        )
        db.set_job_status(conn, job_id, "aborted", f"HTTP {exc.status_code}")
        conn.commit()
        raise DayAborted(str(exc)) from exc

    requested_str = fmt_jst(fetch.requested_at)
    fetched_str = fmt_jst(fetch.fetched_at) if fetch.fetched_at else None

    # ---- 2. archive raw HTML BEFORE parsing ----------------------------------
    raw_path = sha = None
    if fetch.body is not None:
        raw_path, sha = storage.save_raw_html(
            fetch.body, yyyymmdd, venue_code, race_no, slot,
            fetch.fetched_at or fetch.requested_at,
        )

    # ---- network / HTTP errors ------------------------------------------------
    if fetch.error is not None:
        outcome = "network_error" if fetch.http_status is None else "http_error"
        db.insert_fetch_attempt(
            conn, job_id, url, requested_str, fetched_str, fetch.http_status,
            fetch.elapsed_ms, outcome, fetch.error, raw_path, sha,
            COLLECTOR_VERSION,
        )
        return _fail(conn, job_id, attempt_no, fetch.error, retry_sleep)

    html_text = fetch.body.decode("utf-8", errors="replace")

    # ---- 3/4. parse + validate -------------------------------------------------
    try:
        page = parse_odds3t_page(html_text)
    except Exception as exc:  # noqa: BLE001 - any parser crash is parse_error
        db.insert_fetch_attempt(
            conn, job_id, url, requested_str, fetched_str, fetch.http_status,
            fetch.elapsed_ms, "parse_error", f"{type(exc).__name__}: {exc}",
            raw_path, sha, COLLECTOR_VERSION,
        )
        log.error("job %s parse crash: %s", job_id, exc)
        return _fail(conn, job_id, attempt_no, f"parse crash: {exc}", retry_sleep)

    if page.is_no_data:
        db.insert_fetch_attempt(
            conn, job_id, url, requested_str, fetched_str, fetch.http_status,
            fetch.elapsed_ms, "no_data", "official page shows no data",
            raw_path, sha, COLLECTOR_VERSION,
        )
        db.set_job_status(conn, job_id, "no_data", "no_data page")
        conn.commit()
        log.warning("job %s -> no_data", job_id)
        return "no_data"

    # Re-apply any updated deadline times seen on this page.
    try:
        refresh_schedule_from_page(
            conn, race_date, venue_code, page.deadline_times_hhmm, url
        )
    except Exception as exc:  # noqa: BLE001 - schedule refresh is best-effort
        log.warning("schedule refresh failed for job %s: %s", job_id, exc)

    try:
        validate_odds_page(page, yyyymmdd, venue_code, race_no)
    except ValidationError as exc:
        outcome = exc.code if exc.code in ("incomplete", "mismatch") else "parse_error"
        db.insert_fetch_attempt(
            conn, job_id, url, requested_str, fetched_str, fetch.http_status,
            fetch.elapsed_ms, outcome, exc.detail, raw_path, sha,
            COLLECTOR_VERSION,
        )
        log.error("job %s validation failed: %s", job_id, exc)
        return _fail(conn, job_id, attempt_no, str(exc), retry_sleep)

    # final slot must only persist 締切時オッズ; retry until the page flips.
    if slot == "final" and not page.is_final:
        db.insert_fetch_attempt(
            conn, job_id, url, requested_str, fetched_str, fetch.http_status,
            fetch.elapsed_ms, "incomplete",
            "final slot but page still selling", raw_path, sha,
            COLLECTOR_VERSION,
        )
        log.warning("job %s: final slot but page still selling", job_id)
        return _fail(conn, job_id, attempt_no, "page not yet final", retry_sleep)

    # ---- 5. persist snapshot (idempotent) -----------------------------------
    attempt_id = db.insert_fetch_attempt(
        conn, job_id, url, requested_str, fetched_str, fetch.http_status,
        fetch.elapsed_ms, "success", None, raw_path, sha, COLLECTOR_VERSION,
    )

    source_updated = None
    if page.source_updated_hhmm:
        source_updated = fmt_jst(hhmm_on(race_date, page.source_updated_hhmm))

    db.insert_snapshot(
        conn, job_id, race_id, slot, attempt_id, fetched_str,
        source_updated, page.is_final, raw_path, sha,
        [o.as_dict() for o in page.odds],
    )
    db.set_job_status(conn, job_id, "done")
    conn.commit()
    log.info("job %s done: snapshot saved (final=%s)", job_id, page.is_final)
    return "done"


def _fail(
    conn: sqlite3.Connection,
    job_id: int,
    attempt_no: int,
    error: str,
    retry_sleep: bool,
) -> str:
    if attempt_no >= config.MAX_RETRIES:
        db.set_job_status(conn, job_id, "failed", error)
        conn.commit()
        log.error("job %s permanently failed after %d attempts: %s",
                  job_id, attempt_no, error)
        return "failed"
    db.set_job_status(conn, job_id, "failed", error)  # re-selectable
    conn.commit()
    if retry_sleep:
        backoff = config.RETRY_BACKOFF_SEC[
            min(attempt_no - 1, len(config.RETRY_BACKOFF_SEC) - 1)
        ]
        log.warning("job %s attempt %d failed (%s); sleeping %ds before retry",
                    job_id, attempt_no, error, backoff)
        time.sleep(backoff)
    else:
        log.warning("job %s attempt %d failed (%s); will retry on next pass",
                    job_id, attempt_no, error)
    return "retry"


def collect_due_once(
    conn: sqlite3.Connection,
    client: PoliteClient | None = None,
) -> dict[str, int]:
    """Execute every currently-due job once (with inline retry backoff)."""
    own = client is None
    client = client or PoliteClient()
    stats: dict[str, int] = {}
    try:
        while True:
            jobs = db.due_jobs(conn, fmt_jst(now_jst()))
            if not jobs:
                break
            progressed = False
            for job in jobs:
                try:
                    status = execute_job(conn, job, client, retry_sleep=True)
                except DayAborted:
                    stats["aborted"] = stats.get("aborted", 0) + 1
                    raise
                stats[status] = stats.get(status, 0) + 1
                if status != "retry":
                    progressed = True
            if not progressed:
                # every due job is in retry state — let backoff timers run out
                # in the next loop iteration via execute_job's inline sleep.
                continue
    finally:
        if own:
            client.close()
    return stats
