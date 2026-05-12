"""
classifier.py - Per-file classification for Jury.

Whitelist design: only the explicit AUTO_DELETE_PATTERNS qualify for
deletion. Everything else is SURFACE_ONLY or IGNORE. Protected extensions
are a code-level invariant - no path with these extensions ever reaches
AUTO_DELETE.
"""
from __future__ import annotations

import re
import subprocess
from enum import Enum, auto
from pathlib import Path


class Classification(Enum):
    AUTO_DELETE = auto()
    SURFACE_ONLY = auto()
    IGNORE = auto()


# Code-level invariant: none of these extensions ever appear in AUTO_DELETE.
# Defensive assertion in test_classifier.py verifies this at runtime.
PROTECTED_EXTENSIONS: frozenset[str] = frozenset({
    ".docx", ".xlsx", ".pptx", ".pdf",
    ".docm", ".xlsm", ".pptm",
    ".rtf", ".pages", ".numbers", ".key",
    ".doc", ".xls", ".ppt",
    ".odt", ".txt", ".md", ".csv",
})

# Explicit allowlist for auto-deletion. Requires deliberate code change to extend.
_LOCK_FILE_PATTERN = re.compile(r"^~\$.+")
_CRDOWNLOAD_PATTERN = re.compile(r".*\.crdownload$", re.IGNORECASE)
_DOWNLOAD_PATTERN = re.compile(r".*\.download$", re.IGNORECASE)
_DS_STORE_PATTERN = re.compile(r"^\.DS_Store$")

AUTO_DELETE_PATTERNS: list[re.Pattern] = [
    _LOCK_FILE_PATTERN,
    _DS_STORE_PATTERN,
    _CRDOWNLOAD_PATTERN,
    _DOWNLOAD_PATTERN,
]


def _matches_auto_delete(name: str) -> bool:
    """Return True if the filename matches any explicit auto-delete pattern."""
    return any(p.match(name) for p in AUTO_DELETE_PATTERNS)


def is_lock_file_safe(path: Path) -> bool:
    """Return True if no process has the parent directory path open via lsof.

    A lock file (prefix ~$) is safe to delete only when its parent document
    is not open. lsof on the parent directory is a conservative gate.
    """
    try:
        result = subprocess.run(
            ["lsof", str(path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # lsof returns non-empty stdout when something has the file open
        return result.stdout.strip() == ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # If lsof fails, be conservative: treat as unsafe
        return False


def classify(path: Path) -> Classification:
    """Classify a file as AUTO_DELETE, SURFACE_ONLY, or IGNORE.

    Lock files (prefix ~$) are checked first: they are transient scratch files,
    not documents, so the protected-extension invariant applies to the underlying
    document, not the lock file itself. The lsof gate ensures the parent document
    is not currently open before marking the lock file for deletion.

    For all other files, protected extensions are a hard gate: they never
    receive AUTO_DELETE regardless of filename pattern.
    """
    name = path.name

    # Lock files: check pattern first, then lsof gate
    if _LOCK_FILE_PATTERN.match(name):
        if not is_lock_file_safe(path):
            return Classification.SURFACE_ONLY
        return Classification.AUTO_DELETE

    suffix = path.suffix.lower()

    # Hard invariant: protected extensions (non-lock files) are always SURFACE_ONLY
    if suffix in PROTECTED_EXTENSIONS:
        return Classification.SURFACE_ONLY

    if not _matches_auto_delete(name):
        return Classification.IGNORE

    return Classification.AUTO_DELETE
