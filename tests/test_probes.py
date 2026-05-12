"""
test_probes.py - Mocked subprocess calls for TM/iCloud/portfolio probes.

All subprocess calls are patched; no real system calls are made.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from probes import (
    TMStatus,
    iCloudStatus,
    PortfolioStatus,
    probe_icloud,
    probe_portfolio,
    probe_time_machine,
    probe_eofy,
)


class TestTimeMachineProbe:
    def test_tm_unavailable_on_non_zero_exit(self):
        """tmutil patched to exit 1; TMStatus.available is False."""
        with patch("probes.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stderr = "No Time Machine destination configured"
            mock_result.stdout = ""
            mock_run.return_value = mock_result
            status = probe_time_machine()
        assert status.available is False

    def test_tm_available_on_success(self):
        """tmutil returning backup path; TMStatus.available is True."""
        backup_path = "/Volumes/Backup/Backups.backupdb/MyMac/2026-05-11-030000"

        call_count = {"n": 0}
        def side_effect(cmd, **kwargs):
            result = MagicMock()
            if "latestbackup" in cmd:
                result.returncode = 0
                result.stdout = backup_path
                result.stderr = ""
            else:
                result.returncode = 0
                result.stdout = "Running = 0\nPercent = -1\n"
                result.stderr = ""
            return result

        with patch("probes.subprocess.run", side_effect=side_effect):
            status = probe_time_machine()

        assert status.available is True
        assert status.latest_backup == backup_path


class TestICloudProbe:
    def test_icloud_caught_up(self):
        """brctl output with 'caught-up' and no 'needs-upload': status OK."""
        brctl_output = "com.apple.CloudDocs: caught-up\n"
        with patch("probes.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = brctl_output
            mock_result.stderr = ""
            mock_run.return_value = mock_result
            status = probe_icloud()
        assert status.caught_up is True
        assert status.needs_upload is False
        assert status.quota_exceeded is False

    def test_icloud_quota_exceeded(self):
        """brctl output containing CKErrorDomain:25 surfaces quota warning."""
        brctl_output = (
            "com.apple.CloudDocs: needs-upload\n"
            "Error: CKErrorDomain:25 quota exceeded\n"
        )
        with patch("probes.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = brctl_output
            mock_result.stderr = ""
            mock_run.return_value = mock_result
            status = probe_icloud()
        assert status.quota_exceeded is True
        assert status.needs_upload is True

    def test_icloud_brctl_unavailable(self):
        """FileNotFoundError from brctl returns iCloudStatus with available=False equivalent."""
        with patch("probes.subprocess.run", side_effect=FileNotFoundError("brctl not found")):
            status = probe_icloud()
        assert status.caught_up is False
        assert "unavailable" in status.detail.lower()


class TestPortfolioProbe:
    def test_portfolio_probe_absent_log(self, tmp_path):
        """Log path does not exist; returns None."""
        missing = tmp_path / "nonexistent" / "update.log"
        result = probe_portfolio(log_path=missing)
        assert result is None

    def test_portfolio_probe_last_line_success(self, tmp_path):
        """Log ends with 'OK'; returns None (no alert)."""
        log = tmp_path / "update.log"
        log.write_text("build started\nall checks passed\n2026-05-12 03:01 OK\n")
        result = probe_portfolio(log_path=log)
        assert result is None

    def test_portfolio_probe_last_line_failed(self, tmp_path):
        """Log ends with 'FAILED'; returns PortfolioStatus with detail."""
        log = tmp_path / "update.log"
        log.write_text("build started\n2026-05-12 03:01 FAILED - exit code 1\n")
        result = probe_portfolio(log_path=log)
        assert result is not None
        assert isinstance(result, PortfolioStatus)
        assert "FAILED" in result.last_status or "FAILED" in result.detail


class TestEOFYProbe:
    def test_eofy_returns_none_outside_may_june(self, tmp_path):
        """probe_eofy returns None for months outside May-June."""
        for month in [1, 2, 3, 4, 7, 8, 9, 10, 11, 12]:
            result = probe_eofy(date(2026, month, 1), docs_path=tmp_path)
            assert result is None, f"Expected None for month {month}"

    def test_eofy_returns_status_in_may(self, tmp_path):
        """probe_eofy returns EOFYStatus in May."""
        (tmp_path / "invoice_2026.pdf").touch()
        (tmp_path / "receipt_grocery.pdf").touch()
        result = probe_eofy(date(2026, 5, 12), docs_path=tmp_path)
        assert result is not None
        assert result.invoice_count >= 1
        assert result.receipt_count >= 1

    def test_eofy_returns_status_in_june(self, tmp_path):
        """probe_eofy returns EOFYStatus in June."""
        result = probe_eofy(date(2026, 6, 1), docs_path=tmp_path)
        assert result is not None
