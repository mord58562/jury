"""
test_classifier.py - Auto-delete safety and boundary condition tests.

Includes a defensive assertion test that no protected extension ever
appears in the AUTO_DELETE output (code-level invariant).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from classifier import (
    AUTO_DELETE_PATTERNS,
    PROTECTED_EXTENSIONS,
    Classification,
    classify,
    is_lock_file_safe,
)


class TestAutoDeletePatterns:
    def test_auto_delete_ds_store(self, tmp_path):
        """.DS_Store is classified AUTO_DELETE."""
        f = tmp_path / ".DS_Store"
        f.touch()
        assert classify(f) == Classification.AUTO_DELETE

    def test_auto_delete_lock_file_when_safe(self, tmp_path):
        """~$file.docx with lsof returning no open handle is AUTO_DELETE."""
        f = tmp_path / "~$my document.docx"
        f.touch()
        with patch("classifier.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = ""
            mock_result.returncode = 0
            mock_run.return_value = mock_result
            result = classify(f)
        assert result == Classification.AUTO_DELETE

    def test_lock_file_skipped_when_parent_open(self, tmp_path):
        """~$file.docx with lsof returning a hit is SURFACE_ONLY (not deleted)."""
        f = tmp_path / "~$open doc.docx"
        f.touch()
        with patch("classifier.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = "Word  12345  rob  17r  REG  disk0s1  4096  /path/~$open doc.docx"
            mock_result.returncode = 0
            mock_run.return_value = mock_result
            result = classify(f)
        assert result == Classification.SURFACE_ONLY

    def test_crdownload_auto_delete(self, tmp_path):
        """Partial download .crdownload is classified AUTO_DELETE."""
        f = tmp_path / "somefile.crdownload"
        f.touch()
        assert classify(f) == Classification.AUTO_DELETE

    def test_download_auto_delete(self, tmp_path):
        """.download partial file is classified AUTO_DELETE."""
        f = tmp_path / "somefile.download"
        f.touch()
        assert classify(f) == Classification.AUTO_DELETE

    def test_whitelist_is_explicit(self, tmp_path):
        """Unknown extension .xyz that does not match auto-delete patterns is IGNORE."""
        f = tmp_path / "mystery.xyz"
        f.touch()
        assert classify(f) == Classification.IGNORE


class TestProtectedExtensions:
    def test_protected_extension_docx(self, tmp_path):
        """.docx file is SURFACE_ONLY regardless of name."""
        f = tmp_path / "important.docx"
        f.touch()
        assert classify(f) == Classification.SURFACE_ONLY

    def test_protected_extension_pdf(self, tmp_path):
        """.pdf file is SURFACE_ONLY."""
        f = tmp_path / "receipt.pdf"
        f.touch()
        assert classify(f) == Classification.SURFACE_ONLY

    def test_protected_extension_pages(self, tmp_path):
        """.pages file is SURFACE_ONLY."""
        f = tmp_path / "notes.pages"
        f.touch()
        assert classify(f) == Classification.SURFACE_ONLY

    def test_protected_extension_xlsx(self, tmp_path):
        """.xlsx file is SURFACE_ONLY."""
        f = tmp_path / "spreadsheet.xlsx"
        f.touch()
        assert classify(f) == Classification.SURFACE_ONLY

    def test_protected_extension_pptx(self, tmp_path):
        """.pptx file is SURFACE_ONLY."""
        f = tmp_path / "slides.pptx"
        f.touch()
        assert classify(f) == Classification.SURFACE_ONLY

    def test_protected_extension_rtf(self, tmp_path):
        """.rtf file is SURFACE_ONLY."""
        f = tmp_path / "notes.rtf"
        f.touch()
        assert classify(f) == Classification.SURFACE_ONLY

    def test_no_protected_extension_in_auto_delete(self, tmp_path):
        """Defensive assertion: no non-lock-file with a protected extension classifies AUTO_DELETE.

        This verifies the code-level invariant in classifier.py:
        PROTECTED_EXTENSIONS is a hard gate for document files.

        Note: Lock files (prefix ~$) are a special case - they are transient scratch
        files created by Microsoft Office and are NOT documents themselves, even though
        they carry a protected extension (e.g. ~$report.docx has extension .docx but
        is not the report). Lock files are auto-deletable via the lsof gate in classify().

        This test verifies that regular document files - those without the ~$ prefix -
        with protected extensions are never auto-deleted.
        """
        auto_delete_results = []
        for ext in PROTECTED_EXTENSIONS:
            # Use a plain document name (no lock-file prefix)
            f = tmp_path / f"real_document{ext}"
            f.touch()
            result = classify(f)
            if result == Classification.AUTO_DELETE:
                auto_delete_results.append((str(f), ext))

        assert auto_delete_results == [], (
            f"INVARIANT VIOLATED: document files with protected extensions "
            f"classified as AUTO_DELETE: {auto_delete_results}"
        )
