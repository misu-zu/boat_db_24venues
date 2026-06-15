"""Command line interface.

Usage:
    python -m boatrace_odds.cli init-db
    python -m boatrace_odds.cli discover-day --date YYYY-MM-DD
    python -m boatrace_odds.cli collect-due --once
    python -m boatrace_odds.cli daemon
    python -m boatrace_odds.cli audit-day --date YYYY-MM-DD
    python -m boatrace_odds.cli summary-day --date YYYY-MM-DD
    python -m boatrace_odds.cli export-parquet --date-from YYYY-MM-DD --date-to YYYY-MM-DD
    python -m boatrace_odds.cli export-csv --date YYYY-MM-DD --venue-code XX
"""
from __future__ import annotations

import argparse
import logging
import sys

from . import config, db
from .logging_setup import setup_logging
from .timeutil import parse_iso_date

log = logging.getLogger(__name__)


def cmd_init_db(_args) -> int:
    conn = db.connect()
    db.init_db(conn)
    conn.close()
    print(f"initialized database at {config.db_path()}")
    return 0


def cmd_discover_day(args) -> int:
    from .scheduler import discover_day

    race_date = parse_iso_date(args.date)
    conn = db.connect()
    db.init_db(conn)
    result = discover_day(conn, race_date)
    conn.close()
    total = sum(result.values())
    for venue, n in result.items():
        name = config.VENUES.get(venue, venue)
        print(f"  {venue} {name}: {n} races")
    print(f"registered {total} races for {race_date}")
    return 0


def cmd_collect_due(args) -> int:
    from .collector import DayAborted, collect_due_once

    conn = db.connect()
    db.init_db(conn)
    try:
        stats = collect_due_once(conn)
    except DayAborted as exc:
        print(f"CRITICAL: collection aborted for the day: {exc}", file=sys.stderr)
        return 3
    finally:
        conn.close()
    print(f"collect-due finished: {stats or 'nothing due'}")
    return 0


def cmd_daemon(_args) -> int:
    from .daemon import run_daemon

    run_daemon()
    return 0


def cmd_audit_day(args) -> int:
    from .audit import audit_day, format_report

    race_date = parse_iso_date(args.date)
    conn = db.connect()
    report = audit_day(conn, race_date)
    conn.close()
    print(format_report(report))
    missing = [r for r in report if not r["has_snapshot"]]
    print(f"\n{len(report)} slots, {len(missing)} without snapshot")
    return 0


def cmd_summary_day(args) -> int:
    from .summary import format_summary, summary_day

    race_date = parse_iso_date(args.date)
    conn = db.connect()
    summary = summary_day(conn, race_date)
    conn.close()
    print(format_summary(summary))
    return 0


def cmd_export_parquet(args) -> int:
    from .export import export_parquet

    conn = db.connect()
    out = export_parquet(
        conn, parse_iso_date(args.date_from), parse_iso_date(args.date_to)
    )
    conn.close()
    print(f"parquet dataset written to {out}")
    return 0


def cmd_export_csv(args) -> int:
    from .export import export_csv

    if args.venue_code not in config.VENUES:
        print(f"unknown venue code: {args.venue_code}", file=sys.stderr)
        return 2
    conn = db.connect()
    out = export_csv(conn, parse_iso_date(args.date), args.venue_code)
    conn.close()
    print(f"csv written to {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="boatrace_odds",
                                description="BOAT RACE trifecta odds collector")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db").set_defaults(func=cmd_init_db)

    sp = sub.add_parser("discover-day")
    sp.add_argument("--date", required=True, help="YYYY-MM-DD")
    sp.set_defaults(func=cmd_discover_day)

    sp = sub.add_parser("collect-due")
    sp.add_argument("--once", action="store_true",
                    help="process currently-due jobs then exit")
    sp.set_defaults(func=cmd_collect_due)

    sub.add_parser("daemon").set_defaults(func=cmd_daemon)

    sp = sub.add_parser("audit-day")
    sp.add_argument("--date", required=True, help="YYYY-MM-DD")
    sp.set_defaults(func=cmd_audit_day)

    sp = sub.add_parser("summary-day")
    sp.add_argument("--date", required=True, help="YYYY-MM-DD")
    sp.set_defaults(func=cmd_summary_day)

    sp = sub.add_parser("export-parquet")
    sp.add_argument("--date-from", required=True, help="YYYY-MM-DD")
    sp.add_argument("--date-to", required=True, help="YYYY-MM-DD")
    sp.set_defaults(func=cmd_export_parquet)

    sp = sub.add_parser("export-csv")
    sp.add_argument("--date", required=True, help="YYYY-MM-DD")
    sp.add_argument("--venue-code", required=True, help="e.g. 01, 24")
    sp.set_defaults(func=cmd_export_csv)

    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
