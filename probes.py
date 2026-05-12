"""
probes.py - Thin subprocess wrappers for system status checks.

All probes are best-effort: a non-zero exit or missing tool returns a
"unavailable" status rather than raising an exception. digest.py always
exits 0; probe failures surface as informational notes in the digest.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional


_EOFY_SKIP_DIR_NAMES = frozenset({
    "node_modules", "__pycache__", ".git", ".hg", ".svn",
    ".venv", "venv", "env", "miniconda3", "anaconda3",
    ".Trash", "Library", ".cache", ".npm", ".yarn", ".cargo",
    ".rustup", ".gradle", ".m2", "site-packages",
    "build", "dist", ".tox", ".pytest_cache", ".mypy_cache",
    ".idea", ".vscode", ".DS_Store",
})

_EOFY_SKIP_DIR_SUFFIXES = (".app", ".framework", ".bundle", ".xcodeproj", ".photoslibrary")


@dataclass
class TMStatus:
    available: bool
    latest_backup: Optional[str] = None  # ISO string or human label
    running: bool = False
    detail: str = ""


@dataclass
class iCloudStatus:
    caught_up: bool
    needs_upload: bool
    quota_exceeded: bool
    detail: str = ""


@dataclass
class EOFYStatus:
    invoice_count: int
    receipt_count: int
    tax_count: int
    docs_path: Path = Path("/")
    detail: str = ""


@dataclass
class PortfolioStatus:
    last_status: str
    detail: str = ""


def probe_time_machine() -> TMStatus:
    """Run tmutil latestbackup and tmutil status; return TMStatus.

    Non-zero exit (drive not mounted) sets available=False.
    """
    # Try latestbackup first
    try:
        lb_result = subprocess.run(
            ["tmutil", "latestbackup"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if lb_result.returncode != 0:
            return TMStatus(available=False, detail=lb_result.stderr.strip())
        latest = lb_result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return TMStatus(available=False, detail=str(e))

    # Try status
    try:
        st_result = subprocess.run(
            ["tmutil", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        running = "Running = 1" in st_result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        running = False

    return TMStatus(available=True, latest_backup=latest, running=running)


def probe_icloud() -> iCloudStatus:
    """Run brctl status and parse for sync state.

    Looks for com.apple.CloudDocs caught-up / needs-upload / CKErrorDomain:25.
    """
    try:
        result = subprocess.run(
            ["brctl", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = result.stdout + result.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return iCloudStatus(
            caught_up=False,
            needs_upload=False,
            quota_exceeded=False,
            detail=f"brctl unavailable: {e}",
        )

    caught_up = "caught-up" in output
    needs_upload = "needs-upload" in output
    quota_exceeded = "CKErrorDomain:25" in output

    detail_parts = []
    if quota_exceeded:
        detail_parts.append("iCloud quota exceeded (CKErrorDomain:25)")
    if needs_upload:
        detail_parts.append("files pending upload")

    return iCloudStatus(
        caught_up=caught_up,
        needs_upload=needs_upload,
        quota_exceeded=quota_exceeded,
        detail="; ".join(detail_parts),
    )


def probe_eofy(today: date, docs_path: Optional[Path] = None) -> Optional[EOFYStatus]:
    """Return EOFY invoice tally if today is in May or June; else None.

    Walks docs_path recursively for filenames matching invoice|receipt|tax
    (case-insensitive), skipping nested project trees, app bundles, conda
    envs, dotted dirs, and other noise sources.
    """
    if today.month not in (5, 6):
        return None

    scan_root = docs_path or Path.home() / "Documents"
    invoice_count = 0
    receipt_count = 0
    tax_count = 0

    if scan_root.exists():
        for root, dirs, files in os.walk(scan_root):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
                and d not in _EOFY_SKIP_DIR_NAMES
                and not d.endswith(_EOFY_SKIP_DIR_SUFFIXES)
            ]
            for fname in files:
                lower_name = fname.lower()
                if "invoice" in lower_name:
                    invoice_count += 1
                elif "receipt" in lower_name:
                    receipt_count += 1
                elif "tax" in lower_name:
                    tax_count += 1

    return EOFYStatus(
        invoice_count=invoice_count,
        receipt_count=receipt_count,
        tax_count=tax_count,
        docs_path=scan_root,
        detail=f"Scanned {scan_root}",
    )


def probe_portfolio(log_path: Optional[Path] = None) -> Optional[PortfolioStatus]:
    """Read the last line of ~/portfolio/update.log.

    Returns None if the log is absent or the last run succeeded (ends with OK).
    Returns PortfolioStatus if the last run failed (ends with FAILED or similar).
    """
    path = log_path or (Path.home() / "portfolio" / "update.log")
    if not path.exists():
        return None

    try:
        last_line = ""
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    last_line = stripped
        if not last_line:
            return None
        if "OK" in last_line.upper() or last_line.upper().endswith("OK"):
            return None
        return PortfolioStatus(last_status=last_line, detail=f"Last log line: {last_line}")
    except OSError as e:
        return PortfolioStatus(last_status="error", detail=str(e))
