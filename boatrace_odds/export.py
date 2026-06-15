"""Parquet / CSV export of trifecta odds (long format)."""
from __future__ import annotations

import csv
import os
import shutil
import sqlite3
from datetime import date
from pathlib import Path

from . import config
from .timeutil import iso_date

EXPORT_QUERY = """
SELECT
    r.race_date_jst,
    r.venue_code,
    r.venue_name,
    r.race_no,
    s.capture_slot,
    s.is_final,
    s.fetched_at_jst,
    s.source_updated_at_jst,
    t.combination_code,
    t.first_boat,
    t.second_boat,
    t.third_boat,
    t.odds_text,
    t.odds_tenths
FROM trifecta_odds t
JOIN odds_snapshots s ON s.snapshot_id = t.snapshot_id
JOIN races r          ON r.race_id = s.race_id
WHERE r.race_date_jst BETWEEN ? AND ?
ORDER BY r.race_date_jst, r.venue_code, r.race_no,
         s.capture_slot, t.combination_code
"""


def export_parquet(
    conn: sqlite3.Connection,
    date_from: date,
    date_to: date,
    out_dir: Path | None = None,
) -> Path:
    """Write trifecta odds to a partitioned Parquet dataset.

    Partitions: year / month / venue_code.
    Returns the dataset root directory.
    """
    import pandas as pd

    out_dir = out_dir or (config.export_dir() / "parquet")
    out_dir = Path(out_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)

    df = pd.read_sql_query(
        EXPORT_QUERY, conn, params=(iso_date(date_from), iso_date(date_to))
    )
    if df.empty:
        raise SystemExit("no rows to export for the given date range")

    df["year"] = df["race_date_jst"].str[0:4].astype(int)
    df["month"] = df["race_date_jst"].str[5:7].astype(int)
    # venue_code stays a real string column inside the files so the leading
    # zero ('01') survives any reader; a duplicate `venue` column is used as
    # the hive partition key (partition values are stored as directory names
    # and may be re-typed by readers).
    df["venue_code"] = df["venue_code"].astype(str)
    df["venue"] = df["venue_code"]

    tmp_dir = out_dir.with_name(f".{out_dir.name}.tmp-{os.getpid()}")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)

    df.to_parquet(
        tmp_dir,
        engine="pyarrow",
        partition_cols=["year", "month", "venue"],
        index=False,
    )
    if out_dir.exists():
        shutil.rmtree(out_dir)
    tmp_dir.rename(out_dir)
    return out_dir


def export_csv(
    conn: sqlite3.Connection,
    race_date: date,
    venue_code: str,
    out_dir: Path | None = None,
) -> Path:
    """Write one day x one venue to CSV (utf-8-sig for Excel on Windows)."""
    out_dir = out_dir or (config.export_dir() / "csv")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"trifecta_{iso_date(race_date)}_{venue_code}.csv"

    cur = conn.execute(
        EXPORT_QUERY + " ", (iso_date(race_date), iso_date(race_date))
    )
    cols = [d[0] for d in cur.description]
    rows = [r for r in cur.fetchall() if r["venue_code"] == venue_code]

    with open(out_file, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(cols)
        for row in rows:
            writer.writerow([row[c] for c in cols])
    return out_file
