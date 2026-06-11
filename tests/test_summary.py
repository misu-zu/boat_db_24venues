"""One-day collection summary tests."""
from __future__ import annotations

from boatrace_odds.collector import execute_job

from .test_collector import RACE_DATE, StubClient, _setup_race


def test_summary_day_aggregates_jobs_and_snapshots(conn, selling_html):
    job = _setup_race(conn)
    client = StubClient([selling_html])
    assert execute_job(conn, job, client) == "done"

    from boatrace_odds.summary import format_summary, summary_day

    summary = summary_day(conn, RACE_DATE)
    assert summary["races"] == 12
    assert summary["jobs"] == 72
    assert summary["snapshots"] == 1
    assert summary["complete_snapshots"] == 1
    assert summary["odds_rows"] == 120
    assert summary["attempts"] == 1
    assert summary["status_counts"]["done"] == 1
    assert summary["status_counts"]["pending"] == 71
    assert summary["outcome_counts"]["success"] == 1
    assert summary["discovery_counts"]["held"] == 1

    text = format_summary(summary)
    assert "2026-06-10 collection summary" in text
    assert "discovery_status: held=1" in text
    assert "24 大村" in text
    assert "m08" in text
    assert "incomplete_or_attention" in text
