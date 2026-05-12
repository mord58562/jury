"""
conftest.py - pytest fixtures for Jury tests.

Creates zero-byte synthetic file trees mirroring RECON Clusters A through J.
All fixtures use tmp_path so they are isolated per test.

CRITICAL: no fixture touches ~/Documents or ~/Downloads.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def touch(path: Path) -> Path:
    """Create a zero-byte file at path, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


# ---------------------------------------------------------------------------
# Cluster fixtures (one per RECON cluster shape)
# ---------------------------------------------------------------------------

@pytest.fixture()
def cluster_a_docs(tmp_path: Path) -> Path:
    """Cluster A - Acme Timetable UUID + integer variants (~/Documents shape, 5 members)."""
    docs = tmp_path / "Documents"
    for name in [
        "2025 Y3 Acme Student Timetables_Canvas-1.xlsx",
        "2025 Y3 Acme Student Timetables_Canvas-3.xlsx",
        "2025 Y3 Acme Student Timetables_Canvas-164bc587-d530-4b82-bb95-0a1c78bccbfc.xlsx",
        "2025 Y3 Acme Student Timetables_Canvas-6e47dbf5-fc1a-4893-9eb6-f13b64630369.xlsx",
        "2025 Y3 Acme Student Timetables_Canvas-8ff5cb3b-0917-4628-88df-0608bf373f61.xlsx",
    ]:
        touch(docs / name)
    return docs


@pytest.fixture()
def cluster_b_downloads(tmp_path: Path) -> Path:
    """Cluster B - Acme Timetable downloads: URL-encoded, UUID, (n) variants."""
    dl = tmp_path / "Downloads"
    names = [
        "2025+Y3+Acme+Student+Timetables_Canvas.xlsx",
        "2025 Y3 Acme Student Timetables_Canvas.xlsx",
        "2025 Y3 Acme Student Timetables_Canvas-164bc587-d530-4b82-bb95-0a1c78bccbfc.xlsx",
        "2025 Y3 Acme Student Timetables_Canvas-164bc587-d530-4b82-bb95-0a1c78bccbfc (1).xlsx",
        "2025 Y3 Acme Student Timetables_Canvas-6e47dbf5-fc1a-4893-9eb6-f13b64630369.xlsx",
        "2025 Y3 Acme Student Timetables_Canvas-6e47dbf5-fc1a-4893-9eb6-f13b64630369 (1).xlsx",
    ]
    for name in names:
        touch(dl / name)
    return dl


@pytest.fixture()
def cluster_c_downloads(tmp_path: Path) -> Path:
    """Cluster C - Y2 Timetable (n) copies; date segment must not be stripped."""
    dl = tmp_path / "Downloads"
    names = [
        "Y2 Timetable 10.7 - 11.10 (doe 11.9).xlsx",
        "Y2 Timetable 10.7 - 11.10 (doe 11.9) (1).xlsx",
        "Y2 Timetable 10.7 - 11.10 (doe 11.9) (2).xlsx",
        "Y2 Timetable 10.7 - 11.10 (doe 11.9) (3).xlsx",
    ]
    for name in names:
        touch(dl / name)
    return dl


@pytest.fixture()
def cluster_d_docs(tmp_path: Path) -> Path:
    """Cluster D - Weekly report 'final' variants."""
    docs = tmp_path / "Documents"
    names = [
        "y3 weekly report final.docx",
        "y3 weekly report final dot point version.docx",
        "marc report.docx",
        "Jane Doe s9999999 y3 weekly report.docx",
    ]
    for name in names:
        touch(docs / name)
    return docs


@pytest.fixture()
def cluster_e_cross(tmp_path: Path) -> tuple[Path, Path]:
    """Cluster E - SASB 'old -' prefix cross-directory cluster."""
    docs = tmp_path / "Documents"
    dl = tmp_path / "Downloads"
    old_names = [
        "old - XX 24-01-15 - SASB - Cat 5 A.pdf",
        "old - XX 24-01-15 - SASB - Cat 5 B.pdf",
        "old - XX 24-01-15 - SASB - Cat 5 C.pdf",
        "old - XX 24-01-15 - SASB - Cat 5 D.pdf",
    ]
    new_names = [
        "XX 24-01-15 - SASB - Cat 5 A.pdf",
        "XX 24-01-15 - SASB - Cat 5 B.pdf",
        "XX 24-01-15 - SASB - Cat 5 C.pdf",
        "XX 24-01-15 - SASB - Cat 5 D.pdf",
    ]
    for name in old_names:
        touch(docs / name)
    for name in new_names:
        touch(dl / name)
    return docs, dl


