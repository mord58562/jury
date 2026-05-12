"""
test_quarantine.py - Multi-layer safety pipeline tests.

All paths are tmp_path-scoped; no real ~/Library/Application Support is touched.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from quarantine import (
    COOLING_HOURS,
    DAILY_ACTION_CEILING,
    QUARANTINE_TTL_DAYS,
    QuarantineEntry,
    SIDECAR_SUFFIX,
    age_out,
    append_undo_log,
    daily_action_count,
    increment_daily_action,
    is_in_cooling_window,
    list_quarantine,
    move_to_quarantine,
    process_auto_delete_candidate,
    quarantine_target,
    restore_command,
)


def _backdate(path: Path, days: int) -> None:
    when = (datetime.now() - timedelta(days=days)).timestamp()
    os.utime(path, (when, when))


def _no_open(_path: Path) -> bool:
    return False


def _always_open(_path: Path) -> bool:
    return True


def _no_cooling(_path: Path, _now=None) -> bool:
    return False


def _always_cooling(_path: Path, _now=None) -> bool:
    return True


class TestQuarantineTarget:
    def test_includes_date_and_hash(self, tmp_path):
        original = tmp_path / "Downloads" / "file.txt"
        target = quarantine_target(original, today=date(2026, 5, 12), root=tmp_path / "q")
        assert "2026-05-12" in str(target)
        assert target.name == "file.txt"

    def test_different_paths_different_hash(self, tmp_path):
        a = quarantine_target(Path("/x/a.txt"), today=date(2026, 5, 12), root=tmp_path / "q")
        b = quarantine_target(Path("/y/a.txt"), today=date(2026, 5, 12), root=tmp_path / "q")
        # Same basename in different dirs land in different hash subdirs
        assert a != b


class TestMoveToQuarantine:
    def test_moves_file_and_writes_sidecar(self, tmp_path):
        original = tmp_path / "src" / "file.txt"
        original.parent.mkdir()
        original.write_text("hello")
        target = move_to_quarantine(
            original, today=date(2026, 5, 12), root=tmp_path / "q"
        )
        assert target is not None
        assert target.exists()
        assert not original.exists()
        sidecar = target.with_name(target.name + SIDECAR_SUFFIX)
        assert sidecar.exists()
        data = json.loads(sidecar.read_text())
        assert data["original_path"] == str(original)
        assert data["quarantined_at"] == "2026-05-12"

    def test_missing_source_returns_none(self, tmp_path):
        assert move_to_quarantine(
            tmp_path / "ghost.txt", today=date(2026, 5, 12), root=tmp_path / "q"
        ) is None


class TestUndoLog:
    def test_append_creates_file_and_line(self, tmp_path):
        log = tmp_path / "undo.log"
        append_undo_log("quarantine", Path("/a.txt"), Path("/q/a.txt"), log_path=log,
                        now=datetime(2026, 5, 12, 10, 30, 0))
        contents = log.read_text()
        assert "quarantine" in contents
        assert "/a.txt" in contents
        assert "/q/a.txt" in contents


class TestDailyCeiling:
    def test_counter_resets_on_new_day(self, tmp_path):
        sf = tmp_path / "state.json"
        increment_daily_action(today=date(2026, 5, 11), state_file=sf)
        increment_daily_action(today=date(2026, 5, 11), state_file=sf)
        assert daily_action_count(today=date(2026, 5, 11), state_file=sf) == 2
        assert daily_action_count(today=date(2026, 5, 12), state_file=sf) == 0

    def test_counter_persists_within_day(self, tmp_path):
        sf = tmp_path / "state.json"
        increment_daily_action(today=date(2026, 5, 12), state_file=sf)
        increment_daily_action(today=date(2026, 5, 12), state_file=sf)
        increment_daily_action(today=date(2026, 5, 12), state_file=sf)
        assert daily_action_count(today=date(2026, 5, 12), state_file=sf) == 3


class TestCoolingWindow:
    def test_recent_file_is_cooling(self, tmp_path):
        f = tmp_path / "fresh.txt"
        f.write_text("x")
        assert is_in_cooling_window(f) is True

    def test_old_file_not_cooling(self, tmp_path):
        f = tmp_path / "stale.txt"
        f.write_text("x")
        _backdate(f, 2)
        assert is_in_cooling_window(f) is False


class TestProcessCandidate:
    def test_happy_path_quarantines(self, tmp_path):
        f = tmp_path / "downloads" / ".DS_Store"
        f.parent.mkdir()
        f.write_text("x")
        _backdate(f, 5)
        outcome = process_auto_delete_candidate(
            f,
            today=date(2026, 5, 12),
            quarantine_root=tmp_path / "q",
            undo_log=tmp_path / "undo.log",
            state_file=tmp_path / "state.json",
            open_check=_no_open,
            cooling_check=_no_cooling,
        )
        assert outcome == "quarantined"
        assert not f.exists()
        # State counter incremented
        assert daily_action_count(today=date(2026, 5, 12),
                                  state_file=tmp_path / "state.json") == 1

    def test_cooling_window_blocks(self, tmp_path):
        f = tmp_path / "x"
        f.write_text("y")
        outcome = process_auto_delete_candidate(
            f,
            today=date(2026, 5, 12),
            quarantine_root=tmp_path / "q",
            undo_log=tmp_path / "undo.log",
            state_file=tmp_path / "state.json",
            open_check=_no_open,
            cooling_check=_always_cooling,
        )
        assert outcome == "skipped_cooling"
        assert f.exists()

    def test_open_file_blocks(self, tmp_path):
        f = tmp_path / "x"
        f.write_text("y")
        outcome = process_auto_delete_candidate(
            f,
            today=date(2026, 5, 12),
            quarantine_root=tmp_path / "q",
            undo_log=tmp_path / "undo.log",
            state_file=tmp_path / "state.json",
            open_check=_always_open,
            cooling_check=_no_cooling,
        )
        assert outcome == "skipped_open"
        assert f.exists()

    def test_ceiling_blocks(self, tmp_path):
        f = tmp_path / "x"
        f.write_text("y")
        sf = tmp_path / "state.json"
        # Pre-fill counter to the ceiling
        sf.write_text(json.dumps({"date": "2026-05-12", "actions": DAILY_ACTION_CEILING}))
        outcome = process_auto_delete_candidate(
            f,
            today=date(2026, 5, 12),
            quarantine_root=tmp_path / "q",
            undo_log=tmp_path / "undo.log",
            state_file=sf,
            open_check=_no_open,
            cooling_check=_no_cooling,
        )
        assert outcome == "skipped_ceiling"
        assert f.exists()

    def test_missing_returns_missing(self, tmp_path):
        outcome = process_auto_delete_candidate(
            tmp_path / "ghost.txt",
            today=date(2026, 5, 12),
            quarantine_root=tmp_path / "q",
            undo_log=tmp_path / "undo.log",
            state_file=tmp_path / "state.json",
            open_check=_no_open,
            cooling_check=_no_cooling,
        )
        assert outcome == "missing"


class TestListQuarantine:
    def test_returns_entries_with_sidecar_data(self, tmp_path):
        f = tmp_path / "src" / "a.txt"
        f.parent.mkdir()
        f.write_text("hello")
        move_to_quarantine(f, today=date(2026, 5, 1), root=tmp_path / "q")

        entries = list_quarantine(root=tmp_path / "q", today=date(2026, 5, 12))
        assert len(entries) == 1
        e = entries[0]
        assert e.original_path == tmp_path / "src" / "a.txt"
        assert e.quarantined_at == date(2026, 5, 1)
        assert e.age_days == 11
        assert e.days_until_purge == QUARANTINE_TTL_DAYS - 11

    def test_empty_root_returns_empty(self, tmp_path):
        assert list_quarantine(root=tmp_path / "nonexistent",
                               today=date(2026, 5, 12)) == []


class TestAgeOut:
    def test_old_entries_trashed(self, tmp_path):
        f = tmp_path / "src" / "old.txt"
        f.parent.mkdir()
        f.write_text("zzz")
        move_to_quarantine(f, today=date(2026, 1, 1), root=tmp_path / "q")

        trashed_calls = []
        def fake_trash(p):
            trashed_calls.append(p)

        purged = age_out(
            today=date(2026, 5, 12),
            ttl_days=QUARANTINE_TTL_DAYS,
            root=tmp_path / "q",
            undo_log=tmp_path / "undo.log",
            trash_fn=fake_trash,
        )
        assert len(purged) == 1
        assert len(trashed_calls) == 1

    def test_fresh_entries_kept(self, tmp_path):
        f = tmp_path / "src" / "fresh.txt"
        f.parent.mkdir()
        f.write_text("zzz")
        move_to_quarantine(f, today=date(2026, 5, 10), root=tmp_path / "q")

        purged = age_out(
            today=date(2026, 5, 12),
            ttl_days=QUARANTINE_TTL_DAYS,
            root=tmp_path / "q",
            undo_log=tmp_path / "undo.log",
            trash_fn=lambda p: None,
        )
        assert purged == []


class TestRestoreCommand:
    def test_well_formed_mv(self, tmp_path):
        e = QuarantineEntry(
            current_path=tmp_path / "q" / "a.txt",
            original_path=tmp_path / "src" / "a.txt",
            quarantined_at=date(2026, 5, 1),
            original_mtime=0.0,
        )
        cmd = restore_command(e)
        assert cmd.startswith("mv ")
        assert str(tmp_path / "q" / "a.txt") in cmd
        assert str(tmp_path / "src" / "a.txt") in cmd
