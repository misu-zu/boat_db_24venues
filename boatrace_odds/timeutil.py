"""JST time helpers. All persisted timestamps are Asia/Tokyo aware."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

ISO_FMT = "%Y-%m-%dT%H:%M:%S%z"


def now_jst() -> datetime:
    return datetime.now(tz=JST)


def to_jst(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime is not allowed")
    return dt.astimezone(JST)


def fmt_jst(dt: datetime) -> str:
    """Serialize an aware datetime as ISO-8601 with +09:00 offset."""
    return to_jst(dt).strftime(ISO_FMT)


def parse_jst(text: str) -> datetime:
    dt = datetime.strptime(text, ISO_FMT)
    return dt.astimezone(JST)


def date_to_yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


def yyyymmdd_to_date(text: str) -> date:
    return datetime.strptime(text, "%Y%m%d").date()


def iso_date(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def parse_iso_date(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def hhmm_on(d: date, hhmm: str) -> datetime:
    """Combine a race date and an 'HH:MM' string into an aware JST datetime.

    Boat race night sessions never cross midnight, so no day rollover
    handling is required.
    """
    hh, mm = hhmm.split(":")
    return datetime(d.year, d.month, d.day, int(hh), int(mm), tzinfo=JST)


def minutes_before(dt: datetime, minutes: int) -> datetime:
    return dt - timedelta(minutes=minutes)
