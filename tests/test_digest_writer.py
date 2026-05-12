"""
test_digest_writer.py - Markdown output shape, link format, overwrite idempotency.

Includes an explicit U+2014 codepoint check (not a visual scan) per the
em-dash audit protocol.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from digest_writer import (
    render_action_tally,
    render_clusters,
    render_eofy,
    render_icloud_status,
    render_portfolio,
    render_quarantine,
    render_stale_downloads,
    render_tm_status,
    write_digest,
)
from probes import EOFYStatus, PortfolioStatus, TMStatus, iCloudStatus
from scanner import Cluster, FileInfo, ScanResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scan_result(tmp_path: Path) -> ScanResult:
    """Create a minimal ScanResult with one cluster for testing."""
    f1 = tmp_path / "report-1.docx"
    f2 = tmp_path / "report-2.docx"
    f1.touch()
    f2.touch()
    from scanner import cluster_key
    fi1 = FileInfo(path=f1, name=f1.name, stem=f1.stem, suffix=".docx", key=cluster_key(f1.name))
    fi2 = FileInfo(path=f2, name=f2.name, stem=f2.stem, suffix=".docx", key=cluster_key(f2.name))
    cluster = Cluster(key="report", members=[fi1, fi2])
    return ScanResult(clusters=[cluster], auto_delete_candidates=[], all_files=[fi1, fi2])


def _make_empty_probes():
    tm = TMStatus(available=False, detail="drive offline")
    icloud = iCloudStatus(caught_up=True, needs_upload=False, quota_exceeded=False)
    return tm, icloud


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDigestWriter:
    def test_digest_overwrites_existing(self, tmp_path):
        """Writing twice produces only the second run's content."""
        output = tmp_path / "digest.md"
        scan = _make_scan_result(tmp_path)
        tm, icloud = _make_empty_probes()
        today = date(2026, 5, 12)

        write_digest(scan, tm, icloud, None, None, output, generated_at=today, dry_run=True)
        first_content = output.read_text()

        # Write again with a different date to distinguish
        today2 = date(2026, 5, 19)
        write_digest(scan, tm, icloud, None, None, output, generated_at=today2, dry_run=True)
        second_content = output.read_text()

        assert "2026-05-12" not in second_content, "Overwrite did not replace first content"
        assert "2026-05-19" in second_content

    def test_clusters_section_present(self, tmp_path):
        """Output contains 'Filename Clusters' heading."""
        output = tmp_path / "digest.md"
        scan = _make_scan_result(tmp_path)
        tm, icloud = _make_empty_probes()
        write_digest(scan, tm, icloud, None, None, output, generated_at=date(2026, 3, 1), dry_run=True)
        content = output.read_text()
        assert "Filename Clusters" in content

    def test_action_tally_count_no_names(self, tmp_path):
        """Monitor Action Tally section shows counts; no individual filenames."""
        output = tmp_path / "digest.md"
        scan = _make_scan_result(tmp_path)
        f = tmp_path / ".DS_Store"
        f.touch()
        fi = FileInfo(path=f, name=".DS_Store", stem=".DS_Store", suffix="", key="")
        scan.auto_delete_candidates.append(fi)

        tm, icloud = _make_empty_probes()
        write_digest(
            scan, tm, icloud, None, None, output,
            generated_at=date(2026, 3, 1),
            dry_run=True,
            action_tally={"quarantined": 1},
        )
        content = output.read_text()
        assert "would quarantine: 1 file(s)" in content
        section_lines = []
        in_section = False
        for line in content.splitlines():
            if "Monitor Action Tally" in line:
                in_section = True
            elif line.startswith("## ") and in_section:
                break
            if in_section:
                section_lines.append(line)
        section_text = "\n".join(section_lines)
        assert str(tmp_path) not in section_text, (
            "Action tally section must not list individual file paths"
        )

    def test_eofy_section_omitted_in_march(self, tmp_path):
        """EOFY probe returns None in March; section is absent from digest."""
        output = tmp_path / "digest.md"
        scan = _make_scan_result(tmp_path)
        tm, icloud = _make_empty_probes()
        write_digest(scan, tm, icloud, None, None, output, generated_at=date(2026, 3, 1), dry_run=True)
        content = output.read_text()
        assert "EOFY" not in content

    def test_eofy_section_present_in_may(self, tmp_path):
        """EOFY probe returns tally in May; section present."""
        output = tmp_path / "digest.md"
        scan = _make_scan_result(tmp_path)
        tm, icloud = _make_empty_probes()
        eofy = EOFYStatus(
            invoice_count=3,
            receipt_count=1,
            tax_count=2,
            docs_path=Path("/tmp"),
            detail="test",
        )
        write_digest(scan, tm, icloud, eofy, None, output, generated_at=date(2026, 5, 12), dry_run=True)
        content = output.read_text()
        assert "EOFY" in content
        assert "3" in content  # invoice count

    def test_portfolio_section_omitted_on_success(self, tmp_path):
        """Portfolio probe returns None on success; section absent."""
        output = tmp_path / "digest.md"
        scan = _make_scan_result(tmp_path)
        tm, icloud = _make_empty_probes()
        write_digest(scan, tm, icloud, None, None, output, generated_at=date(2026, 3, 1), dry_run=True)
        content = output.read_text()
        assert "Portfolio Status" not in content

    def test_portfolio_section_present_on_failure(self, tmp_path):
        """Portfolio probe returns PortfolioStatus on failure; section present."""
        output = tmp_path / "digest.md"
        scan = _make_scan_result(tmp_path)
        tm, icloud = _make_empty_probes()
        portfolio = PortfolioStatus(last_status="FAILED", detail="build error")
        write_digest(scan, tm, icloud, None, portfolio, output, generated_at=date(2026, 3, 1), dry_run=True)
        content = output.read_text()
        assert "Portfolio Status" in content
        assert "FAILED" in content

    def test_trash_command_in_cluster_entry(self, tmp_path):
        """Each cluster member entry contains the osascript paste command."""
        output = tmp_path / "digest.md"
        scan = _make_scan_result(tmp_path)
        tm, icloud = _make_empty_probes()
        write_digest(scan, tm, icloud, None, None, output, generated_at=date(2026, 3, 1), dry_run=True)
        content = output.read_text()
        assert "osascript" in content
        assert "Finder" in content

    def test_no_em_dashes_in_output(self, tmp_path):
        """Assert chr(0x2014) (U+2014 em-dash) is not in digest output.

        This is a codepoint check, not a visual scan.
        """
        output = tmp_path / "digest.md"
        scan = _make_scan_result(tmp_path)
        tm, icloud = _make_empty_probes()
        eofy = EOFYStatus(invoice_count=1, receipt_count=0, tax_count=0, docs_path=Path("/tmp"))
        portfolio = PortfolioStatus(last_status="FAILED", detail="something went wrong")
        write_digest(
            scan, tm, icloud, eofy, portfolio, output,
            generated_at=date(2026, 5, 12),
            dry_run=True,
        )
        content = output.read_text()
        em_dash = chr(0x2014)
        assert em_dash not in content, (
            f"Em-dash (U+2014) detected in digest output. "
            f"Found at positions: {[i for i, c in enumerate(content) if c == em_dash]}"
        )

    def test_icloud_quota_note_in_header(self, tmp_path):
        """When iCloud quota is exceeded, a note appears near the top of the digest."""
        output = tmp_path / "digest.md"
        scan = _make_scan_result(tmp_path)
        tm = TMStatus(available=False)
        icloud = iCloudStatus(caught_up=False, needs_upload=True, quota_exceeded=True)
        write_digest(scan, tm, icloud, None, None, output, generated_at=date(2026, 5, 12), dry_run=True)
        content = output.read_text()
        assert "quota" in content.lower() or "iCloud quota" in content

    def test_clusters_sorted_by_reclaimable_bytes_desc(self, tmp_path):
        """Cluster ordering in output: larger reclaimable bytes appear first."""
        small_a = FileInfo(path=tmp_path / "small1.pdf", name="small1.pdf",
                           stem="small1", suffix=".pdf", key="small", size=100)
        small_b = FileInfo(path=tmp_path / "small2.pdf", name="small2.pdf",
                           stem="small2", suffix=".pdf", key="small", size=100)
        big_a = FileInfo(path=tmp_path / "big1.pdf", name="big1.pdf",
                         stem="big1", suffix=".pdf", key="big", size=10_000_000)
        big_b = FileInfo(path=tmp_path / "big2.pdf", name="big2.pdf",
                         stem="big2", suffix=".pdf", key="big", size=10_000_000)
        small_cluster = Cluster(key="small", members=[small_a, small_b])
        big_cluster = Cluster(key="big", members=[big_a, big_b])
        scan = ScanResult(
            clusters=[small_cluster, big_cluster],
            auto_delete_candidates=[],
            all_files=[small_a, small_b, big_a, big_b],
        )
        tm, icloud = _make_empty_probes()
        output = tmp_path / "digest.md"
        write_digest(scan, tm, icloud, None, None, output, generated_at=date(2026, 5, 12), dry_run=True)
        content = output.read_text()
        big_pos = content.find("Cluster: `big`")
        small_pos = content.find("Cluster: `small`")
        assert big_pos != -1 and small_pos != -1
        assert big_pos < small_pos, "Larger cluster must render before smaller cluster"

    def test_byte_identical_annotation_in_cluster(self, tmp_path):
        """When cluster members are byte-identical, render a bulk-trash hint."""
        a = tmp_path / "a-1.pdf"
        b = tmp_path / "a-2.pdf"
        a.write_bytes(b"same content here")
        b.write_bytes(b"same content here")
        fi_a = FileInfo(path=a, name="a-1.pdf", stem="a-1", suffix=".pdf",
                        key="a", size=a.stat().st_size)
        fi_b = FileInfo(path=b, name="a-2.pdf", stem="a-2", suffix=".pdf",
                        key="a", size=b.stat().st_size)
        cluster = Cluster(key="a", members=[fi_a, fi_b])
        scan = ScanResult(clusters=[cluster], auto_delete_candidates=[],
                          all_files=[fi_a, fi_b])
        tm, icloud = _make_empty_probes()
        output = tmp_path / "digest.md"
        write_digest(scan, tm, icloud, None, None, output, generated_at=date(2026, 5, 12), dry_run=True)
        content = output.read_text()
        assert "Byte-identical" in content
        assert "Keeper" in content
        assert "Trash the rest" in content

    def test_stale_section_present(self, tmp_path):
        """Stale Downloads section renders entries with name, size, last-opened."""
        from stale import StaleFile
        f = tmp_path / "old.pdf"
        f.write_bytes(b"x")
        stale = [StaleFile(path=f, size=1024, mtime=date(2025, 1, 1), last_used=None)]
        scan = _make_scan_result(tmp_path)
        tm, icloud = _make_empty_probes()
        output = tmp_path / "digest.md"
        write_digest(scan, tm, icloud, None, None, output,
                     generated_at=date(2026, 5, 12), dry_run=True,
                     stale_downloads=stale)
        content = output.read_text()
        assert "Stale Downloads" in content
        assert "old.pdf" in content
        assert "never" in content.lower()

    def test_stale_section_absent_when_empty(self, tmp_path):
        """With no stale files, section still renders but says none detected."""
        scan = _make_scan_result(tmp_path)
        tm, icloud = _make_empty_probes()
        output = tmp_path / "digest.md"
        write_digest(scan, tm, icloud, None, None, output,
                     generated_at=date(2026, 5, 12), dry_run=True,
                     stale_downloads=[])
        content = output.read_text()
        assert "No stale downloads detected" in content

    def test_top_n_cluster_cap_collapses_remainder(self, tmp_path):
        """When more than TOP_N_CLUSTERS exist, only top N render fully; rest collapse."""
        from digest_writer import TOP_N_CLUSTERS
        clusters = []
        files = []
        # TOP_N_CLUSTERS + 5 distinct clusters
        for i in range(TOP_N_CLUSTERS + 5):
            a = FileInfo(path=tmp_path / f"k{i}_a.pdf", name=f"k{i}_a.pdf",
                         stem=f"k{i}_a", suffix=".pdf", key=f"k{i}", size=1000 + i)
            b = FileInfo(path=tmp_path / f"k{i}_b.pdf", name=f"k{i}_b.pdf",
                         stem=f"k{i}_b", suffix=".pdf", key=f"k{i}", size=1000 + i)
            clusters.append(Cluster(key=f"k{i}", members=[a, b]))
            files.extend([a, b])
        scan = ScanResult(clusters=clusters, auto_delete_candidates=[], all_files=files)
        tm, icloud = _make_empty_probes()
        output = tmp_path / "digest.md"
        write_digest(scan, tm, icloud, None, None, output, generated_at=date(2026, 5, 12), dry_run=True)
        content = output.read_text()
        rendered_clusters = content.count("### Cluster:")
        assert rendered_clusters == TOP_N_CLUSTERS, (
            f"Expected {TOP_N_CLUSTERS} cluster headings rendered, got {rendered_clusters}"
        )
        assert "5 more cluster" in content, "Tail-collapse summary missing"
