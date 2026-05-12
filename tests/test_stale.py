"""
test_stale.py - Stale Downloads detection tests.

Uses os.utime to backdate file mtimes; no real Downloads dir is touched.
The Spotlight lookup is replaced with a deterministic stub.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scanner import FileInfo
from stale import STALE_THRESHOLD_DAYS, StaleFile, find_stale_downloads


def _make_fi(path: Path) -> FileInfo:
    return FileInfo(
        path=path,
        name=path.name,
        stem=path.stem,
        suffix=path.suffix.lower(),
        key="x",
        size=path.stat().st_size,
    )


def _backdate(path: Path, days: int) -> None:
    """Set the file's mtime to `days` ago."""
    ts = (date.today() - timedelta(days=days))
    epoch = ts.toordinal() - date(1970, 1, 1).toordinal()
    seconds = epoch * 86400
    os.utime(path, (seconds, seconds))


def _no_spotlight(_path: Path) -> str | None:
    return None


def _always_spotlight(_path: Path) -> str | None:
    return "2026-04-01 10:00:00 +0000"


class TestStaleDownloads:
    def test_old_file_flagged(self, tmp_path):
        f = tmp_path / "old.pdf"
        f.write_bytes(b"x" * 1024)
        _backdate(f, 200)
        result = find_stale_downloads(
            [_make_fi(f)], set(), date.today(), last_used_lookup=_no_spotlight,
        )
        assert len(result) == 1
        assert result[0].path == f
        assert result[0].last_used is None

    def test_recent_file_not_flagged(self, tmp_path):
        f = tmp_path / "recent.pdf"
        f.write_bytes(b"x")
        # File is fresh (mtime ~ now)
        result = find_stale_downloads(
            [_make_fi(f)], set(), date.today(), last_used_lookup=_no_spotlight,
        )
        assert result == []

    def test_clustered_file_excluded(self, tmp_path):
        f = tmp_path / "clustered_old.pdf"
        f.write_bytes(b"x")
        _backdate(f, 200)
        result = find_stale_downloads(
            [_make_fi(f)], {f}, date.today(), last_used_lookup=_no_spotlight,
        )
        assert result == [], "Files in clusters must not also surface as stale"

    def test_auto_delete_excluded(self, tmp_path):
        f = tmp_path / ".DS_Store"
        f.write_bytes(b"x")
        _backdate(f, 200)
        result = find_stale_downloads(
            [_make_fi(f)], set(), date.today(), last_used_lookup=_no_spotlight,
        )
        assert result == []

    def test_dotfile_excluded(self, tmp_path):
        f = tmp_path / ".secret"
        f.write_bytes(b"x")
        _backdate(f, 200)
        result = find_stale_downloads(
            [_make_fi(f)], set(), date.today(), last_used_lookup=_no_spotlight,
        )
        assert result == []

    def test_sorted_by_size_desc(self, tmp_path):
        small = tmp_path / "small.pdf"
        big = tmp_path / "big.pdf"
        small.write_bytes(b"x" * 100)
        big.write_bytes(b"x" * 10_000)
        _backdate(small, 200)
        _backdate(big, 200)
        result = find_stale_downloads(
            [_make_fi(small), _make_fi(big)], set(), date.today(),
            last_used_lookup=_no_spotlight,
        )
        assert [s.path.name for s in result] == ["big.pdf", "small.pdf"]

    def test_spotlight_last_used_captured(self, tmp_path):
        f = tmp_path / "old.pdf"
        f.write_bytes(b"x")
        _backdate(f, 200)
        result = find_stale_downloads(
            [_make_fi(f)], set(), date.today(),
            last_used_lookup=_always_spotlight,
        )
        assert result[0].last_used == "2026-04-01 10:00:00 +0000"

    def test_threshold_boundary(self, tmp_path):
        """A file exactly at the threshold is flagged; one day younger is not."""
        at_threshold = tmp_path / "at.pdf"
        just_under = tmp_path / "under.pdf"
        at_threshold.write_bytes(b"x")
        just_under.write_bytes(b"x")
        _backdate(at_threshold, STALE_THRESHOLD_DAYS)
        _backdate(just_under, STALE_THRESHOLD_DAYS - 1)
        result = find_stale_downloads(
            [_make_fi(at_threshold), _make_fi(just_under)], set(), date.today(),
            last_used_lookup=_no_spotlight,
        )
        names = {s.path.name for s in result}
        assert "at.pdf" in names
        assert "under.pdf" not in names
