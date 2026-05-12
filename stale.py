"""
stale.py - Detect long-untouched top-level files in ~/Downloads.

A "stale" download is a top-level file in ~/Downloads that:
  - is older than STALE_THRESHOLD_DAYS by mtime
  - is not a member of any cluster (clusters already get their own section)
  - is not an auto-delete candidate (those are handled elsewhere)
  - is not a hidden dotfile

Spotlight's kMDItemLastUsedDate is queried per file as an extra signal -
"have you ever opened this?" is often a sharper trash question than the
download date itself.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from classifier import _matches_auto_delete
from scanner import FileInfo


STALE_THRESHOLD_DAYS = 90


@dataclass
class StaleFile:
    path: Path
    size: int
    mtime: date
    last_used: Optional[str]  # display string from Spotlight, or None


def _spotlight_last_used(path: Path) -> Optional[str]:
    """Return kMDItemLastUsedDate as a display string, or None if unset/unavailable."""
    try:
        result = subprocess.run(
            ["mdls", "-name", "kMDItemLastUsedDate", "-raw", str(path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    out = result.stdout.strip()
    if not out or out == "(null)":
        return None
    return out


def find_stale_downloads(
    downloads_files: list[FileInfo],
    clustered_paths: set[Path],
    today: date,
    *,
    min_age_days: int = STALE_THRESHOLD_DAYS,
    last_used_lookup=_spotlight_last_used,
) -> list[StaleFile]:
    """Return stale top-level downloads, sorted by size desc.

    Files in clustered_paths are excluded - the cluster section already
    surfaces those. Auto-delete candidates and dotfiles are excluded too.
    The last_used_lookup is injected so tests can patch Spotlight.
    """
    cutoff_ordinal = today.toordinal() - min_age_days
    out: list[StaleFile] = []
    for fi in downloads_files:
        if fi.path in clustered_paths:
            continue
        if fi.name.startswith("."):
            continue
        if _matches_auto_delete(fi.name):
            continue
        try:
            mtime_ts = fi.path.stat().st_mtime
        except OSError:
            continue
        mtime = date.fromtimestamp(mtime_ts)
        if mtime.toordinal() > cutoff_ordinal:
            continue
        out.append(StaleFile(
            path=fi.path,
            size=fi.size,
            mtime=mtime,
            last_used=last_used_lookup(fi.path),
        ))
    out.sort(key=lambda s: s.size, reverse=True)
    return out
