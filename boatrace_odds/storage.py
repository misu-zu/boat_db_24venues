"""Raw HTML archival: gzip files saved BEFORE parsing."""
from __future__ import annotations

import gzip
import hashlib
from datetime import datetime
from pathlib import Path

from . import config


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def raw_html_target(
    race_date_yyyymmdd: str,
    venue_code: str,
    race_no: int,
    slot: str,
    fetched_at: datetime,
) -> Path:
    """data/raw/html/{YYYY}/{MM}/{DD}/{VENUE}/{R}R/{SLOT}_{TIMESTAMP}.html.gz"""
    yyyy, mm, dd = (
        race_date_yyyymmdd[0:4],
        race_date_yyyymmdd[4:6],
        race_date_yyyymmdd[6:8],
    )
    ts = fetched_at.strftime("%Y%m%dT%H%M%S")
    return (
        config.raw_html_dir()
        / yyyy / mm / dd / venue_code / f"{race_no}R"
        / f"{slot}_{ts}.html.gz"
    )


def save_raw_html(
    html_bytes: bytes,
    race_date_yyyymmdd: str,
    venue_code: str,
    race_no: int,
    slot: str,
    fetched_at: datetime,
) -> tuple[str, str]:
    """Persist gzip-compressed raw HTML. Returns (relative_path, sha256)."""
    target = raw_html_target(race_date_yyyymmdd, venue_code, race_no, slot, fetched_at)
    target.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(target, "wb") as fh:
        fh.write(html_bytes)
    rel = target.relative_to(config.project_root())
    return str(rel).replace("\\", "/"), sha256_hex(html_bytes)


def load_raw_html(path: str | Path) -> bytes:
    p = Path(path)
    if not p.is_absolute():
        p = config.project_root() / p
    with gzip.open(p, "rb") as fh:
        return fh.read()
