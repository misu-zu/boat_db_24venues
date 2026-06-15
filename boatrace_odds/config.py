"""Static configuration for the collector.

Venue codes are STRINGS. Leading zeros are significant and must never
be stripped ("01" != "1").
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# Project root = directory that contains the `data/` tree. Override with the
# BOATRACE_ODDS_HOME environment variable (useful for tests).


def project_root() -> Path:
    env = os.environ.get("BOATRACE_ODDS_HOME")
    if env:
        return Path(env)
    return Path.cwd()


def data_dir() -> Path:
    return project_root() / "data"


def db_path() -> Path:
    return data_dir() / "boatrace_odds.sqlite3"


def raw_html_dir() -> Path:
    return data_dir() / "raw" / "html"


def log_dir() -> Path:
    return data_dir() / "logs"


def export_dir() -> Path:
    return data_dir() / "export"


def lock_file_path() -> Path:
    return data_dir() / "daemon.lock"


# ---------------------------------------------------------------------------
# Target venues (jcd). Keys are zero-padded strings on purpose.
# ---------------------------------------------------------------------------
VENUES: dict[str, str] = {
    "01": "桐生",
    "02": "戸田",
    "03": "江戸川",
    "04": "平和島",
    "05": "多摩川",
    "06": "浜名湖",
    "07": "蒲郡",
    "08": "常滑",
    "09": "津",
    "10": "三国",
    "11": "びわこ",
    "12": "住之江",
    "13": "尼崎",
    "14": "鳴門",
    "15": "丸亀",
    "16": "児島",
    "17": "宮島",
    "18": "徳山",
    "19": "下関",
    "20": "若松",
    "21": "芦屋",
    "22": "福岡",
    "23": "唐津",
    "24": "大村",
}


def target_venues() -> dict[str, str]:
    """Return venues selected for this run.

    Set BOATRACE_ODDS_VENUES to a comma-separated list of venue codes
    such as "03,14,20,07,15" to limit discovery/daemon operation.
    """
    raw = os.environ.get("BOATRACE_ODDS_VENUES", "").strip()
    if not raw:
        return VENUES

    result: dict[str, str] = {}
    for item in raw.split(","):
        code = item.strip().zfill(2)
        if not code:
            continue
        if code not in VENUES:
            raise ValueError(f"unknown venue code in BOATRACE_ODDS_VENUES: {code}")
        result[code] = VENUES[code]
    if not result:
        raise ValueError("BOATRACE_ODDS_VENUES did not contain any venue codes")
    return result

RACE_NUMBERS = tuple(range(1, 13))  # 1R..12R

# ---------------------------------------------------------------------------
# Capture slots: minutes before scheduled deadline. `final` fires after the
# deadline once the page switches to 締切時オッズ.
# ---------------------------------------------------------------------------
CAPTURE_SLOTS: dict[str, int | None] = {
    "m20": 20,
    "m12": 12,
    "m08": 8,
    "m05": 5,
    "m02": 2,
    "final": None,
}

# Minutes AFTER the deadline at which the `final` slot becomes due.
FINAL_SLOT_DELAY_MIN = 2
# Give up on a slot if we are this many minutes past its scheduled time.
SLOT_EXPIRY_MIN = {
    "m20": 7,   # m20 expires when m12 territory starts
    "m12": 3,
    "m08": 2,
    "m05": 2,
    "m02": 1,
    "final": 30,
}

# ---------------------------------------------------------------------------
# HTTP / politeness
# ---------------------------------------------------------------------------
BASE_URL = "https://www.boatrace.jp/owpc/pc/race/odds3t"
USER_AGENT = (
    "boatrace-odds-collector/1.0 (personal research; low-frequency; "
    "single-worker; contact: local user)"
)
CONNECT_TIMEOUT_SEC = 5
READ_TIMEOUT_SEC = 15
MIN_REQUEST_INTERVAL_SEC = 3.0
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = (15, 45, 120)

# HTTP statuses that abort collection for the rest of the day.
ABORT_DAY_STATUSES = (403, 429)

# Daemon loop tick.
DAEMON_TICK_SEC = 10

# Retry same-day schedule discovery at low frequency when official odds pages
# still show no data, which can happen before the first race day pages open.
DISCOVERY_RETRY_SEC = 15 * 60


def odds3t_url(race_date_yyyymmdd: str, venue_code: str, race_no: int) -> str:
    """Build the official odds3t URL. venue_code keeps its leading zero."""
    return (
        f"{BASE_URL}?hd={race_date_yyyymmdd}&jcd={venue_code}&rno={race_no}"
    )