@pytest.fixture()
def cluster_f_docs(tmp_path: Path) -> Path:
    """Cluster F - Certificate year-suffix variants."""
    docs = tmp_path / "Documents"
    names = [
        "Certificate - Jane Doe.pdf",
        "Certificate - Jane Doe 2026.pdf",
    ]
    for name in names:
        touch(docs / name)
    return docs


@pytest.fixture()
def cluster_g_docs(tmp_path: Path) -> Path:
    """Cluster G - Research Proposal docx + pdf same-base pair."""
    docs = tmp_path / "Documents"
    names = [
        "Research Proposal.docx",
        "Research Proposal.pdf",
    ]
    for name in names:
        touch(docs / name)
    return docs


@pytest.fixture()
def cluster_h_downloads(tmp_path: Path) -> Path:
    """Cluster H - Application-letter typo variant (should NOT cluster)."""
    dl = tmp_path / "Downloads"
    names = [
        "letter of motivation for Acme Aplication (English).docx",
        "letter of motivation for Acme Application  draft.docx",
    ]
    for name in names:
        touch(dl / name)
    return dl


@pytest.fixture()
def cluster_i_downloads(tmp_path: Path) -> Path:
    """Cluster I - Survey drafts: early draft, draft, final."""
    dl = tmp_path / "Downloads"
    names = [
        "pre-post intervention survey early draft.docx",
        "pre-post intervention survey draft.docx",
        "pre-post intervention survey.docx",
    ]
    for name in names:
        touch(dl / name)
    return dl


@pytest.fixture()
def cluster_j_downloads(tmp_path: Path) -> Path:
    """Cluster J - NextDNS mobileconfig (n) duplicates."""
    dl = tmp_path / "Downloads"
    names = [
        "nextdns.mobileconfig",
        "nextdns (1).mobileconfig",
        "nextdns (2).mobileconfig",
        "nextdns (3).mobileconfig",
    ]
    for name in names:
        touch(dl / name)
    return dl


# ---------------------------------------------------------------------------
# Generic fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def empty_docs(tmp_path: Path) -> Path:
    """Empty Documents directory."""
    docs = tmp_path / "Documents"
    docs.mkdir(parents=True, exist_ok=True)
    return docs


@pytest.fixture()
def empty_downloads(tmp_path: Path) -> Path:
    """Empty Downloads directory."""
    dl = tmp_path / "Downloads"
    dl.mkdir(parents=True, exist_ok=True)
    return dl


@pytest.fixture()
def nested_project_docs(tmp_path: Path) -> Path:
    """Documents dir with a nested project tree that scanner must NOT descend into.

    Mirrors the real-world hazard: ~/Documents containing a checked-out repo,
    an app bundle, or a conda env. Only the top-level files should be picked
    up by the scanner.
    """
    docs = tmp_path / "Documents"
    touch(docs / "report.pdf")
    touch(docs / "report (1).pdf")
    touch(docs / "myproject/README.md")
    touch(docs / "myproject/LICENSE")
    touch(docs / "myproject/src/__init__.py")
    touch(docs / "myproject/.git/HEAD")
    touch(docs / "Some App.app/Contents/Info.plist")
    touch(docs / "Some App.app/Contents/README.md")
    touch(docs / ".hidden/secret.txt")
    return docs


@pytest.fixture()
def auto_delete_dir(tmp_path: Path) -> Path:
    """Directory with auto-deletable files for classifier tests."""
    d = tmp_path / "misc"
    d.mkdir(parents=True, exist_ok=True)
    touch(d / ".DS_Store")
    touch(d / "~$my document.docx")
    touch(d / "partial.crdownload")
    touch(d / "other.download")
    # A protected file that must NOT be auto-deleted
    touch(d / "important.docx")
    return d
