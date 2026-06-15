"""End-to-end collector tests with a stub HTTP client (no network)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from boatrace_odds import config, db
from boatrace_odds.collector import collect_due_once, execute_job
from boatrace_odds.http_client import FetchResult
from boatrace_odds.scheduler import register_day_schedule
from boatrace_odds.timeutil import fmt_jst, now_jst

RACE_DATE = date(2026, 6, 10)


class StubClient:
    """Returns canned HTML bodies; records every requested URL."""

    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.calls: list[str] = []

    def fetch(self, url: str) -> FetchResult:
        self.calls.append(url)
        body = self.bodies.pop(0) if self.bodies else self.bodies_default
        now = now_jst()
        if isinstance(body, Exception):
            raise body
        if body is None:
            return FetchResult(url=url, requested_at=now, fetched_at=None,
                               http_status=None, elapsed_ms=10, body=None,
                               error="network_error: ConnectionError: boom")
        return FetchResult(url=url, requested_at=now, fetched_at=now,
                           http_status=200, elapsed_ms=10,
                           body=body.encode("utf-8"), error=None)


def _setup_race(conn, venue_code="24", race_no=6, slot="m08",
                deadline_offset_min=8):
    """Register one race whose chosen slot is due right now."""
    deadline = now_jst() + timedelta(minutes=deadline_offset_min)
    hhmm = deadline.strftime("%H:%M")
    register_day_schedule(conn, RACE_DATE, venue_code, {race_no: hhmm},
                          "http://test")
    job = conn.execute(
        "SELECT j.*, r.race_date_jst, r.venue_code, r.venue_name, r.race_no,"
        " r.deadline_at_jst FROM capture_jobs j"
        " JOIN races r ON r.race_id=j.race_id"
        " WHERE r.venue_code=? AND r.race_no=? AND j.capture_slot=?",
        (venue_code, race_no, slot),
    ).fetchone()
    return job


def _refetch(conn, job_id):
    return conn.execute(
        "SELECT j.*, r.race_date_jst, r.venue_code, r.venue_name, r.race_no,"
        " r.deadline_at_jst FROM capture_jobs j"
        " JOIN races r ON r.race_id=j.race_id WHERE j.job_id=?",
        (job_id,),
    ).fetchone()


def test_successful_capture_saves_120_rows(conn, selling_html):
    job = _setup_race(conn)
    client = StubClient([selling_html])
    assert execute_job(conn, job, client) == "done"

    snap = conn.execute("SELECT * FROM odds_snapshots").fetchone()
    assert snap is not None
    assert snap["is_final"] == 0
    n = conn.execute("SELECT COUNT(*) FROM trifecta_odds WHERE snapshot_id=?",
                     (snap["snapshot_id"],)).fetchone()[0]
    assert n == 120


def test_rerun_does_not_duplicate_snapshot(conn, selling_html):
    job = _setup_race(conn)
    client = StubClient([selling_html, selling_html])
    assert execute_job(conn, job, client) == "done"
    job2 = _refetch(conn, job["job_id"])
    assert execute_job(conn, job2, client) == "done"

    assert conn.execute("SELECT COUNT(*) FROM odds_snapshots").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM trifecta_odds").fetchone()[0] == 120
    # second run did not even hit the network
    assert len(client.calls) == 1


def test_fetch_attempts_grow_per_retry(conn, incomplete_html, selling_html):
    job = _setup_race(conn)
    client = StubClient([incomplete_html, selling_html])

    assert execute_job(conn, job, client) == "retry"
    assert conn.execute("SELECT COUNT(*) FROM fetch_attempts").fetchone()[0] == 1
    a1 = conn.execute("SELECT outcome FROM fetch_attempts").fetchone()
    assert a1["outcome"] == "incomplete"

    job2 = _refetch(conn, job["job_id"])
    assert execute_job(conn, job2, client) == "done"
    assert conn.execute("SELECT COUNT(*) FROM fetch_attempts").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM odds_snapshots").fetchone()[0] == 1


def test_collect_due_recovers_running_job_after_restart(conn, selling_html):
    job = _setup_race(conn)
    conn.execute(
        "UPDATE capture_jobs SET status='running', attempt_count=? WHERE job_id=?",
        (config.MAX_RETRIES, job["job_id"]),
    )
    conn.commit()

    client = StubClient([selling_html])
    stats = collect_due_once(conn, client)

    assert stats["recovered"] == 1
    assert stats["done"] == 1
    row = conn.execute(
        "SELECT status, attempt_count FROM capture_jobs WHERE job_id=?",
        (job["job_id"],),
    ).fetchone()
    assert row["status"] == "done"
    assert row["attempt_count"] == config.MAX_RETRIES


def test_retry_backoff_does_not_block_other_due_jobs(
    conn, incomplete_html, selling_html
):
    _setup_race(conn, race_no=6, slot="m08", deadline_offset_min=8)
    conn.execute(
        "UPDATE capture_jobs SET status='skipped'"
        " WHERE capture_slot NOT IN ('m08','m05')"
    )
    conn.execute(
        "UPDATE capture_jobs SET scheduled_at_jst=? WHERE capture_slot='m08'",
        (fmt_jst(now_jst() - timedelta(minutes=1)),),
    )
    conn.execute(
        "UPDATE capture_jobs SET scheduled_at_jst=? WHERE capture_slot='m05'",
        (fmt_jst(now_jst()),),
    )
    conn.commit()
    client = StubClient([incomplete_html, selling_html])

    stats = collect_due_once(conn, client)

    assert stats["retry"] == 1
    assert stats["done"] == 1
    m08 = conn.execute(
        "SELECT status, next_attempt_at_jst FROM capture_jobs"
        " WHERE capture_slot='m08'"
    ).fetchone()
    assert m08["status"] == "failed"
    assert m08["next_attempt_at_jst"] is not None
    assert all(
        row["capture_slot"] != "m08"
        for row in db.due_jobs(conn, fmt_jst(now_jst()))
    )
    assert conn.execute(
        "SELECT status FROM capture_jobs WHERE capture_slot='m05'"
    ).fetchone()["status"] == "done"


def test_raw_html_saved_even_when_parse_fails(conn, incomplete_html, workdir):
    job = _setup_race(conn)
    client = StubClient([incomplete_html])
    execute_job(conn, job, client)

    attempt = conn.execute("SELECT * FROM fetch_attempts").fetchone()
    assert attempt["raw_html_path"] is not None
    raw_file = workdir / attempt["raw_html_path"]
    assert raw_file.exists()
    # gzip round-trip matches the original body
    from boatrace_odds.storage import load_raw_html
    assert load_raw_html(raw_file).decode("utf-8") == incomplete_html
    assert attempt["response_sha256"] is not None


def test_no_data_page_marks_job_no_data(conn, no_data_html):
    job = _setup_race(conn)
    client = StubClient([no_data_html])
    assert execute_job(conn, job, client) == "no_data"
    row = conn.execute("SELECT status FROM capture_jobs WHERE job_id=?",
                       (job["job_id"],)).fetchone()
    assert row["status"] == "no_data"
    assert conn.execute("SELECT COUNT(*) FROM odds_snapshots").fetchone()[0] == 0
    a = conn.execute("SELECT outcome FROM fetch_attempts").fetchone()
    assert a["outcome"] == "no_data"


def test_network_error_recorded(conn):
    job = _setup_race(conn)
    client = StubClient([None])  # simulated connection failure
    assert execute_job(conn, job, client) == "retry"
    a = conn.execute("SELECT outcome, http_status FROM fetch_attempts").fetchone()
    assert a["outcome"] == "network_error"
    assert a["http_status"] is None


def test_final_slot_rejects_selling_page(conn, selling_html):
    job = _setup_race(conn, race_no=6, slot="final", deadline_offset_min=-3)
    client = StubClient([selling_html])
    assert execute_job(conn, job, client) == "retry"
    a = conn.execute("SELECT outcome FROM fetch_attempts").fetchone()
    assert a["outcome"] == "incomplete"


def test_final_slot_accepts_final_page(conn, final_html):
    job = _setup_race(conn, race_no=1, slot="final", deadline_offset_min=-3)
    client = StubClient([final_html])
    assert execute_job(conn, job, client) == "done"
    snap = conn.execute("SELECT is_final FROM odds_snapshots").fetchone()
    assert snap["is_final"] == 1


def test_url_mismatch_rejected(conn, selling_html):
    # register race 7 but the fixture page shows race 6 -> mismatch
    job = _setup_race(conn, race_no=7)
    client = StubClient([selling_html])
    assert execute_job(conn, job, client) == "retry"
    a = conn.execute("SELECT outcome FROM fetch_attempts").fetchone()
    assert a["outcome"] == "mismatch"


def test_day_aborted_on_403(conn):
    from boatrace_odds.collector import DayAborted
    from boatrace_odds.http_client import AccessBlockedError

    job = _setup_race(conn)
    client = StubClient([AccessBlockedError(403, "http://test")])
    with pytest.raises(DayAborted):
        execute_job(conn, job, client)
    row = conn.execute("SELECT status FROM capture_jobs WHERE job_id=?",
                       (job["job_id"],)).fetchone()
    assert row["status"] == "aborted"


def test_venue_code_leading_zero_preserved(conn, selling_html):
    register_day_schedule(conn, RACE_DATE, "01", {1: "12:00"}, "http://t")
    register_day_schedule(conn, RACE_DATE, "03", {1: "12:00"}, "http://t")
    codes = [r["venue_code"] for r in
             conn.execute("SELECT venue_code FROM races ORDER BY venue_code")]
    assert codes == ["01", "03"]
    url = config.odds3t_url("20260610", "01", 1)
    assert "jcd=01" in url


def test_timestamps_are_jst_aware(conn, selling_html):
    job = _setup_race(conn)
    client = StubClient([selling_html])
    execute_job(conn, job, client)
    snap = conn.execute("SELECT fetched_at_jst FROM odds_snapshots").fetchone()
    assert snap["fetched_at_jst"].endswith("+0900")
    from boatrace_odds.timeutil import parse_jst
    dt = parse_jst(snap["fetched_at_jst"])
    assert dt.utcoffset().total_seconds() == 9 * 3600
