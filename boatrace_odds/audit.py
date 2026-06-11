"""audit-day: per-slot completeness report for one day."""
from __future__ import annotations

import sqlite3
from datetime import date

from .timeutil import iso_date, parse_jst


def audit_day(conn: sqlite3.Connection, race_date: date) -> list[dict]:
    """Return one report row per (venue, race, slot)."""
    rows = conn.execute(
        """
        SELECT
            r.venue_code, r.venue_name, r.race_no,
            j.job_id, j.capture_slot, j.status, j.attempt_count,
            j.last_error, j.scheduled_at_jst,
            s.snapshot_id, s.fetched_at_jst,
            (SELECT COUNT(*) FROM trifecta_odds t
              WHERE t.snapshot_id = s.snapshot_id) AS odds_count
        FROM races r
        JOIN capture_jobs j ON j.race_id = r.race_id
        LEFT JOIN odds_snapshots s ON s.job_id = j.job_id
        WHERE r.race_date_jst = ?
        ORDER BY r.venue_code, r.race_no,
            CASE j.capture_slot
                WHEN 'm20' THEN 0 WHEN 'm12' THEN 1 WHEN 'm08' THEN 2
                WHEN 'm05' THEN 3 WHEN 'm02' THEN 4 ELSE 5 END
        """,
        (iso_date(race_date),),
    ).fetchall()

    report: list[dict] = []
    for row in rows:
        delay_sec = None
        if row["fetched_at_jst"] and row["scheduled_at_jst"]:
            delay_sec = int(
                (parse_jst(row["fetched_at_jst"])
                 - parse_jst(row["scheduled_at_jst"])).total_seconds()
            )
        report.append({
            "venue_code": row["venue_code"],
            "venue_name": row["venue_name"],
            "race_no": row["race_no"],
            "slot": row["capture_slot"],
            "job_status": row["status"],
            "has_snapshot": row["snapshot_id"] is not None,
            "attempt_count": row["attempt_count"],
            "last_error": row["last_error"],
            "delay_sec": delay_sec,
            "odds_complete": (row["odds_count"] or 0) == 120,
        })
    return report


def format_report(report: list[dict]) -> str:
    if not report:
        return "(no jobs for this date)"
    header = (
        f"{'venue':<10}{'R':>3} {'slot':<6}{'status':<9}{'snap':<5}"
        f"{'try':>3} {'delay_s':>8} {'120ok':<6}last_error"
    )
    lines = [header, "-" * len(header)]
    for r in report:
        lines.append(
            f"{r['venue_code']} {r['venue_name']:<7}{r['race_no']:>3} "
            f"{r['slot']:<6}{r['job_status']:<9}"
            f"{'yes' if r['has_snapshot'] else 'no':<5}"
            f"{r['attempt_count']:>3} "
            f"{r['delay_sec'] if r['delay_sec'] is not None else '-':>8} "
            f"{'yes' if r['odds_complete'] else 'NO':<6}"
            f"{(r['last_error'] or '')[:60]}"
        )
    return "\n".join(lines)
