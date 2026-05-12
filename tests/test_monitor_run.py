"""
test_monitor_run.py - Test for monitor.run_once integration with quarantine.

Pure pytest unit test. Runs run_once() against tmp_path Downloads + Documents,
isolates the quarantine root + state file, and asserts the tally. Does not
install or invoke launchctl; only exercises Python functions.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import monitor
import quarantine


def _backdate(path: Path, days: int):
    when = (datetime.now() - timedelta(days=days)).timestamp()
    os.utime(path, (when, when))


class TestRunOnce:
    def test_quarantines_safe_candidates_only(self, tmp_path, monkeypatch):
        downloads = tmp_path / "Downloads"
        docs = tmp_path / "Documents"
        downloads.mkdir()
        docs.mkdir()

        (downloads / ".DS_Store").write_text("x")
        (downloads / "partial.crdownload").write_text("x")
        (downloads / "important.docx").write_text("real document")
        for f in downloads.iterdir():
            _backdate(f, 5)

        q_root = tmp_path / "q"
        undo = tmp_path / "undo.log"

        monkeypatch.setattr(quarantine, "is_file_open", lambda p: False)
        monkeypatch.setattr(quarantine, "STATE_FILE", tmp_path / "state.json")

        tally = monitor.run_once(
            docs, downloads,
            today=date(2026, 5, 12),
            now=datetime(2026, 5, 12, 10, 0, 0),
            quarantine_root=q_root,
            undo_log=undo,
        )

        assert (downloads / "important.docx").exists()
        assert tally["quarantined"] == 2
        assert not (downloads / ".DS_Store").exists()
        assert not (downloads / "partial.crdownload").exists()
