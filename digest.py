"""
digest.py - Sunday review entry point for Jury.

Orchestrates scanner, classifier, probes, quarantine, and digest_writer in
sequence. Auto-delete candidates are routed through the quarantine pipeline
(same code path as the always-active monitor), so Sunday and weekday runs
share identical safety gates: cooling window, lsof check, daily ceiling.

Always exits 0 (errors surface in digest/log, never crash the LaunchAgent).

Environment variables:
  JURY_DRY_RUN=1 - suppress quarantine moves; write digest to /tmp/ instead.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path

from classifier import Classification, classify
from digest_writer import write_digest
from probes import (
    probe_eofy,
    probe_icloud,
    probe_portfolio,
    probe_time_machine,
)
from quarantine import list_quarantine, process_auto_delete_candidate
from scanner import scan_dirs
from stale import find_stale_downloads


DRY_RUN = os.environ.get("JURY_DRY_RUN", "0") == "1"

DEFAULT_DOCS = Path.home() / "Documents"
DEFAULT_DOWNLOADS = Path.home() / "Downloads"
DEFAULT_OUTPUT = (
    Path("/tmp/jury-digest.md")
    if DRY_RUN
    else Path.home() / "Documents" / "cleanup-digest.md"
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Jury - weekly file cleanup digest"
    )
    parser.add_argument("--docs", type=Path, default=DEFAULT_DOCS,
                        help="Path to Documents directory")
    parser.add_argument("--downloads", type=Path, default=DEFAULT_DOWNLOADS,
                        help="Path to Downloads directory")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="Output path for the digest markdown file")
    return parser.parse_args()


def _process_candidates(candidates, dry_run: bool) -> dict:
    """Send each AUTO_DELETE candidate through the quarantine pipeline.

    Returns a tally dict keyed by outcome (quarantined, skipped_cooling,
    skipped_open, skipped_ceiling, missing, failed). In dry-run mode no
    files are moved - just the count of files that would have been.
    """
    tally = {
        "quarantined": 0,
        "skipped_cooling": 0,
        "skipped_open": 0,
        "skipped_ceiling": 0,
        "failed": 0,
        "missing": 0,
    }
    today = date.today()
    now = datetime.now()
    for fi in candidates:
        if classify(fi.path) != Classification.AUTO_DELETE:
            continue
        if dry_run:
            tally["quarantined"] += 1
            continue
        outcome = process_auto_delete_candidate(fi.path, today=today, now=now)
        tally[outcome] = tally.get(outcome, 0) + 1
    return tally


def main() -> None:
    args = _parse_args()

    output_path = args.output
    if DRY_RUN and args.output == DEFAULT_OUTPUT and DEFAULT_OUTPUT != Path("/tmp/jury-digest.md"):
        output_path = Path("/tmp/jury-digest.md")

    try:
        scan_result = scan_dirs(args.docs, args.downloads)
    except Exception as e:
        print(f"jury: scan failed: {e}", file=sys.stderr)
        sys.exit(0)

    tally = _process_candidates(scan_result.auto_delete_candidates, dry_run=DRY_RUN)

    tm_status = probe_time_machine()
    icloud_status = probe_icloud()
    eofy_status = probe_eofy(date.today(), docs_path=args.docs)
    portfolio_status = probe_portfolio()

    clustered_paths = {
        fi.path for cluster in scan_result.clusters for fi in cluster.members
    }
    stale_downloads = find_stale_downloads(
        scan_result.downloads_files,
        clustered_paths,
        date.today(),
    )

    quarantine_entries = list_quarantine(today=date.today())

    try:
        write_digest(
            scan_result=scan_result,
            tm_status=tm_status,
            icloud_status=icloud_status,
            eofy_status=eofy_status,
            portfolio_status=portfolio_status,
            stale_downloads=stale_downloads,
            quarantine_entries=quarantine_entries,
            output_path=output_path,
            generated_at=date.today(),
            dry_run=DRY_RUN,
            action_tally=tally,
        )
        print(f"jury: digest written to {output_path}")
    except Exception as e:
        print(f"jury: write failed: {e}", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
