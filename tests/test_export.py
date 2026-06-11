"""Export (Parquet / CSV) and audit tests."""
from __future__ import annotations

from datetime import date, timedelta

from boatrace_odds.collector import execute_job
from boatrace_odds.scheduler import register_day_schedule
from boatrace_odds.timeutil import now_jst

from .test_collector import RACE_DATE, StubClient, _setup_race


def _capture_one(conn, selling_html):
    job = _setup_race(conn)
    client = StubClient([selling_html])
    assert execute_job(conn, job, client) == "done"


def test_export_parquet_roundtrip(conn, selling_html, workdir):
    _capture_one(conn, selling_html)
    from boatrace_odds.export import export_parquet

    out = export_parquet(conn, RACE_DATE, RACE_DATE)
    assert out.exists()

    import pandas as pd
    df = pd.read_parquet(out)
    assert len(df) == 120
    assert set(df["combination_code"]).__len__() == 120
    # venue_code kept as a string inside the files; leading zeros survive
    assert df["venue_code"].iloc[0] == "24"
    assert df["venue_code"].dtype == object
    assert df["odds_tenths"].gt(0).all()
    for col in ("race_date_jst", "race_no", "capture_slot", "fetched_at_jst",
                "source_updated_at_jst", "first_boat", "second_boat",
                "third_boat"):
        assert col in df.columns
    # partition directories: year/month/venue_code
    assert (out / "year=2026").exists()


def test_export_csv(conn, selling_html, workdir):
    _capture_one(conn, selling_html)
    from boatrace_odds.export import export_csv

    out = export_csv(conn, RACE_DATE, "24")
    assert out.exists()
    lines = out.read_text(encoding="utf-8-sig").strip().splitlines()
    assert len(lines) == 121  # header + 120 rows


def test_audit_day_reports_missing(conn, selling_html):
    # register a race with all 6 slots but only capture m08
    _capture_one(conn, selling_html)
    from boatrace_odds.audit import audit_day, format_report

    report = audit_day(conn, RACE_DATE)
    # the captured page also republishes the full 12-race deadline table,
    # so 12 races x 6 slots are registered after schedule refresh
    assert len(report) == 72
    race6 = [r for r in report if r["race_no"] == 6]
    assert len(race6) == 6
    done = [r for r in report if r["has_snapshot"]]
    assert len(done) == 1
    assert done[0]["slot"] == "m08"
    assert done[0]["race_no"] == 6
    assert done[0]["odds_complete"] is True
    assert done[0]["delay_sec"] is not None

    text = format_report(report)
    assert "m08" in text and "final" in text


def test_schedule_change_recomputes_pending_jobs(conn):
    deadline1 = now_jst() + timedelta(minutes=60)
    register_day_schedule(conn, RACE_DATE, "24", {1: deadline1.strftime("%H:%M")},
                          "http://t")
    before = conn.execute(
        "SELECT scheduled_at_jst FROM capture_jobs WHERE capture_slot='m20'"
    ).fetchone()[0]

    deadline2 = deadline1 + timedelta(minutes=10)
    register_day_schedule(conn, RACE_DATE, "24", {1: deadline2.strftime("%H:%M")},
                          "http://t")
    after = conn.execute(
        "SELECT scheduled_at_jst FROM capture_jobs WHERE capture_slot='m20'"
    ).fetchone()[0]
    assert before != after
    # schedule observations history has both entries
    n = conn.execute(
        "SELECT COUNT(*) FROM race_schedule_observations"
    ).fetchone()[0]
    assert n == 2
