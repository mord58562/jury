"""
quarantine.py - Multi-layer safety pipeline for Jury.

A candidate file is moved to a dated, per-path quarantine directory under
~/Library/Application Support/jury/quarantine/YYYY-MM-DD/<hash>/<name>.
After QUARANTINE_TTL_DAYS the day directory is final-trashed via osascript.

Gates (any one returning a "skip" reason prevents the action):
  - cooling window: file modified or accessed in the last COOLING_HOURS
  - open-file gate: lsof returns a hit on the file or its parent
  - daily ceiling: state.json counter caps actions per day
  - protected extension is already enforced by classify() upstream

Every action appends a line to undo.log:
    <iso timestamp>\\t<action>\\t<original_path>\\t-> <current_path>

Tests inject paths and clocks via keyword arguments so no module-global
state needs to be touched.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional


APP_SUPPORT_ROOT = Path.home() / "Library" / "Application Support" / "jury"
QUARANTINE_ROOT = APP_SUPPORT_ROOT / "quarantine"
UNDO_LOG = APP_SUPPORT_ROOT / "undo.log"
STATE_FILE = APP_SUPPORT_ROOT / "state.json"
LOCKFILE = APP_SUPPORT_ROOT / "monitor.lock"

QUARANTINE_TTL_DAYS = 30
EXPIRING_SOON_DAYS = 7   # surface in digest when <= this many days remain
DAILY_ACTION_CEILING = 200
COOLING_HOURS = 24

SIDECAR_SUFFIX = ".jury-restore.json"


@dataclass
class QuarantineEntry:
    current_path: Path
    original_path: Path
    quarantined_at: date
    original_mtime: float

    @property
    def age_days(self) -> int:
        return (date.today() - self.quarantined_at).days

    @property
    def days_until_purge(self) -> int:
        return QUARANTINE_TTL_DAYS - self.age_days


def is_file_open(path: Path) -> bool:
    """Return True if lsof reports any process has this file open.

    Conservative on failure: treat unknown state as "open" so we don't act.
    """
    try:
        result = subprocess.run(
            ["lsof", "--", str(path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return True
    return result.stdout.strip() != ""


def is_in_cooling_window(
    path: Path,
    now: Optional[datetime] = None,
    cooling_hours: int = COOLING_HOURS,
) -> bool:
    """Return True if the file was modified or accessed within cooling_hours."""
    now = now or datetime.now()
    try:
        st = path.stat()
    except OSError:
        return True
    youngest = max(st.st_mtime, st.st_atime)
    cutoff = now.timestamp() - cooling_hours * 3600
    return youngest > cutoff


def _path_hash(path: Path) -> str:
    return hashlib.sha256(str(path).encode()).hexdigest()[:8]


def quarantine_target(
    original: Path,
    today: Optional[date] = None,
    root: Path = QUARANTINE_ROOT,
) -> Path:
    """Deterministic destination path for an original file."""
    today = today or date.today()
    return root / today.isoformat() / _path_hash(original) / original.name


def move_to_quarantine(
    original: Path,
    today: Optional[date] = None,
    root: Path = QUARANTINE_ROOT,
) -> Optional[Path]:
    """Move the file into quarantine and drop a sidecar. Returns the new path."""
    today = today or date.today()
    target = quarantine_target(original, today=today, root=root)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        original_mtime = original.stat().st_mtime
    except OSError:
        return None
    try:
        os.rename(str(original), str(target))
    except OSError:
        try:
            shutil.move(str(original), str(target))
        except OSError:
            return None
    sidecar = target.with_name(target.name + SIDECAR_SUFFIX)
    try:
        sidecar.write_text(json.dumps({
            "original_path": str(original),
            "quarantined_at": today.isoformat(),
            "original_mtime": original_mtime,
        }))
    except OSError:
        pass
    return target


def append_undo_log(
    action: str,
    original: Path,
    current: Optional[Path] = None,
    log_path: Path = UNDO_LOG,
    now: Optional[datetime] = None,
) -> None:
    """Append one tab-delimited line describing the action."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = (now or datetime.now()).isoformat(timespec="seconds")
    parts = [ts, action, str(original)]
    if current is not None:
        parts.append(f"-> {current}")
    line = "\t".join(parts) + "\n"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def _load_state(state_file: Path) -> dict:
    try:
        return json.loads(state_file.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict, state_file: Path) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        state_file.write_text(json.dumps(state))
    except OSError:
        pass


def daily_action_count(
    today: Optional[date] = None,
    state_file: Path = STATE_FILE,
) -> int:
    today = today or date.today()
    state = _load_state(state_file)
    if state.get("date") != today.isoformat():
        return 0
    return int(state.get("actions", 0))


def increment_daily_action(
    today: Optional[date] = None,
    state_file: Path = STATE_FILE,
) -> int:
    today = today or date.today()
    state = _load_state(state_file)
    if state.get("date") != today.isoformat():
        state = {"date": today.isoformat(), "actions": 0}
    state["actions"] = int(state.get("actions", 0)) + 1
    _save_state(state, state_file)
    return state["actions"]


