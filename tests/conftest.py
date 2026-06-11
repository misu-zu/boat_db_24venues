from __future__ import annotations

import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
TEST_NOW = "2026-06-10T12:00:00+0900"


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    """Keep fixture-based capture windows stable on any real date."""
    from boatrace_odds.timeutil import parse_jst

    frozen = parse_jst(TEST_NOW)

    def _now():
        return frozen

    monkeypatch.setattr("boatrace_odds.timeutil.now_jst", _now)
    monkeypatch.setattr("boatrace_odds.db.now_jst", _now)
    monkeypatch.setattr("boatrace_odds.collector.now_jst", _now)
    monkeypatch.setattr("boatrace_odds.scheduler.now_jst", _now)
    monkeypatch.setattr("tests.test_collector.now_jst", _now, raising=False)
    monkeypatch.setattr("tests.test_export.now_jst", _now, raising=False)


@pytest.fixture()
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture()
def selling_html() -> str:
    return (FIXTURES / "selling.html").read_text(encoding="utf-8")


@pytest.fixture()
def final_html() -> str:
    return (FIXTURES / "final.html").read_text(encoding="utf-8")


@pytest.fixture()
def no_data_html() -> str:
    return (FIXTURES / "no_data.html").read_text(encoding="utf-8")


@pytest.fixture()
def incomplete_html() -> str:
    return (FIXTURES / "incomplete.html").read_text(encoding="utf-8")


@pytest.fixture()
def workdir(tmp_path, monkeypatch):
    """Redirect all data/ paths into a temp dir via BOATRACE_ODDS_HOME."""
    monkeypatch.setenv("BOATRACE_ODDS_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def conn(workdir):
    from boatrace_odds import db

    connection = db.connect()
    db.init_db(connection)
    yield connection
    connection.close()
