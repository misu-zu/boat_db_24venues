"""Long-running local scheduler.

- checks the local job queue every 10 s
- touches the network ONLY when a job is due
- detects date change and auto-discovers the new day's schedule
- prevents double-start with a PID lock file (Windows-safe)
- safe to restart: pending jobs simply resume
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date

from . import config, db
from .collector import DayAborted, collect_due_once
from .http_client import PoliteClient
from .scheduler import discover_day
from .timeutil import iso_date, now_jst

log = logging.getLogger(__name__)


class AlreadyRunningError(Exception):
    pass


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness check that works on Windows and POSIX."""
    if pid <= 0:
        return False
    try:
        import psutil  # type: ignore
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class PidLock:
    """File-based single-instance lock, robust to crashes."""

    def __init__(self, path=None):
        self.path = path or config.lock_file_path()

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                old_pid = int(self.path.read_text().strip())
            except (ValueError, OSError):
                old_pid = -1
            if old_pid > 0 and _pid_alive(old_pid):
                raise AlreadyRunningError(
                    f"daemon already running (pid {old_pid}, lock {self.path})"
                )
            log.warning("removing stale lock file (pid %s)", old_pid)
            self.path.unlink(missing_ok=True)
        self.path.write_text(str(os.getpid()), encoding="ascii")

    def release(self) -> None:
        try:
            if self.path.exists() and self.path.read_text().strip() == str(os.getpid()):
                self.path.unlink()
        except OSError:
            pass


def run_daemon(tick_sec: int = config.DAEMON_TICK_SEC) -> None:
    lock = PidLock()
    try:
        lock.acquire()
    except AlreadyRunningError as exc:
        log.critical("%s", exc)
        raise SystemExit(2)

    log.info("daemon started (pid %d)", os.getpid())
    conn = db.connect()
    db.init_db(conn)
    client = PoliteClient()
    current_day: date | None = None
    discovered_day: date | None = None
    last_discovery_attempt_monotonic = 0.0
    aborted_day: date | None = None

    try:
        while True:
            today = now_jst().date()  # JST date, independent of OS timezone

            if current_day != today:
                current_day = today
                discovered_day = None
                last_discovery_attempt_monotonic = 0.0

            # Keep retrying same-day discovery at low frequency until at least
            # one race is registered. Future-day odds pages may show no_data
            # until the race day pages open.
            should_discover = (
                aborted_day != today
                and discovered_day != today
                and (
                    last_discovery_attempt_monotonic == 0.0
                    or time.monotonic() - last_discovery_attempt_monotonic
                    >= config.DISCOVERY_RETRY_SEC
                )
            )
            if should_discover:
                log.info("discovering schedule for %s", today)
                last_discovery_attempt_monotonic = time.monotonic()
                try:
                    result = discover_day(conn, today, client=client)
                    total_races = sum(result.values())
                    statuses = db.venue_day_statuses(conn, iso_date(today))
                    target_count = len(config.target_venues())
                    failed = [s for s in statuses
                              if s["status"] == "discovery_failed"]
                    held = [s for s in statuses if s["status"] == "held"]
                    no_meeting = [s for s in statuses
                                  if s["status"] == "no_meeting"]
                    if (
                        len(statuses) >= target_count
                        and not failed
                        and held
                    ):
                        discovered_day = today
                        log.info(
                            "discovery complete for %s: held=%d no_meeting=%d"
                            " races=%d",
                            today, len(held), len(no_meeting), total_races,
                        )
                    else:
                        log.warning(
                            "discovery incomplete for %s: statuses=%d/%d"
                            " held=%d no_meeting=%d failed=%d races=%d;"
                            " will retry in %ds",
                            today, len(statuses), target_count, len(held),
                            len(no_meeting), len(failed), total_races,
                            config.DISCOVERY_RETRY_SEC,
                        )
                except Exception as exc:  # noqa: BLE001
                    log.error("discover failed: %s", exc)

            if aborted_day == today:
                time.sleep(tick_sec)
                continue

            try:
                stats = collect_due_once(conn, client=client)
                if stats:
                    log.info("tick stats: %s", stats)
            except DayAborted:
                log.critical("403/429 received — stopping auto collection for %s",
                             today)
                aborted_day = today
            except Exception as exc:  # noqa: BLE001
                log.error("tick error: %s", exc)

            time.sleep(tick_sec)
    except KeyboardInterrupt:
        log.info("daemon interrupted; shutting down")
    finally:
        client.close()
        conn.close()
        lock.release()
        log.info("daemon stopped")