def process_auto_delete_candidate(
    candidate_path: Path,
    *,
    today: Optional[date] = None,
    now: Optional[datetime] = None,
    quarantine_root: Path = QUARANTINE_ROOT,
    undo_log: Path = UNDO_LOG,
    state_file: Path = STATE_FILE,
    ceiling: int = DAILY_ACTION_CEILING,
    cooling_hours: int = COOLING_HOURS,
    open_check: Callable[[Path], bool] = is_file_open,
    cooling_check: Optional[Callable[[Path, Optional[datetime]], bool]] = None,
) -> str:
    """Run an auto-delete candidate through every safety gate.

    Returns one of:
      'quarantined' - moved to quarantine, log + counter updated
      'skipped_cooling' - file touched within cooling window
      'skipped_open'    - lsof reports the file is open
      'skipped_ceiling' - daily action ceiling already reached
      'failed'          - file system error during move
      'missing'         - candidate path no longer exists
    """
    today = today or date.today()
    if not candidate_path.exists():
        return "missing"
    if cooling_check is None:
        if is_in_cooling_window(candidate_path, now=now, cooling_hours=cooling_hours):
            return "skipped_cooling"
    else:
        if cooling_check(candidate_path, now):
            return "skipped_cooling"
    if open_check(candidate_path):
        return "skipped_open"
    if daily_action_count(today=today, state_file=state_file) >= ceiling:
        return "skipped_ceiling"
    target = move_to_quarantine(candidate_path, today=today, root=quarantine_root)
    if target is None:
        return "failed"
    append_undo_log("quarantine", candidate_path, target, log_path=undo_log, now=now)
    increment_daily_action(today=today, state_file=state_file)
    return "quarantined"


def list_quarantine(
    root: Path = QUARANTINE_ROOT,
    today: Optional[date] = None,
) -> list[QuarantineEntry]:
    """Walk the quarantine dir, parse sidecars, return entries sorted by age desc."""
    today = today or date.today()
    entries: list[QuarantineEntry] = []
    if not root.exists():
        return entries
    for day_dir in root.iterdir():
        if not day_dir.is_dir():
            continue
        try:
            day_date = date.fromisoformat(day_dir.name)
        except ValueError:
            continue
        for sub in day_dir.rglob("*"):
            if not sub.is_file():
                continue
            if sub.name.endswith(SIDECAR_SUFFIX):
                continue
            sidecar = sub.with_name(sub.name + SIDECAR_SUFFIX)
            original_path = sub
            original_mtime = 0.0
            quarantined_at = day_date
            if sidecar.exists():
                try:
                    data = json.loads(sidecar.read_text())
                    original_path = Path(data.get("original_path", str(sub)))
                    original_mtime = float(data.get("original_mtime", 0.0))
                    quarantined_at = date.fromisoformat(data.get("quarantined_at", day_dir.name))
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
            entries.append(QuarantineEntry(
                current_path=sub,
                original_path=original_path,
                quarantined_at=quarantined_at,
                original_mtime=original_mtime,
            ))
    entries.sort(key=lambda e: e.quarantined_at)
    return entries


def age_out(
    today: Optional[date] = None,
    ttl_days: int = QUARANTINE_TTL_DAYS,
    root: Path = QUARANTINE_ROOT,
    undo_log: Path = UNDO_LOG,
    trash_fn: Optional[Callable[[Path], None]] = None,
) -> list[Path]:
    """Final-trash quarantine entries older than ttl_days. Returns the paths trashed."""
    today = today or date.today()
    trashed: list[Path] = []
    if not root.exists():
        return trashed
    for day_dir in sorted(root.iterdir()):
        if not day_dir.is_dir():
            continue
        try:
            day_date = date.fromisoformat(day_dir.name)
        except ValueError:
            continue
        if (today - day_date).days < ttl_days:
            continue
        for sub in day_dir.rglob("*"):
            if not sub.is_file():
                continue
            if sub.name.endswith(SIDECAR_SUFFIX):
                continue
            sidecar = sub.with_name(sub.name + SIDECAR_SUFFIX)
            original = sub
            if sidecar.exists():
                try:
                    data = json.loads(sidecar.read_text())
                    original = Path(data.get("original_path", str(sub)))
                except (OSError, json.JSONDecodeError):
                    pass
            if trash_fn is None:
                _final_trash(sub)
            else:
                trash_fn(sub)
            trashed.append(sub)
            append_undo_log("final_trash", original, sub, log_path=undo_log)
        try:
            shutil.rmtree(day_dir)
        except OSError:
            pass
    return trashed


def _final_trash(path: Path) -> None:
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                f'tell application "Finder" to delete POSIX file "{path}"',
            ],
            capture_output=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def restore_command(entry: QuarantineEntry) -> str:
    """Return a shell one-liner that moves the file back to its original path."""
    current = str(entry.current_path).replace('"', '\\"')
    original = str(entry.original_path).replace('"', '\\"')
    return f'mv "{current}" "{original}"'
