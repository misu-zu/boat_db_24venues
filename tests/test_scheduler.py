"""Daily venue discovery tests."""
from __future__ import annotations

from datetime import date

from boatrace_odds.scheduler import discover_day

from .test_collector import StubClient


def test_discover_day_records_held_and_no_meeting(conn, no_data_html, selling_html):
    race_date = date(2026, 6, 10)
    client = StubClient([no_data_html, selling_html])

    result = discover_day(
        conn,
        race_date,
        client=client,
        venues={"01": "桐生", "24": "大村"},
    )

    assert result == {"01": 0, "24": 12}

    rows = conn.execute(
        "SELECT venue_code, venue_name, status, race_count"
        " FROM venue_day_status ORDER BY venue_code"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("01", "桐生", "no_meeting", 0),
        ("24", "大村", "held", 12),
    ]
    assert conn.execute("SELECT COUNT(*) FROM races").fetchone()[0] == 12
    assert conn.execute("SELECT COUNT(*) FROM capture_jobs").fetchone()[0] == 72


def test_discover_day_records_discovery_failed(conn):
    race_date = date(2026, 6, 10)
    client = StubClient([None])

    result = discover_day(
        conn,
        race_date,
        client=client,
        venues={"01": "桐生"},
    )

    assert result == {"01": 0}
    row = conn.execute("SELECT * FROM venue_day_status").fetchone()
    assert row["venue_code"] == "01"
    assert row["status"] == "discovery_failed"
    assert row["race_count"] == 0
    assert "network_error" in row["error_detail"]
    assert conn.execute("SELECT COUNT(*) FROM capture_jobs").fetchone()[0] == 0
