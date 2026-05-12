"""
monitor.py - Always-active filesystem monitor entry point.

Invoked by launchd whenever a watched directory changes (WatchPaths). Each
invocation is a one-shot process: scan top-level Downloads + Documents,
push auto-delete candidates through the quarantine pipeline, age out
expired entries, exit. Idle RAM = 0; bursts are coalesced by launchd's
ThrottleInterval.

Concurrent invocations are gated by a fcntl flock on a lockfile - if
another monitor is already running, this invocation exits silently
rather than racing.

Exit code is always 0 so launchd does not throttle us. Errors are
swallowed into the undo log for postmortem.
"""
from __future__ import annotations

import fcntl
import sys
from datetime import date, datetime
from pathlib import Path

from quarantine import (
    APP_SUPPORT_ROOT,
    LOCKFILE,
    QUARANTINE_ROOT,
    UNDO_LOG,
    age_out,
    append_undo_log,
    process_auto_delete_candidate,
)
from scanner import scan_dirs


LAST_RUN_FILE = APP_SUPPORT_ROOT / "monitor.last_run"


def _acquire_lock(lockfile: Path):
    lockfile.parent.mkdir(parents=True, exist_ok=True)
    try:
        fp = open(lockfile, "w")
    except OSError:
        return None
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fp.close()
        return None
    return fp


def _record_run(now: datetime) -> None:
    LAST_RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        LAST_RUN_FILE.write_text(now.isoformat(timespec="seconds"))
    except OSError:
        pass


def run_once(
    docs: Path,
    downloads: Path,
    *,
    today=None,
    now=None,
    quarantine_root: Path = QUARANTINE_ROOT,
    undo_log: Path = UNDO_LOG,
) -> dict:
    """Single monitor pass. Returns a tally for observability/testing."""
    today = today or date.today()
    now = now or datetime.now()

    scan = scan_dirs(docs, downloads)
    tally = {
        "quarantined": 0,
        "skipped_cooling": 0,
        "skipped_open": 0,
        "skipped_ceiling": 0,
        "failed": 0,
        "missing": 0,
        "aged_out": 0,
    }
    for fi in scan.auto_delete_candidates:
        outcome = process_auto_delete_candidate(
            fi.path,
            today=today,
            now=now,
            quarantine_root=quarantine_root,
            undo_log=undo_log,
        )
        tally[outcome] = tally.get(outcome, 0) + 1

    purged = age_out(today=today, root=quarantine_root, undo_log=undo_log)
    tally["aged_out"] = len(purged)
    return tally


def main() -> int:
    lock_fp = _acquire_lock(LOCKFILE)
    if lock_fp is None:
        return 0  # coalesce with the in-flight invocation

    try:
        docs = Path.home() / "Documents"
        downloads = Path.home() / "Downloads"
        now = datetime.now()
        try:
            run_once(docs, downloads, now=now)
        except Exception as e:
            append_undo_log("monitor_error", Path(str(e)))
        _record_run(now)
    finally:
        try:
            lock_fp.close()
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
