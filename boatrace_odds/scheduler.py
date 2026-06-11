"""Discovery of daily race schedules and capture-job planning.

Discovery fetches each venue's 1R odds page once (24 requests per day,
3 s apart) to read the 1R-12R scheduled deadline times, then plans the
m20/m12/m08/m05/m02/final capture jobs.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta

from . import config, db
from .http_client import PoliteClient
from .parser import parse_odds3t_page
from .timeutil import date_to_yyyymmdd, fmt_jst, hhmm_on, iso_date, now_jst

log = logging.getLogger(__name__)


def plan_jobs_for_race(
    conn: sqlite3.Connection,
    race_id: int,
    deadline_at_jst_str: str,
) -> None:
    """Create/update the 6 capture jobs for one race from its deadline."""
    from .timeutil import parse_jst

    deadline = parse_jst(deadline_at_jst_str)
    for slot, minutes in config.CAPTURE_SLOTS.items():
        if minutes is None:  # final: fires after the deadline
            scheduled = deadline + timedelta(minutes=config.FINAL_SLOT_DELAY_MIN)
        else:
            scheduled = deadline - timedelta(minutes=minutes)
        db.upsert_job(conn, race_id, slot, fmt_jst(scheduled))


def register_day_schedule(
    conn: sqlite3.Connection,
    race_date: date,
    venue_code: str,
    deadlines_hhmm: dict[int, str],
    source_url: str,
) -> int:
    """Upsert races + schedule observations + capture jobs for one venue/day.

    Returns the number of races registered/updated.
    """
    venue_name = config.VENUES.get(venue_code, venue_code)
    count = 0
    for race_no, hhmm in sorted(deadlines_hhmm.items()):
        deadline = hhmm_on(race_date, hhmm)
        deadline_str = fmt_jst(deadline)
        race_id = db.upsert_race(
            conn, iso_date(race_date), venue_code, venue_name, race_no,
            deadline_str,
        )
        db.record_schedule_observation(conn, race_id, deadline_str, source_url)
        plan_jobs_for_race(conn, race_id, deadline_str)
        count += 1
    conn.commit()
    db.upsert_venue_day_status(
        conn, iso_date(race_date), venue_code, venue_name, "held", count,
        source_url,
    )
    conn.commit()
    return count


def discover_day(
    conn: sqlite3.Connection,
    race_date: date,
    client: PoliteClient | None = None,
    venues: dict[str, str] | None = None,
) -> dict[str, int]:
    """Fetch each venue's 1R page and register that day's schedule.

    Returns {venue_code: number_of_races} (0 = no_data / not held / failed).
    Every venue is also recorded in venue_day_status so non-held venues are
    explicit in the database.
    """
    own_client = client is None
    client = client or PoliteClient()
    venues = venues or config.target_venues()
    yyyymmdd = date_to_yyyymmdd(race_date)
    result: dict[str, int] = {}
    try:
        for venue_code, venue_name in venues.items():
            url = config.odds3t_url(yyyymmdd, venue_code, 1)
            log.info("discover %s %s -> %s", race_date, venue_code, url)
            fetch = client.fetch(url)
            if fetch.error or fetch.body is None:
                log.warning("discover failed for %s: %s", venue_code, fetch.error)
                db.upsert_venue_day_status(
                    conn, iso_date(race_date), venue_code, venue_name,
                    "discovery_failed", 0, url, fetch.error or "empty body",
                    fmt_jst(fetch.fetched_at or fetch.requested_at or now_jst()),
                )
                conn.commit()
                result[venue_code] = 0
                continue
            discovered_at = fmt_jst(fetch.fetched_at or fetch.requested_at)
            try:
                page = parse_odds3t_page(fetch.body.decode("utf-8", errors="replace"))
            except Exception as exc:  # noqa: BLE001 - discovery must continue
                log.warning("discover parse failed for %s: %s", venue_code, exc)
                db.upsert_venue_day_status(
                    conn, iso_date(race_date), venue_code, venue_name,
                    "discovery_failed", 0, url,
                    f"{type(exc).__name__}: {exc}", discovered_at,
                )
                conn.commit()
                result[venue_code] = 0
                continue
            if page.is_no_data:
                log.info("no meeting at %s on %s", venue_code, race_date)
                db.upsert_venue_day_status(
                    conn, iso_date(race_date), venue_code, venue_name,
                    "no_meeting", 0, url, None, discovered_at,
                )
                conn.commit()
                result[venue_code] = 0
                continue
            if not page.deadline_times_hhmm:
                log.warning("no deadline table for %s on %s", venue_code, race_date)
                db.upsert_venue_day_status(
                    conn, iso_date(race_date), venue_code, venue_name,
                    "discovery_failed", 0, url,
                    "no deadline table", discovered_at,
                )
                conn.commit()
                result[venue_code] = 0
                continue
            n = register_day_schedule(
                conn, race_date, venue_code, page.deadline_times_hhmm, url
            )
            db.upsert_venue_day_status(
                conn, iso_date(race_date), venue_code, venue_name, "held", n,
                url, None, discovered_at,
            )
            conn.commit()
            log.info("registered %d races for %s on %s", n, venue_code, race_date)
            result[venue_code] = n
    finally:
        if own_client:
            client.close()
    return result


def refresh_schedule_from_page(
    conn: sqlite3.Connection,
    race_date: date,
    venue_code: str,
    deadlines_hhmm: dict[int, str],
    source_url: str,
) -> None:
    """Re-apply deadline times observed on any fetched page.

    If a scheduled deadline moved, pending jobs are recomputed
    (db.upsert_job only reschedules jobs still in 'pending').
    """
    if deadlines_hhmm:
        register_day_schedule(conn, race_date, venue_code, deadlines_hhmm, source_url)
