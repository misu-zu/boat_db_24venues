"""summary-day: aggregate one race day's collection status."""
from __future__ import annotations

from collections import Counter, defaultdict
import sqlite3
from datetime import date

from .timeutil import iso_date, parse_jst

SLOT_ORDER = ("m20", "m12", "m08", "m05", "m02", "final")
STATUS_ORDER = (
    "pending", "running", "done", "no_data", "failed", "skipped",
    "expired", "aborted",
)
OUTCOME_ORDER = (
    "success", "no_data", "http_error", "network_error", "parse_error",
    "incomplete", "mismatch",
)
DISCOVERY_STATUS_ORDER = ("held", "no_meeting", "discovery_failed")


def _counter_text(counter: Counter, keys: tuple[str, ...]) -> str:
    parts = [f"{key}={counter[key]}" for key in keys if counter[key]]
    return ", ".join(parts) if parts else "-"


def _completion(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "-"
    return f"{numerator}/{denominator} ({numerator / denominator:.1%})"


def summary_day(conn: sqlite3.Connection, race_date: date) -> dict:
    """Return aggregate collection status for one day."""
    rows = conn.execute(
        """
        SELECT
            r.race_id, r.venue_code, r.venue_name, r.race_no,
            j.job_id, j.capture_slot, j.status, j.attempt_count,
            j.scheduled_at_jst, j.last_error,
            s.snapshot_id, s.fetched_at_jst,
            (SELECT COUNT(*) FROM trifecta_odds t
              WHERE t.snapshot_id = s.snapshot_id) AS odds_count,
            (SELECT COUNT(*) FROM fetch_attempts f
              WHERE f.job_id = j.job_id) AS fetch_attempt_count
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

    outcome_rows = conn.execute(
        """
        SELECT f.outcome, COUNT(*) AS n
        FROM fetch_attempts f
        JOIN capture_jobs j ON j.job_id = f.job_id
        JOIN races r ON r.race_id = j.race_id
        WHERE r.race_date_jst = ?
        GROUP BY f.outcome
        """,
        (iso_date(race_date),),
    ).fetchall()

    discovery_rows = conn.execute(
        """
        SELECT *
        FROM venue_day_status
        WHERE race_date_jst = ?
        ORDER BY venue_code
        """,
        (iso_date(race_date),),
    ).fetchall()

    races = {(r["venue_code"], r["race_no"]) for r in rows}
    status_counts = Counter(r["status"] for r in rows)
    outcome_counts = Counter({r["outcome"]: r["n"] for r in outcome_rows})
    discovery_counts = Counter(r["status"] for r in discovery_rows)
    total_attempts = sum(int(r["fetch_attempt_count"] or 0) for r in rows)
    total_odds_rows = sum(int(r["odds_count"] or 0) for r in rows)
    snapshots = [r for r in rows if r["snapshot_id"] is not None]
    complete_snapshots = [r for r in snapshots if int(r["odds_count"] or 0) == 120]

    venues: dict[tuple[str, str], dict] = {}
    venue_races: dict[tuple[str, str], set[int]] = defaultdict(set)
    slots: dict[str, dict] = {}
    incomplete: list[dict] = []

    for row in rows:
        venue_key = (row["venue_code"], row["venue_name"])
        venue_races[venue_key].add(int(row["race_no"]))
        venue = venues.setdefault(
            venue_key,
            {
                "venue_code": row["venue_code"],
                "venue_name": row["venue_name"],
                "jobs": 0,
                "snapshots": 0,
                "complete_snapshots": 0,
                "odds_rows": 0,
                "attempts": 0,
                "statuses": Counter(),
            },
        )
        slot = slots.setdefault(
            row["capture_slot"],
            {
                "slot": row["capture_slot"],
                "jobs": 0,
                "snapshots": 0,
                "complete_snapshots": 0,
                "odds_rows": 0,
                "attempts": 0,
                "statuses": Counter(),
                "delays": [],
            },
        )

        odds_count = int(row["odds_count"] or 0)
        attempts = int(row["fetch_attempt_count"] or 0)
        has_snapshot = row["snapshot_id"] is not None
        is_complete = odds_count == 120

        for target in (venue, slot):
            target["jobs"] += 1
            target["snapshots"] += 1 if has_snapshot else 0
            target["complete_snapshots"] += 1 if is_complete else 0
            target["odds_rows"] += odds_count
            target["attempts"] += attempts
            target["statuses"][row["status"]] += 1

        if has_snapshot and row["fetched_at_jst"] and row["scheduled_at_jst"]:
            delay = int(
                (parse_jst(row["fetched_at_jst"])
                 - parse_jst(row["scheduled_at_jst"])).total_seconds()
            )
            slot["delays"].append(delay)

        if row["status"] != "done" or not is_complete:
            incomplete.append({
                "venue_code": row["venue_code"],
                "venue_name": row["venue_name"],
                "race_no": row["race_no"],
                "slot": row["capture_slot"],
                "status": row["status"],
                "attempt_count": row["attempt_count"],
                "last_error": row["last_error"],
                "has_snapshot": has_snapshot,
                "odds_count": odds_count,
            })

    for key, race_numbers in venue_races.items():
        venues[key]["races"] = len(race_numbers)

    venue_rows = [venues[key] for key in sorted(venues)]
    slot_rows = [slots[key] for key in SLOT_ORDER if key in slots]
    for slot in slot_rows:
        delays = slot.pop("delays")
        slot["avg_delay_sec"] = (
            round(sum(delays) / len(delays), 1) if delays else None
        )

    return {
        "date": iso_date(race_date),
        "races": len(races),
        "jobs": len(rows),
        "snapshots": len(snapshots),
        "complete_snapshots": len(complete_snapshots),
        "odds_rows": total_odds_rows,
        "attempts": total_attempts,
        "status_counts": status_counts,
        "outcome_counts": outcome_counts,
        "discovery_counts": discovery_counts,
        "discovery": [dict(r) for r in discovery_rows],
        "venues": venue_rows,
        "slots": slot_rows,
        "incomplete": incomplete,
    }


def format_summary(summary: dict, max_incomplete: int = 40) -> str:
    """Format a summary generated by summary_day."""
    lines = [f"{summary['date']} collection summary"]
    lines.append(
        "total: "
        f"races={summary['races']} jobs={summary['jobs']} "
        f"snapshots={summary['snapshots']} "
        f"complete={summary['complete_snapshots']} "
        f"odds_rows={summary['odds_rows']} attempts={summary['attempts']}"
    )
    lines.append(
        "job_status: "
        f"{_counter_text(summary['status_counts'], STATUS_ORDER)}"
    )
    lines.append(
        "fetch_outcome: "
        f"{_counter_text(summary['outcome_counts'], OUTCOME_ORDER)}"
    )
    lines.append(
        "discovery_status: "
        f"{_counter_text(summary['discovery_counts'], DISCOVERY_STATUS_ORDER)}"
    )

    if summary["discovery"]:
        lines.append("")
        lines.append(
            f"{'venue':<10}{'discovery':<18}{'races':>6} detail"
        )
        lines.append("-" * 78)
        for row in summary["discovery"]:
            detail = row["error_detail"] or ""
            lines.append(
                f"{row['venue_code']} {row['venue_name']:<7}"
                f"{row['status']:<18}{row['race_count']:>6} "
                f"{detail[:50]}"
            )

    if not summary["jobs"]:
        return "\n".join(lines)

    lines.append("")
    lines.append(
        f"{'venue':<10}{'races':>6}{'jobs':>6}{'snap':>6} "
        f"{'complete':>13}{'odds':>8}{'tries':>7} status"
    )
    lines.append("-" * 78)
    for venue in summary["venues"]:
        lines.append(
            f"{venue['venue_code']} {venue['venue_name']:<7}"
            f"{venue['races']:>6}{venue['jobs']:>6}"
            f"{venue['snapshots']:>6} "
            f"{_completion(venue['complete_snapshots'], venue['jobs']):>13}"
            f"{venue['odds_rows']:>8}{venue['attempts']:>7} "
            f"{_counter_text(venue['statuses'], STATUS_ORDER)}"
        )

    lines.append("")
    lines.append(
        f"{'slot':<6}{'jobs':>6}{'snap':>6} {'complete':>13}"
        f"{'odds':>8}{'tries':>7}{'avg_delay_s':>12} status"
    )
    lines.append("-" * 78)
    for slot in summary["slots"]:
        avg_delay = (
            f"{slot['avg_delay_sec']:.1f}"
            if slot["avg_delay_sec"] is not None else "-"
        )
        lines.append(
            f"{slot['slot']:<6}{slot['jobs']:>6}{slot['snapshots']:>6} "
            f"{_completion(slot['complete_snapshots'], slot['jobs']):>13}"
            f"{slot['odds_rows']:>8}{slot['attempts']:>7}"
            f"{avg_delay:>12} "
            f"{_counter_text(slot['statuses'], STATUS_ORDER)}"
        )

    if summary["incomplete"]:
        lines.append("")
        lines.append(f"incomplete_or_attention (first {max_incomplete})")
        lines.append(
            f"{'venue':<10}{'R':>3} {'slot':<6}{'status':<9}"
            f"{'try':>3} {'snap':<5}{'odds':>5} last_error"
        )
        lines.append("-" * 78)
        for row in summary["incomplete"][:max_incomplete]:
            lines.append(
                f"{row['venue_code']} {row['venue_name']:<7}"
                f"{row['race_no']:>3} {row['slot']:<6}{row['status']:<9}"
                f"{row['attempt_count']:>3} "
                f"{'yes' if row['has_snapshot'] else 'no':<5}"
                f"{row['odds_count']:>5} {(row['last_error'] or '')[:50]}"
            )
    return "\n".join(lines)
